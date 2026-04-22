"""
IBKR 数据模块
─────────────
使用 ib_insync 连接 gnzsnz/ib-gateway-docker：
  docker run -d \
    -e TWSUSERID=your_user \
    -e TWSPASSWORD=your_pass \
    -e TRADING_MODE=paper \
    -p 4002:4002 \
    ghcr.io/gnzsnz/ib-gateway:latest

ib_insync 在同一 asyncio event loop 中运行，与主程序共享 loop。
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from ib_insync import IB, Contract, Stock, Option, util

from config import (
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID,
    TICKER, EXCHANGE, SHARES_FLOAT,
)

log = logging.getLogger(__name__)


@dataclass
class DailyBar:
    date:   str
    open:   float
    high:   float
    low:    float
    close:  float
    volume: int


@dataclass
class Snapshot:
    price:     float
    volume:    int
    high:      float
    low:       float
    timestamp: datetime


class IBKRData:
    def __init__(self):
        self.ib          = IB()
        self._contract   = Stock(TICKER, EXCHANGE, "USD")
        self._peak_price = 0.0
        self._daily_bars: list[DailyBar] = []
        self._last_snap:  Optional[Snapshot] = None

    # ── 连接 ──────────────────────────────────────────────────────────────────

    async def connect(self):
        """连接到 IB Gateway，带重试"""
        for attempt in range(1, 6):
            try:
                await self.ib.connectAsync(
                    host     = IBKR_HOST,
                    port     = IBKR_PORT,
                    clientId = IBKR_CLIENT_ID,
                )
                log.info("IBKR connected (attempt %d)", attempt)
                # 补全合约细节
                await self.ib.qualifyContractsAsync(self._contract)
                return
            except Exception as e:
                log.warning("IBKR connect attempt %d failed: %s", attempt, e)
                await util.sleep(5 * attempt)
        raise ConnectionError("Cannot connect to IB Gateway after 5 attempts")

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    async def ensure_connected(self):
        if not self.is_connected():
            log.warning("IBKR disconnected, reconnecting...")
            await self.connect()

    # ── 实时快照 ──────────────────────────────────────────────────────────────

    async def get_snapshot(self) -> Snapshot:
        await self.ensure_connected()
        try:
            tickers = await self.ib.reqTickersAsync(self._contract)
            t = tickers[0]

            price  = float(t.last or t.close or t.bid or 0)
            volume = int(t.volume or 0)
            high   = float(t.high or 0)
            low    = float(t.low  or 0)

            snap = Snapshot(
                price     = price,
                volume    = volume,
                high      = high,
                low       = low,
                timestamp = datetime.now(),
            )

            if price > self._peak_price:
                self._peak_price = price

            self._last_snap = snap
            return snap

        except Exception as e:
            log.error("Snapshot error: %s", e)
            if self._last_snap:
                return self._last_snap
            raise

    # ── 日K数据 ───────────────────────────────────────────────────────────────

    async def get_daily_bars(self, days: int = 20) -> list[DailyBar]:
        await self.ensure_connected()
        try:
            bars = await self.ib.reqHistoricalDataAsync(
                contract        = self._contract,
                endDateTime     = "",           # 最新
                durationStr     = f"{days} D",
                barSizeSetting  = "1 day",
                whatToShow      = "TRADES",
                useRTH          = True,
                formatDate      = 1,
                keepUpToDate    = False,
            )

            daily = [
                DailyBar(
                    date   = str(b.date),
                    open   = b.open,
                    high   = b.high,
                    low    = b.low,
                    close  = b.close,
                    volume = int(b.volume),
                )
                for b in bars
            ]

            self._daily_bars = daily

            peak = max((b.close for b in daily), default=0.0)
            if peak > self._peak_price:
                self._peak_price = peak

            return daily

        except Exception as e:
            log.error("Daily bars error: %s", e)
            return self._daily_bars    # 返回缓存

    # ── 期权未平仓量 ──────────────────────────────────────────────────────────

    async def get_options_oi(self) -> dict[str, float]:
        """
        返回 {"total_oi": N, "put_call_ratio": R}
        取最近两个到期日的ATM附近期权合约
        """
        await self.ensure_connected()
        try:
            # 获取期权链参数（到期日、行权价列表）
            chains = await self.ib.reqSecDefOptParamsAsync(
                underlyingSymbol   = TICKER,
                futFopExchange     = "",
                underlyingSecType  = "STK",
                underlyingConId    = self._contract.conId,
            )

            if not chains:
                return {"total_oi": 0, "put_call_ratio": 0}

            chain      = chains[0]
            expirations = sorted(chain.expirations)[:2]   # 最近两个到期日
            snap        = self._last_snap
            atm_price   = snap.price if snap else 0

            # 取ATM附近5个行权价
            strikes = sorted(
                chain.strikes,
                key = lambda x: abs(x - atm_price)
            )[:5]

            total_oi = put_oi = call_oi = 0

            for exp in expirations:
                for strike in strikes:
                    for right in ("P", "C"):
                        opt = Option(TICKER, exp, strike, right, "SMART")
                        try:
                            await self.ib.qualifyContractsAsync(opt)
                            tickers = await self.ib.reqTickersAsync(opt)
                            oi = int(tickers[0].openInterest or 0)
                            total_oi += oi
                            if right == "P":
                                put_oi += oi
                            else:
                                call_oi += oi
                        except Exception:
                            pass   # 单个合约失败不影响整体

            put_call = put_oi / call_oi if call_oi > 0 else 0
            return {"total_oi": total_oi, "put_call_ratio": put_call}

        except Exception as e:
            log.error("Options OI error: %s", e)
            return {"total_oi": 0, "put_call_ratio": 0}

    # ── 属性 ──────────────────────────────────────────────────────────────────

    @property
    def peak_price(self) -> float:
        return self._peak_price

    @property
    def daily_bars(self) -> list[DailyBar]:
        return self._daily_bars


ibkr_data = IBKRData()