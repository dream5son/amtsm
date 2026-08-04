"use client";

import { FormEvent, useEffect, useState } from "react";

import { fetchStrategy, StrategyConfig, updateStrategy } from "@/lib/api";

const DEFAULT_STRATEGY: StrategyConfig = {
  global_buy_n: 60,
  global_buy_x: 1.1,
  global_sell_n: 60,
  global_sell_y: 0.9,
};

export default function StrategySettingsPanel() {
  const [strategy, setStrategy] = useState<StrategyConfig>(DEFAULT_STRATEGY);
  const [loadingStrategy, setLoadingStrategy] = useState(true);
  const [strategyOpen, setStrategyOpen] = useState(false);
  const [savingStrategy, setSavingStrategy] = useState(false);
  const [strategyError, setStrategyError] = useState("");
  const [message, setMessage] = useState("");
  const [strategyForm, setStrategyForm] = useState({
    global_buy_n: String(DEFAULT_STRATEGY.global_buy_n),
    global_buy_x: String(DEFAULT_STRATEGY.global_buy_x),
    global_sell_n: String(DEFAULT_STRATEGY.global_sell_n),
    global_sell_y: String(DEFAULT_STRATEGY.global_sell_y),
  });

  useEffect(() => {
    async function loadStrategy() {
      setLoadingStrategy(true);
      try {
        const data = await fetchStrategy();
        setStrategy(data);
        setStrategyForm({
          global_buy_n: String(data.global_buy_n),
          global_buy_x: String(data.global_buy_x),
          global_sell_n: String(data.global_sell_n),
          global_sell_y: String(data.global_sell_y),
        });
      } catch {
        setMessage("策略参数加载失败，已使用默认值");
        setStrategy(DEFAULT_STRATEGY);
        setStrategyForm({
          global_buy_n: String(DEFAULT_STRATEGY.global_buy_n),
          global_buy_x: String(DEFAULT_STRATEGY.global_buy_x),
          global_sell_n: String(DEFAULT_STRATEGY.global_sell_n),
          global_sell_y: String(DEFAULT_STRATEGY.global_sell_y),
        });
      } finally {
        setLoadingStrategy(false);
      }
    }

    void loadStrategy();
  }, []);

  function openStrategyModal() {
    setStrategyForm({
      global_buy_n: String(strategy.global_buy_n),
      global_buy_x: String(strategy.global_buy_x),
      global_sell_n: String(strategy.global_sell_n),
      global_sell_y: String(strategy.global_sell_y),
    });
    setStrategyError("");
    setStrategyOpen(true);
  }

  function closeStrategyModal() {
    if (savingStrategy) {
      return;
    }
    setStrategyOpen(false);
    setStrategyError("");
  }

  async function onSaveStrategy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

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

    const payload: StrategyConfig = {
      global_buy_n: parsedBuyN,
      global_buy_x: Number(parsedBuyX.toFixed(2)),
      global_sell_n: parsedSellN,
      global_sell_y: Number(parsedSellY.toFixed(2)),
    };

    setSavingStrategy(true);
    setStrategyError("");

    try {
      const updated = await updateStrategy(payload);
      setStrategy(updated);
      setMessage("策略参数已更新");
      setStrategyOpen(false);
    } catch {
      setStrategyError("保存失败，请稍后重试");
    } finally {
      setSavingStrategy(false);
    }
  }

  return (
    <>
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="mb-2 inline-flex rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600">
              Trading Signal Workspace
            </p>
            <h2 className="text-2xl font-semibold tracking-tight text-slate-900 md:text-3xl">A股交易信号监控工作台</h2>
            <p className="mt-2 text-sm text-slate-600 md:text-base">策略参数维护</p>
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
              <p className="font-semibold text-slate-900">{loadingStrategy ? "--" : `${strategy.global_buy_n} 天`}</p>
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-3 text-sm leading-tight">
              <p className="text-slate-500">买点倍数</p>
              <p className="font-semibold text-slate-900">{loadingStrategy ? "--" : `${strategy.global_buy_x.toFixed(2)} 倍`}</p>
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-3 text-sm leading-tight">
              <p className="text-slate-500">卖点交易日数</p>
              <p className="font-semibold text-slate-900">{loadingStrategy ? "--" : `${strategy.global_sell_n} 天`}</p>
            </div>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-3 text-sm leading-tight">
              <p className="text-slate-500">卖点倍数</p>
              <p className="font-semibold text-slate-900">{loadingStrategy ? "--" : `${strategy.global_sell_y.toFixed(2)} 倍`}</p>
            </div>
          </div>
        </div>

        {message ? (
          <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">{message}</div>
        ) : null}
      </section>

      {strategyOpen ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/40 px-4 py-8">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">策略参数设置</h3>
                <p className="mt-1 text-xs text-slate-500">修改后下一轮计算生效</p>
              </div>
              <button
                type="button"
                onClick={closeStrategyModal}
                className="rounded-md px-2 py-1 text-sm text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
              >
                关闭
              </button>
            </div>

            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              <p>买点交易日数：买点计算回看周期，单位天。</p>
              <p>买点倍数：买点触发阈值，单位倍。</p>
              <p>卖点交易日数：卖点计算回看周期，单位天。</p>
              <p>卖点倍数：卖点触发阈值，单位倍。</p>
            </div>

            <form onSubmit={onSaveStrategy} className="grid gap-3">
              <label className="grid gap-1 text-sm text-slate-700">
                买点交易日数（天）
                <input
                  value={strategyForm.global_buy_n}
                  onChange={(event) => setStrategyForm((prev) => ({ ...prev, global_buy_n: event.target.value }))}
                  inputMode="numeric"
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                />
              </label>

              <label className="grid gap-1 text-sm text-slate-700">
                买点倍数（倍）
                <input
                  value={strategyForm.global_buy_x}
                  onChange={(event) => setStrategyForm((prev) => ({ ...prev, global_buy_x: event.target.value }))}
                  inputMode="decimal"
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                />
              </label>

              <label className="grid gap-1 text-sm text-slate-700">
                卖点交易日数（天）
                <input
                  value={strategyForm.global_sell_n}
                  onChange={(event) => setStrategyForm((prev) => ({ ...prev, global_sell_n: event.target.value }))}
                  inputMode="numeric"
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                />
              </label>

              <label className="grid gap-1 text-sm text-slate-700">
                卖点倍数（倍）
                <input
                  value={strategyForm.global_sell_y}
                  onChange={(event) => setStrategyForm((prev) => ({ ...prev, global_sell_y: event.target.value }))}
                  inputMode="decimal"
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none ring-sky-200 transition focus:ring"
                />
              </label>

              {strategyError ? <p className="m-0 text-xs text-rose-600">{strategyError}</p> : null}

              <div className="mt-1 flex items-center justify-end gap-2">
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