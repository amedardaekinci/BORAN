#!/usr/bin/env python3
"""
Live Client API poller — League of Legends.

Background daemon thread ile 127.0.0.1:2999 API'sini pollar.
Orbwalker cached değeri sıfır latency ile okur.

Kullanım:
    from core.live_client import LiveClientPoller

    poller = LiveClientPoller()
    poller.start()
    ...
    as_val = poller.attack_speed   # cached, block etmez
    poller.stop()
"""

import json
import ssl
import threading
import time
import urllib.request

LIVE_CLIENT_URL = "https://127.0.0.1:2999/liveclientdata/activeplayer"


class LiveClientPoller:
    """Background poller for Live Client API (attack speed)."""

    def __init__(self, poll_interval: float = 1.0):
        self._interval = poll_interval
        self._attack_speed: float = 0.0
        self._game_time: float = 0.0
        self._last_update: float = 0.0
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        # Riot localhost self-signed cert — skip verification
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    @property
    def attack_speed(self) -> float:
        """Cached attack speed. 0.0 if not yet fetched."""
        with self._lock:
            return self._attack_speed

    @property
    def is_valid(self) -> bool:
        """True if recent valid data (<5s old)."""
        with self._lock:
            return self._attack_speed > 0 and (time.time() - self._last_update) < 5.0

    def _poll_once(self):
        """Tek bir API call. Hata sessizce yutulur."""
        try:
            req = urllib.request.Request(LIVE_CLIENT_URL)
            with urllib.request.urlopen(req, timeout=2, context=self._ssl_ctx) as resp:
                data = json.loads(resp.read())
                as_val = data["championStats"]["attackSpeed"]
                if as_val and as_val > 0:
                    with self._lock:
                        self._attack_speed = as_val
                        self._last_update = time.time()
        except Exception:
            pass  # API down, oyun başlamamış, vb.

    def _poll_loop(self):
        """Background thread ana loop."""
        while self._running:
            self._poll_once()
            time.sleep(self._interval)

    def start(self):
        """Background polling thread'i başlat."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Background thread'i durdur."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
