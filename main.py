"""Entry point: scrape SolisCloud, format, send via WhatsApp."""
import sys
import traceback
from pathlib import Path

from config import load
from solis_client import fetch_yesterday
from report import format_report
from whatsapp import send_template


def main() -> int:
    cfg = load()
    screenshot_dir = Path("artifacts")
    try:
        report = fetch_yesterday(cfg.solis_user, cfg.solis_pass, cfg.timezone, screenshot_dir)
        body = format_report(report, cfg.plant_name)
        print("Report:\n" + body)
        resp = send_template(
            cfg.whatsapp_token,
            cfg.whatsapp_phone_number_id,
            cfg.whatsapp_to,
            cfg.whatsapp_template_name,
            body,
        )
        print(f"WhatsApp send response: {resp}")
        return 0
    except Exception as e:
        traceback.print_exc()
        # Try to notify failure too
        try:
            send_template(
                cfg.whatsapp_token,
                cfg.whatsapp_phone_number_id,
                cfg.whatsapp_to,
                cfg.whatsapp_template_name,
                f"Solar report FAILED: {type(e).__name__}: {e}"[:900],
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
