"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  archiveSignalStrategy,
  cloneSignalStrategy,
  createSignalStrategy,
  dryRunSignalStrategy,
  fetchSignalOperators,
  fetchSignalStrategies,
  fetchWatchlist,
  setDefaultSignalStrategy,
  SignalGate,
  SignalOperatorDefinition,
  SignalOperatorName,
  SignalRecipeChannel,
  SignalStrategy,
  SignalStrategyDryRunResult,
  SignalStrategyRecipe,
  updateSignalStrategy,
} from "@/lib/api";

type ChannelName = "buy" | "sell" | "addon";
type VolumeOperatorName = "volume_increase" | "volume_abs_change";

type StrategyFormState = {
  name: string;
  description: string;
  recipe: SignalStrategyRecipe;
};

const DEFAULT_RECIPE: SignalStrategyRecipe = {
  schema_version: 1,
  channels: {
    buy: {
      all: [
        {
          op: "price_near_low",
          version: 1,
          params: { lookback_days: 60, factor: 1.1 },
        },
      ],
    },
    sell: {
      all: [
        {
          op: "price_near_high",
          version: 1,
          params: { lookback_days: 60, factor: 0.9 },
        },
      ],
    },
    addon: null,
  },
};

const CHANNEL_LABELS: Record<ChannelName, string> = {
  buy: "买入",
  sell: "卖出",
  addon: "加仓",
};

const OPERATOR_LABELS: Record<SignalOperatorName, string> = {
  price_near_low: "接近阶段低点",
  price_near_high: "接近阶段高点",
  volume_increase: "成交量增加",
  volume_abs_change: "成交量绝对变动",
};

const DETAIL_LABELS: Record<string, string> = {
  price: "当前价格",
  baseline_price: "基准价格",
  factor: "系数",
  threshold: "触发阈值",
  lookback_days: "回看交易日",
  current_volume: "当前成交量",
  avg_volume: "平均成交量",
  actual_n: "实际样本数",
  change_pct: "成交量变化",
  min_change_pct: "最小增幅",
  min_abs_change_pct: "最小绝对变幅",
  identity: "恒通过条件",
};

function cloneRecipe(recipe: SignalStrategyRecipe): SignalStrategyRecipe {
  return structuredClone(recipe);
}

function makeForm(strategy?: SignalStrategy): StrategyFormState {
  return {
    name: strategy?.name ?? "",
    description: strategy?.description ?? "",
    recipe: cloneRecipe(strategy?.recipe ?? DEFAULT_RECIPE),
  };
}

function isVolumeOperator(op: SignalOperatorName): op is VolumeOperatorName {
  return op === "volume_increase" || op === "volume_abs_change";
}

function makeVolumeGate(op: VolumeOperatorName): SignalGate {
  if (op === "volume_increase") {
    return {
      op,
      version: 1,
      params: { lookback_days: 7, min_change_pct: 0 },
    };
  }
  return {
    op,
    version: 1,
    params: { lookback_days: 7, min_abs_change_pct: 0.3 },
  };
}

function allRecipeChannels(recipe: SignalStrategyRecipe): SignalRecipeChannel[] {
  return [
    recipe.channels.buy,
    recipe.channels.sell,
    ...(recipe.channels.addon ? [recipe.channels.addon] : []),
  ];
}

function validateRecipeForm(recipe: SignalStrategyRecipe): string | null {
  for (const channel of allRecipeChannels(recipe)) {
    for (const gate of channel.all) {
      if (!Number.isInteger(gate.params.lookback_days) || gate.params.lookback_days <= 0) {
        return "回看交易日必须是大于 0 的整数";
      }
      if (
        (gate.op === "price_near_low" || gate.op === "price_near_high") &&
        (!Number.isFinite(gate.params.factor) || gate.params.factor <= 0)
      ) {
        return "价格系数必须是大于 0 的数字";
      }
      if (
        gate.op === "volume_increase" &&
        (!Number.isFinite(gate.params.min_change_pct) || gate.params.min_change_pct < 0)
      ) {
        return "成交量最小增幅不能小于 0";
      }
      if (
        gate.op === "volume_abs_change" &&
        (!Number.isFinite(gate.params.min_abs_change_pct) ||
          gate.params.min_abs_change_pct < 0)
      ) {
        return "成交量最小绝对变幅不能小于 0";
      }
    }
  }
  return null;
}

