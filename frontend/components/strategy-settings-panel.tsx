"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  clearStrategyOverride,
  DEFAULT_STRATEGY,
  DEFAULT_TRAILING_LADDER,
  fetchStrategy,
  fetchStrategyOverride,
  StrategyConfig,
  TrailingLadderLevel,
  updateStrategy,
  updateStrategyOverride,
} from "@/lib/api";

type LadderFormRow = {
  min_pnl: string;
  max_pnl: string;
  drawdown: string;
};

type StrategyFormState = {
  global_buy_n: string;
  global_buy_x: string;
  global_sell_n: string;
  global_sell_y: string;
  stop_loss_pct: string;
  break_even_trigger_pct: string;
  break_even_buffer_pct: string;
  enable_partial_take_profit: boolean;
  enable_addon_alert: boolean;
  enable_tech_sell_while_holding: boolean;
  ladder: LadderFormRow[];
};

function pctToDisplay(value: number): string {
  return (value * 100).toFixed(value * 100 >= 1 ? 1 : 2);
}

function ladderToForm(levels: TrailingLadderLevel[]): LadderFormRow[] {
  return levels.map((level) => ({
    min_pnl: pctToDisplay(level.min_pnl),
    max_pnl: level.max_pnl == null ? "" : pctToDisplay(level.max_pnl),
    drawdown: pctToDisplay(level.drawdown),
  }));
}

function strategyToForm(data: StrategyConfig): StrategyFormState {
  return {
    global_buy_n: String(data.global_buy_n),
    global_buy_x: String(data.global_buy_x),
    global_sell_n: String(data.global_sell_n),
    global_sell_y: String(data.global_sell_y),
    stop_loss_pct: pctToDisplay(data.stop_loss_pct),
    break_even_trigger_pct: pctToDisplay(data.break_even_trigger_pct),
    break_even_buffer_pct: pctToDisplay(data.break_even_buffer_pct),
    enable_partial_take_profit: data.enable_partial_take_profit,
    enable_addon_alert: data.enable_addon_alert,
    enable_tech_sell_while_holding: data.enable_tech_sell_while_holding,
    ladder: ladderToForm(data.trailing_ladder?.length ? data.trailing_ladder : DEFAULT_TRAILING_LADDER),
  };
}

function parsePct(raw: string, label: string): number {
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0 || value >= 100) {
    throw new Error(`${label}必须是 0–100 之间的数字（不含 100）`);
  }
  return value / 100;
}

function parseLadder(rows: LadderFormRow[]): TrailingLadderLevel[] {
  if (rows.length === 0) {
    throw new Error("阶梯表不能为空");
  }
  const levels: TrailingLadderLevel[] = rows.map((row, index) => {
    const min = parsePct(row.min_pnl, `第 ${index + 1} 档最小浮盈`);
    const maxRaw = row.max_pnl.trim();
    const max = maxRaw === "" ? null : parsePct(maxRaw, `第 ${index + 1} 档最大浮盈`);
    const drawdown = parsePct(row.drawdown, `第 ${index + 1} 档回撤`);
    if (drawdown <= 0) {
      throw new Error(`第 ${index + 1} 档回撤必须大于 0`);
    }
    if (max != null && min >= max) {
      throw new Error(`第 ${index + 1} 档最小浮盈必须小于最大浮盈`);
    }
    return { min_pnl: min, max_pnl: max, drawdown };
  });
  return levels;
}

export type StrategyDialogTarget = {
  stock_code: string;
  stock_name: string;
} | null;

type StrategySettingsPanelProps = {
  stockTarget?: StrategyDialogTarget;
  onStockTargetClose?: () => void;
  onSaved?: () => void;
};

