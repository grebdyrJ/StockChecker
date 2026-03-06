import json
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from os import getenv
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

try:
    from win10toast import ToastNotifier
except Exception:
    ToastNotifier = None


if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class EmailConfig:
    enabled: bool
    smtp_server: str
    smtp_port: int
    use_starttls: bool
    use_ssl: bool
    username: str
    password: str
    from_address: str
    to_addresses: list[str]
    subject_prefix: str


@dataclass
class Config:
    url: str
    check_every_seconds: int
    request_timeout_seconds: int
    in_stock_keywords: list[str]
    out_of_stock_keywords: list[str]
    css_selector: Optional[str]
    notify_every_in_stock_check: bool
    open_browser_on_in_stock: bool
    user_agent: str
    email_notifications: Optional[EmailConfig]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def load_email_config(raw: dict) -> Optional[EmailConfig]:
    email_raw = raw.get("email_notifications")
    if not email_raw:
        return None

    password = email_raw.get("password", "")
    password_env = email_raw.get("password_env")
    if password_env:
        password = getenv(password_env, password)

    to_addresses = email_raw.get("to_addresses", [])
    if isinstance(to_addresses, str):
        to_addresses = [to_addresses]

    return EmailConfig(
        enabled=bool(email_raw.get("enabled", False)),
        smtp_server=email_raw.get("smtp_server", "smtp.fastmail.com"),
        smtp_port=int(email_raw.get("smtp_port", 465)),
        use_starttls=bool(email_raw.get("use_starttls", False)),
        use_ssl=bool(email_raw.get("use_ssl", True)),
        username=email_raw.get("username", ""),
        password=password,
        from_address=email_raw.get("from_address", email_raw.get("username", "")),
        to_addresses=to_addresses,
        subject_prefix=email_raw.get("subject_prefix", "[Stock Alert]"),
    )


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return Config(
        url=raw["url"],
        check_every_seconds=int(raw.get("check_every_seconds", 60)),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 20)),
        in_stock_keywords=[s.lower() for s in raw.get("in_stock_keywords", ["in stock"])],
        out_of_stock_keywords=[s.lower() for s in raw.get("out_of_stock_keywords", ["out of stock", "sold out", "unavailable"])],
        css_selector=raw.get("css_selector"),
        notify_every_in_stock_check=bool(raw.get("notify_every_in_stock_check", False)),
        open_browser_on_in_stock=bool(raw.get("open_browser_on_in_stock", False)),
        user_agent=raw.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        ),
        email_notifications=load_email_config(raw),
    )


def fetch_page(url: str, timeout: int, user_agent: str) -> str:
    headers = {"User-Agent": user_agent}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def status_from_text(text: str, in_keywords: list[str], out_keywords: list[str]) -> str:
    t = normalize_text(text)
    has_out = any(k in t for k in out_keywords)
    has_in = any(k in t for k in in_keywords)
    if has_out:
        return "out_of_stock"
    if has_in:
        return "in_stock"
    return "unknown"


def detect_stock_status(html: str, cfg: Config) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    # First choice: inspect a specific stock element if the site has one.
    if cfg.css_selector:
        selected = soup.select_one(cfg.css_selector)
        if selected:
            text = selected.get_text(" ", strip=True)
            status = status_from_text(text, cfg.in_stock_keywords, cfg.out_of_stock_keywords)
            if status != "unknown":
                return status, f"selector `{cfg.css_selector}` text: {text!r}"

    # Fallback: inspect the whole page text.
    page_text = soup.get_text(" ", strip=True)
    status = status_from_text(page_text, cfg.in_stock_keywords, cfg.out_of_stock_keywords)
    return status, "full-page text check"


def notify(title: str, message: str) -> None:
    if ToastNotifier is not None:
        try:
            ToastNotifier().show_toast(title, message, duration=10, threaded=True)
            return
        except Exception:
            pass
    # Fallback: terminal bell + message
    print("\a", end="", flush=True)
    log(f"{title}: {message}")


def send_email_notification(cfg: EmailConfig, subject: str, body: str) -> None:
    if not cfg.enabled:
        return
    if not cfg.username or not cfg.password or not cfg.to_addresses:
        raise ValueError("Email config missing username/password/to_addresses")

    msg = EmailMessage()
    msg["Subject"] = f"{cfg.subject_prefix} {subject}".strip()
    msg["From"] = cfg.from_address
    msg["To"] = ", ".join(cfg.to_addresses)
    msg.set_content(body)

    if cfg.use_ssl:
        with smtplib.SMTP_SSL(cfg.smtp_server, cfg.smtp_port, timeout=20) as server:
            server.login(cfg.username, cfg.password)
            server.send_message(msg)
        return

    with smtplib.SMTP(cfg.smtp_server, cfg.smtp_port, timeout=20) as server:
        if cfg.use_starttls:
            server.starttls()
        server.login(cfg.username, cfg.password)
        server.send_message(msg)


def open_product_page(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


def main() -> int:
    if not CONFIG_PATH.exists():
        print("Missing config.json. Copy the sample from README and customize it.")
        return 1

    cfg = load_config(CONFIG_PATH)
    log(f"Starting stock watcher for: {cfg.url}")
    if cfg.css_selector:
        log(f"Using css_selector: {cfg.css_selector}")

    last_status = None
    while True:
        try:
            html = fetch_page(cfg.url, cfg.request_timeout_seconds, cfg.user_agent)
            status, source = detect_stock_status(html, cfg)
            log(f"Status={status} ({source})")

            should_notify = False
            if status == "in_stock":
                if cfg.notify_every_in_stock_check:
                    should_notify = True
                elif last_status != "in_stock":
                    should_notify = True

            if should_notify:
                notify("Stock Alert", "Item appears to be IN STOCK. Check now.")
                if cfg.email_notifications and cfg.email_notifications.enabled:
                    email_subject = "Item is in stock"
                    email_body = (
                        f"Detected in stock at {now()}.\n\n"
                        f"URL: {cfg.url}\n"
                        f"Detection: {source}\n"
                    )
                    try:
                        send_email_notification(cfg.email_notifications, email_subject, email_body)
                        log("Email notification sent.")
                    except Exception as e:
                        log(f"Email notification error: {e}")
                if cfg.open_browser_on_in_stock:
                    open_product_page(cfg.url)

            last_status = status
        except requests.HTTPError as e:
            log(f"HTTP error: {e}")
        except requests.RequestException as e:
            log(f"Network error: {e}")
        except Exception as e:
            log(f"Unexpected error: {e}")

        time.sleep(cfg.check_every_seconds)


if __name__ == "__main__":
    sys.exit(main())

