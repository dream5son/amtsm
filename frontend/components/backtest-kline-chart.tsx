"use client";

import { useEffect, useRef } from "react";
import { dispose, init, registerLocale, registerStyles, type Chart } from "klinecharts";

import { BacktestKlineBar, BacktestTrade } from "@/lib/api";

type BacktestKlineChartProps = {
  bars: BacktestKlineBar[];
  trades: BacktestTrade[];
  selectedTradeId?: number | null;
  onSelectTrade?: (tradeId: number) => void;
};

function dateToTimestamp(dateStr: string): number {
  return new Date(`${dateStr}T00:00:00Z`).getTime();
}

// 中国股市习惯：红涨绿跌，与欧美市场（绿涨红跌）相反。
const UP_COLOR = "#f43f5e";
const DOWN_COLOR = "#10b981";
const FLAT_COLOR = "#94a3b8";
const PERIOD_END_COLOR = "#f59e0b";

// 买入点/盈利标注沿用"涨"的红色，亏损标注沿用"跌"的绿色，保持红绿语义统一。
const BUY_COLOR = UP_COLOR;
const PROFIT_COLOR = UP_COLOR;
const LOSS_COLOR = DOWN_COLOR;

const LOCALE = "zh-CN";
const STYLE_THEME = "amtsm";
const MA_PANE_ID = "candle_pane";

// 语言包（参考 klinecharts i18n 指南）：内置 zh-CN 已覆盖大部分文案，这里补充/统一提示信息的标点。
registerLocale(LOCALE, {
  time: "时间：",
  open: "开盘：",
  high: "最高：",
  low: "最低：",
  close: "收盘：",
  volume: "成交量：",
  turnover: "成交额：",
  change: "涨跌幅：",
  second: "秒",
  minute: "分",
  hour: "小时",
  day: "日",
  week: "周",
  month: "月",
  year: "年",
});

// 样式主题（参考 klinecharts styles 指南）：遵循中国股市习惯（涨=红，跌=绿），
// 并丰富十字光标提示信息（增加涨跌幅字段）。模块级注册一次即可，避免每次渲染重复调用。
registerStyles(STYLE_THEME, {
  grid: {
    horizontal: { color: "#eef2f7" },
    vertical: { color: "#eef2f7" },
  },
  candle: {
    bar: {
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      noChangeColor: FLAT_COLOR,
      upBorderColor: UP_COLOR,
      downBorderColor: DOWN_COLOR,
      noChangeBorderColor: FLAT_COLOR,
      upWickColor: UP_COLOR,
      downWickColor: DOWN_COLOR,
      noChangeWickColor: FLAT_COLOR,
    },
    priceMark: {
      last: {
        upColor: UP_COLOR,
        downColor: DOWN_COLOR,
      },
    },
    tooltip: {
      showRule: "follow_cross",
      legend: {
        template: [
          { title: "time", value: "{time}" },
          { title: "open", value: "{open}" },
          { title: "high", value: "{high}" },
          { title: "low", value: "{low}" },
          { title: "close", value: "{close}" },
          { title: "change", value: "{change}" },
          { title: "volume", value: "{volume}" },
        ],
      },
    },
  },
  indicator: {
    bars: [
      {
        upColor: UP_COLOR,
        downColor: DOWN_COLOR,
        noChangeColor: FLAT_COLOR,
      },
    ],
    lines: [{ color: "#f59e0b" }, { color: "#6366f1" }, { color: "#0ea5e9" }],
  },
});

/**
 * KLineChart (story 10) wrapper: renders the backtest date-range candles and
 * overlays buy/sell markers sourced from the same `backtest_trades` rows as
 * the trade ledger table, so both views share one data source. The chart
 * instance is created when mounted and destroyed on unmount (dialog close).
 */
