"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  BuyPreview,
  previewBuy,
  registerBuy,
  WatchlistItem,
} from "@/lib/api";

function todayISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

interface RegisterBuyDialogProps {
  open: boolean;
  item: WatchlistItem | null;
  onClose: () => void;
  onSuccess: () => void;
}

export default function RegisterBuyDialog({
  open,
  item,
  onClose,
  onSuccess,
}: RegisterBuyDialogProps) {
  const [qty, setQty] = useState("");
  const [price, setPrice] = useState("");
  const [tradeDate, setTradeDate] = useState(todayISO());
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [preview, setPreview] = useState<BuyPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    if (!open || !item) return;
    setQty("");
    setPrice(item.latest_price != null ? String(item.latest_price) : "");
    setTradeDate(todayISO());
    setError("");
    setPreview(null);
  }, [open, item]);

  useEffect(() => {
    if (!open || !item) return;
    const q = Number(qty);
    const p = Number(price);
    if (!Number.isFinite(q) || q <= 0 || !Number.isFinite(p) || p <= 0) {
      setPreview(null);
      return;
    }
    if (item.position_qty <= 0) {
      setPreview(null);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setPreviewLoading(true);
      void previewBuy(item.stock_code, q, p)
        .then((data) => {
          if (!cancelled) setPreview(data);
        })
        .catch(() => {
          if (!cancelled) setPreview(null);
        })
        .finally(() => {
          if (!cancelled) setPreviewLoading(false);
        });
    }, 300);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, item, qty, price]);

  if (!open || !item) return null;

  const target = item;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    const q = Number(qty);
    const p = Number(price);
    if (!Number.isInteger(q) || q <= 0) {
      setError("买入数量须为正整数");
      return;
    }
    if (!Number.isFinite(p) || p <= 0) {
      setError("买入价格须大于 0");
      return;
    }
    if (!tradeDate) {
      setError("请选择买入日期");
      return;
    }

    setSubmitting(true);
    try {
      await registerBuy(target.stock_code, {
        qty: q,
        price: p,
        trade_date: tradeDate,
      });
      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登记买入失败");
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
          <h2 className="text-lg font-semibold text-slate-900">登记买入</h2>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            ✕
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-600">
          {item.stock_name}（{item.stock_code}）
          {item.position_qty > 0 ? ` · 当前持仓 ${item.position_qty} 股` : " · 空仓建仓"}
        </p>

        <form onSubmit={(e) => void onSubmit(e)} className="space-y-3">
          <label className="block text-sm">
            <span className="text-slate-600">买入数量</span>
            <input
              type="number"
              min={1}
              step={1}
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 outline-none ring-sky-200 focus:ring"
            />
          </label>
          <label className="block text-sm">
            <span className="text-slate-600">买入价格</span>
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
            <span className="text-slate-600">买入日期</span>
            <input
              type="date"
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 outline-none ring-sky-200 focus:ring"
            />
          </label>

          {item.position_qty > 0 && (preview || previewLoading) ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              {previewLoading && !preview ? (
                <span>计算加仓预览...</span>
              ) : preview ? (
                <>
                  <div>加仓后数量：{preview.new_qty}</div>
                  <div>新成本价：{preview.new_avg_cost.toFixed(3)}</div>
                  <div>新止损参考价：{preview.new_stop_price.toFixed(3)}</div>
                </>
              ) : null}
            </div>
          ) : null}

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
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:bg-sky-300"
            >
              {submitting ? "提交中..." : "确认买入"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
