"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  BacktestParamsOverride,
  createBacktest,
  WatchlistItem,
} from "@/lib/api";
import { todayISO } from "@/lib/datetime";

interface ParamGroupForm {
  key: string;
  n: string;
  x: string;
  y: string;
  stop_loss_pct: string;
  break_even_trigger_pct: string;
  break_even_buffer_pct: string;
}

function emptyGroup(key: string): ParamGroupForm {
  return {
    key,
    n: "",
    x: "",
    y: "",
    stop_loss_pct: "",
    break_even_trigger_pct: "",
    break_even_buffer_pct: "",
  };
}

function toOverride(g: ParamGroupForm): BacktestParamsOverride {
  const num = (s: string): number | undefined => (s.trim() === "" ? undefined : Number(s));
  return {
    n: num(g.n),
    x: num(g.x),
    y: num(g.y),
    stop_loss_pct: num(g.stop_loss_pct),
    break_even_trigger_pct: num(g.break_even_trigger_pct),
    break_even_buffer_pct: num(g.break_even_buffer_pct),
  };
}

const NUMERIC_FIELDS: {
  key: keyof Omit<ParamGroupForm, "key">;
  label: string;
  step: number;
}[] = [
  { key: "n", label: "N（基准日数）", step: 1 },
  { key: "x", label: "X（买入系数）", step: 0.01 },
  { key: "y", label: "Y（卖出系数）", step: 0.01 },
  { key: "stop_loss_pct", label: "初始止损比例", step: 0.01 },
  { key: "break_even_trigger_pct", label: "保本触发比例", step: 0.01 },
  { key: "break_even_buffer_pct", label: "保本缓冲比例", step: 0.001 },
];

interface BacktestConfigDialogProps {
  open: boolean;
  item: WatchlistItem | null;
  onClose: () => void;
  onSubmitted: (jobCount: number) => void;
}

export default function BacktestConfigDialog({
  open,
  item,
  onClose,
  onSubmitted,
}: BacktestConfigDialogProps) {
  const [sinceListing, setSinceListing] = useState(true);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState(todayISO());
  const [groups, setGroups] = useState<ParamGroupForm[]>([emptyGroup("g0")]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSinceListing(true);
    setStartDate("");
    setEndDate(todayISO());
    setGroups([emptyGroup("g0")]);
    setError("");
  }, [open, item]);

  if (!open || !item) return null;

  const target = item;

  function updateGroup(key: string, field: keyof ParamGroupForm, value: string) {
    setGroups((prev) =>
      prev.map((g) => (g.key === key ? { ...g, [field]: value } : g)),
    );
  }

  function addGroup() {
    setGroups((prev) => [...prev, emptyGroup(`g${prev.length}-${Date.now()}`)]);
  }

  function removeGroup(key: string) {
    setGroups((prev) => (prev.length > 1 ? prev.filter((g) => g.key !== key) : prev));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");

    if (!sinceListing && startDate && endDate && startDate >= endDate) {
      setError("开始日期必须早于结束日期");
      return;
    }

    setSubmitting(true);
    try {
      const response = await createBacktest({
        stock_code: target.stock_code,
        start_date: sinceListing || !startDate ? undefined : startDate,
        end_date: endDate || undefined,
        params: groups.map(toOverride),
      });
      onSubmitted(response.jobs.length);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发起回测失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">发起回测</h2>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            ✕
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-600">
          {item.stock_name}（{item.stock_code}）
        </p>

        <form onSubmit={(e) => void onSubmit(e)} className="space-y-4">
          <div>
            <span className="mb-1 block text-sm text-slate-600">回测区间</span>
            <label className="mb-2 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={sinceListing}
                onChange={(e) => setSinceListing(e.target.checked)}
              />
              从该股票上市至今
            </label>
            {!sinceListing ? (
              <div className="flex items-center gap-2">
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
                />
                <span className="text-slate-400">至</span>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
                />
              </div>
            ) : null}
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-slate-600">参数组合（留空 = 跟随当前生效参数）</span>
              <button
                type="button"
                onClick={addGroup}
                className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                + 添加参数组合
              </button>
            </div>
            <div className="space-y-3">
              {groups.map((g, idx) => (
                <div key={g.key} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-500">组合 {idx + 1}</span>
                    {groups.length > 1 ? (
                      <button
                        type="button"
                        onClick={() => removeGroup(g.key)}
                        className="text-xs text-rose-600 hover:underline"
                      >
                        移除
                      </button>
                    ) : null}
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    {NUMERIC_FIELDS.map((field) => (
                      <label key={field.key} className="block">
                        <span className="text-slate-500">{field.label}</span>
                        <input
                          type="number"
                          step={field.step}
                          value={g[field.key]}
                          onChange={(e) => updateGroup(g.key, field.key, e.target.value)}
                          placeholder="当前值"
                          className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-slate-900 outline-none ring-sky-200 focus:ring"
                        />
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            历史回测仅供参考，不构成投资建议；回测为离线模拟，不会产生实盘通知或委托。
          </p>

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
              {submitting ? "提交中..." : "确认发起回测"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
