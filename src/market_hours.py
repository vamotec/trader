"""
美股交易时段判断
─────────────────
只判断"周一~周五 + 时间窗口"，假日靠 IBKR 自身报错兜底（反正 wrapper 日志已静音）。
期权开盘推迟 20 分钟，等 OCC 刷 OI + 首波流动性稳定。
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

OPTIONS_OPEN  = time(9, 50)   # 9:30 开盘推迟 20 分钟
OPTIONS_CLOSE = time(16, 0)


def is_options_market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(ET)).astimezone(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return OPTIONS_OPEN <= t < OPTIONS_CLOSE
