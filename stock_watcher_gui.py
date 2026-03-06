import queue
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from tkinter import ttk

import requests

from stock_watcher import (
    CONFIG_PATH,
    detect_stock_status,
    fetch_page,
    load_config,
    notify,
    open_product_page,
    send_email_notification,
)


@dataclass
class CheckEvent:
    timestamp: datetime
    status: str
    source: str
    error: str | None = None


class StockWatcherGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Stock Watcher")
        self.root.geometry("900x560")

        self.cfg = load_config(CONFIG_PATH)
        self.last_status: str | None = None
        self.last_in_stock_at: datetime | None = None

        self.history: deque[CheckEvent] = deque(maxlen=40)
        self.queue: queue.Queue[CheckEvent] = queue.Queue()

        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self._set_idle_state()
        self._schedule_queue_pump()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(main)
        top.pack(fill=tk.X)

        ttk.Label(top, text="URL:", width=10).grid(row=0, column=0, sticky=tk.W)
        self.url_var = tk.StringVar(value=self.cfg.url)
        ttk.Entry(top, textvariable=self.url_var, state="readonly").grid(
            row=0, column=1, sticky=tk.EW, padx=(0, 10)
        )
        top.columnconfigure(1, weight=1)

        button_frame = ttk.Frame(top)
        button_frame.grid(row=0, column=2, sticky=tk.E)
        self.start_btn = ttk.Button(button_frame, text="Start", command=self.start_monitoring)
        self.stop_btn = ttk.Button(button_frame, text="Stop", command=self.stop_monitoring)
        self.check_now_btn = ttk.Button(button_frame, text="Check Now", command=self.check_now)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn.pack(side=tk.LEFT, padx=4)
        self.check_now_btn.pack(side=tk.LEFT, padx=4)

        status_card = ttk.LabelFrame(main, text="Current State", padding=12)
        status_card.pack(fill=tk.X, pady=(12, 8))

        self.state_var = tk.StringVar(value="Idle")
        self.last_check_var = tk.StringVar(value="-")
        self.last_in_stock_var = tk.StringVar(value="Never (this session)")
        self.next_check_var = tk.StringVar(value="-")

        self._row(status_card, 0, "Status", self.state_var)
        self._row(status_card, 1, "Last Check", self.last_check_var)
        self._row(status_card, 2, "Last In Stock", self.last_in_stock_var)
        self._row(status_card, 3, "Next Check", self.next_check_var)

        history_card = ttk.LabelFrame(main, text="Recent Checks", padding=10)
        history_card.pack(fill=tk.BOTH, expand=True)

        self.history_list = tk.Listbox(history_card, height=15)
        self.history_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(history_card, orient=tk.VERTICAL, command=self.history_list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_list.configure(yscrollcommand=scrollbar.set)

        self.footer_var = tk.StringVar(value=f"Config: {CONFIG_PATH}")
        ttk.Label(main, textvariable=self.footer_var).pack(fill=tk.X, pady=(8, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _row(self, parent: ttk.LabelFrame, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(parent, text=f"{label}:", width=14).grid(row=row, column=0, sticky=tk.W, pady=2)
        ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky=tk.W, pady=2)

    def _set_idle_state(self) -> None:
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _set_running_state(self) -> None:
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def start_monitoring(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.stop_event.clear()
        self.worker = threading.Thread(target=self._monitor_loop, daemon=True)
        self.worker.start()
        self._set_running_state()
        self.footer_var.set(f"Monitoring every {self.cfg.check_every_seconds}s")

    def stop_monitoring(self) -> None:
        self.stop_event.set()
        self._set_idle_state()
        self.next_check_var.set("-")
        self.footer_var.set("Monitoring stopped")

    def check_now(self) -> None:
        threading.Thread(target=self._single_check, daemon=True).start()

    def _single_check(self) -> None:
        event = self._run_check()
        self.queue.put(event)

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            start = time.time()
            event = self._run_check()
            self.queue.put(event)

            elapsed = time.time() - start
            wait_s = max(self.cfg.check_every_seconds - elapsed, 0)
            next_time = datetime.now().timestamp() + wait_s
            self.queue.put(
                CheckEvent(
                    timestamp=datetime.fromtimestamp(next_time),
                    status="next_check",
                    source="",
                )
            )
            if self.stop_event.wait(wait_s):
                break

    def _run_check(self) -> CheckEvent:
        ts = datetime.now()
        try:
            html = fetch_page(self.cfg.url, self.cfg.request_timeout_seconds, self.cfg.user_agent)
            status, source = detect_stock_status(html, self.cfg)
            self._handle_notifications(status, source)
            return CheckEvent(timestamp=ts, status=status, source=source)
        except requests.HTTPError as exc:
            return CheckEvent(timestamp=ts, status="error", source="http", error=str(exc))
        except requests.RequestException as exc:
            return CheckEvent(timestamp=ts, status="error", source="network", error=str(exc))
        except Exception as exc:
            return CheckEvent(timestamp=ts, status="error", source="unexpected", error=str(exc))

    def _handle_notifications(self, status: str, source: str) -> None:
        should_notify = False
        if status == "in_stock":
            if self.cfg.notify_every_in_stock_check:
                should_notify = True
            elif self.last_status != "in_stock":
                should_notify = True

        if not should_notify:
            self.last_status = status
            return

        notify("Stock Alert", "Item appears to be IN STOCK. Check now.")
        if self.cfg.email_notifications and self.cfg.email_notifications.enabled:
            email_subject = "Item is in stock"
            email_body = (
                f"Detected in stock at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
                f"URL: {self.cfg.url}\n"
                f"Detection: {source}\n"
            )
            try:
                send_email_notification(self.cfg.email_notifications, email_subject, email_body)
            except Exception as exc:
                self.queue.put(
                    CheckEvent(
                        timestamp=datetime.now(),
                        status="error",
                        source="email",
                        error=f"Email notification error: {exc}",
                    )
                )

        if self.cfg.open_browser_on_in_stock:
            open_product_page(self.cfg.url)

        self.last_status = status

    def _schedule_queue_pump(self) -> None:
        self._drain_queue()
        self.root.after(250, self._schedule_queue_pump)

    def _drain_queue(self) -> None:
        while True:
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                break

            if event.status == "next_check":
                self.next_check_var.set(event.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
                continue

            self.history.appendleft(event)
            self._render_event(event)
            self._render_history()

    def _render_event(self, event: CheckEvent) -> None:
        t = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        self.last_check_var.set(t)

        if event.status == "in_stock":
            self.state_var.set("IN STOCK")
            self.last_in_stock_at = event.timestamp
            self.last_in_stock_var.set(t)
        elif event.status == "out_of_stock":
            self.state_var.set("Out of stock")
        elif event.status == "unknown":
            self.state_var.set("Unknown")
        elif event.status == "error":
            self.state_var.set("Error")
            self.footer_var.set(event.error or "Unknown error")

        if event.status != "error":
            self.footer_var.set(f"Last source: {event.source}")

    def _render_history(self) -> None:
        self.history_list.delete(0, tk.END)
        for item in self.history:
            t = item.timestamp.strftime("%H:%M:%S")
            if item.status == "error":
                line = f"[{t}] ERROR ({item.source}) {item.error}"
            else:
                line = f"[{t}] {item.status} ({item.source})"
            self.history_list.insert(tk.END, line)

    def on_close(self) -> None:
        self.stop_monitoring()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    StockWatcherGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
