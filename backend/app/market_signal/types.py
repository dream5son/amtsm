"""Shared signal type constants."""

from __future__ import annotations

SIGNAL_BUY = "BUY"  # 空仓买入机会
SIGNAL_SELL = "SELL"  # 技术高位辅助卖出（可选）
SIGNAL_ADDON = "ADDON"  # 持仓中加仓机会
SIGNAL_STOP_LOSS = "STOP_LOSS"  # 触及止损线且浮亏离场
SIGNAL_TAKE_PROFIT = "TAKE_PROFIT"  # 触及止损线且浮盈离场（保本/移动止盈）
SIGNAL_PARTIAL_TP = "PARTIAL_TP"  # 浮盈跨阶梯档，建议部分减仓
SIGNAL_PERIOD_END = "PERIOD_END"  # 回测区间结束时未平仓的记账式平仓
