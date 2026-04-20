"""Playwright scraper for SolisCloud daily generation + alerts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright


SOLIS_LOGIN_URL = "https://www.soliscloud.com/#/login"
SOLIS_PLANT_URL_TEMPLATE = "https://www.soliscloud.com/#/station/stationDetails/generalSituation/{plant_id}"


@dataclass
class DailyReport:
    report_date: date
    generation_kwh: float | None
    alerts: list[str]
    raw_text: str


def _today_in_tz(tz: str) -> date:
    from datetime import datetime
    return datetime.now(ZoneInfo(tz)).date()


def _login(page: Page, user: str, password: str) -> None:
    page.goto(SOLIS_LOGIN_URL, wait_until="networkidle")
    # Wait for SPA to render the login form
    page.wait_for_selector("input[placeholder='Username/Email']", timeout=15000)

    # Make sure we're on the Account tab (not Verification Code)
    try:
        page.get_by_text("Account", exact=True).first.click()
    except Exception:
        pass

    page.locator("input[placeholder='Username/Email']").first.fill(user)
    page.locator("input[placeholder='Password']").first.fill(password)

    # Check the "I have read and agree to Privacy Policy" checkbox (required)
    try:
        agree = page.get_by_text(re.compile("I have read and agree", re.I)).first
        # The checkbox is a sibling/parent element; click on the label triggers it
        agree.click()
    except Exception:
        # fallback: click the second checkbox on the page (first is "Remember")
        checkboxes = page.locator("input[type='checkbox']")
        if checkboxes.count() >= 2:
            checkboxes.nth(1).check()

    page.get_by_role("button", name=re.compile("^log ?in$", re.I)).first.click()
    # Wait for navigation away from the login page
    try:
        page.wait_for_url(lambda u: "login" not in u, timeout=20000)
    except Exception:
        page.wait_for_load_state("networkidle")


def _extract_kwh(text: str) -> float | None:
    """Extract Daily Yield value from the Operating Data section."""
    # Operating Data ... Daily Yield <num> kWh|MWh
    m = re.search(
        r"Operating Data[\s\S]{0,1200}?Daily Yield[\s\S]{0,80}?([\d,]+\.?\d*)\s*(k|M)Wh",
        text, re.I,
    )
    if m:
        value = float(m.group(1).replace(",", ""))
        if m.group(2).upper() == "M":
            value *= 1000
        return value
    # Fallback: any Daily Yield
    m = re.search(r"Daily Yield[\s\S]{0,80}?([\d,]+\.?\d*)\s*(k|M)Wh", text, re.I)
    if m:
        value = float(m.group(1).replace(",", ""))
        if m.group(2).upper() == "M":
            value *= 1000
        return value
    return None


def _extract_alerts(text: str) -> list[str]:
    alerts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200:
            continue
        if line.lower() in ("alarm", "alert", "alarms", "alerts"):
            continue  # skip menu labels
        if re.search(r"\bfault\b|\boffline\b|\berror\b|\bwarning\b", line, re.I):
            alerts.append(line)
    seen, out = set(), []
    for a in alerts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:3]


def fetch_yesterday(
    user: str,
    password: str,
    tz: str,
    plant_id: str,
    screenshot_dir: Path | None = None,
) -> DailyReport:
    yesterday = _today_in_tz(tz) - timedelta(days=1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            _login(page, user, password)

            # Navigate to the plant detail page
            page.goto(SOLIS_PLANT_URL_TEMPLATE.format(plant_id=plant_id), wait_until="networkidle")
            page.wait_for_timeout(5000)  # let charts/data populate

            # Click date prev-arrow in the Operating Data section to go to yesterday.
            # Element-UI uses .el-icon-arrow-left for navigation arrows.
            try:
                page.locator(".el-icon-arrow-left").first.click(timeout=5000)
                page.wait_for_timeout(4000)
            except Exception:
                # Fallback: look for a button whose sibling contains a date string
                pass

            text = page.inner_text("body")
            if screenshot_dir:
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot_dir / "plant.png"), full_page=True)

            return DailyReport(
                report_date=yesterday,
                generation_kwh=_extract_kwh(text),
                alerts=_extract_alerts(text),
                raw_text=text[:3000],
            )
        except PWTimeout as e:
            if screenshot_dir:
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot_dir / "error.png"), full_page=True)
            raise RuntimeError(f"Solis scrape timed out: {e}") from e
        finally:
            ctx.close()
            browser.close()
