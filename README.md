# Windows Stock Watcher

This script checks a product page on a schedule and alerts you when it appears to be in stock.

## 1) Install Python packages

```powershell
pip install requests beautifulsoup4 win10toast
```

## 2) Configure

Edit `config.json`:
- `url`: product page URL.
- `check_every_seconds`: how often to check.
- `css_selector`: optional; use a selector for the stock status element (recommended).
- `in_stock_keywords` / `out_of_stock_keywords`: phrases used to decide stock state.
- `open_browser_on_in_stock`: opens the product page automatically on alert.
- `email_notifications`: SMTP settings (Fastmail example is included).

Tip: open the page in browser DevTools and inspect the stock text. Put that selector in `css_selector` for better accuracy.

## 3) Fastmail email alerts

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

## 4) Run

```powershell
python stock_watcher.py
```

Leave the terminal open while monitoring.

## 5) Optional: run in background at login

Create a shortcut in your Startup folder that runs:

```powershell
python C:\Users\jrryd\OneDrive\Documents\Codex\GPU\stock_watcher.py
```

Startup folder path:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

## Notes

- If email is misconfigured, desktop toast alerts still work and the script logs the email error.
- Some stores render stock status with JavaScript after page load. If this script always shows `unknown`, the page likely needs a browser-automation version (Playwright/Selenium).
- Respect website terms and avoid very aggressive check intervals.
