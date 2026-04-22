import os
from dataclasses import dataclass, field
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

# ── IBKR ─────────────────────────────────────────────────────────────────────
# gnzsnz/ib-gateway-docker 默认暴露:
#   4003 = live trading
#   4004 = paper trading
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "4004"))   # 4004=paper, 4003=live
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

# ── ntfy ─────────────────────────────────────────────────────────────────────
# 格式：https://ntfy.trazar.ai/trader_alert_你的随机字符串
NTFY_URL = os.getenv("NTFY_URL", "")

# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── DeepSeek ─────────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# ── CAR 股票参数 ───────────────────────────────────────────────────────────────
TICKER       = "CAR"
EXCHANGE     = "NASDAQ"
SHARES_FLOAT = 35_000_000   # 流通股，逼空期间实际更小，定期校准

# ── 信号阈值（可按需调整）─────────────────────────────────────────────────────
@dataclass
class Thresholds:
    turnover_max:          float = 0.10   # 换手率低于10%
    turnover_decline_days: int   = 3      # 需连续衰竭天数
    price_drop_from_peak:  float = 0.30   # 从高点回落30%
    volume_shrink_ratio:   float = 0.60   # 下跌日成交量<5日均量60%
    oi_drop_pct:           float = 0.15   # 期权OI单日降幅15%
    single_day_drop_alert: float = 0.08   # 单日跌幅8%立即推送

THRESHOLDS = Thresholds()

# ── 轮询间隔（秒）────────────────────────────────────────────────────────────
INTERVAL_MARKET  = 30
INTERVAL_SEC     = 60
INTERVAL_NEWS    = 300
INTERVAL_OPTIONS = 300
INTERVAL_DAILY   = 900    # 15min 刷新日K

# ── 类型别名 ──────────────────────────────────────────────────────────────────
SignalLevel    = Literal["INFO", "WARNING", "ALERT", "CRITICAL"]
OverallSignal  = Literal["HOLD", "PREPARE", "ENTER", "URGENT"]
NewsCatalyst   = Literal["squeeze_ending", "new_squeeze", "neutral"]