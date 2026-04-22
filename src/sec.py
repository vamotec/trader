"""
SEC EDGAR 实时监控
──────────────────
轮询 EDGAR RSS（约1-2分钟延迟，免费无需key）

ATM发行的文件路径：
  8-K  → 披露重大事件（ATM计划启动）
  424B → 招股说明书补充（ATM发行的直接证据）
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
import xmltodict

from notifier import notifier

log = logging.getLogger(__name__)

# Avis Budget Group CIK
CAR_CIK = "0000723612"

# ATM高置信度关键词（出现2个以上即触发）
ATM_KEYWORDS = [
    "at-the-market",
    "equity distribution",
    "aggregate offering price",
    "prospectus supplement",
    "atm offering",
    "capital markets cooperative",
]

# 逼空结束其他信号
SQUEEZE_END_KEYWORDS = [
    "secondary offering",
    "going concern",
    "debt covenant",
    "material weakness",
    "share repurchase termination",
]

HEADERS = {"User-Agent": "CAR-Monitor research@example.com"}


class SECMonitor:
    def __init__(self):
        self._seen:       set[str]  = set()
        self._start_time: datetime  = datetime.now(timezone.utc)

    async def check(self):
        await self._check_form("8-K")
        await self._check_form("424B")

    async def _check_form(self, form_type: str):
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={CAR_CIK}"
            f"&type={form_type}&dateb=&owner=include&count=10&output=atom"
        )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()

            feed    = xmltodict.parse(resp.text)
            entries = feed.get("feed", {}).get("entry", [])
            if isinstance(entries, dict):
                entries = [entries]

            for entry in entries:
                filing_id  = entry.get("id", "")
                title      = entry.get("title", "")
                link_data  = entry.get("link", {})
                link       = link_data.get("@href", "") if isinstance(link_data, dict) else ""
                updated    = entry.get("updated", "")

                if filing_id in self._seen:
                    continue
                self._seen.add(filing_id)

                # 首次启动时跳过超过24h的旧文件
                try:
                    filing_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if filing_dt < self._start_time - timedelta(hours=24):
                        continue
                except ValueError:
                    pass

                await self._analyze(form_type, title, link)

        except Exception as e:
            log.error("SEC %s check error: %s", form_type, e)

    async def _analyze(self, form_type: str, title: str, link: str):
        # 424B 直接是ATM发行最强信号
        if form_type == "424B":
            await notifier.send(
                "CRITICAL",
                "🔴 424B注册文件！ATM发行几乎确认",
                f"424B是ATM发行的直接法律文件\n\n"
                f"📄 {title}\n🔗 {link}\n\n"
                f"⚡ 等开盘第一波反弹再入场，不追跳水",
            )
            return

        # 8-K：下载全文扫描关键词
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(link, headers=HEADERS)
            content = resp.text.lower()
        except Exception as e:
            log.warning("Cannot fetch 8-K content: %s", e)
            content = title.lower()

        atm_hits         = [kw for kw in ATM_KEYWORDS if kw in content]
        squeeze_end_hits = [kw for kw in SQUEEZE_END_KEYWORDS if kw in content]

        if len(atm_hits) >= 2:
            await notifier.send(
                "CRITICAL",
                "🔴 8-K检测到ATM发行信号！",
                f"*这是最重要的做空入场前置信号*\n\n"
                f"📄 {title}\n"
                f"🔍 匹配关键词: {', '.join(atm_hits)}\n"
                f"🔗 {link}\n\n"
                f"⚡ ATM意味着公司将在高位发新股，逼空结构将被打破\n"
                f"等待开盘后第一波反弹再建空仓",
            )
        elif squeeze_end_hits:
            await notifier.send(
                "ALERT",
                "⚠️ 8-K含逼空结束信号",
                f"📄 {title}\n🔍 {', '.join(squeeze_end_hits)}\n🔗 {link}",
            )
        else:
            log.info("New 8-K (no ATM signal): %s", title)


sec_monitor = SECMonitor()