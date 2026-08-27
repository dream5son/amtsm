"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  assignWatchlistStrategy,
  createBacktest,
  SignalStrategy,
  WatchlistItem,
} from "@/lib/api";

type DraftStrategyId = number | null;

type WatchlistStrategyDialogProps = {
  open: boolean;
  item: WatchlistItem | null;
  strategies: SignalStrategy[];
  onClose: () => void;
  onAssigned: (message: string) => void;
  onBacktestSubmitted: (jobCount: number) => void;
};

function resolveDefaultStrategyId(
  strategies: SignalStrategy[],
  item: WatchlistItem,
): number | null {
  const marked = strategies.find((strategy) => strategy.is_default);
  if (marked) return marked.id;
  if (item.signal_strategy_id == null) return item.effective_signal_strategy_id;
  return null;
}

function resolveBacktestStrategyId(
  draft: DraftStrategyId,
  strategies: SignalStrategy[],
  item: WatchlistItem,
): number | null {
  if (draft != null) return draft;
  return resolveDefaultStrategyId(strategies, item);
}

export default function WatchlistStrategyDialog({
  open,
  item,
  strategies,
  onClose,
  onAssigned,
  onBacktestSubmitted,
}: WatchlistStrategyDialogProps) {
  const [draft, setDraft] = useState<DraftStrategyId>(item?.signal_strategy_id ?? null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [assigning, setAssigning] = useState(false);
  const [backtesting, setBacktesting] = useState(false);

  const defaultName = useMemo(() => {
    if (!item) return "";
    const marked = strategies.find((strategy) => strategy.is_default);
    if (marked) return marked.name;
    if (item.signal_strategy_id == null) return item.effective_signal_strategy_name;
    return "";
  }, [item, strategies]);

  if (!open || !item) return null;

  const target = item;
  const inFlightBacktest =
    target.backtest_status === "PENDING" || target.backtest_status === "RUNNING";
  const busy = assigning || backtesting;

  async function onBacktest() {
    setError("");
    setStatus("");
    if (inFlightBacktest) {
      setError(`${target.stock_name} 回测正在进行中，请稍候`);
      return;
    }
    const strategyId = resolveBacktestStrategyId(draft, strategies, target);
    if (strategyId == null) {
      setError("无法确定用于回测的信号策略，请先选择一个策略");
      return;
    }

    setBacktesting(true);
    try {
      const response = await createBacktest({
        stock_code: target.stock_code,
        params: [{ signal_strategy_id: strategyId }],
      });
      onBacktestSubmitted(response.jobs.length);
      const name =
        strategies.find((strategy) => strategy.id === strategyId)?.name ?? "选定策略";
      setStatus(`已提交「${name}」回测（${response.jobs.length} 组参数），进行中...`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "发起回测失败");
    } finally {
      setBacktesting(false);
    }
  }

  async function onConfirm(event: FormEvent) {
    event.preventDefault();
    setError("");
    setStatus("");
    setAssigning(true);
    try {
      const effective = await assignWatchlistStrategy(target.stock_code, draft);
      onAssigned(
        draft == null
          ? `${target.stock_name} 已改为跟随默认信号策略「${effective.name}」`
          : `${target.stock_name} 已使用信号策略「${effective.name}」`,
      );
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "信号策略分配失败，请稍后重试");
    } finally {
      setAssigning(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-labelledby="watchlist-strategy-dialog-title"
        className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2
            id="watchlist-strategy-dialog-title"
            className="text-lg font-semibold text-slate-900"
          >
            设置信号策略
          </h2>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            关闭
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-600">
          {target.stock_name}（{target.stock_code}）
        </p>

        <form onSubmit={(e) => void onConfirm(e)} className="space-y-4">
          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm text-slate-600">选择策略</legend>
            <label
              className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                draft == null
                  ? "border-sky-300 bg-sky-50"
                  : "border-slate-200 hover:bg-slate-50"
              }`}
            >
              <input
                type="radio"
                name="watchlist-signal-strategy"
                value=""
                checked={draft == null}
                onChange={() => setDraft(null)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium text-slate-900">跟随默认</span>
                {defaultName ? (
                  <span className="mt-0.5 block text-xs text-slate-500">当前默认：{defaultName}</span>
                ) : null}
              </span>
            </label>
            {strategies.map((strategy) => (
              <label
                key={strategy.id}
                className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                  draft === strategy.id
                    ? "border-sky-300 bg-sky-50"
                    : "border-slate-200 hover:bg-slate-50"
                }`}
              >
                <input
                  type="radio"
                  name="watchlist-signal-strategy"
                  value={strategy.id}
                  checked={draft === strategy.id}
                  onChange={() => setDraft(strategy.id)}
                  className="mt-0.5"
                />
                <span>
                  <span className="font-medium text-slate-900">{strategy.name}</span>
                  {strategy.is_default ? (
                    <span className="ml-1.5 rounded border border-sky-200 bg-sky-50 px-1 py-0.5 text-[10px] text-sky-700">
                      默认
                    </span>
                  ) : null}
                  {strategy.builtin_key ? (
                    <span className="mt-0.5 block text-xs text-slate-500">内置</span>
                  ) : null}
                </span>
              </label>
            ))}
          </fieldset>

          <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
            回测使用当前选中策略，区间为该股票上市至今，风控参数跟随该股当前配置。回测不会改写绑定，点确定后才会保存。
          </p>

          {error ? <p className="text-sm text-rose-600">{error}</p> : null}
          {status ? <p className="text-sm text-slate-700">{status}</p> : null}

          <div className="flex flex-wrap justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => void onBacktest()}
              disabled={busy || inFlightBacktest}
              className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {backtesting || inFlightBacktest ? "回测中..." : "回测该策略"}
            </button>
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:bg-sky-300"
            >
              {assigning ? "保存中..." : "确定"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
