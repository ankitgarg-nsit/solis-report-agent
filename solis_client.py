"""Scrape SolisCloud for yesterday's generation + alerts.

The SolisCloud web portal is a wujie (micro-frontend) wrapper around a
Vue 2 app hosted in an iframe — so DOM clicks from the outer frame can't
reach the plant detail widgets. Instead we piggy-back on the browser's
authenticated session and talk to the internal XHR endpoints the SPA
itself uses.

The API uses per-endpoint request signing (AWS-style:
`authorization: WEB <keyid>:<hmac>` plus `content-md5`). The sign is
time-limited and includes method+path, so headers can't be replayed
across endpoints. Workaround:

  * For /api/station/stationAllEnergy we replay with yesterday's date —
    body fields aren't part of the signature on this endpoint.
  * For /api/station/detailMix we just read the response that the SPA
    naturally fetches when we load the plant detail page. Current inverter
    state is what we want for the alert line anyway.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import (
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

SOLIS_LOGIN_URL = "https://www.soliscloud.com/#/login"
SOLIS_PLANT_URL_TEMPLATE = (
    "https://www.soliscloud.com/station/stationDetails/generalSituation/{plant_id}"
)

API_ENERGY = "https://v3.soliscloud.com/api/station/stationAllEnergy"

INVERTER_STATE = {1: "online", 2: "offline", 3: "alarm"}


@dataclass
class DailyReport:
    report_date: date
    generation_kwh: float | None
    alerts: list[str]
    raw_text: str  # kept for debug; now holds the raw energy JSON


def _today_in_tz(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def _login(page: Page, user: str, password: str) -> None:
    """Known-working login flow (preserved from earlier iterations)."""
    page.goto(SOLIS_LOGIN_URL, wait_until="networkidle")
    page.wait_for_selector("input[placeholder='Username/Email']", timeout=15000)

    page.locator("input[placeholder='Username/Email']").first.fill(user)
    page.locator("input[placeholder='Password']").first.fill(password)

    # Toggle the "I agree" checkbox via native DOM click so Vue's reactivity
    # enables the Login button (synthetic events from Playwright are ignored).
    page.evaluate(
        """() => {
            for (const lbl of document.querySelectorAll('label.el-checkbox')) {
                if (!lbl.classList.contains('is-checked')) lbl.click();
            }
        }"""
    )
    page.wait_for_timeout(400)

    # Two Login buttons exist in the DOM — use :visible filter.
    page.locator("button.el-button--primary:has-text('Login'):visible").first.click()
    page.wait_for_url(lambda u: "login" not in u, timeout=20000)


def _capture_plant_session(
    page: Page, plant_id: str
) -> tuple[dict[str, str], dict | None]:
    """Navigate to the plant detail page. Capture two things during the
    natural page load:

    1. Headers on the stationAllEnergy POST (for replay with yesterday's date).
       These headers include a signed Authorization that is time-limited but
       body-independent, so we can change beginTime and the server accepts it.
    2. Response body of detailMix (gives us current inverter state — no need
       to replay this one, today's state is what we want for the alerts line).

    Other endpoints (alarmReadAll, etc.) use different signatures we can't
    reuse, so we simply take whatever fires naturally.
    """
    energy_headers: dict[str, str] = {}
    detail_response: dict | None = None

    def on_request(req):
        if "/api/station/stationAllEnergy" in req.url and req.method == "POST":
            energy_headers.update(dict(req.headers))

    def on_response(resp):
        nonlocal detail_response
        if "/api/station/detailMix" in resp.url and resp.status == 200:
            try:
                detail_response = json.loads(resp.text())
            except Exception:
                pass

    page.on("request", on_request)
    page.on("response", on_response)
    try:
        with page.expect_request(
            lambda r: "/api/station/stationAllEnergy" in r.url
            and r.method == "POST",
            timeout=30000,
        ):
            page.goto(
                SOLIS_PLANT_URL_TEMPLATE.format(plant_id=plant_id),
                wait_until="domcontentloaded",
            )
        # Let other natural calls (detailMix, etc.) finish.
        page.wait_for_timeout(2500)
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

    # Strip pseudo-headers and Cookie (Playwright inherits cookies itself).
    clean_headers = {
        k: v
        for k, v in energy_headers.items()
        if not k.startswith(":")
        and k.lower() not in {"cookie", "content-length", "host"}
    }
    return clean_headers, detail_response


def _post_json(page: Page, url: str, body: dict, headers: dict[str, str]) -> dict:
    resp = page.request.post(
        url,
        data=json.dumps(body),
        headers={**headers, "Content-Type": "application/json;charset=UTF-8"},
    )
    if resp.status != 200:
        raise RuntimeError(
            f"POST {url} → HTTP {resp.status}: {resp.text()[:400]}"
        )
    try:
        parsed = json.loads(resp.text())
    except Exception as e:
        raise RuntimeError(f"POST {url} returned non-JSON: {resp.text()[:400]}") from e
    if not parsed.get("success"):
        raise RuntimeError(f"POST {url} → unsuccessful: {parsed}")
    return parsed.get("data") or {}


def _fetch_energy(page: Page, plant_id: str, target_day: date, headers: dict) -> float | None:
    body = {
        "type": 0,
        "id": plant_id,
        "beginTime": target_day.isoformat(),
        "localTime": int(time.time() * 1000),
        "localTimeZone": 5.5,
        "language": "2",
    }
    data = _post_json(page, API_ENERGY, body, headers)
    energy = data.get("energy")
    unit = (data.get("energyStr") or "kWh").lower()
    if energy is None:
        return None
    energy_kwh = float(energy)
    if unit == "mwh":
        energy_kwh *= 1000.0
    return round(energy_kwh, 2)


def _alerts_from_detail(detail_response: dict | None) -> list[str]:
    """Derive alert lines from the detailMix response captured during page load.

    The API uses per-endpoint request signing that's not trivially reusable
    across endpoints, so we don't try to call detailMix again — we just use
    what the SPA fetched for us. That response gives current inverter state,
    which is what a daily report should flag anyway.
    """
    if not detail_response:
        return ["state unavailable (detailMix not captured)"]
    data = (detail_response.get("data") or {}) if isinstance(detail_response, dict) else {}
    alerts: list[str] = []
    state = data.get("state")
    if state in (2, 3):
        alerts.append(f"Inverter {INVERTER_STATE[state]}")
    station_name = data.get("stationName")
    if station_name and state in (2, 3):
        alerts[-1] = f"{alerts[-1]} ({station_name})"
    return alerts


def fetch_yesterday(
    user: str,
    password: str,
    tz: str,
    plant_id: str,
    screenshot_dir: Path | None = None,
) -> DailyReport:
    yesterday = _today_in_tz(tz) - timedelta(days=1)
    artifacts = screenshot_dir
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            _login(page, user, password)
            if artifacts:
                page.screenshot(path=str(artifacts / "01-after-login.png"))

            headers, detail_response = _capture_plant_session(page, plant_id)
            if artifacts:
                page.screenshot(path=str(artifacts / "02-plant-page.png"))
                (artifacts / "02-api-headers.json").write_text(
                    json.dumps(headers, indent=2), encoding="utf-8"
                )
                if detail_response:
                    (artifacts / "02-detail-response.json").write_text(
                        json.dumps(detail_response, indent=2), encoding="utf-8"
                    )

            generation_kwh = _fetch_energy(page, plant_id, yesterday, headers)
            alerts = _alerts_from_detail(detail_response)

            raw = json.dumps(
                {
                    "yesterday": yesterday.isoformat(),
                    "generation_kwh": generation_kwh,
                    "alerts": alerts,
                },
                indent=2,
            )
            if artifacts:
                (artifacts / "03-result.json").write_text(raw, encoding="utf-8")

            return DailyReport(
                report_date=yesterday,
                generation_kwh=generation_kwh,
                alerts=alerts,
                raw_text=raw,
            )
        except PWTimeout as e:
            if artifacts:
                page.screenshot(path=str(artifacts / "error.png"), full_page=True)
                (artifacts / "error.html").write_text(
                    page.content(), encoding="utf-8"
                )
            raise RuntimeError(f"Solis scrape timed out: {e}") from e
        finally:
            ctx.close()
            browser.close()
