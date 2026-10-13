"use client";

import { useEffect, useRef } from "react";
import { dispose, init, registerLocale, registerStyles, type Chart } from "klinecharts";

import { StockKlineBar } from "@/lib/api";

type StockKlineChartProps = {
  bars: StockKlineBar[];
  symbol?: string;
};

type ChartKLineBar = {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

const UP_COLOR = "#f43f5e";
const DOWN_COLOR = "#10b981";
const FLAT_COLOR = "#94a3b8";
const LOCALE = "zh-CN";
const STYLE_THEME = "amtsm";

function dateToTimestamp(dateStr: string): number {
  return new Date(`${dateStr}T00:00:00Z`).getTime();
}

function toChartBars(bars: StockKlineBar[]): ChartKLineBar[] {
  return bars.map((bar) => ({
    timestamp: dateToTimestamp(bar.date),
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  }));
}

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
    bars: [{ upColor: UP_COLOR, downColor: DOWN_COLOR, noChangeColor: FLAT_COLOR }],
    lines: [{ color: "#f59e0b" }, { color: "#6366f1" }, { color: "#0ea5e9" }],
  },
});

export default function StockKlineChart({ bars, symbol = "stock" }: StockKlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = init(container, {
      locale: LOCALE,
      styles: STYLE_THEME,
    }) as Chart | null;
    if (!chart) {
      return () => undefined;
    }

    chart.setSymbol({ ticker: symbol });
    chart.setPeriod({ type: "day", span: 1 });
    chart.setDataLoader({
      getBars: async ({ type, callback }) => {
        if (type === "init") {
          callback(toChartBars(bars), { forward: false, backward: false });
          return;
        }
        callback([], false);
      },
    });

    return () => {
      dispose(container);
    };
  }, [bars, symbol]);

  return <div ref={containerRef} className="h-[460px] w-full" />;
}
