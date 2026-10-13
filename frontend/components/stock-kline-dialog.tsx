"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { fetchStockKline, StockKlineBar, WatchlistItem } from "@/lib/api";

const StockKlineChart = dynamic(() => import("@/components/stock-kline-chart"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[460px] items-center justify-center text-sm text-slate-500">
      加载 K 线图中...
    </div>
  ),
});

type StockKlineDialogProps = {
  open: boolean;
  item: WatchlistItem | null;
  onClose: () => void;
};

export default function StockKlineDialog({ open, item, onClose }: StockKlineDialogProps) {
  const [bars, setBars] = useState<StockKlineBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || !item) {
      setBars([]);
      setError("");
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError("");

    void fetchStockKline(item.stock_code, { limit: 500 })
      .then((data) => {
        if (cancelled) return;
        setBars(data);
      })
      .catch(() => {
        if (!cancelled) {
          setBars([]);
          setError("K 线数据加载失败，暂时无法展示该股票行情");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, item]);

  if (!open || !item) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-5xl overflow-hidden rounded-2xl bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">{item.stock_name}</h2>
            <p className="text-sm text-slate-500">{item.stock_code}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-100"
          >
            关闭
          </button>
        </div>

        <div className="p-4">
          {loading ? (
            <div className="flex h-[460px] items-center justify-center text-sm text-slate-500">
              正在加载 K 线图...
            </div>
          ) : error ? (
            <div className="flex h-[460px] items-center justify-center text-sm text-rose-600">
              {error}
            </div>
          ) : (
            <StockKlineChart bars={bars} symbol={item.stock_code} />
          )}
        </div>
      </div>
    </div>
  );
}
