"use client";

import { useEffect, useState } from "react";

import {
  AlertHistoryItem,
  AlertSentStatus,
  fetchAlertHistory,
  WatchlistItem,
} from "@/lib/api";
import { renderSignal } from "@/lib/signal";

const PAGE_SIZE = 20;

interface SignalHistoryDrawerProps {
  open: boolean;
  item: WatchlistItem | null;
  onClose: () => void;
}

function formatPrice(value: number): string {
  return value.toFixed(3);
}

function formatCoeff(value: number): string {
  return value.toFixed(4).replace(/\.?0+$/, "") || "0";
}

function formatSentTime(value: string | null): string {
  if (!value) {
    return "--";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function sentStatusLabel(status: AlertSentStatus): { label: string; cls: string } {
  if (status === "SUCCESS") {
    return { label: "已发送", cls: "text-emerald-700" };
  }
  if (status === "FAILED") {
    return { label: "发送失败", cls: "text-rose-700" };
  }
  if (status === "PENDING") {
    return { label: "待发送", cls: "text-amber-700" };
  }
  return { label: status, cls: "text-slate-500" };
}

export default function SignalHistoryDrawer({
  open,
  item,
  onClose,
}: SignalHistoryDrawerProps) {
  const [page, setPage] = useState(0);
  const [items, setItems] = useState<AlertHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const stockCode = item?.stock_code ?? null;

  useEffect(() => {
    setPage(0);
  }, [open, stockCode]);

  useEffect(() => {
    if (!open || !stockCode) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void fetchAlertHistory(stockCode, { limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      .then((data) => {
        if (cancelled) return;
        setItems(data.items);
        setTotal(data.total);
      })
      .catch(() => {
        if (!cancelled) setError("加载历史信号失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, stockCode, page]);

  if (!open || !item) return null;

  const totalPages = total > 0 ? Math.ceil(total / PAGE_SIZE) : 0;
  const currentPage = totalPages > 0 ? page + 1 : 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-md flex-col bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">历史信号</h2>
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
          {!loading && !error && items.length === 0 ? (
            <p className="text-sm text-slate-500">暂无历史信号记录</p>
          ) : null}
          {!loading && items.length > 0 ? (
            <ul className="space-y-3">
              {items.map((row) => {
                const signal = renderSignal(row.signal_type);
                const status = sentStatusLabel(row.sent_status);
                return (
                  <li
                    key={row.id}
                    className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm"
                  >
                    <div className="mb-1 flex items-center justify-between">
                      <span className={`inline-flex items-center gap-1 font-medium ${signal.cls}`}>
                        <span>{signal.dot}</span>
                        <span>{signal.label}</span>
                      </span>
                      <span className="text-slate-500">{row.trade_date}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-1 text-slate-700">
                      <span>触发价：{formatPrice(row.trigger_price)}</span>
                      <span>基准价：{formatPrice(row.baseline_price)}</span>
                      <span>系数：{formatCoeff(row.used_coeff)}</span>
                      <span className={status.cls}>状态：{status.label}</span>
                      <span className="col-span-2 text-slate-500">
                        发送时间：{formatSentTime(row.sent_time)}
                      </span>
                    </div>
                    {row.sent_status === "FAILED" && row.error_message ? (
                      <p className="mt-1 text-xs text-rose-600">{row.error_message}</p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>

        {total > 0 ? (
          <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3 text-sm">
            <p className="text-slate-500">
              共 {total} 条 · 第 {currentPage} / {totalPages} 页
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage((prev) => Math.max(0, prev - 1))}
                disabled={page <= 0 || loading}
                className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
              >
                上一页
              </button>
              <button
                type="button"
                onClick={() => setPage((prev) => prev + 1)}
                disabled={page + 1 >= totalPages || loading}
                className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
              >
                下一页
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
