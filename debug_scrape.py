"""Local-only debug driver: runs the SolisCloud scraper and prints the result.

Does NOT need WhatsApp credentials — we're only exercising the scrape path
so we can inspect the generated artifacts (HTML dumps, network log,
arrow-left candidates) under ./artifacts.

Usage (from the repo root):

    .venv\\Scripts\\activate        # Windows
    set SOLIS_USER=you@example.com
    set SOLIS_PASS=yourpassword
    set SOLIS_PLANT_ID=1298491919449994751
    python debug_scrape.py

Or put the three vars in a .env file and run:
    python debug_scrape.py --dotenv
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from solis_client import fetch_yesterday


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        print(f"[debug_scrape] no .env at {path}; relying on shell env")
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    print(f"[debug_scrape] loaded env from {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dotenv", action="store_true", help="Load .env from repo root")
    ap.add_argument("--tz", default=os.environ.get("TIMEZONE", "Asia/Kolkata"))
    args = ap.parse_args()

    if args.dotenv:
        _load_dotenv(Path(__file__).parent / ".env")

    missing = [k for k in ("SOLIS_USER", "SOLIS_PASS", "SOLIS_PLANT_ID") if not os.environ.get(k)]
    if missing:
        print(f"[debug_scrape] missing env vars: {missing}", file=sys.stderr)
        return 2

    artifacts = Path("artifacts")
    print(f"[debug_scrape] writing artifacts to {artifacts.resolve()}")

    report = fetch_yesterday(
        user=os.environ["SOLIS_USER"],
        password=os.environ["SOLIS_PASS"],
        tz=args.tz,
        plant_id=os.environ["SOLIS_PLANT_ID"],
        screenshot_dir=artifacts,
    )

    print()
    print("=== SCRAPE RESULT ===")
    print(f"report_date:     {report.report_date}")
    print(f"generation_kwh:  {report.generation_kwh}")
    print(f"alerts:          {report.alerts}")
    print(f"raw_text (head): {report.raw_text[:300]!r}")
    print()
    print("Artifacts:")
    for p in sorted(artifacts.glob("*")):
        size = p.stat().st_size
        print(f"  {p.name:<40} {size:>10,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
