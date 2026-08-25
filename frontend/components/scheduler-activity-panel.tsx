"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ActivityLevel,
  ActivityLogItem,
  emptyActivityJobs,
  getSchedulerActivitySSEUrl,
  SCHEDULER_JOBS,
  SchedulerJobId,
  SchedulerJobView,
  fetchSchedulerActivity,
} from "@/lib/api";
import { formatDateTimeCompact, formatTime } from "@/lib/datetime";

const DISPLAY_LIMIT = 20;
const RECURRING_JOBS = SCHEDULER_JOBS.filter((job) => job.group === "recurring");
const ONCE_JOBS = SCHEDULER_JOBS.filter((job) => job.group === "once");

type JobDef = (typeof SCHEDULER_JOBS)[number];
type UnreadState = Partial<Record<SchedulerJobId, { hasNew: boolean; hasError: boolean }>>;
type SchedulerViews = Partial<Record<SchedulerJobId, SchedulerJobView>>;

const LEVEL_LABEL: Record<ActivityLevel, string> = {
  info: "信息",
  error: "错误",
  notify: "通知",
};

const LEVEL_BADGE_CLASS: Record<ActivityLevel, string> = {
  info: "border-slate-200 bg-slate-50 text-slate-600",
  error: "border-rose-200 bg-rose-50 text-rose-700",
  notify: "border-amber-200 bg-amber-50 text-amber-800",
};

const LEVEL_TEXT_CLASS: Record<ActivityLevel, string> = {
  info: "text-slate-700",
  error: "text-rose-700",
  notify: "text-amber-800",
};

function isSchedulerJobId(value: string): value is SchedulerJobId {
  return SCHEDULER_JOBS.some((job) => job.id === value);
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function formatInterval(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) {
    return `每 ${seconds / 3600} 小时`;
  }
  if (seconds >= 60 && seconds % 60 === 0) {
    return `每 ${seconds / 60} 分钟`;
  }
  return `每 ${seconds} 秒`;
}

function mergeSchedulerViews(incoming?: SchedulerJobView[]): SchedulerViews {
  const next: SchedulerViews = {};
  for (const view of incoming ?? []) {
    if (isSchedulerJobId(view.id)) {
      next[view.id] = view;
    }
  }
  return next;
}

function resolveView(job: JobDef, views: SchedulerViews): SchedulerJobView {
  const incoming = views[job.id];
  return {
    id: job.id,
    kind: incoming?.kind ?? job.kind,
    interval_seconds:
      incoming?.interval_seconds ?? ("intervalSeconds" in job ? job.intervalSeconds : null),
    hour: incoming?.hour ?? ("hour" in job ? job.hour : null),
    minute: incoming?.minute ?? ("minute" in job ? job.minute : null),
    next_run_time: incoming?.next_run_time ?? null,
    pending: incoming?.pending ?? [],
    note: incoming?.note ?? ("note" in job ? job.note : null),
  };
}

function formatTabSchedule(view: SchedulerJobView): string {
  if (view.kind === "interval" && view.interval_seconds) {
    return formatInterval(view.interval_seconds);
  }
  if (view.kind === "cron" && view.hour != null && view.minute != null) {
    return `${pad2(view.hour)}:${pad2(view.minute)}`;
  }
  if (view.kind === "date") {
    return view.next_run_time ? "待执行" : "一次性";
  }
  const pending = view.pending?.length ?? 0;
  return pending > 0 ? `${pending} 待执行` : "按需";
}

function formatJobMeta(view: SchedulerJobView): string {
  const parts: string[] = [];
  if (view.kind === "interval" && view.interval_seconds) {
    parts.push(formatInterval(view.interval_seconds));
  } else if (view.kind === "cron" && view.hour != null && view.minute != null) {
    parts.push(`每天 ${pad2(view.hour)}:${pad2(view.minute)}`);
  } else if (view.kind === "date") {
    parts.push("启动时执行一次");
    parts.push(view.next_run_time ? `下次 ${formatDateTimeCompact(view.next_run_time)}` : "已完成");
  } else if (view.kind === "on_demand") {
    parts.push("新增自选时触发");
    const pending = view.pending ?? [];
    if (pending.length > 0) {
      parts.push(`待执行 ${pending.join(" ")}`);
    }
  }
  if (view.note) {
    parts.push(view.note);
  }
  if (view.kind === "cron" && view.next_run_time) {
    parts.push(`下次 ${formatDateTimeCompact(view.next_run_time)}`);
  }
  return parts.join(" · ");
}

