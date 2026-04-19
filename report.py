"""Format the daily report into a single string for WhatsApp body."""
from solis_client import DailyReport


def format_report(r: DailyReport, plant_name: str | None) -> str:
    header = f"Solar report — {r.report_date.strftime('%d %b %Y')}"
    if plant_name:
        header += f" ({plant_name})"
    gen = f"Generation: {r.generation_kwh:.1f} kWh" if r.generation_kwh is not None else "Generation: unavailable"
    if r.alerts:
        alerts_block = "Alerts:\n- " + "\n- ".join(r.alerts)
    else:
        alerts_block = "Alerts: none"
    return f"{header}\n{gen}\n{alerts_block}"
