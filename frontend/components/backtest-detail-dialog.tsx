"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  applyBacktest,
  BacktestJob,
  BacktestKlineResponse,
  BACKTEST_KLINE_PAGE_SIZE,
  fetchBacktest,
  fetchBacktestKline,
  fetchBacktestsByCompareGroup,
  WatchlistItem,
} from "@/lib/api";

// klinecharts touches `window` at module load time; load it client-side only
// so the (closed-by-default) dialog doesn't break server-side prerendering.
const BacktestKlineChart = dynamic(() => import("@/components/backtest-kline-chart"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[460px] items-center justify-center text-sm text-slate-500">加载图表组件中...</div>
  ),
});

type BacktestDetailDialogProps = {
  open: boolean;
  item: WatchlistItem | null;
  onClose: () => void;
  onApplied?: () => void;
};

type SortKey = "win_rate" | "avg_win_loss_ratio" | "max_drawdown";

function formatPct(value: number | null, digits = 1): string {
  if (value === null) return "-";
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(digits)}%`;
}

function formatRatio(value: number | null): string {
  if (value === null) return "-";
  return value.toFixed(2);
}

function exitReasonLabel(reason: "STOP_LOSS" | "TAKE_PROFIT" | "PERIOD_END"): string {
  if (reason === "STOP_LOSS") return "止损";
  if (reason === "TAKE_PROFIT") return "止盈";
  return "未平仓（区间结束）";
}

function summarizeParams(paramsJson: string): string {
  try {
    const p = JSON.parse(paramsJson) as Record<string, unknown>;
    const parts: string[] = [];
    if (p.n != null) parts.push(`N${p.n}`);
    if (p.x != null) parts.push(`X${Number(p.x).toFixed(2)}`);
    if (p.y != null) parts.push(`Y${Number(p.y).toFixed(2)}`);
    if (p.stop_loss_pct != null) parts.push(`止损${(Number(p.stop_loss_pct) * 100).toFixed(1)}%`);
    if (p.break_even_trigger_pct != null) {
      parts.push(`保本触发${(Number(p.break_even_trigger_pct) * 100).toFixed(1)}%`);
    }
    if (p.break_even_buffer_pct != null) {
      parts.push(`保本缓冲${(Number(p.break_even_buffer_pct) * 100).toFixed(2)}%`);
    }
    if (Array.isArray(p.trailing_ladder) && p.trailing_ladder.length > 0) {
      parts.push(`阶梯${p.trailing_ladder.length}档`);
    }
    return parts.join(" · ");
  } catch {
    return paramsJson;
  }
}

function strategySnapshotLabel(job: BacktestJob): string {
  if (job.strategy_name_snapshot) {
    return `${job.strategy_name_snapshot}${job.strategy_version ? ` · v${job.strategy_version}` : ""}`;
  }
  if (job.signal_strategy_id) return `信号策略 #${job.signal_strategy_id}`;
  return "未记录策略快照";
}