export default function StrategySettingsPanel({
  stockTarget = null,
  onStockTargetClose,
  onSaved,
}: StrategySettingsPanelProps) {
  const [strategy, setStrategy] = useState<StrategyConfig>(DEFAULT_STRATEGY);
  const [loadingStrategy, setLoadingStrategy] = useState(true);
  const [strategyOpen, setStrategyOpen] = useState(false);
  const [savingStrategy, setSavingStrategy] = useState(false);
  const [strategyError, setStrategyError] = useState("");
  const [message, setMessage] = useState("");
  const [strategyForm, setStrategyForm] = useState<StrategyFormState>(strategyToForm(DEFAULT_STRATEGY));
  const [overrideMode, setOverrideMode] = useState(false);

  const isStockMode = stockTarget != null;
  const modalOpen = strategyOpen || isStockMode;

  useEffect(() => {
    async function loadStrategy() {
      setLoadingStrategy(true);
      try {
        const data = await fetchStrategy();
        setStrategy(normalizeStrategy(data));
        setStrategyForm(strategyToForm(normalizeStrategy(data)));
      } catch {
        setMessage("策略参数加载失败，已使用默认值");
        setStrategy(DEFAULT_STRATEGY);
        setStrategyForm(strategyToForm(DEFAULT_STRATEGY));
      } finally {
        setLoadingStrategy(false);
      }
    }

    void loadStrategy();
  }, []);

  useEffect(() => {
    if (!stockTarget) {
      return;
    }

    async function loadOverride() {
      setStrategyError("");
      try {
        const global = normalizeStrategy(await fetchStrategy());
        setStrategy(global);
        const override = await fetchStrategyOverride(stockTarget!.stock_code);
        const resolved = override.resolved;
        const formBase = strategyToForm(global);
        if (resolved) {
          formBase.stop_loss_pct = pctToDisplay(resolved.stop_loss_pct);
          formBase.break_even_trigger_pct = pctToDisplay(resolved.break_even_trigger_pct);
          formBase.break_even_buffer_pct = pctToDisplay(resolved.break_even_buffer_pct);
          formBase.enable_partial_take_profit = resolved.enable_partial_take_profit;
          formBase.enable_addon_alert = resolved.enable_addon_alert;
          formBase.enable_tech_sell_while_holding = resolved.enable_tech_sell_while_holding;
          formBase.ladder = ladderToForm(
            resolved.trailing_ladder?.length ? resolved.trailing_ladder : DEFAULT_TRAILING_LADDER,
          );
          formBase.global_buy_n = String(resolved.n ?? global.global_buy_n);
          formBase.global_buy_x = String(resolved.x ?? global.global_buy_x);
          formBase.global_sell_n = String(global.global_sell_n);
          formBase.global_sell_y = String(resolved.y ?? global.global_sell_y);
        }
        setStrategyForm(formBase);
        setOverrideMode(Boolean(override.has_override));
      } catch {
        setStrategyError("加载单股策略失败");
      }
    }

    void loadOverride();
  }, [stockTarget]);

  function normalizeStrategy(data: StrategyConfig): StrategyConfig {
    return {
      ...DEFAULT_STRATEGY,
      ...data,
      trailing_ladder:
        data.trailing_ladder?.length > 0 ? data.trailing_ladder : DEFAULT_TRAILING_LADDER,
    };
  }

  function openStrategyModal() {
    onStockTargetClose?.();
    setStrategyForm(strategyToForm(strategy));
    setStrategyError("");
    setOverrideMode(false);
    setStrategyOpen(true);
  }

  function closeStrategyModal() {
    if (savingStrategy) {
      return;
    }
    setStrategyOpen(false);
    setStrategyError("");
    onStockTargetClose?.();
  }

  async function onClearOverride() {
    if (!stockTarget) return;
    setSavingStrategy(true);
    setStrategyError("");
    try {
      await clearStrategyOverride(stockTarget.stock_code);
      setMessage(`已清除 ${stockTarget.stock_name} 单股覆盖`);
      closeStrategyModal();
      onSaved?.();
    } catch {
      setStrategyError("清除覆盖失败，请稍后重试");
    } finally {
      setSavingStrategy(false);
    }
  }

  async function onSaveStrategy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    try {
      const parsedBuyN = Number(strategyForm.global_buy_n);
      const parsedBuyX = Number(strategyForm.global_buy_x);
      const parsedSellN = Number(strategyForm.global_sell_n);
      const parsedSellY = Number(strategyForm.global_sell_y);

      if (!Number.isInteger(parsedBuyN) || parsedBuyN <= 0) {
        setStrategyError("买点交易日数必须是大于 0 的整数");
        return;
      }
      if (!Number.isInteger(parsedSellN) || parsedSellN <= 0) {
        setStrategyError("卖点交易日数必须是大于 0 的整数");
        return;
      }
      if (
        !Number.isFinite(parsedBuyX) ||
        parsedBuyX <= 0 ||
        !Number.isFinite(parsedSellY) ||
        parsedSellY <= 0
      ) {
        setStrategyError("买点倍数和卖点倍数必须是大于 0 的数字");
        return;
      }

      const stopLoss = parsePct(strategyForm.stop_loss_pct, "初始止损");
      const breakEvenTrigger = parsePct(strategyForm.break_even_trigger_pct, "保本触发");
      const breakEvenBuffer = parsePct(strategyForm.break_even_buffer_pct, "保本缓冲");
      const ladder = parseLadder(strategyForm.ladder);

      setSavingStrategy(true);
      setStrategyError("");

      if (isStockMode && stockTarget) {
        await updateStrategyOverride(stockTarget.stock_code, {
          custom_n: parsedBuyN,
          custom_x: Number(parsedBuyX.toFixed(2)),
          custom_y: Number(parsedSellY.toFixed(2)),
          stop_loss_pct: stopLoss,
          break_even_trigger_pct: breakEvenTrigger,
          break_even_buffer_pct: breakEvenBuffer,
          trailing_ladder: ladder,
          enable_partial_take_profit: strategyForm.enable_partial_take_profit,
          enable_addon_alert: strategyForm.enable_addon_alert,
          enable_tech_sell_while_holding: strategyForm.enable_tech_sell_while_holding,
        });
        setMessage(`已保存 ${stockTarget.stock_name} 单股策略覆盖`);
        closeStrategyModal();
        onSaved?.();
      } else {
        const payload: StrategyConfig = {
          global_buy_n: parsedBuyN,
          global_buy_x: Number(parsedBuyX.toFixed(2)),
          global_sell_n: parsedSellN,
          global_sell_y: Number(parsedSellY.toFixed(2)),
          stop_loss_pct: stopLoss,
          break_even_trigger_pct: breakEvenTrigger,
          break_even_buffer_pct: breakEvenBuffer,
          trailing_ladder: ladder,
          enable_partial_take_profit: strategyForm.enable_partial_take_profit,
          enable_addon_alert: strategyForm.enable_addon_alert,
          enable_tech_sell_while_holding: strategyForm.enable_tech_sell_while_holding,
        };
        const updated = normalizeStrategy(await updateStrategy(payload));
        setStrategy(updated);
        setMessage("策略参数已更新");
        setStrategyOpen(false);
        onSaved?.();
      }
    } catch (error) {
      setStrategyError(error instanceof Error ? error.message : "保存失败，请稍后重试");
    } finally {
      setSavingStrategy(false);
    }
  }

  function updateLadderRow(index: number, patch: Partial<LadderFormRow>) {
    setStrategyForm((prev) => ({
      ...prev,
      ladder: prev.ladder.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    }));
  }

  return (
    <>
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="mb-2 inline-flex rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600">
              Trading Signal Workspace
            </p>
            <p className="mt-2 text-sm text-slate-600 md:text-base">策略参数维护（含止盈止损）</p>
          </div>

          <button
            type="button"
            onClick={openStrategyModal}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
          >
            策略设置
          </button>
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-3 text-sm leading-tight">
              <p className="text-slate-500">买点交易日数</p>
              <p className="font-semibold text-slate-900">
                {loadingStrategy ? "--" : `${strategy.global_buy_n} 天`}
              </p>
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-3 text-sm leading-tight">
              <p className="text-slate-500">初始止损</p>
              <p className="font-semibold text-slate-900">
                {loadingStrategy ? "--" : `${pctToDisplay(strategy.stop_loss_pct)}%`}
              </p>
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-3 text-sm leading-tight">
              <p className="text-slate-500">分批止盈</p>
              <p className="font-semibold text-slate-900">
                {loadingStrategy ? "--" : strategy.enable_partial_take_profit ? "开" : "关"}
              </p>
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-3 text-sm leading-tight">
              <p className="text-slate-500">持仓加仓提醒</p>
              <p className="font-semibold text-slate-900">
                {loadingStrategy ? "--" : strategy.enable_addon_alert ? "开" : "关"}
              </p>
            </div>
          </div>
        </div>

        {message ? (
          <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
            {message}
          </div>
        ) : null}
      </section>

      {modalOpen ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/40 px-4 py-8">
          <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">
                  {isStockMode
                    ? `本股策略覆盖 · ${stockTarget?.stock_name ?? ""}`
                    : "策略参数设置"}
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  {isStockMode
                    ? "保存后仅对该股生效；可清除覆盖以恢复全局默认"
                    : "修改后下一轮计算生效；持仓股止损参考价将按新参数重算"}
                </p>
                {isStockMode && overrideMode ? (
                  <p className="mt-1 text-xs text-amber-700">当前存在单股覆盖</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={closeStrategyModal}
                className="rounded-md px-2 py-1 text-sm text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
              >
                关闭
              </button>
            </div>

            <form onSubmit={onSaveStrategy} className="grid gap-3">
              <p className="m-0 text-xs font-medium text-slate-500">买卖点参数</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="grid gap-1 text-sm text-slate-700">
                  买点交易日数（天）
                  <input
                    value={strategyForm.global_buy_n}
                    onChange={(event) =>
                      setStrategyForm((prev) => ({ ...prev, global_buy_n: event.target.value }))
                    }
                    inputMode="numeric"
                    className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                  />
                </label>
                <label className="grid gap-1 text-sm text-slate-700">
                  买点倍数（倍）
                  <input
                    value={strategyForm.global_buy_x}
                    onChange={(event) =>
                      setStrategyForm((prev) => ({ ...prev, global_buy_x: event.target.value }))
                    }
                    inputMode="decimal"
                    className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                  />
                </label>
                {!isStockMode ? (
                  <label className="grid gap-1 text-sm text-slate-700">
                    卖点交易日数（天）
                    <input
                      value={strategyForm.global_sell_n}
                      onChange={(event) =>
                        setStrategyForm((prev) => ({ ...prev, global_sell_n: event.target.value }))
                      }
                      inputMode="numeric"
                      className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                    />
                  </label>
                ) : null}
                <label className="grid gap-1 text-sm text-slate-700">
                  卖点倍数（倍）
                  <input
                    value={strategyForm.global_sell_y}
                    onChange={(event) =>
                      setStrategyForm((prev) => ({ ...prev, global_sell_y: event.target.value }))
                    }
                    inputMode="decimal"
                    className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                  />
                </label>
              </div>

              <p className="mb-0 mt-2 text-xs font-medium text-slate-500">止盈止损</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <label className="grid min-w-0 gap-1 text-sm text-slate-700">
                  初始止损（%）
                  <input
                    value={strategyForm.stop_loss_pct}
                    onChange={(event) =>
                      setStrategyForm((prev) => ({ ...prev, stop_loss_pct: event.target.value }))
                    }
                    inputMode="decimal"
                    className="w-full min-w-0 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                  />
                </label>
                <label className="grid min-w-0 gap-1 text-sm text-slate-700">
                  保本触发（%）
                  <input
                    value={strategyForm.break_even_trigger_pct}
                    onChange={(event) =>
                      setStrategyForm((prev) => ({
                        ...prev,
                        break_even_trigger_pct: event.target.value,
                      }))
                    }
                    inputMode="decimal"
                    className="w-full min-w-0 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                  />
                </label>
                <label className="grid min-w-0 gap-1 text-sm text-slate-700">
                  保本缓冲（%）
                  <input
                    value={strategyForm.break_even_buffer_pct}
                    onChange={(event) =>
                      setStrategyForm((prev) => ({
                        ...prev,
                        break_even_buffer_pct: event.target.value,
                      }))
                    }
                    inputMode="decimal"
                    className="w-full min-w-0 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                  />
                </label>
              </div>

              <div className="overflow-x-auto rounded-lg border border-slate-200">
                <table className="min-w-full text-left text-xs text-slate-700">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      <th className="px-2 py-2 font-medium">浮盈下限%</th>
                      <th className="px-2 py-2 font-medium">浮盈上限%（空=无上限）</th>
                      <th className="px-2 py-2 font-medium">回撤容忍%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strategyForm.ladder.map((row, index) => (
                      <tr key={index} className="border-t border-slate-100">
                        <td className="px-2 py-1.5">
                          <input
                            value={row.min_pnl}
                            onChange={(event) =>
                              updateLadderRow(index, { min_pnl: event.target.value })
                            }
                            className="w-full rounded border border-slate-300 px-2 py-1"
                          />
                        </td>
                        <td className="px-2 py-1.5">
                          <input
                            value={row.max_pnl}
                            onChange={(event) =>
                              updateLadderRow(index, { max_pnl: event.target.value })
                            }
                            className="w-full rounded border border-slate-300 px-2 py-1"
                          />
                        </td>
                        <td className="px-2 py-1.5">
                          <input
                            value={row.drawdown}
                            onChange={(event) =>
                              updateLadderRow(index, { drawdown: event.target.value })
                            }
                            className="w-full rounded border border-slate-300 px-2 py-1"
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={strategyForm.enable_partial_take_profit}
                  onChange={(event) =>
                    setStrategyForm((prev) => ({
                      ...prev,
                      enable_partial_take_profit: event.target.checked,
                    }))
                  }
                />
                启用分批止盈建议（浮盈跨档时提醒）
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={strategyForm.enable_addon_alert}
                  onChange={(event) =>
                    setStrategyForm((prev) => ({
                      ...prev,
                      enable_addon_alert: event.target.checked,
                    }))
                  }
                />
                持仓中允许加仓提醒
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={strategyForm.enable_tech_sell_while_holding}
                  onChange={(event) =>
                    setStrategyForm((prev) => ({
                      ...prev,
                      enable_tech_sell_while_holding: event.target.checked,
                    }))
                  }
                />
                持仓中保留技术高位卖点辅助
              </label>

              {strategyError ? <p className="m-0 text-xs text-rose-600">{strategyError}</p> : null}

              <div className="mt-1 flex flex-wrap items-center justify-end gap-2">
                {isStockMode ? (
                  <button
                    type="button"
                    onClick={() => void onClearOverride()}
                    disabled={savingStrategy}
                    className="mr-auto rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm text-amber-800 transition-colors hover:bg-amber-50 disabled:cursor-not-allowed disabled:text-slate-400"
                  >
                    清除覆盖
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={closeStrategyModal}
                  disabled={savingStrategy}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:text-slate-400"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={savingStrategy}
                  className="rounded-lg bg-sky-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-sky-300"
                >
                  {savingStrategy ? "保存中..." : "保存"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
