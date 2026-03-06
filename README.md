# Windows Stock Watcher

This app checks a product page on a schedule and alerts you when it appears to be in stock.

## Configure

Edit `config.json`:
- `url`: product page URL.
- `check_every_seconds`: how often to check.
- `css_selector`: optional; use a selector for the stock status element (recommended).
- `in_stock_keywords` / `out_of_stock_keywords`: phrases used to decide stock state.
- `open_browser_on_in_stock`: opens the product page automatically on alert.
- `email_notifications`: SMTP settings (Fastmail example is included).

Tip: open the page in browser DevTools and inspect the stock text. Put that selector in `css_selector` for better accuracy.

## Fastmail email alerts

1. In Fastmail, create an app password for SMTP (Settings -> Password & Security -> App passwords).
2. In PowerShell, set the app password in your current session:

```powershell
$env:FASTMAIL_APP_PASSWORD = "your-fastmail-app-password"
```

3. Update these fields in `config.json`:
- `email_notifications.username`
- `email_notifications.from_address`
- `email_notifications.to_addresses`

Default Fastmail SMTP values in the sample config:
- `smtp_server`: `smtp.fastmail.com`
- `smtp_port`: `465`
- `use_ssl`: `true`
- `use_starttls`: `false`

## Run with Python (dev mode)

```powershell
pip install -r requirements.txt
python stock_watcher.py
```

GUI mode:

```powershell
python stock_watcher_gui.py
```

## Build standalone EXE (no Python needed on target machine)

Build machine steps:

```powershell
pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Outputs:
- `dist\\stock-watcher.exe` (CLI)
- `dist\\stock-watcher-gui.exe` (GUI)

Deploy steps:
- Copy your chosen EXE and `config.json` to the target Windows machine.
- Set `FASTMAIL_APP_PASSWORD` on that machine if using email alerts.
- Run the EXE directly.

Important: the app reads `config.json` from the same folder as the EXE.

## GUI features

The GUI includes:
- Current stock state (`IN STOCK`, `Out of stock`, `Unknown`, `Error`)
- Last check time
- Last in-stock time (current session)
- Next scheduled check time
- Recent check history with detection source/errors

## Optional: run at login

Create a shortcut in your Startup folder that points to your chosen EXE.

Startup folder path:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

## Notes

- If email is misconfigured, desktop toast alerts still work and the app logs the email error.
- Some stores render stock status with JavaScript after page load. If this app always shows `unknown`, the page likely needs a browser-automation version (Playwright/Selenium).
- Respect website terms and avoid very aggressive check intervals.