export default function BacktestDetailDialog({
  open,
  item,
  onClose,
  onApplied,
}: BacktestDetailDialogProps) {
  const [primaryJob, setPrimaryJob] = useState<BacktestJob | null>(null);
  const [jobs, setJobs] = useState<BacktestJob[]>([]);
  const [jobError, setJobError] = useState("");
  const [loadingJob, setLoadingJob] = useState(false);

  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [klineData, setKlineData] = useState<BacktestKlineResponse | null>(null);
  const [klineError, setKlineError] = useState("");
  const [loadingKline, setLoadingKline] = useState(false);

  const [selectedTradeId, setSelectedTradeId] = useState<number | null>(null);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortAsc, setSortAsc] = useState(false);

  const [applyTarget, setApplyTarget] = useState<BacktestJob | null>(null);
  const [updateGlobal, setUpdateGlobal] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState("");
  const [applySuccess, setApplySuccess] = useState("");

  useEffect(() => {
    if (!open || !item || item.backtest_job_id == null) return;
    let cancelled = false;
    setPrimaryJob(null);
    setJobs([]);
    setJobError("");
    setSelectedJobId(null);
    setKlineData(null);
    setSelectedTradeId(null);
    setSortKey(null);
    setApplyTarget(null);
    setUpdateGlobal(false);
    setApplyError("");
    setApplySuccess("");
    setLoadingJob(true);

    void fetchBacktest(item.backtest_job_id)
      .then(async (job) => {
        if (cancelled) return;
        setPrimaryJob(job);
        setSelectedJobId(job.id);
        try {
          const group = await fetchBacktestsByCompareGroup(job.compare_group_id);
          if (!cancelled) setJobs(group);
        } catch {
          if (!cancelled) setJobs([job]);
        }
      })
      .catch(() => {
        if (!cancelled) setJobError("加载回测任务失败，请稍后重试");
      })
      .finally(() => {
        if (!cancelled) setLoadingJob(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, item]);

  const selectedJob = useMemo(
    () => jobs.find((j) => j.id === selectedJobId) ?? primaryJob,
    [jobs, selectedJobId, primaryJob],
  );

  useEffect(() => {
    if (!open || !selectedJob) return;
    if (selectedJob.status !== "SUCCESS") {
      setKlineData(null);
      setKlineError("");
      return;
    }
    let cancelled = false;
    setLoadingKline(true);
    setKlineError("");
    void fetchBacktestKline(selectedJob.id, { limit: BACKTEST_KLINE_PAGE_SIZE })
      .then((data) => {
        if (!cancelled) setKlineData(data);
      })
      .catch(() => {
        if (!cancelled) {
          setKlineError("K 线数据加载失败，暂无法展示行情与买卖点");
          setKlineData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingKline(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, selectedJob]);

  const onSelectTrade = useCallback((tradeId: number) => {
    setSelectedTradeId(tradeId);
  }, []);

  function openApplyConfirm(job: BacktestJob) {
    setApplyTarget(job);
    setUpdateGlobal(false);
    setApplyError("");
    setApplySuccess("");
  }

  function closeApplyConfirm() {
    if (applying) return;
    setApplyTarget(null);
    setApplyError("");
  }

  async function confirmApply() {
    if (!applyTarget) return;
    setApplying(true);
    setApplyError("");
    try {
      await applyBacktest(applyTarget.id, { update_global: updateGlobal });
      setApplySuccess("参数已更新，建议重新回测以刷新首列圆环");
      setApplyTarget(null);
      onApplied?.();
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : "应用失败，请稍后重试");
    } finally {
      setApplying(false);
    }
  }

  if (!open || !item) return null;

  const successJobs = jobs.filter((j) => j.status === "SUCCESS");
  const showCompare = successJobs.length > 1;

  const sortedCompareJobs = [...successJobs].sort((a, b) => {
    if (!sortKey) return 0;
    const av = a[sortKey] ?? -Infinity;
    const bv = b[sortKey] ?? -Infinity;
    return sortAsc ? av - bv : bv - av;
  });

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortAsc((prev) => !prev);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  }

  const selectedTrade = klineData?.trades.find((t) => t.id === selectedTradeId) ?? null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">回测详情</h2>
            <p className="text-sm text-slate-500">
              {item.stock_name}（{item.stock_code}）
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            关闭
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loadingJob ? <p className="text-sm text-slate-500">加载中...</p> : null}
          {jobError ? <p className="text-sm text-rose-600">{jobError}</p> : null}

          {!loadingJob && selectedJob && selectedJob.status === "FAILED" ? (
            <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
              回测失败{selectedJob.error_message ? `：${selectedJob.error_message}` : ""}
            </div>
          ) : null}

          {!loadingJob && selectedJob && (selectedJob.status === "PENDING" || selectedJob.status === "RUNNING") ? (
            <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
              回测正在进行中，请稍后再查看详情
            </div>
          ) : null}

          {!loadingJob && selectedJob && selectedJob.status === "SUCCESS" ? (
            <>
              {selectedJob.sample_insufficient ? (
                <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                  交易次数过少，样本不足，结果仅供参考
                </div>
              ) : null}

              <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="text-xs text-slate-500">历史胜率</p>
                  <p className="text-lg font-semibold text-slate-900">{formatPct(selectedJob.win_rate, 0)}</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="text-xs text-slate-500">平均盈亏比</p>
                  <p className="text-lg font-semibold text-slate-900">{formatRatio(selectedJob.avg_win_loss_ratio)}</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="text-xs text-slate-500">最大回撤</p>
                  <p className="text-lg font-semibold text-rose-700">{formatPct(selectedJob.max_drawdown)}</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="text-xs text-slate-500">交易次数</p>
                  <p className="text-lg font-semibold text-slate-900">{selectedJob.trade_count ?? "-"}</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="text-xs text-slate-500">累计收益</p>
                  <p className="text-lg font-semibold text-slate-900">{formatPct(selectedJob.total_return)}</p>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="text-xs text-slate-500">年化收益</p>
                  <p className="text-lg font-semibold text-slate-900">{formatPct(selectedJob.annual_return)}</p>
                </div>
              </div>

              <div className="mb-4 flex flex-wrap items-center gap-3">
                <div className="text-sm text-slate-600">
                  <p>
                    信号策略：
                    <span className="font-medium text-slate-800">
                      {strategySnapshotLabel(selectedJob)}
                    </span>
                  </p>
                  <p title={selectedJob.params_json}>
                    当前参数：{summarizeParams(selectedJob.params_json)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => openApplyConfirm(selectedJob)}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
                >
                  应用为单股策略
                </button>
                {applySuccess ? <p className="text-sm text-emerald-700">{applySuccess}</p> : null}
              </div>

              <div className="mb-2 flex items-center justify-between">
                <p className="text-sm font-medium text-slate-700">
                  K 线图（{selectedJob.start_date} ~ {selectedJob.end_date}）
                </p>
                {selectedTrade ? (
                  <p className="text-xs text-slate-600">
                    已选中：{selectedTrade.entry_date} 买入 {selectedTrade.entry_price.toFixed(2)} →{" "}
                    {selectedTrade.exit_date} {exitReasonLabel(selectedTrade.exit_reason)}{" "}
                    {selectedTrade.exit_price.toFixed(2)}， 盈亏 {formatPct(selectedTrade.pnl_pct)}
                    {selectedTrade.pnl_amount != null
                      ? `（${selectedTrade.pnl_amount.toFixed(2)} 元，按虚拟1手/100股折算）`
                      : ""}
                    ， 持仓 {selectedTrade.hold_days} 天
                  </p>
                ) : null}
              </div>

              <div className="mb-4 rounded-xl border border-slate-200 bg-white">
                {loadingKline ? (
                  <div className="flex h-[460px] items-center justify-center text-sm text-slate-500">加载中...</div>
                ) : klineError ? (
                  <div className="flex h-[460px] items-center justify-center text-sm text-rose-600">{klineError}</div>
                ) : klineData ? (
                  <BacktestKlineChart
                    jobId={selectedJob.id}
                    bars={klineData.bars}
                    hasMoreBefore={klineData.has_more_before}
                    hasMoreAfter={klineData.has_more_after}
                    trades={klineData.trades}
                    selectedTradeId={selectedTradeId}
                    onSelectTrade={onSelectTrade}
                  />
                ) : (
                  <div className="flex h-[460px] items-center justify-center text-sm text-slate-500">暂无 K 线数据</div>
                )}
              </div>

              <p className="mb-2 text-sm font-medium text-slate-700">虚拟交易流水</p>
              <div className="mb-4 overflow-x-auto">
                {klineData && klineData.trades.length > 0 ? (
                  <table className="w-full min-w-[640px] border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-slate-500">
                        <th className="py-2 pr-2 text-left font-medium">买入日/价</th>
                        <th className="py-2 pr-2 text-left font-medium">卖出日/价</th>
                        <th className="py-2 pr-2 text-left font-medium">持仓天数</th>
                        <th className="py-2 pr-2 text-left font-medium">盈亏</th>
                        <th className="py-2 pr-2 text-left font-medium">退出原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {klineData.trades.map((trade) => (
                        <tr
                          key={trade.id}
                          onClick={() => setSelectedTradeId(trade.id)}
                          className={
                            trade.id === selectedTradeId
                              ? "cursor-pointer border-b border-slate-100 bg-sky-50"
                              : "cursor-pointer border-b border-slate-100 hover:bg-slate-50"
                          }
                        >
                          <td className="py-2 pr-2 text-slate-700">
                            {trade.entry_date} / {trade.entry_price.toFixed(2)}
                          </td>
                          <td className="py-2 pr-2 text-slate-700">
                            {trade.exit_date} / {trade.exit_price.toFixed(2)}
                          </td>
                          <td className="py-2 pr-2 text-slate-700">{trade.hold_days}</td>
                          <td
                            className={
                              trade.exit_reason === "PERIOD_END"
                                ? "py-2 pr-2 font-medium text-amber-700"
                                : trade.pnl_pct >= 0
                                  ? "py-2 pr-2 font-medium text-rose-700"
                                  : "py-2 pr-2 font-medium text-emerald-700"
                            }
                          >
                            {formatPct(trade.pnl_pct)}
                            {trade.pnl_amount != null ? ` (${trade.pnl_amount.toFixed(2)} 元)` : ""}
                          </td>
                          <td className="py-2 pr-2 text-slate-700">{exitReasonLabel(trade.exit_reason)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="py-2 text-sm text-slate-500">暂无交易流水</p>
                )}
              </div>

              {showCompare ? (
                <>
                  <p className="mb-2 text-sm font-medium text-slate-700">多参数对比（本次提交共 {jobs.length} 组）</p>
                  <div className="mb-2 overflow-x-auto">
                    <table className="w-full min-w-[720px] border-collapse text-sm">
                      <thead>
                        <tr className="border-b border-slate-200 text-slate-500">
                          <th className="py-2 pr-2 text-left font-medium">信号策略 / 参数</th>
                          <th
                            className="cursor-pointer py-2 pr-2 text-left font-medium hover:text-slate-700"
                            onClick={() => toggleSort("win_rate")}
                          >
                            胜率{sortKey === "win_rate" ? (sortAsc ? " ▲" : " ▼") : ""}
                          </th>
                          <th
                            className="cursor-pointer py-2 pr-2 text-left font-medium hover:text-slate-700"
                            onClick={() => toggleSort("avg_win_loss_ratio")}
                          >
                            盈亏比{sortKey === "avg_win_loss_ratio" ? (sortAsc ? " ▲" : " ▼") : ""}
                          </th>
                          <th
                            className="cursor-pointer py-2 pr-2 text-left font-medium hover:text-slate-700"
                            onClick={() => toggleSort("max_drawdown")}
                          >
                            最大回撤{sortKey === "max_drawdown" ? (sortAsc ? " ▲" : " ▼") : ""}
                          </th>
                          <th className="py-2 pr-2 text-left font-medium">交易次数</th>
                          <th className="py-2 text-left font-medium">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedCompareJobs.map((job) => (
                          <tr
                            key={job.id}
                            className={
                              job.id === selectedJobId
                                ? "border-b border-slate-100 bg-sky-50"
                                : "border-b border-slate-100"
                            }
                          >
                            <td className="py-2 pr-2 text-slate-700" title={job.params_json}>
                              <span className="block font-medium text-slate-800">
                                {strategySnapshotLabel(job)}
                              </span>
                              <span className="block text-xs text-slate-500">
                                {summarizeParams(job.params_json)}
                              </span>
                            </td>
                            <td className="py-2 pr-2 text-slate-900">{formatPct(job.win_rate, 0)}</td>
                            <td className="py-2 pr-2 text-slate-900">{formatRatio(job.avg_win_loss_ratio)}</td>
                            <td className="py-2 pr-2 text-rose-700">{formatPct(job.max_drawdown)}</td>
                            <td className="py-2 pr-2 text-slate-700">{job.trade_count ?? "-"}</td>
                            <td className="py-2">
                              <div className="flex flex-wrap gap-1">
                                <button
                                  type="button"
                                  disabled={job.id === selectedJobId}
                                  onClick={() => {
                                    setSelectedJobId(job.id);
                                    setSelectedTradeId(null);
                                  }}
                                  className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  {job.id === selectedJobId ? "当前" : "查看"}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => openApplyConfirm(job)}
                                  className="rounded-md border border-slate-900 px-2 py-1 text-xs font-medium text-slate-900 hover:bg-slate-100"
                                >
                                  应用
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : null}
            </>
          ) : null}

          <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            历史回测仅供参考，不构成投资建议；回测为离线模拟，盈亏金额按虚拟 1 手（100 股）折算，不代表真实收益。
          </p>
        </div>
      </div>

      {applyTarget ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
          onClick={closeApplyConfirm}
        >
          <div
            className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-slate-900">应用为单股策略</h3>
            <p className="mt-1 text-sm text-slate-500">
              将以下参数写入 {item.stock_name}（{item.stock_code}）的单股覆盖配置
            </p>
            <p className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800">
              {summarizeParams(applyTarget.params_json)}
            </p>
            <label className="mt-3 flex items-start gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={updateGlobal}
                onChange={(e) => setUpdateGlobal(e.target.checked)}
                className="mt-0.5"
              />
              <span>同时更新全局策略（默认关闭；勾选后全局 N/X/Y 与止盈止损也会被覆盖）</span>
            </label>
            {applyError ? <p className="mt-2 text-sm text-rose-600">{applyError}</p> : null}
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                disabled={applying}
                onClick={closeApplyConfirm}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                disabled={applying}
                onClick={() => void confirmApply()}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                {applying ? "应用中..." : "确认应用"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
