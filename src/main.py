"""
CAR Short Squeeze Monitor
─────────────────────────
启动前确认：
  1. gnzsnz/ib-gateway-docker 已运行并登录
  2. .env 已配置三个 KEY
  3. pip install -r requirements.txt

运行：
  python src/main.py
"""

import asyncio
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    INTERVAL_MARKET, INTERVAL_SEC, INTERVAL_NEWS,
    INTERVAL_OPTIONS, INTERVAL_DAILY, THRESHOLDS, TICKER,
)
from ibkr_api import ibkr_data
from news import news_analyzer
from notifier import notifier
from sec import sec_monitor
from signals import signal_analyzer, SignalState

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("car_monitor.log"),
    ],
)
log = logging.getLogger("main")
ET  = ZoneInfo("America/New_York")

# ATM 发行状态（检测到后保持，直到人工 reset）
_atm_active = False

# ── 终端状态显示 ──────────────────────────────────────────────────────────────

def _print_status(state: SignalState, price: float):
    COLOR = {
        "HOLD":    "\033[37m",
        "PREPARE": "\033[33m",
        "ENTER":   "\033[31m",
        "URGENT":  "\033[35m",
    }
    RESET = "\033[0m"
    now   = datetime.now(ET).strftime("%H:%M:%S ET")
    c     = COLOR.get(state.overall, "")

    print("\033[2J\033[H", end="")   # clear screen
    print("╔══════════════════════════════════════════╗")
    print("║      CAR Short Squeeze Monitor           ║")
    print("╚══════════════════════════════════════════╝")
    print(f"  时间:  {now}")
    print(f"  价格:  ${price:.2f}")
    print(f"  信号:  {c}{state.overall}{RESET}  (满足条件 {state.conditions_met}/4)")
    print("──────────────────────────────────────────")
    for line in state.details:
        print(f"  {line}")
    print("──────────────────────────────────────────")


# ── 监控循环 ──────────────────────────────────────────────────────────────────

async def loop_market_data():
    """30秒：拉实时快照 + 评估信号"""
    global _atm_active
    log.info("Market data loop started")
    while True:
        try:
            snap      = await ibkr_data.get_snapshot()
            news_res  = news_analyzer.last_result
            news_cat  = (
                "squeeze_ending" if news_res and news_res.squeeze_ending_signal else
                "new_squeeze"    if news_res and news_res.new_squeeze_signal    else
                "neutral"
            )
            state = await signal_analyzer.evaluate(snap, _atm_active, news_cat)
            _print_status(state, snap.price)
        except Exception as e:
            log.error("Market data loop error: %s", e)
        await asyncio.sleep(INTERVAL_MARKET)


async def loop_daily_bars():
    """15分钟：刷新日K，用于换手率/缩量计算"""
    log.info("Daily bar refresh loop started")
    await asyncio.sleep(5)   # 等主连接建立
    while True:
        try:
            bars = await ibkr_data.get_daily_bars(20)
            signal_analyzer.update_bars(bars, ibkr_data.peak_price)
            log.info("Daily bars refreshed: %d bars, peak=$%.0f", len(bars), ibkr_data.peak_price)
        except Exception as e:
            log.error("Daily bar refresh error: %s", e)
        await asyncio.sleep(INTERVAL_DAILY)


async def loop_sec():
    """1分钟：轮询 EDGAR RSS"""
    log.info("SEC monitor loop started")
    while True:
        try:
            await sec_monitor.check()
        except Exception as e:
            log.error("SEC loop error: %s", e)
        await asyncio.sleep(INTERVAL_SEC)


async def loop_news():
    """5分钟：新闻 NLP 分析"""
    log.info("News analyzer loop started")
    await asyncio.sleep(10)
    while True:
        try:
            await news_analyzer.analyze()
        except Exception as e:
            log.error("News loop error: %s", e)
        await asyncio.sleep(INTERVAL_NEWS)


async def loop_options_oi():
    """5分钟：期权 OI"""
    log.info("Options OI loop started")
    await asyncio.sleep(8)
    while True:
        try:
            result = await ibkr_data.get_options_oi()
            oi = result.get("total_oi", 0)
            if oi > 0:
                signal_analyzer.update_oi(oi)
                log.info("Options OI updated: %.0fK (P/C=%.2f)",
                         oi / 1000, result.get("put_call_ratio", 0))
        except Exception as e:
            log.error("Options OI loop error: %s", e)
        await asyncio.sleep(INTERVAL_OPTIONS)


# ── 启动 ──────────────────────────────────────────────────────────────────────

async def main():
    log.info("CAR Monitor starting...")
    notifier.init()

    # 连接 IBKR
    await ibkr_data.connect()

    # 启动通知
    await notifier.send(
        "INFO", "CAR Monitor 已启动",
        f"股票: {TICKER}\n"
        f"信号阈值:\n"
        f"  • 换手率 < {THRESHOLDS.turnover_max*100:.0f}% 连续{THRESHOLDS.turnover_decline_days}天\n"
        f"  • 价格从高点回落 > {THRESHOLDS.price_drop_from_peak*100:.0f}%\n"
        f"  • 下跌日成交量 < 5日均量{THRESHOLDS.volume_shrink_ratio*100:.0f}%\n"
        f"  • 期权OI单日降幅 > {THRESHOLDS.oi_drop_pct*100:.0f}%\n"
        f"  • ATM 8-K/424B 文件出现",
    )

    # 预加载日K
    bars = await ibkr_data.get_daily_bars(20)
    signal_analyzer.update_bars(bars, ibkr_data.peak_price)

    # 并发运行所有循环
    await asyncio.gather(
        loop_market_data(),
        loop_daily_bars(),
        loop_sec(),
        loop_news(),
        loop_options_oi(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down...")
    except Exception as e:
        log.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)