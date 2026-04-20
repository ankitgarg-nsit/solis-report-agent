"""Playwright scraper for SolisCloud daily generation + alerts.

DEBUG MODE: dumps full HTML, screenshots, and complete network log on every run
so we can identify the internal XHR endpoints and replay them directly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright


SOLIS_LOGIN_URL = "https://www.soliscloud.com/#/login"
SOLIS_PLANT_URL_TEMPLATE = "https://www.soliscloud.com/station/stationDetails/generalSituation/{plant_id}"


@dataclass
class DailyReport:
    report_date: date
    generation_kwh: float | None
    alerts: list[str]
    raw_text: str


def _today_in_tz(tz: str) -> date:
    from datetime import datetime
    return datetime.now(ZoneInfo(tz)).date()


def _login(page: Page, user: str, password: str, screenshot_dir: Path | None = None) -> None:
    page.goto(SOLIS_LOGIN_URL, wait_until="networkidle")
    page.wait_for_selector("input[placeholder='Username/Email']", timeout=15000)

    # Fill credentials
    page.locator("input[placeholder='Username/Email']").first.fill(user)
    page.locator("input[placeholder='Password']").first.fill(password)

    # Toggle the "I have read and agree" checkbox by clicking its __inner span.
    # Element-UI hides the real <input>, so we must click the visual marker.
    # The "Remember" checkbox is already checked; we want the SECOND one.
    try:
        # Find all el-checkbox labels; click the inner of the unchecked one
        inners = page.locator("label.el-checkbox:not(.is-checked) .el-checkbox__inner").all()
        for inner in inners:
            try:
                inner.click(force=True, timeout=3000)
                print("DEBUG: clicked unchecked checkbox inner")
                break
            except Exception as e:
                print(f"DEBUG: checkbox inner click failed: {e}")
    except Exception as e:
        print(f"DEBUG: checkbox enumeration failed: {e}")

    if screenshot_dir:
        page.screenshot(path=str(screenshot_dir / "00-login-filled.png"), full_page=True)

    # Now click Login; wait for the user/userLogin POST to fire
    # (that's the real indicator that the form submitted)
    try:
        with page.expect_response(
            lambda r: "/api/user/userLogin" in r.url or "/api/login" in r.url,
            timeout=15000,
        ):
            page.get_by_role("button", name=re.compile("^log ?in$", re.I)).first.click()
        print("DEBUG: login POST fired")
    except Exception as e:
        print(f"DEBUG: login POST did NOT fire in 15s: {e}")
        # Fallback: just click and wait
        page.get_by_role("button", name=re.compile("^log ?in$", re.I)).first.click()

    # Wait for navigation away from login
    try:
        page.wait_for_url(lambda u: "login" not in u, timeout=20000)
        print(f"DEBUG: login succeeded, now at {page.url}")
    except Exception:
        print(f"DEBUG: still on login page after click — URL: {page.url}")
        page.wait_for_load_state("networkidle")


def _extract_kwh(text: str) -> float | None:
    m = re.search(
        r"Operating Data[\s\S]{0,1200}?Daily Yield[\s\S]{0,80}?([\d,]+\.?\d*)\s*(k|M)Wh",
        text, re.I,
    )
    if m:
        value = float(m.group(1).replace(",", ""))
        if m.group(2).upper() == "M":
            value *= 1000
        return value
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
            continue
        if re.search(r"\bfault\b|\boffline\b|\berror\b|\bwarning\b", line, re.I):
            alerts.append(line)
    seen, out = set(), []
    for a in alerts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:3]


def _dump_arrow_candidates(page: Page, path: Path) -> None:
    """Find all plausible prev-date arrow candidates and dump metadata."""
    try:
        candidates_raw = page.evaluate("""
        () => {
            const selectors = [
                '[class*="arrow-left"]',
                '[class*="arrow"]',
                '[class*="prev"]',
                'i[class*="icon"]',
                'span[class*="icon"]',
                'button',
            ];
            const seen = new Set();
            const out = [];
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    if (seen.has(el)) continue;
                    seen.add(el);
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    // Climb up to find first ancestor with visible text
                    let ancestor = el;
                    let ancestor_text = '';
                    for (let i = 0; i < 5 && ancestor; i++) {
                        const t = (ancestor.textContent || '').trim();
                        if (t && t.length < 200) { ancestor_text = t; break; }
                        ancestor = ancestor.parentElement;
                    }
                    out.push({
                        tag: el.tagName,
                        class: el.className,
                        text: (el.textContent || '').trim().slice(0, 100),
                        aria_label: el.getAttribute('aria-label') || '',
                        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                        ancestor_text: ancestor_text.slice(0, 200),
                    });
                    if (out.length > 100) break;
                }
                if (out.length > 100) break;
            }
            return out;
        }
        """)
        path.write_text(json.dumps(candidates_raw, indent=2), encoding="utf-8")
    except Exception as e:
        path.write_text(json.dumps({"error": str(e)}), encoding="utf-8")


def fetch_yesterday(
    user: str,
    password: str,
    tz: str,
    plant_id: str,
    screenshot_dir: Path | None = None,
) -> DailyReport:
    yesterday = _today_in_tz(tz) - timedelta(days=1)

    network_log: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # Network logging — capture every response with body (if JSON/text)
        def on_response(response):
            try:
                req = response.request
                entry = {
                    "url": response.url,
                    "method": req.method,
                    "status": response.status,
                    "request_post_data": (req.post_data or "")[:5000],
                    "content_type": response.headers.get("content-type", ""),
                }
                ct = entry["content_type"].lower()
                if ("json" in ct or "text" in ct) and entry["status"] < 400:
                    try:
                        entry["response_body"] = response.text()[:30000]
                    except Exception:
                        entry["response_body"] = "<failed to read>"
                network_log.append(entry)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            if screenshot_dir:
                screenshot_dir.mkdir(parents=True, exist_ok=True)

            _login(page, user, password, screenshot_dir)

            page.goto(SOLIS_PLANT_URL_TEMPLATE.format(plant_id=plant_id), wait_until="networkidle")
            page.wait_for_timeout(8000)

            print(f"DEBUG: URL after nav: {page.url}")
            print(f"DEBUG: Title: {page.title()}")

            if screenshot_dir:
                page.screenshot(path=str(screenshot_dir / "01-after-nav.png"), full_page=True)
                _dump_arrow_candidates(page, screenshot_dir / "02-arrow-left-candidates.json")
                (screenshot_dir / "03-page-before-click.html").write_text(
                    page.content(), encoding="utf-8"
                )

            # Attempt prev-arrow click (best-effort, won't fail the run)
            for selector in [
                ".el-icon-arrow-left",
                "i.el-icon-arrow-left",
                "[class*='arrow-left']",
                "button:has-text('<')",
            ]:
                try:
                    loc = page.locator(selector).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click(timeout=3000)
                        print(f"DEBUG: clicked with selector: {selector}")
                        page.wait_for_timeout(4000)
                        break
                except Exception as e:
                    print(f"DEBUG: selector {selector} failed: {e}")

            text = page.inner_text("body")
            print(f"DEBUG: body text first 800 chars: {text[:800]}")

            if screenshot_dir:
                page.screenshot(path=str(screenshot_dir / "04-after-click.png"), full_page=True)
                (screenshot_dir / "05-page-after-click.html").write_text(
                    page.content(), encoding="utf-8"
                )

            return DailyReport(
                report_date=yesterday,
                generation_kwh=_extract_kwh(text),
                alerts=_extract_alerts(text),
                raw_text=text[:3000],
            )
        except PWTimeout as e:
            if screenshot_dir:
                page.screenshot(path=str(screenshot_dir / "error.png"), full_page=True)
            raise RuntimeError(f"Solis scrape timed out: {e}") from e
        finally:
            # Always dump the network log
            if screenshot_dir:
                try:
                    with open(screenshot_dir / "network.jsonl", "w", encoding="utf-8") as f:
                        for entry in network_log:
                            f.write(json.dumps(entry) + "\n")
                except Exception as e:
                    print(f"DEBUG: failed to write network log: {e}")
            ctx.close()
            browser.close()
