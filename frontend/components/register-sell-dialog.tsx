"use client";

import { FormEvent, useEffect, useState } from "react";

import { registerSell, WatchlistItem } from "@/lib/api";

function todayISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

interface RegisterSellDialogProps {
  open: boolean;
  item: WatchlistItem | null;
  onClose: () => void;
  onSuccess: (realizedPnl: number) => void;
}

export default function RegisterSellDialog({
  open,
  item,
  onClose,
  onSuccess,
}: RegisterSellDialogProps) {
  const [qty, setQty] = useState("");
  const [price, setPrice] = useState("");
  const [tradeDate, setTradeDate] = useState(todayISO());
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open || !item) return;
    setQty("");
    setPrice(item.latest_price != null ? String(item.latest_price) : "");
    setTradeDate(todayISO());
    setError("");
  }, [open, item]);

  if (!open || !item) return null;

  const target = item;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    const q = Number(qty);
    const p = Number(price);
    if (!Number.isInteger(q) || q <= 0) {
      setError("卖出数量须为正整数");
      return;
    }
    if (q > target.position_qty) {
      setError(`卖出数量不能超过持有数量 ${target.position_qty}`);
      return;
    }
    if (!Number.isFinite(p) || p <= 0) {
      setError("卖出价格须大于 0");
      return;
    }
    if (!tradeDate) {
      setError("请选择卖出日期");
      return;
    }

    if (q === target.position_qty) {
      const confirmed = window.confirm(
        "将清仓并清空止盈止损状态，确认全部卖出吗？",
      );
      if (!confirmed) return;
    }

    setSubmitting(true);
    try {
      const result = await registerSell(target.stock_code, {
        qty: q,
        price: p,
        trade_date: tradeDate,
      });
      onSuccess(result.realized_pnl);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登记减持失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">登记减持</h2>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            ✕
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-600">
          {item.stock_name}（{item.stock_code}） · 可卖数量 {item.position_qty} 股
          {item.avg_cost != null ? ` · 成本 ${item.avg_cost.toFixed(3)}` : ""}
        </p>

        <form onSubmit={(e) => void onSubmit(e)} className="space-y-3">
          <label className="block text-sm">
            <span className="text-slate-600">卖出数量</span>
            <input
              type="number"
              min={1}
              max={item.position_qty}
              step={1}
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 outline-none ring-sky-200 focus:ring"
            />
          </label>
          <label className="block text-sm">
            <span className="text-slate-600">卖出价格</span>
            <input
              type="number"
              min={0}
              step={0.01}
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 outline-none ring-sky-200 focus:ring"
            />
          </label>
          <label className="block text-sm">
            <span className="text-slate-600">卖出日期</span>
            <input
              type="date"
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 outline-none ring-sky-200 focus:ring"
            />
          </label>

          {error ? <p className="text-sm text-rose-600">{error}</p> : null}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-700 disabled:bg-rose-300"
            >
              {submitting ? "提交中..." : "确认减持"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
