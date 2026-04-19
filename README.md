# Solis Daily Report Agent

Scrapes SolisCloud once a day, sends yesterday's generation + alerts to WhatsApp.

Runs at 07:00 IST via GitHub Actions.

## Required GitHub repo secrets

Settings → Secrets and variables → Actions → New repository secret:

- `SOLIS_USER` — your SolisCloud email
- `SOLIS_PASS` — your SolisCloud password
- `WHATSAPP_TOKEN` — permanent system-user token from Meta
- `WHATSAPP_PHONE_NUMBER_ID` — from Meta WhatsApp API setup
- `WHATSAPP_TO` — your phone in international format, no `+` (e.g. `919876543210`)
- `WHATSAPP_TEMPLATE_NAME` — name of the approved template (default `solar_daily_report`)
- `TIMEZONE` — `Asia/Kolkata`
- `PLANT_NAME` — optional, e.g. `Rooftop 5kW`

## WhatsApp template

Create in Meta Business Manager → WhatsApp Manager → Templates:

- Name: `solar_daily_report`
- Category: Utility
- Language: English
- Body: `Solar report:\n{{1}}`

Wait for Meta approval (usually minutes).

## Run manually

Actions tab → Daily Solis Report → Run workflow.