function keepLatest(items: ActivityLogItem[]): ActivityLogItem[] {
  if (items.length <= DISPLAY_LIMIT) {
    return items;
  }
  return items.slice(-DISPLAY_LIMIT);
}

function mergeSnapshot(
  incoming: Partial<Record<SchedulerJobId, ActivityLogItem[]>>,
): Record<SchedulerJobId, ActivityLogItem[]> {
  const next = emptyActivityJobs();
  for (const job of SCHEDULER_JOBS) {
    next[job.id] = keepLatest(incoming[job.id] ?? []);
  }
  return next;
}

function appendItems(
  prev: Record<SchedulerJobId, ActivityLogItem[]>,
  items: ActivityLogItem[],
): Record<SchedulerJobId, ActivityLogItem[]> {
  if (items.length === 0) {
    return prev;
  }
  const next = { ...prev };
  for (const item of items) {
    if (!isSchedulerJobId(item.job)) {
      continue;
    }
    const existing = next[item.job];
    if (existing.some((row) => row.id === item.id)) {
      continue;
    }
    next[item.job] = keepLatest([...existing, item]);
  }
  return next;
}

export default function SchedulerActivityPanel() {
  const [expanded, setExpanded] = useState(false);
  const [activeJob, setActiveJob] = useState<SchedulerJobId>("market_polling");
  const [logs, setLogs] = useState<Record<SchedulerJobId, ActivityLogItem[]>>(emptyActivityJobs);
  const [schedulerViews, setSchedulerViews] = useState<SchedulerViews>({});
  const [unread, setUnread] = useState<UnreadState>({});
  const [sseError, setSseError] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const activeJobRef = useRef(activeJob);

  useEffect(() => {
    activeJobRef.current = activeJob;
  }, [activeJob]);

  useEffect(() => {
    let cancelled = false;

    void fetchSchedulerActivity()
      .then((data) => {
        if (cancelled || !("jobs" in data)) {
          return;
        }
        setLogs(mergeSnapshot(data.jobs));
        if (data.scheduler) {
          setSchedulerViews(mergeSchedulerViews(data.scheduler));
        }
      })
      .catch(() => {
        // SSE snapshot is the fallback.
      });

    const es = new EventSource(getSchedulerActivitySSEUrl());
    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as {
          type?: string;
          jobs?: Partial<Record<SchedulerJobId, ActivityLogItem[]>>;
          items?: ActivityLogItem[];
          scheduler?: SchedulerJobView[];
        };
        setSseError(false);
        if (payload.scheduler) {
          setSchedulerViews(mergeSchedulerViews(payload.scheduler));
        }
        if (payload.type === "snapshot" && payload.jobs) {
          setLogs(mergeSnapshot(payload.jobs));
          return;
        }
        if (payload.type === "delta" && payload.items) {
          const items = payload.items;
          setLogs((prev) => appendItems(prev, items));
          setUnread((prev) => {
            const next = { ...prev };
            for (const item of items) {
              if (!isSchedulerJobId(item.job) || item.job === activeJobRef.current) {
                continue;
              }
              const current = next[item.job] ?? { hasNew: false, hasError: false };
              next[item.job] = {
                hasNew: true,
                hasError: current.hasError || item.level === "error",
              };
            }
            return next;
          });
        }
      } catch {
        // Keep last known logs; stream is best-effort.
      }
    };
    es.onerror = () => {
      setSseError(true);
    };
    return () => {
      cancelled = true;
      es.close();
    };
  }, []);

  const activeItems = logs[activeJob];
  const latest = activeItems.length > 0 ? activeItems[activeItems.length - 1] : null;
  const displayItems = useMemo(() => [...activeItems].reverse(), [activeItems]);

  useEffect(() => {
    if (!expanded) {
      return;
    }
    const node = listRef.current;
    if (node) {
      node.scrollTop = 0;
    }
  }, [expanded, activeJob]);

  function onSelectJob(job: SchedulerJobId) {
    setActiveJob(job);
    setUnread((prev) => {
      if (!prev[job]) {
        return prev;
      }
      const next = { ...prev };
      delete next[job];
      return next;
    });
  }

  const tickerClass = useMemo(() => {
    if (!latest) {
      return "text-slate-400";
    }
    return LEVEL_TEXT_CLASS[latest.level];
  }, [latest]);

  const activeDef = SCHEDULER_JOBS.find((job) => job.id === activeJob) ?? SCHEDULER_JOBS[0];
  const activeView = resolveView(activeDef, schedulerViews);
  const jobMeta = formatJobMeta(activeView);

  function renderJobButton(job: JobDef) {
    const selected = job.id === activeJob;
    const mark = unread[job.id];
    const view = resolveView(job, schedulerViews);
    const schedule = formatTabSchedule(view);
    return (
      <button
        key={job.id}
        type="button"
        aria-pressed={selected}
        onClick={() => onSelectJob(job.id)}
        className={`relative rounded-md px-2.5 py-1 text-left transition-colors ${
          selected
            ? "bg-slate-900 text-white"
            : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
        }`}
      >
        <span className="block text-xs font-medium leading-4">{job.label}</span>
        <span
          className={`block text-[10px] font-normal leading-4 ${
            selected ? "text-white/70" : "text-slate-400"
          }`}
        >
          {schedule}
        </span>
        {mark?.hasNew ? (
          <span
            className={`absolute right-1 top-1 h-1.5 w-1.5 rounded-full ${
              mark.hasError ? "bg-rose-500" : "bg-sky-400"
            } ${selected ? "hidden" : ""}`}
            aria-label={mark.hasError ? "有新错误" : "有新日志"}
          />
        ) : null}
      </button>
    );
  }

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2 md:px-4">
        <nav className="flex min-w-0 flex-wrap items-center gap-2" aria-label="调度任务类型">
          <div className="flex min-w-0 flex-wrap gap-1">{RECURRING_JOBS.map(renderJobButton)}</div>
          <span className="hidden h-8 w-px bg-slate-200 sm:block" aria-hidden />
          <div className="flex min-w-0 flex-wrap gap-1">{ONCE_JOBS.map(renderJobButton)}</div>
        </nav>
        <h2 className="shrink-0 text-base font-bold tracking-tight text-slate-900">
          调度任务执行日志
        </h2>
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="ml-auto rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100"
        >
          {expanded ? "折叠" : "展开"}
        </button>
      </div>

      {jobMeta ? (
        <p className="border-b border-slate-100 px-4 py-1.5 text-[11px] text-slate-500">
          {jobMeta}
        </p>
      ) : null}

      {sseError ? (
        <p className="border-b border-amber-100 bg-amber-50 px-4 py-1.5 text-xs text-amber-800">
          日志流连接异常，将自动重试
        </p>
      ) : null}

      {expanded ? (
        <div ref={listRef} className="max-h-64 overflow-y-auto px-4 py-2">
          {displayItems.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-400">暂无执行日志</p>
          ) : (
            <ul className="space-y-1.5">
              {displayItems.map((item) => (
                <li key={item.id} className="flex items-start gap-2 text-xs leading-5">
                  <time className="shrink-0 font-mono text-slate-400" dateTime={item.ts}>
                    {formatTime(item.ts)}
                  </time>
                  <span
                    className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${LEVEL_BADGE_CLASS[item.level]}`}
                  >
                    {LEVEL_LABEL[item.level]}
                  </span>
                  <span className={`min-w-0 break-all ${LEVEL_TEXT_CLASS[item.level]}`}>
                    {item.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="flex h-9 items-center gap-2 overflow-hidden px-4">
          {latest ? (
            <>
              <time className="shrink-0 font-mono text-xs text-slate-400" dateTime={latest.ts}>
                {formatTime(latest.ts)}
              </time>
              <span
                className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${LEVEL_BADGE_CLASS[latest.level]}`}
              >
                {LEVEL_LABEL[latest.level]}
              </span>
              <div className="min-w-0 flex-1 overflow-hidden">
                <p
                  className={`truncate text-xs ${tickerClass}`}
                  title={`${formatTime(latest.ts)} ${latest.message}`}
                >
                  {latest.message}
                </p>
              </div>
            </>
          ) : (
            <p className="text-xs text-slate-400">暂无执行日志</p>
          )}
        </div>
      )}
    </section>
  );
}
