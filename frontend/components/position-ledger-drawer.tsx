"use client";

import { useEffect, useState } from "react";

import { fetchLedgers, LedgerItem, WatchlistItem } from "@/lib/api";

interface PositionLedgerDrawerProps {
  open: boolean;
  item: WatchlistItem | null;
  onClose: () => void;
}

export default function PositionLedgerDrawer({
  open,
  item,
  onClose,
}: PositionLedgerDrawerProps) {
  const [ledgers, setLedgers] = useState<LedgerItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || !item) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void fetchLedgers(item.stock_code)
      .then((data) => {
        if (!cancelled) setLedgers(data);
      })
      .catch(() => {
        if (!cancelled) setError("加载流水失败");
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
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-md flex-col bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">买卖流水</h2>
            <p className="text-sm text-slate-500">
              {item.stock_name}（{item.stock_code}）
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? <p className="text-sm text-slate-500">加载中...</p> : null}
          {error ? <p className="text-sm text-rose-600">{error}</p> : null}
          {!loading && !error && ledgers.length === 0 ? (
            <p className="text-sm text-slate-500">暂无买卖流水记录</p>
          ) : null}
          {!loading && ledgers.length > 0 ? (
            <ul className="space-y-3">
              {ledgers.map((row) => (
                <li
                  key={row.id}
                  className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm"
                >
                  <div className="mb-1 flex items-center justify-between">
                    <span
                      className={
                        row.side === "BUY"
                          ? "font-medium text-emerald-700"
                          : "font-medium text-rose-700"
                      }
                    >
                      {row.side === "BUY" ? "买入" : "减持"}
                    </span>
                    <span className="text-slate-500">{row.trade_date}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-1 text-slate-700">
                    <span>数量：{row.qty}</span>
                    <span>价格：{row.price.toFixed(3)}</span>
                    {row.side === "SELL" && row.realized_pnl != null ? (
                      <span
                        className={
                          row.realized_pnl >= 0 ? "text-emerald-700" : "text-rose-700"
                        }
                      >
                        已实现盈亏：{row.realized_pnl.toFixed(2)}
                      </span>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}