export default function BacktestKlineChart({
  bars,
  trades,
  selectedTradeId,
  onSelectTrade,
}: BacktestKlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const overlayTradeMapRef = useRef<Map<string, { tradeId: number; kind: "buy" | "sell" }>>(
    new Map(),
  );
  const indicatorIdsRef = useRef<{ ma: string | null; vol: string | null }>({
    ma: null,
    vol: null,
  });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = init(container, {
      locale: LOCALE,
      styles: STYLE_THEME,
    });
    chartRef.current = chart;
    if (chart) {
      // klinecharts v10 only pulls bars via the dataLoader once symbol *and*
      // period are also set (see setSymbol/setPeriod/setDataLoader all
      // funnel into the same "load if all three are present" check) — without
      // these two calls getBars is never invoked and the chart stays empty.
      chart.setSymbol({ ticker: "backtest" });
      chart.setPeriod({ type: "day", span: 1 });
    }

    return () => {
      chartRef.current = null;
      indicatorIdsRef.current = { ma: null, vol: null };
      if (container) dispose(container);
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const klineData = bars.map((bar) => ({
      timestamp: dateToTimestamp(bar.trade_date),
      open: bar.open_price,
      high: bar.high_price,
      low: bar.low_price,
      close: bar.close_price,
      volume: bar.volume,
    }));

    chart.setDataLoader({
      getBars: ({ type, callback }) => {
        callback(type === "init" ? klineData : [], false);
      },
    });

    // 有数据时才丰富展示：主图叠加均线（MA），并在下方新增成交量柱状图窗格；
    // 数据清空时（如切换到空区间）移除指标，避免留下空白窗格。
    const ids = indicatorIdsRef.current;
    if (klineData.length > 0) {
      if (!ids.ma) {
        ids.ma = chart.createIndicator(
          { name: "MA", paneId: MA_PANE_ID, calcParams: [5, 10, 20] },
          false,
        );
      }
      if (!ids.vol) {
        ids.vol = chart.createIndicator("VOL", false);
      }
    } else {
      if (ids.ma) {
        chart.removeIndicator({ id: ids.ma });
        ids.ma = null;
      }
      if (ids.vol) {
        chart.removeIndicator({ id: ids.vol });
        ids.vol = null;
      }
    }
  }, [bars]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    chart.removeOverlay();
    overlayTradeMapRef.current = new Map();

    trades.forEach((trade) => {
      const isPeriodEnd = trade.exit_reason === "PERIOD_END";
      const isProfit = trade.pnl_pct >= 0;
      const sellColor = isPeriodEnd ? PERIOD_END_COLOR : isProfit ? PROFIT_COLOR : LOSS_COLOR;

      const buyId = chart.createOverlay({
        name: "simpleAnnotation",
        lock: true,
        points: [{ timestamp: dateToTimestamp(trade.entry_date), value: trade.entry_price }],
        extendData: `买 ${trade.entry_price.toFixed(2)}`,
        styles: {
          line: { color: BUY_COLOR },
          polygon: { color: BUY_COLOR },
          text: { color: "#ffffff", backgroundColor: BUY_COLOR },
        },
        onClick: () => {
          onSelectTrade?.(trade.id);
          return false;
        },
      });

      const sellLabel = isPeriodEnd
        ? `未平仓 ${(trade.pnl_pct * 100).toFixed(1)}%`
        : `${trade.exit_reason === "STOP_LOSS" ? "止损" : "止盈"} ${(trade.pnl_pct * 100).toFixed(1)}%`;

      const sellId = chart.createOverlay({
        name: "simpleAnnotation",
        lock: true,
        points: [{ timestamp: dateToTimestamp(trade.exit_date), value: trade.exit_price }],
        extendData: sellLabel,
        styles: {
          line: { color: sellColor },
          polygon: { color: sellColor },
          text: { color: "#ffffff", backgroundColor: sellColor },
        },
        onClick: () => {
          onSelectTrade?.(trade.id);
          return false;
        },
      });

      if (typeof buyId === "string") {
        overlayTradeMapRef.current.set(buyId, { tradeId: trade.id, kind: "buy" });
      }
      if (typeof sellId === "string") {
        overlayTradeMapRef.current.set(sellId, { tradeId: trade.id, kind: "sell" });
      }
    });
  }, [trades, onSelectTrade]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || selectedTradeId == null) return;
    const trade = trades.find((t) => t.id === selectedTradeId);
    if (!trade) return;
    chart.scrollToTimestamp(dateToTimestamp(trade.exit_date), 200);
  }, [selectedTradeId, trades]);

  return <div ref={containerRef} className="h-[460px] w-full" />;
}
