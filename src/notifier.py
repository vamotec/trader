import logging
from datetime import datetime
from email.header import Header
from zoneinfo import ZoneInfo

import httpx

from config import NTFY_URL, SignalLevel

log = logging.getLogger(__name__)
ET  = ZoneInfo("America/New_York")

PRIORITY_MAP = {
    "INFO":     "low",
    "WARNING":  "default",
    "ALERT":    "high",
    "CRITICAL": "urgent",
}

TAGS_MAP = {
    "INFO":     "bell",
    "WARNING":  "warning",
    "ALERT":    "rotating_light",
    "CRITICAL": "skull",
}


class Notifier:
    def init(self):
        if not NTFY_URL:
            log.warning("No NTFY_URL — notifications disabled")
        else:
            log.info("ntfy ready: %s", NTFY_URL)

    async def send(self, level: SignalLevel, title: str, body: str):
        now  = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
        full = f"{body}\n\n{now}"

        log.info("[%s] %s | %s", level, title, body)

        if not NTFY_URL:
            return

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(
                    NTFY_URL,
                    content = full.encode(),
                    headers = {
                        "Title":    Header(title, "utf-8").encode(),
                        "Priority": PRIORITY_MAP[level],
                        "Tags":     TAGS_MAP[level],
                    },
                )
        except Exception as e:
            log.error("ntfy send failed: %s", e)


notifier = Notifier()