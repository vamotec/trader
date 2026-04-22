"""
信号分析核心
────────────
四维联动判断逼空结束时机
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from ibkr_api import DailyBar, Snapshot
from config import SHARES_FLOAT, THRESHOLDS, OverallSignal, NewsCatalyst
from notifier import notifier

log = logging.getLogger(__name__)


@dataclass
class SignalState:
    turnover_declining:       bool
    price_off_peak:           bool
    volume_shrinking_on_down: bool
    oi_declining:             bool
    atm_filing_detected:      bool
    news_catalyst:            NewsCatalyst
    overall:                  OverallSignal
    details:                  list[str] = field(default_factory=list)

    @property
    def conditions_met(self) -> int:
        return sum([
            self.turnover_declining,
            self.price_off_peak,
            self.volume_shrinking_on_down,
            self.oi_declining,
        ])


class SignalAnalyzer:
    def __init__(self):
        self._bars:        list[DailyBar]       = []
        self._peak:        float                = 0.0
        self._prev_oi:     float                = 0.0
        self._curr_oi:     float                = 0.0
        self._last_state:  Optional[SignalState] = None

    # ── 数据更新 ──────────────────────────────────────────────────────────────

    def update_bars(self, bars: list[DailyBar], peak: float):
        self._bars = bars
        self._peak = peak

    def update_oi(self, oi: float):
        if self._curr_oi > 0:
            self._prev_oi = self._curr_oi
        self._curr_oi = oi

    # ── 单项信号 ──────────────────────────────────────────────────────────────

    def _check_turnover(self) -> tuple[bool, str]:
        n = THRESHOLDS.turnover_decline_days
        if len(self._bars) < n + 1:
            return False, "数据不足"

        recent    = self._bars[-n:]
        turnovers = [b.volume / SHARES_FLOAT for b in recent]
        latest    = turnovers[-1]

        below    = latest < THRESHOLDS.turnover_max
        # 严格单调递减
        declining = all(turnovers[i] <= turnovers[i-1] for i in range(1, len(turnovers)))

        pcts = " → ".join(f"{v*100:.1f}%" for v in turnovers)
        return (below and declining), f"换手率 {pcts}（阈值<{THRESHOLDS.turnover_max*100:.0f}%）"

    def _check_price_off_peak(self, price: float) -> tuple[bool, str]:
        if self._peak == 0:
            return False, "无峰值"
        drop = (self._peak - price) / self._peak
        signal = drop >= THRESHOLDS.price_drop_from_peak
        return signal, f"峰值${self._peak:.0f} → 当前${price:.2f} (回落{drop*100:.1f}%)"

    def _check_volume_on_down_days(self) -> tuple[bool, str]:
        if len(self._bars) < 6:
            return False, "数据不足"

        window    = self._bars[-6:]
        avg_vol   = sum(b.volume for b in window[:5]) / 5
        down_days = [
            b for i, b in enumerate(window[1:], 1)
            if b.close < window[i-1].close
        ]

        if len(down_days) < 2:
            return False, f"下跌日不足({len(down_days)}天)"

        all_shrink = all(
            b.volume < avg_vol * THRESHOLDS.volume_shrink_ratio
            for b in down_days
        )
        return (all_shrink), (
            f"下跌日缩量 {len(down_days)}天 "
            f"| 5日均量{avg_vol/1000:.0f}K "
            f"| 全部缩量:{all_shrink}"
        )

    def _check_oi_declining(self) -> tuple[bool, str]:
        if self._prev_oi == 0 or self._curr_oi == 0:
            return False, "OI数据不足"
        drop = (self._prev_oi - self._curr_oi) / self._prev_oi
        signal = drop >= THRESHOLDS.oi_drop_pct
        return signal, f"OI {self._prev_oi/1000:.0f}K→{self._curr_oi/1000:.0f}K (降{drop*100:.1f}%)"

    def _day_drop(self, snap: Snapshot) -> float:
        if not self._bars:
            return 0.0
        prev_close = self._bars[-1].close
        return (prev_close - snap.price) / prev_close if prev_close > 0 else 0.0

    # ── 综合评估 ──────────────────────────────────────────────────────────────

    async def evaluate(
        self,
        snap:        Snapshot,
        atm_filing:  bool,
        news_cat:    NewsCatalyst,
    ) -> SignalState:

        t_sig,  t_det  = self._check_turnover()
        p_sig,  p_det  = self._check_price_off_peak(snap.price)
        v_sig,  v_det  = self._check_volume_on_down_days()
        oi_sig, oi_det = self._check_oi_declining()
        day_drop       = self._day_drop(snap)

        details = [
            f"{'✅' if t_sig  else '❌'} 换手率衰竭  | {t_det}",
            f"{'✅' if p_sig  else '❌'} 价格回落    | {p_det}",
            f"{'✅' if v_sig  else '❌'} 下跌日缩量  | {v_det}",
            f"{'✅' if oi_sig else '❌'} 期权OI衰减  | {oi_det}",
            f"{'✅' if atm_filing else '❌'} ATM文件    | {'已检测到！' if atm_filing else '暂无'}",
            f"📰 新闻催化剂 | {news_cat}",
        ]

        # 有新逼空催化剂时降级
        has_new_catalyst = (news_cat == "new_squeeze")
        cond = sum([t_sig, p_sig, v_sig, oi_sig])

        if atm_filing:
            overall: OverallSignal = "URGENT"
        elif cond >= 3 and not has_new_catalyst:
            overall = "ENTER"
        elif cond >= 2 and not has_new_catalyst:
            overall = "PREPARE"
        else:
            overall = "HOLD"

        state = SignalState(
            turnover_declining       = t_sig,
            price_off_peak           = p_sig,
            volume_shrinking_on_down = v_sig,
            oi_declining             = oi_sig,
            atm_filing_detected      = atm_filing,
            news_catalyst            = news_cat,
            overall                  = overall,
            details                  = details,
        )

        await self._maybe_notify(state, snap, day_drop)
        self._last_state = state
        return state

    async def _maybe_notify(self, state: SignalState, snap: Snapshot, day_drop: float):
        prev = self._last_state

        # 单日跌幅预警（不是入场信号，是即时推送）
        if day_drop >= THRESHOLDS.single_day_drop_alert:
            await notifier.send(
                "ALERT",
                f"单日跌幅 {day_drop*100:.1f}%",
                f"当前价格: ${snap.price:.2f}\n\n" + "\n".join(state.details),
            )

        # 信号升级时推送
        order = ["HOLD", "PREPARE", "ENTER", "URGENT"]
        prev_idx = order.index(prev.overall) if prev else 0
        curr_idx = order.index(state.overall)

        if curr_idx > prev_idx:
            level_map  = {"PREPARE": "WARNING", "ENTER": "ALERT", "URGENT": "CRITICAL"}
            title_map  = {
                "PREPARE": "⚠️ 技术信号开始满足，准备做空计划",
                "ENTER":   "🚨 做空入场信号触发！",
                "URGENT":  "🔴 ATM确认！立即执行做空",
            }
            if state.overall != "HOLD":
                met = " | ".join([
                    s for flag, s in [
                        (state.turnover_declining,       "换手率衰竭"),
                        (state.price_off_peak,           "价格回落30%+"),
                        (state.volume_shrinking_on_down, "下跌日缩量"),
                        (state.oi_declining,             "OI衰减"),
                        (state.atm_filing_detected,      "ATM文件"),
                    ] if flag
                ])
                await notifier.send(
                    level_map[state.overall],
                    title_map[state.overall],
                    f"满足条件: {met}\n\n" + "\n".join(state.details),
                )

    @property
    def last_state(self) -> Optional[SignalState]:
        return self._last_state


signal_analyzer = SignalAnalyzer()