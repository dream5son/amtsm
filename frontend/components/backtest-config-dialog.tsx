"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  BacktestParamsOverride,
  createBacktest,
  fetchSignalStrategies,
  SignalStrategy,
  WatchlistItem,
} from "@/lib/api";
import { todayISO } from "@/lib/datetime";

interface ParamGroupForm {
  key: string;
  signal_strategy_id: string;
  stop_loss_pct: string;
  break_even_trigger_pct: string;
  break_even_buffer_pct: string;
}

function emptyGroup(key: string, strategyId = ""): ParamGroupForm {
  return {
    key,
    signal_strategy_id: strategyId,
    stop_loss_pct: "",
    break_even_trigger_pct: "",
    break_even_buffer_pct: "",
  };
}

function toOverride(g: ParamGroupForm): BacktestParamsOverride {
  const num = (s: string): number | undefined => (s.trim() === "" ? undefined : Number(s));
  return {
    signal_strategy_id: Number(g.signal_strategy_id),
    stop_loss_pct: num(g.stop_loss_pct),
    break_even_trigger_pct: num(g.break_even_trigger_pct),
    break_even_buffer_pct: num(g.break_even_buffer_pct),
  };
}

const NUMERIC_FIELDS: {
  key: "stop_loss_pct" | "break_even_trigger_pct" | "break_even_buffer_pct";
  label: string;
  step: number;
}[] = [
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
  const [strategies, setStrategies] = useState<SignalStrategy[]>([]);
  const [loadingStrategies, setLoadingStrategies] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setSinceListing(true);
    setStartDate("");
    setEndDate(todayISO());
    setGroups([emptyGroup("g0", item ? String(item.effective_signal_strategy_id) : "")]);
    setError("");
    setLoadingStrategies(true);
    void fetchSignalStrategies()
      .then((available) => {
        if (cancelled) return;
        setStrategies(available);
        const effectiveId = item?.effective_signal_strategy_id;
        const selectedId =
          effectiveId && available.some((strategy) => strategy.id === effectiveId)
            ? effectiveId
            : available[0]?.id;
        setGroups([emptyGroup("g0", selectedId ? String(selectedId) : "")]);
        if (available.length === 0) setError("暂无可用于回测的信号策略");
      })
      .catch(() => {
        if (!cancelled) setError("信号策略加载失败，请关闭后重试");
      })
      .finally(() => {
        if (!cancelled) setLoadingStrategies(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, item]);

  if (!open || !item) return null;

  const target = item;

  function updateGroup(key: string, field: keyof ParamGroupForm, value: string) {
    setGroups((prev) =>
      prev.map((g) => (g.key === key ? { ...g, [field]: value } : g)),
    );
  }

  function addGroup() {
    setGroups((prev) => {
      const selectedIds = new Set(prev.map((group) => group.signal_strategy_id));
      const nextStrategy =
        strategies.find((strategy) => !selectedIds.has(String(strategy.id))) ?? strategies[0];
      return [
        ...prev,
        emptyGroup(
          `g${prev.length}-${Date.now()}`,
          nextStrategy ? String(nextStrategy.id) : "",
        ),
      ];
    });
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
    if (
      groups.some(
        (group) =>
          !Number.isInteger(Number(group.signal_strategy_id)) ||
          Number(group.signal_strategy_id) <= 0,
      )
    ) {
      setError("请为每个对比组选择一个信号策略");
      return;
    }
    const invalidRiskValue = groups.some((group) =>
      NUMERIC_FIELDS.some((field) => {
        const raw = group[field.key].trim();
        if (raw === "") return false;
        const value = Number(raw);
        return !Number.isFinite(value) || value < 0 || value >= 1;
      }),
    );
    if (invalidRiskValue) {
      setError("风控比例必须是 0 到 1 之间的数字（不含 1）");
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
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">发起回测</h2>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            关闭
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
              <div>
                <span className="text-sm text-slate-600">信号策略对比组</span>
                <p className="mt-0.5 text-xs text-slate-500">
                  每组选择一个保存的信号策略，风控比例留空时跟随该股票当前配置
                </p>
              </div>
              <button
                type="button"
                onClick={addGroup}
                disabled={loadingStrategies || strategies.length === 0}
                className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                添加策略组
              </button>
            </div>
            <div className="space-y-3">
              {groups.map((g, idx) => (
                <div key={g.key} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-500">对比组 {idx + 1}</span>
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
                  <label className="mb-3 block text-xs">
                    <span className="text-slate-500">信号策略</span>
                    <select
                      required
                      value={g.signal_strategy_id}
                      disabled={loadingStrategies}
                      onChange={(e) =>
                        updateGroup(g.key, "signal_strategy_id", e.target.value)
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-900 outline-none ring-sky-200 focus:ring disabled:bg-slate-100"
                    >
                      <option value="">
                        {loadingStrategies ? "正在加载策略..." : "请选择信号策略"}
                      </option>
                      {strategies.map((strategy) => (
                        <option key={strategy.id} value={strategy.id}>
                          {strategy.name}
                          {strategy.builtin_key ? "（内置）" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    {NUMERIC_FIELDS.map((field) => (
                      <label key={field.key} className="block">
                        <span className="text-slate-500">{field.label}</span>
                        <input
                          type="number"
                          step={field.step}
                          min={0}
                          max="0.999"
                          value={g[field.key]}
                          onChange={(e) => updateGroup(g.key, field.key, e.target.value)}
                          placeholder="跟随当前"
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
