"""Format the daily report into a single string for WhatsApp body."""
from solis_client import DailyReport


def format_report(r: DailyReport, plant_name: str | None) -> str:
    # WhatsApp template params can't contain newlines; join with separators
    date_str = r.report_date.strftime('%d %b %Y')
    gen = f"{r.generation_kwh:.1f} kWh" if r.generation_kwh is not None else "unavailable"
    if r.alerts:
        # Join alerts with commas, cap total length
        alerts_str = "; ".join(r.alerts)[:300]
    else:
        alerts_str = "none"
    return f"{date_str} · Generation: {gen} · Alerts: {alerts_str}"
