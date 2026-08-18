"use client";

import { useEffect, useRef, useState } from "react";
import { dispose, init, registerLocale, registerStyles, type Chart } from "klinecharts";

import { BacktestKlineBar, BacktestTrade, BACKTEST_KLINE_PAGE_SIZE, fetchBacktestKline } from "@/lib/api";

type BacktestKlineChartProps = {
  jobId: number;
  bars: BacktestKlineBar[];
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
  trades: BacktestTrade[];
  selectedTradeId?: number | null;
  onSelectTrade?: (tradeId: number) => void;
};

type ChartKLineBar = {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type FocusRange = {
  start: string;
  end: string;
  scrollTo: number;
};

const JUMP_PAD_DAYS = 180;

function dateToTimestamp(dateStr: string): number {
  return new Date(`${dateStr}T00:00:00Z`).getTime();
}

function timestampToDate(timestamp: number): string {
  return new Date(timestamp).toISOString().slice(0, 10);
}

function shiftDate(dateStr: string, days: number): string {
  return timestampToDate(dateToTimestamp(dateStr) + days * 86_400_000);
}

function toChartBars(bars: BacktestKlineBar[]): ChartKLineBar[] {
  return bars.map((bar) => ({
    timestamp: dateToTimestamp(bar.trade_date),
    open: bar.open_price,
    high: bar.high_price,
    low: bar.low_price,
    close: bar.close_price,
    volume: bar.volume,
  }));
}

type BarAnchor = {
  timestamp: number;
  high: number;
  low: number;
};

function loadedBarsByTimestamp(chart: Chart): Map<number, BarAnchor> {
  const list = (chart as Chart & { getDataList?: () => BarAnchor[] }).getDataList?.() ?? [];
  return new Map(list.map((bar) => [bar.timestamp, bar]));
}

/**
 * Draw buy/sell tips only after candles exist, anchoring each tip to that
 * day's candle (buy below low, sell above high) so markers stay glued to bars
 * as pages are prepended/appended.
 */
function applyTradeOverlays(
  chart: Chart,
  trades: BacktestTrade[],
  onSelectTrade: ((tradeId: number) => void) | undefined,
  overlayMap: Map<string, { tradeId: number; kind: "buy" | "sell" }>,
) {
  chart.removeOverlay();
  overlayMap.clear();

  const barsByTs = loadedBarsByTimestamp(chart);
  if (barsByTs.size === 0) return;

  trades.forEach((trade) => {
    const isPeriodEnd = trade.exit_reason === "PERIOD_END";
    const isProfit = trade.pnl_pct >= 0;
    const sellColor = isPeriodEnd ? PERIOD_END_COLOR : isProfit ? PROFIT_COLOR : LOSS_COLOR;

    const buyBar = barsByTs.get(dateToTimestamp(trade.entry_date));
    if (buyBar) {
      const buyId = chart.createOverlay({
        name: "simpleAnnotation",
        lock: true,
        points: [{ timestamp: buyBar.timestamp, value: buyBar.low }],
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
      if (typeof buyId === "string") {
        overlayMap.set(buyId, { tradeId: trade.id, kind: "buy" });
      }
    }

    const sellBar = barsByTs.get(dateToTimestamp(trade.exit_date));
    if (!sellBar) return;

    const sellLabel = isPeriodEnd
      ? `未平仓 ${(trade.pnl_pct * 100).toFixed(1)}%`
      : `${trade.exit_reason === "STOP_LOSS" ? "止损" : "止盈"} ${(trade.pnl_pct * 100).toFixed(1)}%`;

    const sellId = chart.createOverlay({
      name: "simpleAnnotation",
      lock: true,
      points: [{ timestamp: sellBar.timestamp, value: sellBar.high }],
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
    if (typeof sellId === "string") {
      overlayMap.set(sellId, { tradeId: trade.id, kind: "sell" });
    }
  });
}

function syncIndicators(
  chart: Chart,
  hasBars: boolean,
  ids: { ma: string | null; vol: string | null },
) {
  if (hasBars) {
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

type PagedChart = Chart & {
  resetData: () => void;
  getDataList: () => Array<{ timestamp: number }>;
};

/**
 * KLineChart (story 10) wrapper: renders the backtest date-range candles and
 * overlays buy/sell markers sourced from the same `backtest_trades` rows as
 * the trade ledger table, so both views share one data source. The chart
 * instance is created when mounted and destroyed on unmount (dialog close).
 *
 * Initial bars are the latest page (about one year of daily candles). Dragging
 * to either edge loads the adjacent page; clicking a trade outside the loaded
 * range fetches a window around that date and scrolls to it.
 */
export default function BacktestKlineChart({
  jobId,
  bars,
  hasMoreBefore,
  hasMoreAfter,
  trades,
  selectedTradeId,
  onSelectTrade,
}: BacktestKlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<PagedChart | null>(null);
  const overlayTradeMapRef = useRef<Map<string, { tradeId: number; kind: "buy" | "sell" }>>(
    new Map(),
  );
  const indicatorIdsRef = useRef<{ ma: string | null; vol: string | null }>({
    ma: null,
    vol: null,
  });
  const initialPageRef = useRef({ bars, hasMoreBefore, hasMoreAfter });
  const jobIdRef = useRef(jobId);
  const tradesRef = useRef(trades);
  const onSelectTradeRef = useRef(onSelectTrade);
  const focusRangeRef = useRef<FocusRange | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  initialPageRef.current = { bars, hasMoreBefore, hasMoreAfter };
  jobIdRef.current = jobId;
  tradesRef.current = trades;
  onSelectTradeRef.current = onSelectTrade;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = init(container, {
      locale: LOCALE,
      styles: STYLE_THEME,
    }) as PagedChart | null;
    chartRef.current = chart;
    if (!chart) {
      return () => {
        chartRef.current = null;
        indicatorIdsRef.current = { ma: null, vol: null };
        if (container) dispose(container);
      };
    }

    let cancelled = false;

    const paintOverlays = () => {
      if (cancelled) return;
      applyTradeOverlays(
        chart,
        tradesRef.current,
        onSelectTradeRef.current,
        overlayTradeMapRef.current,
      );
    };

    const paintOverlaysAfterBars = () => {
      // Wait two frames so klinecharts can merge bars and paint candles
      // before tips are anchored to those candles.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (cancelled) return;
          paintOverlays();
        });
      });
    };

    // klinecharts v10 only pulls bars via the dataLoader once symbol *and*
    // period are also set (see setSymbol/setPeriod/setDataLoader all
    // funnel into the same "load if all three are present" check) — without
    // these two calls getBars is never invoked and the chart stays empty.
    chart.setSymbol({ ticker: "backtest" });
    chart.setPeriod({ type: "day", span: 1 });
    chart.setDataLoader({
      getBars: async ({ type, timestamp, callback }) => {
        const focus = type === "init" ? focusRangeRef.current : null;
        const showLoading = type !== "init" || focus != null;
        if (showLoading) setLoadingMore(true);
        try {
          if (type === "init") {
            focusRangeRef.current = null;
            if (focus) {
              const page = await fetchBacktestKline(jobIdRef.current, {
                start: focus.start,
                end: focus.end,
                include_trades: false,
              });
              if (cancelled) {
                callback([], false);
                return;
              }
              const mapped = toChartBars(page.bars);
              callback(mapped, {
                forward: page.has_more_before,
                backward: page.has_more_after,
              });
              syncIndicators(chart, mapped.length > 0, indicatorIdsRef.current);
              paintOverlaysAfterBars();
              chart.scrollToTimestamp(focus.scrollTo, 200);
              return;
            }
            const page = initialPageRef.current;
            const mapped = toChartBars(page.bars);
            callback(mapped, {
              forward: page.hasMoreBefore,
              backward: page.hasMoreAfter,
            });
            syncIndicators(chart, mapped.length > 0, indicatorIdsRef.current);
            paintOverlaysAfterBars();
            return;
          }

          if (timestamp == null || (type !== "forward" && type !== "backward")) {
            callback([], false);
            return;
          }

          const cursor = timestampToDate(timestamp);
          const page =
            type === "forward"
              ? await fetchBacktestKline(jobIdRef.current, {
                  before: cursor,
                  limit: BACKTEST_KLINE_PAGE_SIZE,
                  include_trades: false,
                })
              : await fetchBacktestKline(jobIdRef.current, {
                  after: cursor,
                  limit: BACKTEST_KLINE_PAGE_SIZE,
                  include_trades: false,
                });
          if (cancelled) {
            callback([], false);
            return;
          }
          callback(toChartBars(page.bars), {
            forward: page.has_more_before,
            backward: page.has_more_after,
          });
          paintOverlaysAfterBars();
        } catch {
          callback([], false);
        } finally {
          if (showLoading) setLoadingMore(false);
        }
      },
    });

    return () => {
      cancelled = true;
      chartRef.current = null;
      indicatorIdsRef.current = { ma: null, vol: null };
      if (container) dispose(container);
    };
  }, [jobId]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    requestAnimationFrame(() => {
      applyTradeOverlays(chart, trades, onSelectTrade, overlayTradeMapRef.current);
    });
  }, [trades, onSelectTrade]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || selectedTradeId == null) return;
    const trade = trades.find((t) => t.id === selectedTradeId);
    if (!trade) return;
    const ts = dateToTimestamp(trade.exit_date);
    const loaded = chart.getDataList().some((bar) => bar.timestamp === ts);
    if (loaded) {
      chart.scrollToTimestamp(ts, 200);
      return;
    }
    focusRangeRef.current = {
      start: shiftDate(trade.exit_date, -JUMP_PAD_DAYS),
      end: shiftDate(trade.exit_date, JUMP_PAD_DAYS),
      scrollTo: ts,
    };
    chart.resetData();
  }, [selectedTradeId, trades]);

  return (
    <div className="relative h-[460px] w-full">
      <div ref={containerRef} className="h-[460px] w-full" />
      {loadingMore ? (
        <div className="pointer-events-none absolute inset-x-0 top-3 z-10 flex justify-center">
          <div className="flex items-center gap-2 rounded-full bg-slate-900/80 px-3 py-1.5 text-xs text-white shadow">
            <span
              className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white"
              aria-hidden
            />
            正在加载更多 K 线...
          </div>
        </div>
      ) : null}
    </div>
  );
}
