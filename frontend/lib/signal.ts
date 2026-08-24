import { SignalType } from "@/lib/api";

export const SIGNAL_T1_TIP = "当日买入部分暂受T+1限制，无法当日卖出";
export const SIGNAL_LIMIT_BOARD_TIP = "该股当前可能处于涨跌停状态，请注意流动性风险";

export function renderSignal(
  signal: SignalType | null,
): { dot: string; label: string; cls: string } {
  if (signal === "BUY") {
    return { dot: "🟢", label: "买入", cls: "text-emerald-700" };
  }
  if (signal === "SELL") {
    return { dot: "🔴", label: "卖出", cls: "text-rose-700" };
  }
  if (signal === "STOP_LOSS") {
    return { dot: "🛑", label: "止损", cls: "text-rose-700" };
  }
  if (signal === "TAKE_PROFIT") {
    return { dot: "💰", label: "止盈", cls: "text-emerald-700" };
  }
  if (signal === "PARTIAL_TP") {
    return { dot: "🟡", label: "分批止盈", cls: "text-amber-700" };
  }
  if (signal === "ADDON") {
    return { dot: "🔵", label: "加仓", cls: "text-sky-700" };
  }
  return { dot: "⚪", label: "无信号", cls: "text-slate-500" };
}
