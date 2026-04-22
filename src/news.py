"""
新闻 NLP 分析
─────────────
抓取 Yahoo Finance RSS → LLM 分析催化剂性质
LLM 主力 DeepSeek，备用 Anthropic（见 llm.py）
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
import xmltodict

from llm import chat
from notifier import notifier

log = logging.getLogger(__name__)

RSS_SOURCES = [
    "https://finance.yahoo.com/rss/headline?s=CAR",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=CAR&region=US&lang=en-US",
]

PROMPT = """\
你是专业量化交易分析师，专注分析 Avis Budget Group (CAR) 的逼空（short squeeze）行情。

最新新闻：
{news_text}

只返回 JSON，不要任何其他文字：
{{
  "new_squeeze_signal":    false,
  "squeeze_ending_signal": false,
  "atm_signal":            false,
  "short_timing":          "wait",
  "confidence":            0.7,
  "reasoning":             "简短原因（中文）",
  "key_points":            ["要点1", "要点2"]
}}

字段说明：
- new_squeeze_signal: 是否有新逼空催化剂（机构增持/利好）
- squeeze_ending_signal: 是否有逼空结束信号（ATM/减持/基本面恶化）
- atm_signal: 是否提到ATM或股票发行
- short_timing: "good"=可做空, "wait"=等待, "avoid"=不要做空\
"""


@dataclass
class CatalystAnalysis:
    new_squeeze_signal:    bool      = False
    squeeze_ending_signal: bool      = False
    atm_signal:            bool      = False
    short_timing:          str       = "wait"
    confidence:            float     = 0.0
    reasoning:             str       = ""
    key_points:            list[str] = field(default_factory=list)


@dataclass
class NewsItem:
    title:       str
    description: str
    url:         str


class NewsAnalyzer:
    def __init__(self):
        self._seen_urls:   set[str]                   = set()
        self._last_result: Optional[CatalystAnalysis] = None

    async def analyze(self) -> Optional[CatalystAnalysis]:
        items     = await self._fetch_news()
        new_items = [i for i in items if i.url not in self._seen_urls]

        if not new_items:
            return self._last_result

        news_text = "\n\n".join(
            f"[{idx+1}] {item.title}\n{item.description}"
            for idx, item in enumerate(new_items[:8])
        )

        try:
            raw = await chat(PROMPT.format(news_text=news_text))
            m   = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                log.warning("LLM returned no JSON: %s", raw[:200])
                return self._last_result

            data   = json.loads(m.group())
            result = CatalystAnalysis(**{
                k: data.get(k, v)
                for k, v in CatalystAnalysis().__dict__.items()
            })
            self._last_result = result

            for item in new_items:
                self._seen_urls.add(item.url)

            await self._notify(result)
            return result

        except Exception as e:
            log.error("News analysis error: %s", e)
            return self._last_result

    async def _notify(self, r: CatalystAnalysis):
        conf   = f"{r.confidence*100:.0f}%"
        points = "\n".join(f"• {p}" for p in r.key_points)

        if r.atm_signal or r.squeeze_ending_signal:
            level = "CRITICAL" if r.atm_signal else "ALERT"
            title = "新闻提及ATM发行" if r.atm_signal else "新闻显示逼空结束信号"
            await notifier.send(
                level, title,
                f"置信度: {conf}\n\n{r.reasoning}\n\n{points}",
            )
        elif r.new_squeeze_signal:
            await notifier.send(
                "WARNING", "⚠️ 新逼空催化剂出现，推迟做空",
                f"{r.reasoning}\n\n{points}",
            )

    async def _fetch_news(self) -> list[NewsItem]:
        items: list[NewsItem] = []
        for url in RSS_SOURCES:
            try:
                async with httpx.AsyncClient(timeout=8) as c:
                    resp = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
                feed    = xmltodict.parse(resp.text)
                entries = feed.get("rss", {}).get("channel", {}).get("item", [])
                if isinstance(entries, dict):
                    entries = [entries]
                for e in entries[:6]:
                    title = e.get("title", "")
                    desc  = re.sub(r"<[^>]+>", "", e.get("description", ""))[:400]
                    link  = e.get("link", "")
                    if title:
                        items.append(NewsItem(title=title, description=desc, url=link))
            except Exception as ex:
                log.warning("RSS fetch failed (%s): %s", url, ex)
        return items

    @property
    def last_result(self) -> Optional[CatalystAnalysis]:
        return self._last_result


news_analyzer = NewsAnalyzer()