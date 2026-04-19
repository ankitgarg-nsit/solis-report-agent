"""Environment-driven config."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    solis_user: str
    solis_pass: str
    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_to: str
    whatsapp_template_name: str
    timezone: str
    plant_name: str | None


def load() -> Config:
    def req(key: str) -> str:
        v = os.environ.get(key)
        if not v:
            raise SystemExit(f"Missing env var: {key}")
        return v

    return Config(
        solis_user=req("SOLIS_USER"),
        solis_pass=req("SOLIS_PASS"),
        whatsapp_token=req("WHATSAPP_TOKEN"),
        whatsapp_phone_number_id=req("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_to=req("WHATSAPP_TO"),
        whatsapp_template_name=os.environ.get("WHATSAPP_TEMPLATE_NAME", "solar_daily_report"),
        timezone=os.environ.get("TIMEZONE", "Asia/Kolkata"),
        plant_name=os.environ.get("PLANT_NAME"),
    )
