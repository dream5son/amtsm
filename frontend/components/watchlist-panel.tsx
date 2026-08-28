"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import BacktestConfigDialog from "@/components/backtest-config-dialog";
import BacktestDetailDialog from "@/components/backtest-detail-dialog";
import BacktestRing from "@/components/backtest-ring";
import PositionLedgerDrawer from "@/components/position-ledger-drawer";
import RegisterBuyDialog from "@/components/register-buy-dialog";
import RegisterSellDialog from "@/components/register-sell-dialog";
import SignalHistoryDrawer from "@/components/signal-history-drawer";
import StatusBadge from "@/components/status-badge";
import WatchlistStrategyDialog from "@/components/watchlist-strategy-dialog";
import {
  createWatchlist,
  fetchSignalStrategies,
  fetchWatchlist,
  getSystemStatusSSEUrl,
  removeWatchlist,
  searchStocks,
  SignalStrategy,
  StockSearchItem,
  WatchlistItem,
} from "@/lib/api";
import { renderSignal, SIGNAL_LIMIT_BOARD_TIP, SIGNAL_T1_TIP } from "@/lib/signal";

function formatPrice(value: number | null): string {
  if (value === null) {
    return "--";
  }
  return value.toFixed(2);
}

function formatChangePct(value: number | null): string {
  if (value === null) {
    return "--";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPctRatio(value: number | null): string {
  if (value === null) {
    return "-";
  }
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

function positionStatusLabel(status: WatchlistItem["position_status"]): string {
  if (status === "HOLDING") return "持仓中";
  if (status === "PARTIAL") return "部分减持";
  return "空仓监控";
}

type WatchlistPanelProps = {
  onOpenStrategy?: (item: WatchlistItem) => void;
};

export default function WatchlistPanel({ onOpenStrategy }: WatchlistPanelProps) {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [signalStrategies, setSignalStrategies] = useState<SignalStrategy[]>([]);
  const [watchlistLoading, setWatchlistLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<StockSearchItem[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [showAddPanel, setShowAddPanel] = useState(false);
  const [addingCode, setAddingCode] = useState<string | null>(null);
  const [removingCode, setRemovingCode] = useState<string | null>(null);
  const [strategyTarget, setStrategyTarget] = useState<WatchlistItem | null>(null);
  const [message, setMessage] = useState("");
  const [quoteDelay, setQuoteDelay] = useState(false);
  const [buyTarget, setBuyTarget] = useState<WatchlistItem | null>(null);
  const [sellTarget, setSellTarget] = useState<WatchlistItem | null>(null);
  const [ledgerTarget, setLedgerTarget] = useState<WatchlistItem | null>(null);
  const [signalHistoryTarget, setSignalHistoryTarget] = useState<WatchlistItem | null>(null);
  const [backtestTarget, setBacktestTarget] = useState<WatchlistItem | null>(null);
  const [detailTarget, setDetailTarget] = useState<WatchlistItem | null>(null);

  useEffect(() => {
    void loadWatchlist();
    void fetchSignalStrategies()
      .then(setSignalStrategies)
      .catch(() => {
        setMessage("信号策略列表加载失败，暂时无法调整股票策略");
      });
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function silentRefreshWatchlist() {
      try {
        const data = await fetchWatchlist();
        if (!cancelled) {
          setWatchlist(data);
        }
      } catch {
        // Keep last known list; refresh is best-effort.
      }
    }

    const timer = window.setInterval(() => {
      void silentRefreshWatchlist();
    }, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const hasInFlightBacktest = useMemo(
    () =>
      watchlist.some(
        (item) => item.backtest_status === "PENDING" || item.backtest_status === "RUNNING",
      ),
    [watchlist],
  );

  useEffect(() => {
    if (!hasInFlightBacktest) return;
    let cancelled = false;

    const timer = window.setInterval(() => {
      void fetchWatchlist()
        .then((data) => {
          if (!cancelled) setWatchlist(data);
        })
        .catch(() => {
          // Best-effort polling; keep last known state on failure.
        });
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [hasInFlightBacktest]);

  const wasInFlightBacktestRef = useRef(false);
  useEffect(() => {
    if (wasInFlightBacktestRef.current && !hasInFlightBacktest) {
      setMessage("回测已完成，可点击首列圆环或「详情」查看结果");
    }
    wasInFlightBacktestRef.current = hasInFlightBacktest;
  }, [hasInFlightBacktest]);

  useEffect(() => {
    const url = getSystemStatusSSEUrl();
    const es = new EventSource(url);
    es.onmessage = (event) => {
      try {
        const status = JSON.parse(event.data) as { quote_delay?: boolean };
        setQuoteDelay(Boolean(status.quote_delay));
      } catch {
        // Keep last known flag; status stream is best-effort.
      }
    };
    return () => es.close();
  }, []);

  async function loadWatchlist() {
    setWatchlistLoading(true);
    try {
      const data = await fetchWatchlist();
      setWatchlist(data);
    } catch {
      setMessage("自选列表加载失败，请稍后重试");
    } finally {
      setWatchlistLoading(false);
    }
  }

  async function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const q = query.trim();
    if (!q) {
      setSearchResults([]);
      setMessage("请输入代码、拼音首字母或名称");
      return;
    }

    setSearchLoading(true);
    setMessage("");

    try {
      const data = await searchStocks(q, 20);
      setSearchResults(data);
      if (data.length === 0) {
        setMessage("未找到相关股票");
      }
    } catch {
      setMessage("搜索失败，请稍后重试");
      setSearchResults([]);
    } finally {
      setSearchLoading(false);
    }
  }

  async function onAdd(item: StockSearchItem) {
    setAddingCode(item.stock_code);
    setMessage("");
    try {
      const result = await createWatchlist(item.stock_code, item.stock_name);
      if (result.message === "已存在") {
        setMessage(`${item.stock_name} 已在自选列表中`);
      } else {
        setMessage(`已添加 ${item.stock_name} 到自选列表`);
      }
      await loadWatchlist();
    } catch {
      setMessage("添加失败，请稍后重试");
    } finally {
      setAddingCode(null);
    }
  }

  async function onRemove(item: WatchlistItem) {
    const confirmed = window.confirm(`确认移除 ${item.stock_name} (${item.stock_code}) 吗？`);
    if (!confirmed) {
      return;
    }

    setRemovingCode(item.stock_code);
    setMessage("");
    try {
      await removeWatchlist(item.stock_code);
      setMessage(`已移除 ${item.stock_name}`);
      await loadWatchlist();
    } catch {
      setMessage("移除失败，请稍后重试");
    } finally {
      setRemovingCode(null);
    }
  }

  const watchlistCodes = useMemo(
    () => new Set(watchlist.map((item) => item.stock_code)),
    [watchlist],
  );

  const showPositionRiskColumns = useMemo(
    () => watchlist.some((item) => item.position_qty > 0),
    [watchlist],
  );

  const liveStrategyTarget = useMemo(() => {
    if (!strategyTarget) return null;
    return (
      watchlist.find((row) => row.stock_code === strategyTarget.stock_code) ?? strategyTarget
    );
  }, [strategyTarget, watchlist]);

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:p-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 md:text-3xl">关注列表</h1>
          <p className="text-xs text-slate-500">{watchlistLoading ? "加载中..." : `${watchlist.length} 只`}</p>
        </div>
        <button
          type="button"
          onClick={() => setShowAddPanel((prev) => !prev)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
        >
          {showAddPanel ? "收起添加" : "添加股票"}
        </button>
      </div>

      {quoteDelay ? (
        <div
          role="status"
          className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
        >
          行情延迟：实时行情接口连续失败，系统已标记延迟状态，信号判定可能滞后。
        </div>
      ) : null}

      {showAddPanel ? (
        <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <form onSubmit={onSearch} className="flex flex-wrap gap-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入 600519 / GZMT / 贵州茅台"
              className="min-w-[240px] flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none ring-sky-200 transition focus:ring"
            />
            <button
              type="submit"
              disabled={searchLoading}
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-sky-300"
            >
              {searchLoading ? "搜索中..." : "搜索"}
            </button>
          </form>

          <div className="mt-3 overflow-x-auto">
            {searchResults.length > 0 ? (
              <table className="w-full min-w-[560px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500">
                    <th className="py-2 text-left font-medium">代码</th>
                    <th className="py-2 text-left font-medium">名称</th>
                    <th className="py-2 text-left font-medium">交易所</th>
                    <th className="py-2 text-left font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {searchResults.map((item) => {
                    const isInWatchlist = watchlistCodes.has(item.stock_code);
                    return (
                      <tr key={item.stock_code} className="border-b border-slate-100">
                        <td className="py-2 pr-2 text-slate-700">{item.stock_code}</td>
                        <td className="py-2 pr-2 text-slate-900">{item.stock_name}</td>
                        <td className="py-2 pr-2 text-slate-700">{item.exchange}</td>
                        <td className="py-2">
                          <button
                            type="button"
                            onClick={() => void onAdd(item)}
                            disabled={isInWatchlist || addingCode === item.stock_code}
                            className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                          >
                            {isInWatchlist ? "已加入" : addingCode === item.stock_code ? "添加中..." : "加入自选"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <p className="py-2 text-sm text-slate-500">
                {searchLoading ? "正在搜索，请稍候" : "输入关键词后即可搜索股票并加入自选"}
              </p>
            )}
          </div>
        </div>
      ) : null}

      <div className="min-w-0 overflow-x-auto">
        {watchlist.length > 0 ? (
          <table
            className={
              showPositionRiskColumns
                ? "w-full min-w-[1540px] border-collapse text-sm"
                : "w-full min-w-[1220px] border-collapse text-sm"
            }
          >
            <thead>
              <tr className="border-b border-slate-200 text-slate-500">
                <th
                  className="whitespace-nowrap py-2 pr-2 text-left font-medium"
                  title="回测历史胜率：盈利交易笔数 / 总交易笔数，点击圆环查看详情"
                >
                  胜率
                </th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">代码</th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">名称</th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">最新价</th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">涨跌幅</th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">持仓状态</th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">持仓数量</th>
                {showPositionRiskColumns ? (
                  <>
                    <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">成本价</th>
                    <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">浮动盈亏</th>
                    <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">止损参考价</th>
                    <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">距止损</th>
                  </>
                ) : null}
                <th className="min-w-[140px] whitespace-nowrap py-2 pr-2 text-left font-medium">信号策略</th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">状态</th>
                <th className="whitespace-nowrap py-2 pr-2 text-left font-medium">信号</th>
                <th className="w-[400px] min-w-[400px] whitespace-nowrap py-2 text-left font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {watchlist.map((item) => {
                const signal = renderSignal(item.signal_type);
                const holding = item.position_status !== "EMPTY" && item.position_qty > 0;
                const riskCls = holding ? "py-2 pr-2 font-semibold text-slate-900" : "py-2 pr-2 text-slate-500";
                return (
                  <tr key={item.stock_code} className="border-b border-slate-100">
                    <td className="py-2 pr-2">
                      <BacktestRing item={item} onClick={setDetailTarget} />
                    </td>
                    <td className="py-2 pr-2 text-slate-700">{item.stock_code}</td>
                    <td className="py-2 pr-2 text-slate-900">{item.stock_name}</td>
                    <td className="py-2 pr-2 text-slate-700">{formatPrice(item.latest_price)}</td>
                    <td
                      className={
                        item.change_pct !== null && item.change_pct > 0
                          ? "py-2 pr-2 text-rose-700"
                          : item.change_pct !== null && item.change_pct < 0
                            ? "py-2 pr-2 text-emerald-700"
                            : "py-2 pr-2 text-slate-700"
                      }
                    >
                      {formatChangePct(item.change_pct)}
                    </td>
                    <td className={riskCls}>{positionStatusLabel(item.position_status)}</td>
                    <td className={riskCls}>{holding ? item.position_qty : "0"}</td>
                    {showPositionRiskColumns ? (
                      <>
                        <td className={riskCls}>
                          {holding && item.avg_cost != null ? item.avg_cost.toFixed(3) : ""}
                        </td>
                        <td
                          className={
                            !holding
                              ? "py-2 pr-2 text-slate-500"
                              : item.unrealized_pnl != null && item.unrealized_pnl > 0
                                ? "py-2 pr-2 font-semibold text-rose-700"
                                : item.unrealized_pnl != null && item.unrealized_pnl < 0
                                  ? "py-2 pr-2 font-semibold text-emerald-700"
                                  : "py-2 pr-2 font-semibold text-slate-900"
                          }
                        >
                          {holding && item.unrealized_pnl != null
                            ? `${item.unrealized_pnl.toFixed(2)} (${formatPctRatio(item.unrealized_pnl_pct)})`
                            : ""}
                        </td>
                        <td className={riskCls}>
                          {holding && item.stop_price != null ? item.stop_price.toFixed(3) : ""}
                        </td>
                        <td className={riskCls}>
                          {holding ? formatPctRatio(item.stop_distance_pct) : ""}
                        </td>
                      </>
                    ) : null}
                    <td className="min-w-[140px] py-2 pr-2">
                      <div className="flex items-center gap-1.5">
                        <span
                          className="max-w-[120px] truncate text-xs font-medium text-slate-700"
                          title={item.effective_signal_strategy_name}
                        >
                          {item.effective_signal_strategy_name}
                        </span>
                        {item.signal_strategy_id == null ? (
                          <span className="shrink-0 rounded border border-sky-200 bg-sky-50 px-1 py-0.5 text-[10px] text-sky-700">
                            继承
                          </span>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setStrategyTarget(item)}
                          aria-label={`编辑 ${item.stock_name} 的信号策略`}
                          title="编辑信号策略"
                          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
                        >
                          <svg
                            xmlns="http://www.w3.org/2000/svg"
                            viewBox="0 0 20 20"
                            fill="currentColor"
                            className="h-3.5 w-3.5"
                            aria-hidden="true"
                          >
                            <path d="M13.586 3.586a2 2 0 1 1 2.828 2.828l-8.25 8.25a2 2 0 0 1-.828.485l-2.716.679a.5.5 0 0 1-.606-.606l.679-2.716a2 2 0 0 1 .485-.828l8.25-8.25Z" />
                          </svg>
                        </button>
                      </div>
                    </td>
                    <td className="py-2 pr-2">
                      <StatusBadge status={item.status} />
                    </td>
                    <td className={`py-2 pr-2 ${signal.cls}`}>
                      <span className="inline-flex items-center gap-1">
                        <span>{signal.dot}</span>
                        <span className="text-xs">{signal.label}</span>
                        {item.signal_t1_note ? (
                          <span
                            className="cursor-help text-[10px] font-semibold text-amber-700"
                            title={SIGNAL_T1_TIP}
                            aria-label={SIGNAL_T1_TIP}
                          >
                            T+1
                          </span>
                        ) : null}
                        {item.signal_limit_board ? (
                          <span
                            className="cursor-help text-[10px] font-semibold text-rose-700"
                            title={SIGNAL_LIMIT_BOARD_TIP}
                            aria-label={SIGNAL_LIMIT_BOARD_TIP}
                          >
                            停
                          </span>
                        ) : null}
                      </span>
                    </td>
                    <td className="w-[400px] min-w-[400px] py-2">
                      <div className="flex flex-wrap gap-1">
                        <button
                          type="button"
                          onClick={() => setBuyTarget(item)}
                          className="rounded-md border border-sky-300 px-2 py-1 text-xs font-medium text-sky-700 transition-colors hover:bg-sky-50"
                        >
                          买入
                        </button>
                        {holding ? (
                          <button
                            type="button"
                            onClick={() => setSellTarget(item)}
                            className="rounded-md border border-amber-300 px-2 py-1 text-xs font-medium text-amber-800 transition-colors hover:bg-amber-50"
                          >
                            减持
                          </button>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setLedgerTarget(item)}
                          className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100"
                        >
                          流水
                        </button>
                        <button
                          type="button"
                          onClick={() => setSignalHistoryTarget(item)}
                          className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100"
                        >
                          历史信号
                        </button>
                        <button
                          type="button"
                          onClick={() => onOpenStrategy?.(item)}
                          className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100"
                        >
                          风控
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (
                              item.backtest_status === "PENDING" ||
                              item.backtest_status === "RUNNING"
                            ) {
                              setMessage(`${item.stock_name} 回测正在进行中，请稍候`);
                              return;
                            }
                            setBacktestTarget(item);
                          }}
                          className="rounded-md border border-indigo-300 px-2 py-1 text-xs font-medium text-indigo-700 transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
                          disabled={
                            item.backtest_status === "PENDING" || item.backtest_status === "RUNNING"
                          }
                        >
                          {item.backtest_status === "PENDING" || item.backtest_status === "RUNNING"
                            ? "回测中..."
                            : "回测"}
                        </button>
                        <button
                          type="button"
                          onClick={() => void onRemove(item)}
                          disabled={removingCode === item.stock_code}
                          className="rounded-md border border-rose-300 px-2 py-1 text-xs font-medium text-rose-700 transition-colors hover:bg-rose-50 disabled:cursor-not-allowed disabled:bg-rose-50 disabled:text-rose-300"
                        >
                          {removingCode === item.stock_code ? "移除中..." : "移除"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="py-2 text-sm text-slate-500">当前还没有添加股票</p>
        )}
      </div>

      {message ? (
        <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-sm">{message}</div>
      ) : null}

      <RegisterBuyDialog
        open={buyTarget != null}
        item={buyTarget}
        onClose={() => setBuyTarget(null)}
        onSuccess={() => {
          setMessage(buyTarget ? `已登记买入 ${buyTarget.stock_name}` : "已登记买入");
          void loadWatchlist();
        }}
      />
      <RegisterSellDialog
        open={sellTarget != null}
        item={sellTarget}
        onClose={() => setSellTarget(null)}
        onSuccess={(realizedPnl) => {
          const name = sellTarget?.stock_name ?? "";
          setMessage(
            `已登记减持 ${name}，本笔已实现盈亏 ${realizedPnl.toFixed(2)}`,
          );
          void loadWatchlist();
        }}
      />
      <PositionLedgerDrawer
        open={ledgerTarget != null}
        item={ledgerTarget}
        onClose={() => setLedgerTarget(null)}
      />
      <SignalHistoryDrawer
        open={signalHistoryTarget != null}
        item={signalHistoryTarget}
        onClose={() => setSignalHistoryTarget(null)}
      />
      {liveStrategyTarget ? (
        <WatchlistStrategyDialog
          key={liveStrategyTarget.stock_code}
          open
          item={liveStrategyTarget}
          strategies={signalStrategies}
          onClose={() => setStrategyTarget(null)}
          onAssigned={(nextMessage) => {
            setMessage(nextMessage);
            void loadWatchlist();
          }}
          onBacktestSubmitted={(jobCount) => {
            setMessage(
              `已提交 ${liveStrategyTarget.stock_name} 回测（${jobCount} 组参数），进行中...`,
            );
            void loadWatchlist();
          }}
        />
      ) : null}
      <BacktestConfigDialog
        open={backtestTarget != null}
        item={backtestTarget}
        onClose={() => setBacktestTarget(null)}
        onSubmitted={(jobCount) => {
          setMessage(
            backtestTarget
              ? `已提交 ${backtestTarget.stock_name} 回测（${jobCount} 组参数），进行中...`
              : "回测已提交",
          );
          void loadWatchlist();
        }}
      />
      <BacktestDetailDialog
        open={detailTarget != null}
        item={detailTarget}
        onClose={() => setDetailTarget(null)}
        onApplied={() => {
          setMessage("参数已更新，建议重新回测以刷新首列圆环");
          void loadWatchlist();
        }}
      />
    </section>
  );
}