function displayDetail(key: string, value: unknown): string {
  if (value == null) return "--";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    if (key.endsWith("_pct")) return `${(value * 100).toFixed(2)}%`;
    if (key.includes("volume")) return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    return value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  }
  return String(value);
}

type ChannelEditorProps = {
  channelName: ChannelName;
  channel: SignalRecipeChannel;
  volumeOperators: VolumeOperatorName[];
  onParamChange: (gateIndex: number, param: string, value: number) => void;
  onAddGate: (op: VolumeOperatorName) => void;
  onRemoveGate: (gateIndex: number) => void;
  onMoveGate: (gateIndex: number, direction: -1 | 1) => void;
};

function ChannelEditor({
  channelName,
  channel,
  volumeOperators,
  onParamChange,
  onAddGate,
  onRemoveGate,
  onMoveGate,
}: ChannelEditorProps) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">{CHANNEL_LABELS[channelName]}通道</h4>
          <p className="mt-0.5 text-xs text-slate-500">全部条件依次计算，并且全部通过后触发</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {volumeOperators.map((op) => (
            <button
              key={op}
              type="button"
              onClick={() => onAddGate(op)}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
            >
              添加{OPERATOR_LABELS[op]}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-2">
        {channel.all.map((gate, index) => (
          <div key={`${gate.op}-${index}`} className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-slate-100 px-1.5 text-[11px] font-semibold text-slate-600">
                  {index + 1}
                </span>
                <span className="text-sm font-medium text-slate-800">
                  {OPERATOR_LABELS[gate.op]}
                </span>
                {index === 0 ? (
                  <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-500">
                    固定首项
                  </span>
                ) : null}
              </div>
              {index > 0 ? (
                <div className="flex items-center gap-1 text-xs">
                  <button
                    type="button"
                    disabled={index === 1}
                    onClick={() => onMoveGate(index, -1)}
                    className="rounded px-1.5 py-1 text-slate-500 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
                  >
                    上移
                  </button>
                  <button
                    type="button"
                    disabled={index === channel.all.length - 1}
                    onClick={() => onMoveGate(index, 1)}
                    className="rounded px-1.5 py-1 text-slate-500 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
                  >
                    下移
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemoveGate(index)}
                    className="rounded px-1.5 py-1 text-rose-600 hover:bg-rose-50"
                  >
                    移除
                  </button>
                </div>
              ) : null}
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <label className="grid gap-1 text-xs text-slate-600">
                lookback_days（交易日）
                <input
                  type="number"
                  min={1}
                  step={1}
                  required
                  value={gate.params.lookback_days}
                  onChange={(event) =>
                    onParamChange(index, "lookback_days", Number(event.target.value))
                  }
                  className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
                />
              </label>
              {gate.op === "price_near_low" || gate.op === "price_near_high" ? (
                <label className="grid gap-1 text-xs text-slate-600">
                  factor（系数）
                  <input
                    type="number"
                    min="0.0001"
                    step="0.01"
                    required
                    value={gate.params.factor}
                    onChange={(event) =>
                      onParamChange(index, "factor", Number(event.target.value))
                    }
                    className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
                  />
                </label>
              ) : null}
              {gate.op === "volume_increase" ? (
                <label className="grid gap-1 text-xs text-slate-600">
                  min_change_pct（0.3 表示 30%）
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    required
                    value={gate.params.min_change_pct}
                    onChange={(event) =>
                      onParamChange(index, "min_change_pct", Number(event.target.value))
                    }
                    className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
                  />
                </label>
              ) : null}
              {gate.op === "volume_abs_change" ? (
                <label className="grid gap-1 text-xs text-slate-600">
                  min_abs_change_pct（0.3 表示 30%）
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    required
                    value={gate.params.min_abs_change_pct}
                    onChange={(event) =>
                      onParamChange(index, "min_abs_change_pct", Number(event.target.value))
                    }
                    className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
                  />
                </label>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

type SignalStrategyPanelProps = {
  onChanged?: () => void;
};

export default function SignalStrategyPanel({ onChanged }: SignalStrategyPanelProps) {
  const [strategies, setStrategies] = useState<SignalStrategy[]>([]);
  const [operators, setOperators] = useState<SignalOperatorDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [defaultStrategyId, setDefaultStrategyId] = useState<number | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingStrategy, setEditingStrategy] = useState<SignalStrategy | null>(null);
  const [form, setForm] = useState<StrategyFormState>(() => makeForm());
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);

  const [dryStrategyId, setDryStrategyId] = useState("");
  const [dryStockCode, setDryStockCode] = useState("");
  const [dryAsOf, setDryAsOf] = useState("");
  const [dryRunning, setDryRunning] = useState(false);
  const [dryError, setDryError] = useState("");
  const [dryResult, setDryResult] = useState<SignalStrategyDryRunResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([
      fetchSignalStrategies(),
      fetchSignalOperators(),
      fetchWatchlist(),
    ]).then(([strategyResult, operatorResult, watchlistResult]) => {
      if (cancelled) return;
      if (strategyResult.status === "fulfilled") {
        setStrategies(strategyResult.value);
        setDryStrategyId((current) =>
          strategyResult.value.some((strategy) => String(strategy.id) === current)
            ? current
            : String(strategyResult.value[0]?.id ?? ""),
        );
      } else {
        setMessage("信号策略加载失败，请稍后重试");
      }
      if (operatorResult.status === "fulfilled") {
        setOperators(operatorResult.value);
      }
      if (watchlistResult.status === "fulfilled") {
        const inherited = watchlistResult.value.find((item) => item.signal_strategy_id == null);
        setDefaultStrategyId(inherited?.effective_signal_strategy_id ?? null);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const volumeOperators = useMemo<VolumeOperatorName[]>(() => {
    const fromApi = operators.map((operator) => operator.op).filter(isVolumeOperator);
    return fromApi.length > 0 ? fromApi : ["volume_increase", "volume_abs_change"];
  }, [operators]);

  async function refreshStrategies() {
    const next = await fetchSignalStrategies();
    setStrategies(next);
    setDryStrategyId((current) =>
      next.some((strategy) => String(strategy.id) === current)
        ? current
        : String(next[0]?.id ?? ""),
    );
  }

  function openCreate() {
    setEditingStrategy(null);
    setForm(makeForm());
    setFormError("");
    setDialogOpen(true);
  }

  function openEdit(strategy: SignalStrategy) {
    setEditingStrategy(strategy);
    setForm(makeForm(strategy));
    setFormError("");
    setDialogOpen(true);
  }

  function closeDialog() {
    if (saving) return;
    setDialogOpen(false);
    setFormError("");
  }

  function updateGateParam(
    channelName: ChannelName,
    gateIndex: number,
    param: string,
    value: number,
  ) {
    setForm((current) => {
      const recipe = cloneRecipe(current.recipe);
      const channel = recipe.channels[channelName];
      if (!channel) return current;
      const gate = channel.all[gateIndex];
      if (!gate) return current;

      const priceGate = gate.op === "price_near_low" || gate.op === "price_near_high";
      if (param === "lookback_days" && priceGate) {
        for (const recipeChannel of allRecipeChannels(recipe)) {
          const first = recipeChannel.all[0];
          if (first.op === "price_near_low" || first.op === "price_near_high") {
            first.params.lookback_days = value;
          }
        }
      } else if (param === "lookback_days" && isVolumeOperator(gate.op)) {
        for (const recipeChannel of allRecipeChannels(recipe)) {
          for (const recipeGate of recipeChannel.all) {
            if (isVolumeOperator(recipeGate.op)) {
              recipeGate.params.lookback_days = value;
            }
          }
        }
      } else if (param === "factor" && priceGate) {
        gate.params.factor = value;
        if (channelName === "buy" || channelName === "addon") {
          const buyGate = recipe.channels.buy.all[0];
          if (buyGate.op === "price_near_low") buyGate.params.factor = value;
          const addonGate = recipe.channels.addon?.all[0];
          if (addonGate?.op === "price_near_low") addonGate.params.factor = value;
        }
      } else if (param === "min_change_pct" && gate.op === "volume_increase") {
        gate.params.min_change_pct = value;
      } else if (param === "min_abs_change_pct" && gate.op === "volume_abs_change") {
        gate.params.min_abs_change_pct = value;
      }

      return { ...current, recipe };
    });
  }

  function addGate(channelName: ChannelName, op: VolumeOperatorName) {
    setForm((current) => {
      const recipe = cloneRecipe(current.recipe);
      const channel = recipe.channels[channelName];
      if (!channel) return current;
      channel.all.push(makeVolumeGate(op));
      const existingVolume = allRecipeChannels(recipe)
        .flatMap((recipeChannel) => recipeChannel.all)
        .find((gate) => isVolumeOperator(gate.op) && gate !== channel.all.at(-1));
      if (existingVolume) {
        const added = channel.all.at(-1);
        if (added && isVolumeOperator(added.op)) {
          added.params.lookback_days = existingVolume.params.lookback_days;
        }
      }
      return { ...current, recipe };
    });
  }

  function removeGate(channelName: ChannelName, gateIndex: number) {
    setForm((current) => {
      const recipe = cloneRecipe(current.recipe);
      const channel = recipe.channels[channelName];
      if (!channel || gateIndex === 0) return current;
      channel.all.splice(gateIndex, 1);
      return { ...current, recipe };
    });
  }

  function moveGate(channelName: ChannelName, gateIndex: number, direction: -1 | 1) {
    setForm((current) => {
      const recipe = cloneRecipe(current.recipe);
      const channel = recipe.channels[channelName];
      if (!channel) return current;
      const nextIndex = gateIndex + direction;
      if (gateIndex <= 0 || nextIndex <= 0 || nextIndex >= channel.all.length) return current;
      [channel.all[gateIndex], channel.all[nextIndex]] = [
        channel.all[nextIndex],
        channel.all[gateIndex],
      ];
      return { ...current, recipe };
    });
  }

  function toggleAddon(enabled: boolean) {
    setForm((current) => {
      const recipe = cloneRecipe(current.recipe);
      recipe.channels.addon = enabled ? cloneRecipe(current.recipe).channels.buy : null;
      return { ...current, recipe };
    });
  }

  async function saveStrategy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) {
      setFormError("请输入策略名称");
      return;
    }
    const recipeError = validateRecipeForm(form.recipe);
    if (recipeError) {
      setFormError(recipeError);
      return;
    }

    setSaving(true);
    setFormError("");
    try {
      if (editingStrategy) {
        await updateSignalStrategy(editingStrategy.id, {
          expected_version: editingStrategy.recipe_version,
          name,
          description: form.description.trim() || null,
          recipe: form.recipe,
        });
        setMessage(`已更新信号策略「${name}」`);
      } else {
        await createSignalStrategy({
          name,
          description: form.description.trim() || null,
          recipe: form.recipe,
        });
        setMessage(`已创建信号策略「${name}」`);
      }
      await refreshStrategies();
      setDialogOpen(false);
      onChanged?.();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  async function cloneStrategy(strategy: SignalStrategy) {
    const suggested = `${strategy.name} 副本`;
    const name = window.prompt("请输入新策略名称", suggested)?.trim();
    if (!name) return;
    setBusyAction(`clone-${strategy.id}`);
    setMessage("");
    try {
      await cloneSignalStrategy(strategy.id, name);
      await refreshStrategies();
      setMessage(`已克隆为「${name}」`);
      onChanged?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "克隆失败，请稍后重试");
    } finally {
      setBusyAction(null);
    }
  }

  async function archiveStrategy(strategy: SignalStrategy) {
    if (!window.confirm(`确认归档信号策略「${strategy.name}」吗？`)) return;
    setBusyAction(`archive-${strategy.id}`);
    setMessage("");
    try {
      await archiveSignalStrategy(strategy.id);
      await refreshStrategies();
      setMessage(`已归档「${strategy.name}」`);
      onChanged?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "归档失败，请稍后重试");
    } finally {
      setBusyAction(null);
    }
  }

  async function setAsDefault(strategy: SignalStrategy) {
    setBusyAction(`default-${strategy.id}`);
    setMessage("");
    try {
      await setDefaultSignalStrategy(strategy.id);
      setDefaultStrategyId(strategy.id);
      setMessage(`已将「${strategy.name}」设为默认信号策略`);
      onChanged?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "设置默认策略失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function runDryRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const strategyId = Number(dryStrategyId);
    if (!Number.isInteger(strategyId) || strategyId <= 0) {
      setDryError("请选择要试算的信号策略");
      return;
    }
    if (!dryStockCode.trim()) {
      setDryError("请输入股票代码");
      return;
    }

    setDryRunning(true);
    setDryError("");
    setDryResult(null);
    try {
      const result = await dryRunSignalStrategy({
        stock_code: dryStockCode.trim(),
        strategy_id: strategyId,
        as_of: dryAsOf || undefined,
        policy: "live",
      });
      setDryResult(result);
    } catch (error) {
      setDryError(error instanceof Error ? error.message : "试算失败，请稍后重试");
    } finally {
      setDryRunning(false);
    }
  }

  return (
    <>
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-slate-900">信号策略编排</h2>
            <p className="mt-1 text-sm text-slate-500">
              组合价格与成交量条件，并将保存的策略用于实盘监控和回测
            </p>
          </div>
          <button
            type="button"
            onClick={openCreate}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700"
          >
            新建策略
          </button>
        </div>

        <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[760px] border-collapse text-sm">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left font-medium">策略</th>
                <th className="px-3 py-2 text-left font-medium">说明</th>
                <th className="px-3 py-2 text-left font-medium">版本</th>
                <th className="px-3 py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((strategy) => (
                <tr key={strategy.id} className="border-t border-slate-100">
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium text-slate-900">{strategy.name}</span>
                      {strategy.builtin_key ? (
                        <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-medium text-slate-600">
                          内置
                        </span>
                      ) : null}
                      {defaultStrategyId === strategy.id ? (
                        <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-medium text-sky-700">
                          默认
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="max-w-sm px-3 py-3 text-slate-600">
                    {strategy.description || "暂无说明"}
                  </td>
                  <td className="px-3 py-3 text-slate-600">v{strategy.recipe_version}</td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <button
                        type="button"
                        disabled={
                          defaultStrategyId === strategy.id ||
                          busyAction === `default-${strategy.id}`
                        }
                        onClick={() => void setAsDefault(strategy)}
                        className="rounded-md border border-sky-300 px-2 py-1 text-xs font-medium text-sky-700 hover:bg-sky-50 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {defaultStrategyId === strategy.id ? "当前默认" : "设为默认"}
                      </button>
                      <button
                        type="button"
                        disabled={busyAction === `clone-${strategy.id}`}
                        onClick={() => void cloneStrategy(strategy)}
                        className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                      >
                        克隆
                      </button>
                      {!strategy.builtin_key ? (
                        <>
                          <button
                            type="button"
                            onClick={() => openEdit(strategy)}
                            className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            disabled={busyAction === `archive-${strategy.id}`}
                            onClick={() => void archiveStrategy(strategy)}
                            className="rounded-md border border-rose-300 px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50"
                          >
                            归档
                          </button>
                        </>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {loading ? (
            <p className="border-t border-slate-100 px-3 py-4 text-sm text-slate-500">加载中...</p>
          ) : strategies.length === 0 ? (
            <p className="border-t border-slate-100 px-3 py-4 text-sm text-slate-500">
              暂无可用信号策略
            </p>
          ) : null}
        </div>

        {message ? (
          <p className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
            {message}
          </p>
        ) : null}

        <div className="mt-5 border-t border-slate-200 pt-5">
          <div className="mb-3">
            <h3 className="text-base font-semibold text-slate-900">策略试算</h3>
            <p className="mt-0.5 text-xs text-slate-500">
              使用历史快照检查各通道结果及每个条件的观测详情
            </p>
          </div>
          <form onSubmit={(event) => void runDryRun(event)} className="grid gap-2 md:grid-cols-[1.4fr_1fr_1fr_auto]">
            <label className="grid gap-1 text-xs text-slate-600">
              信号策略
              <select
                value={dryStrategyId}
                onChange={(event) => setDryStrategyId(event.target.value)}
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
              >
                {strategies.map((strategy) => (
                  <option key={strategy.id} value={strategy.id}>
                    {strategy.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-xs text-slate-600">
              股票代码
              <input
                value={dryStockCode}
                onChange={(event) => setDryStockCode(event.target.value)}
                placeholder="例如 600519"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
              />
            </label>
            <label className="grid gap-1 text-xs text-slate-600">
              截止日期（可选）
              <input
                type="date"
                value={dryAsOf}
                onChange={(event) => setDryAsOf(event.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none ring-sky-200 focus:ring"
              />
            </label>
            <button
              type="submit"
              disabled={dryRunning || strategies.length === 0}
              className="self-end rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {dryRunning ? "试算中..." : "运行试算"}
            </button>
          </form>

          {dryError ? <p className="mt-2 text-sm text-rose-600">{dryError}</p> : null}
          {dryResult ? (
            <div className="mt-4 grid gap-3">
              <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
                <span>
                  {dryResult.stock_code} · {dryResult.as_of}
                </span>
                {dryResult.strategy ? <span>· {dryResult.strategy.name}</span> : null}
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                {(["buy", "sell", "addon"] as const).map((channelName) => {
                  const trace = dryResult.traces[channelName];
                  const passed = dryResult.decision[channelName];
                  return (
                    <div
                      key={channelName}
                      className="rounded-xl border border-slate-200 bg-slate-50 p-3"
                    >
                      <div className="mb-2 flex items-center justify-between">
                        <span className="text-sm font-semibold text-slate-900">
                          {CHANNEL_LABELS[channelName]}
                        </span>
                        <span
                          className={
                            passed
                              ? "rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700"
                              : "rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600"
                          }
                        >
                          {passed ? "通过" : trace.present ? "未通过" : "未启用"}
                        </span>
                      </div>
                      <div className="grid gap-2">
                        {trace.gates.map((gate) => (
                          <div
                            key={`${gate.op}-${gate.index}`}
                            className="rounded-lg border border-slate-200 bg-white p-2.5"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-medium text-slate-800">
                                {gate.index + 1}. {OPERATOR_LABELS[gate.op]}
                              </span>
                              <span
                                className={
                                  gate.passed
                                    ? "text-xs font-medium text-emerald-700"
                                    : "text-xs font-medium text-rose-700"
                                }
                              >
                                {gate.passed ? "通过" : "未通过"}
                              </span>
                            </div>
                            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                              {Object.entries(gate.details).map(([key, value]) => (
                                <div key={key} className="flex min-w-0 justify-between gap-2">
                                  <dt className="truncate text-slate-500">
                                    {DETAIL_LABELS[key] ?? key}
                                  </dt>
                                  <dd className="text-right text-slate-700">
                                    {displayDetail(key, value)}
                                  </dd>
                                </div>
                              ))}
                            </dl>
                          </div>
                        ))}
                        {trace.gates.length === 0 ? (
                          <p className="text-xs text-slate-500">该通道未启用</p>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      {dialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="max-h-[92vh] w-full max-w-5xl overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-xl md:p-6">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">
                  {editingStrategy ? "编辑信号策略" : "新建信号策略"}
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  价格条件固定为每个通道首项；价格和成交量回看周期会自动保持一致
                </p>
              </div>
              <button
                type="button"
                onClick={closeDialog}
                className="rounded-md px-2 py-1 text-sm text-slate-500 hover:bg-slate-100"
              >
                关闭
              </button>
            </div>

            <form onSubmit={(event) => void saveStrategy(event)} className="grid gap-4">
              <div className="grid gap-3 md:grid-cols-2">
                <label className="grid gap-1 text-sm text-slate-700">
                  策略名称
                  <input
                    value={form.name}
                    maxLength={100}
                    required
                    onChange={(event) =>
                      setForm((current) => ({ ...current, name: event.target.value }))
                    }
                    className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 focus:ring"
                  />
                </label>
                <label className="grid gap-1 text-sm text-slate-700">
                  策略说明（可选）
                  <input
                    value={form.description}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, description: event.target.value }))
                    }
                    className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 focus:ring"
                  />
                </label>
              </div>

              <ChannelEditor
                channelName="buy"
                channel={form.recipe.channels.buy}
                volumeOperators={volumeOperators}
                onParamChange={(index, param, value) =>
                  updateGateParam("buy", index, param, value)
                }
                onAddGate={(op) => addGate("buy", op)}
                onRemoveGate={(index) => removeGate("buy", index)}
                onMoveGate={(index, direction) => moveGate("buy", index, direction)}
              />
              <ChannelEditor
                channelName="sell"
                channel={form.recipe.channels.sell}
                volumeOperators={volumeOperators}
                onParamChange={(index, param, value) =>
                  updateGateParam("sell", index, param, value)
                }
                onAddGate={(op) => addGate("sell", op)}
                onRemoveGate={(index) => removeGate("sell", index)}
                onMoveGate={(index, direction) => moveGate("sell", index, direction)}
              />

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
                <div>
                  <p className="text-sm font-medium text-slate-800">启用加仓通道</p>
                  <p className="mt-0.5 text-xs text-slate-500">
                    开启时先复制当前买入通道，再单独调整成交量条件
                  </p>
                </div>
                <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={form.recipe.channels.addon != null}
                    onChange={(event) => toggleAddon(event.target.checked)}
                  />
                  {form.recipe.channels.addon ? "已启用" : "未启用"}
                </label>
              </div>
              {form.recipe.channels.addon ? (
                <ChannelEditor
                  channelName="addon"
                  channel={form.recipe.channels.addon}
                  volumeOperators={volumeOperators}
                  onParamChange={(index, param, value) =>
                    updateGateParam("addon", index, param, value)
                  }
                  onAddGate={(op) => addGate("addon", op)}
                  onRemoveGate={(index) => removeGate("addon", index)}
                  onMoveGate={(index, direction) => moveGate("addon", index, direction)}
                />
              ) : null}

              {formError ? <p className="text-sm text-rose-600">{formError}</p> : null}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  disabled={saving}
                  onClick={closeDialog}
                  className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:bg-sky-300"
                >
                  {saving ? "保存中..." : "保存策略"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
