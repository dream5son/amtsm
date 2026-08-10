"use client";

import { WatchlistItem } from "@/lib/api";

const SIZE = 32;
const STROKE = 4;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

type BacktestRingProps = {
  item: WatchlistItem;
  onClick?: (item: WatchlistItem) => void;
};

/**
 * Compact donut/ring showing the stock's historical backtest win rate
 * (story 09). Click opens the backtest detail dialog (story 10).
 */
export default function BacktestRing({ item, onClick }: BacktestRingProps) {
  const {
    backtest_status: status,
    backtest_win_rate: winRate,
    backtest_trade_count: tradeCount,
    backtest_sample_insufficient: sampleInsufficient,
    backtest_stale: stale,
    backtest_error_message: errorMessage,
  } = item;

  const clickable = status === "SUCCESS" || status === "FAILED";

  let tooltip = "尚未回测";
  if (status === "PENDING" || status === "RUNNING") {
    tooltip = "回测进行中...";
  } else if (status === "SUCCESS" && winRate != null) {
    tooltip = `历史胜率 ${(winRate * 100).toFixed(0)}%${
      tradeCount != null ? `，共 ${tradeCount} 笔交易` : ""
    }`;
    if (sampleInsufficient) tooltip += "；样本不足，仅供参考";
    if (stale) tooltip += "；过期：参数已变更，结果可能过期";
    if (errorMessage) tooltip += `；最近一次重跑失败：${errorMessage}（展示为历史结果）`;
  } else if (status === "FAILED") {
    tooltip = errorMessage ? `回测失败：${errorMessage}` : "回测失败";
  }

  function handleClick() {
    if (!clickable) return;
    onClick?.(item);
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      title={tooltip}
      disabled={!clickable}
      className={`relative inline-flex items-center justify-center rounded-full ${
        clickable ? "cursor-pointer" : "cursor-default"
      }`}
      style={{ width: SIZE, height: SIZE }}
      aria-label={tooltip}
    >
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke="#e2e8f0"
          strokeWidth={STROKE}
        />
        {status === "PENDING" || status === "RUNNING" ? (
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke="#38bdf8"
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={`${CIRCUMFERENCE * 0.25} ${CIRCUMFERENCE * 0.75}`}
            style={{
              transformOrigin: `${SIZE / 2}px ${SIZE / 2}px`,
              animation: "backtest-ring-spin 1.2s linear infinite",
            }}
          />
        ) : status === "SUCCESS" && winRate != null ? (
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke={sampleInsufficient ? "#f59e0b" : winRate >= 0.5 ? "#10b981" : "#f43f5e"}
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={`${CIRCUMFERENCE * Math.max(0, Math.min(1, winRate))} ${CIRCUMFERENCE}`}
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
            style={stale ? { opacity: 0.55 } : undefined}
          />
        ) : status === "FAILED" ? (
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke="#f43f5e"
            strokeWidth={STROKE}
            strokeDasharray={`${CIRCUMFERENCE * 0.5} ${CIRCUMFERENCE * 0.5}`}
            opacity={0.4}
          />
        ) : null}
      </svg>
      <span className="pointer-events-none absolute text-[10px] font-semibold text-slate-700">
        {status === "SUCCESS" && winRate != null
          ? `${Math.round(winRate * 100)}%`
          : status === "FAILED"
            ? "!"
            : status === "PENDING" || status === "RUNNING"
              ? ""
              : "--"}
      </span>
      {(sampleInsufficient && status === "SUCCESS") || stale ? (
        <span
          className="absolute -right-1 -top-1 flex items-center gap-0.5"
          title={stale ? "过期" : sampleInsufficient ? "样本不足" : undefined}
        >
          {stale ? (
            <span className="rounded bg-amber-500 px-0.5 text-[8px] font-bold leading-none text-white">
              过期
            </span>
          ) : (
            <span
              className="h-2 w-2 rounded-full border border-white bg-amber-500"
              aria-hidden="true"
            />
          )}
        </span>
      ) : null}
    </button>
  );
}
