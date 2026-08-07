"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import JobLogDialog from "@/components/job-log-dialog";
import PositionLedgerDrawer from "@/components/position-ledger-drawer";
import RegisterBuyDialog from "@/components/register-buy-dialog";
import RegisterSellDialog from "@/components/register-sell-dialog";
import StatusBadge from "@/components/status-badge";
import {
  createWatchlist,
  fetchSystemStatus,
  fetchWatchlist,
  getJobStatusSSEUrl,
  removeWatchlist,
  searchStocks,
  StockSearchItem,
  WatchlistItem,
} from "@/lib/api";

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

function renderSignal(signal: "BUY" | "SELL" | null): { dot: string; label: string; cls: string } {
  if (signal === "BUY") {
    return { dot: "🟢", label: "买入", cls: "text-emerald-700" };
  }
  if (signal === "SELL") {
    return { dot: "🔴", label: "卖出", cls: "text-rose-700" };
  }
  return { dot: "⚪", label: "无信号", cls: "text-slate-500" };
}

export default function WatchlistPanel() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [watchlistLoading, setWatchlistLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<StockSearchItem[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [showAddPanel, setShowAddPanel] = useState(false);
  const [addingCode, setAddingCode] = useState<string | null>(null);
  const [removingCode, setRemovingCode] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [jobStatus, setJobStatus] = useState("未开始");
  const [sseError, setSseError] = useState(false);
  const [logDialogOpen, setLogDialogOpen] = useState(false);
  const [quoteDelay, setQuoteDelay] = useState(false);
  const [buyTarget, setBuyTarget] = useState<WatchlistItem | null>(null);
  const [sellTarget, setSellTarget] = useState<WatchlistItem | null>(null);
  const [ledgerTarget, setLedgerTarget] = useState<WatchlistItem | null>(null);

  useEffect(() => {
    void loadWatchlist();
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

  useEffect(() => {
    let cancelled = false;

    async function pollSystemStatus() {
      try {
        const status = await fetchSystemStatus();
        if (!cancelled) {
          setQuoteDelay(Boolean(status.quote_delay));
        }
      } catch {
        // Keep last known flag; health endpoint is best-effort.
      }
    }

    void pollSystemStatus();
    const timer = window.setInterval(() => {
      void pollSystemStatus();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // SSE subscription for job status
  useEffect(() => {
    const url = getJobStatusSSEUrl();
    const es = new EventSource(url);
    es.onmessage = (event) => {
      setJobStatus(event.data);
      setSseError(false);
    };
    es.onerror = () => {
      setSseError(true);
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

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:p-6">
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

      <div className="overflow-x-auto">
        {watchlist.length > 0 ? (
          <table className="w-full min-w-[1280px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500">
                <th className="py-2 text-left font-medium">代码</th>
                <th className="py-2 text-left font-medium">名称</th>
                <th className="py-2 text-left font-medium">最新价</th>
                <th className="py-2 text-left font-medium">涨跌幅</th>
                <th className="py-2 text-left font-medium">持仓状态</th>
                <th className="py-2 text-left font-medium">持仓数量</th>
                <th className="py-2 text-left font-medium">成本价</th>
                <th className="py-2 text-left font-medium">浮动盈亏</th>
                <th className="py-2 text-left font-medium">止损参考价</th>
                <th className="py-2 text-left font-medium">距止损</th>
                <th className="py-2 text-left font-medium">状态</th>
                <th className="py-2 text-left font-medium">信号</th>
                <th className="py-2 text-left font-medium">定时任务</th>
                <th className="py-2 text-left font-medium">备注</th>
                <th className="py-2 text-left font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {watchlist.map((item) => {
                const signal = renderSignal(item.signal_type);
                const holding = item.position_status !== "EMPTY" && item.position_qty > 0;
                const riskCls = holding ? "py-2 pr-2 font-semibold text-slate-900" : "py-2 pr-2 text-slate-500";
                return (
                  <tr key={item.stock_code} className="border-b border-slate-100">
                    <td className="py-2 pr-2 text-slate-700">{item.stock_code}</td>
                    <td className="py-2 pr-2 text-slate-900">{item.stock_name}</td>
                    <td className="py-2 pr-2 text-slate-700">{formatPrice(item.latest_price)}</td>
                    <td
                      className={
                        item.change_pct !== null && item.change_pct > 0
                          ? "py-2 pr-2 text-emerald-700"
                          : item.change_pct !== null && item.change_pct < 0
                            ? "py-2 pr-2 text-rose-700"
                            : "py-2 pr-2 text-slate-700"
                      }
                    >
                      {formatChangePct(item.change_pct)}
                    </td>
                    <td className={riskCls}>{positionStatusLabel(item.position_status)}</td>
                    <td className={riskCls}>{holding ? item.position_qty : "0"}</td>
                    <td className={riskCls}>
                      {holding && item.avg_cost != null ? item.avg_cost.toFixed(3) : "-"}
                    </td>
                    <td
                      className={
                        !holding
                          ? "py-2 pr-2 text-slate-500"
                          : item.unrealized_pnl != null && item.unrealized_pnl > 0
                            ? "py-2 pr-2 font-semibold text-emerald-700"
                            : item.unrealized_pnl != null && item.unrealized_pnl < 0
                              ? "py-2 pr-2 font-semibold text-rose-700"
                              : "py-2 pr-2 font-semibold text-slate-900"
                      }
                    >
                      {holding && item.unrealized_pnl != null
                        ? `${item.unrealized_pnl.toFixed(2)} (${formatPctRatio(item.unrealized_pnl_pct)})`
                        : "-"}
                    </td>
                    <td className={riskCls}>
                      {holding && item.stop_price != null ? item.stop_price.toFixed(3) : "-"}
                    </td>
                    <td className={riskCls}>
                      {holding ? formatPctRatio(item.stop_distance_pct) : "-"}
                    </td>
                    <td className="py-2 pr-2">
                      <StatusBadge status={item.status} />
                    </td>
                    <td className={`py-2 pr-2 ${signal.cls}`}>
                      <span className="inline-flex items-center gap-1">
                        <span>{signal.dot}</span>
                        <span className="text-xs">{signal.label}</span>
                      </span>
                    </td>
                    <td className="py-2 pr-2">
                      <span
                        className={
                          jobStatus === "执行中"
                            ? "inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700"
                            : jobStatus === "执行失败"
                              ? "inline-flex items-center rounded-full bg-rose-100 px-2 py-0.5 text-xs text-rose-700"
                              : "inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                        }
                      >
                        {jobStatus === "执行中" && <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />}
                        {jobStatus}
                      </span>
                      {sseError && <span className="ml-1 text-xs text-amber-600">⚠</span>}
                      <button
                        type="button"
                        onClick={() => setLogDialogOpen(true)}
                        className="ml-2 text-xs text-sky-600 underline hover:text-sky-800"
                      >
                        查看日志
                      </button>
                    </td>
                    <td className="py-2 pr-2 text-slate-600">
                      {item.insufficient_days && item.insufficient_days > 0 ? `数据不足 ${item.insufficient_days} 天` : "--"}
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-1.5">
                        <button
                          type="button"
                          onClick={() => setBuyTarget(item)}
                          className="rounded-md border border-sky-300 px-2.5 py-1.5 text-xs font-medium text-sky-700 transition-colors hover:bg-sky-50"
                        >
                          登记买入
                        </button>
                        {holding ? (
                          <button
                            type="button"
                            onClick={() => setSellTarget(item)}
                            className="rounded-md border border-amber-300 px-2.5 py-1.5 text-xs font-medium text-amber-800 transition-colors hover:bg-amber-50"
                          >
                            登记减持
                          </button>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setLedgerTarget(item)}
                          className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100"
                        >
                          流水
                        </button>
                        <button
                          type="button"
                          onClick={() => void onRemove(item)}
                          disabled={removingCode === item.stock_code}
                          className="rounded-md border border-rose-300 px-2.5 py-1.5 text-xs font-medium text-rose-700 transition-colors hover:bg-rose-50 disabled:cursor-not-allowed disabled:bg-rose-50 disabled:text-rose-300"
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

      <JobLogDialog open={logDialogOpen} onClose={() => setLogDialogOpen(false)} />
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
    </section>
  );
}