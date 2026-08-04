"use client";

import { useEffect, useState } from "react";

import { fetchJobLogLatest, JobLogDetail } from "@/lib/api";

interface JobLogDialogProps {
  open: boolean;
  onClose: () => void;
}

export default function JobLogDialog({ open, onClose }: JobLogDialogProps) {
  const [data, setData] = useState<JobLogDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (open) {
      void loadLog(1);
    }
  }, [open]);

  async function loadLog(p: number) {
    setLoading(true);
    try {
      const result = await fetchJobLogLatest(p);
      setData(result);
      setPage(p);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="max-h-[80vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">任务执行日志</h2>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-700">
            ✕
          </button>
        </div>

        {loading && <p className="text-sm text-slate-500">加载中...</p>}

        {data?.job_log && (
          <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
              <div>
                <span className="text-slate-500">交易日：</span>
                {data.job_log.trade_date}
              </div>
              <div>
                <span className="text-slate-500">状态：</span>
                <span className={data.job_log.status === "FAILED" ? "text-rose-600" : "text-emerald-600"}>
                  {data.job_log.status}
                </span>
              </div>
              <div>
                <span className="text-slate-500">开始：</span>
                {data.job_log.started_at}
              </div>
              <div>
                <span className="text-slate-500">结束：</span>
                {data.job_log.finished_at ?? "--"}
              </div>
              <div>
                <span className="text-slate-500">总数：</span>
                {data.job_log.total_count}
              </div>
              <div>
                <span className="text-slate-500">成功/失败：</span>
                {data.job_log.success_count}/{data.job_log.failed_count}
              </div>
            </div>
            {data.job_log.error_summary && (
              <p className="mt-2 text-xs text-rose-600">错误摘要：{data.job_log.error_summary}</p>
            )}
          </div>
        )}

        {data?.items && data.items.length > 0 && (
          <>
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-1.5 text-left font-medium">代码</th>
                  <th className="py-1.5 text-left font-medium">名称</th>
                  <th className="py-1.5 text-left font-medium">N</th>
                  <th className="py-1.5 text-left font-medium">实际N</th>
                  <th className="py-1.5 text-left font-medium">状态</th>
                  <th className="py-1.5 text-left font-medium">Low_min</th>
                  <th className="py-1.5 text-left font-medium">High_max</th>
                  <th className="py-1.5 text-left font-medium">错误</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id} className={`border-b border-slate-100 ${item.status === "FAILED" ? "bg-rose-50" : ""}`}>
                    <td className="py-1.5 text-slate-700">{item.stock_code}</td>
                    <td className="py-1.5 text-slate-700">{item.stock_name ?? "--"}</td>
                    <td className="py-1.5 text-slate-700">{item.strategy_n}</td>
                    <td className="py-1.5 text-slate-700">{item.actual_n ?? "--"}</td>
                    <td className={`py-1.5 ${item.status === "FAILED" ? "text-rose-600" : "text-emerald-600"}`}>
                      {item.status}
                    </td>
                    <td className="py-1.5 text-slate-700">{item.low_min?.toFixed(2) ?? "--"}</td>
                    <td className="py-1.5 text-slate-700">{item.high_max?.toFixed(2) ?? "--"}</td>
                    <td className="py-1.5 text-rose-600">{item.error_message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => void loadLog(page - 1)}
                className="rounded border px-2 py-1 text-xs disabled:opacity-40"
              >
                上一页
              </button>
              <button
                type="button"
                onClick={() => void loadLog(page + 1)}
                className="rounded border px-2 py-1 text-xs"
              >
                下一页
              </button>
            </div>
          </>
        )}

        {!loading && !data?.job_log && <p className="text-sm text-slate-500">暂无任务日志</p>}
      </div>
    </div>
  );
}
