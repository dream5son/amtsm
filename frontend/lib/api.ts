const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type WatchlistItem = {
  stock_code: string;
  stock_name: string;
  status: string;
  created_at: string;
  latest_price: number | null;
  change_pct: number | null;
  actual_n: number | null;
  effective_n: number;
  insufficient_days: number | null;
  signal_type: "BUY" | "SELL" | null;
  position_status: "EMPTY" | "HOLDING" | "PARTIAL";
  position_qty: number;
  avg_cost: number | null;
  stop_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  stop_distance_pct: number | null;
};

export type BuyPreview = {
  stock_code: string;
  old_qty: number;
  new_qty: number;
  old_avg_cost: number | null;
  new_avg_cost: number;
  old_stop_price: number | null;
  new_stop_price: number;
  is_addon: boolean;
};

export type PositionSnapshot = {
  stock_code: string;
  qty: number;
  avg_cost: number | null;
  highest_since_hold: number | null;
  stop_price: number | null;
  position_status: "EMPTY" | "HOLDING" | "PARTIAL";
  opened_at: string | null;
};

export type BuyResult = {
  position: PositionSnapshot;
  ledger_id: number;
};

export type SellResult = {
  position: PositionSnapshot;
  ledger_id: number;
  realized_pnl: number;
};

export type LedgerItem = {
  id: number;
  stock_code: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  trade_date: string;
  realized_pnl: number | null;
  note: string | null;
  created_at: string | null;
};

export type PositionTradePayload = {
  qty: number;
  price: number;
  trade_date: string;
  note?: string | null;
};

export type StockSearchItem = {
  stock_code: string;
  stock_name: string;
  exchange: string;
};

export type StrategyConfig = {
  global_buy_n: number;
  global_buy_x: number;
  global_sell_n: number;
  global_sell_y: number;
};

export type JobStatus = {
  status: string;
  latest_job: JobLog | null;
};

export type JobLog = {
  id: number;
  job_name: string;
  trade_date: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  total_count: number;
  success_count: number;
  failed_count: number;
  error_summary: string | null;
};

export type JobLogItem = {
  id: number;
  stock_code: string;
  stock_name: string | null;
  strategy_n: number;
  actual_n: number | null;
  status: string;
  low_min: number | null;
  high_max: number | null;
  error_message: string | null;
  processed_at: string | null;
};

export type JobLogDetail = {
  job_log: JobLog | null;
  items: JobLogItem[];
};

export async function fetchWatchlist(): Promise<WatchlistItem[]> {
  const res = await fetch(`${API_BASE}/api/watchlist`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch watchlist");
  }
  return (await res.json()) as WatchlistItem[];
}

export async function fetchStrategy(): Promise<StrategyConfig> {
  const res = await fetch(`${API_BASE}/api/strategy`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch strategy");
  }
  return (await res.json()) as StrategyConfig;
}

export async function searchStocks(query: string, limit = 20): Promise<StockSearchItem[]> {
  const q = query.trim();
  if (!q) {
    return [];
  }

  const params = new URLSearchParams({
    q,
    limit: String(limit),
  });

  const res = await fetch(`${API_BASE}/api/stocks/search?${params.toString()}`, {
    cache: "no-store",
  });

  if (!res.ok) {
    throw new Error("failed to search stocks");
  }

  return (await res.json()) as StockSearchItem[];
}

export async function createWatchlist(stock_code: string, stock_name: string): Promise<{ message: string }> {
  const res = await fetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ stock_code, stock_name }),
  });

  if (res.status === 409) {
    return { message: "已存在" };
  }

  if (!res.ok) {
    throw new Error("failed to add watchlist");
  }

  return (await res.json()) as { message: string };
}

export async function removeWatchlist(stock_code: string): Promise<{ message: string }> {
  const res = await fetch(`${API_BASE}/api/watchlist/${stock_code}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    throw new Error("failed to remove watchlist");
  }

  return (await res.json()) as { message: string };
}

export async function updateStrategy(payload: StrategyConfig): Promise<StrategyConfig> {
  const res = await fetch(`${API_BASE}/api/strategy`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    throw new Error("failed to update strategy");
  }

  return (await res.json()) as StrategyConfig;
}

export async function fetchJobStatus(): Promise<JobStatus> {
  const res = await fetch(`${API_BASE}/api/jobs/daily-baseline/status`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch job status");
  }
  return (await res.json()) as JobStatus;
}

export async function fetchJobLogLatest(page = 1, size = 50): Promise<JobLogDetail> {
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  const res = await fetch(`${API_BASE}/api/jobs/daily-baseline/logs/latest?${params}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch job log");
  }
  return (await res.json()) as JobLogDetail;
}

export type SystemStatus = {
  quote_delay: boolean;
  consecutive_poll_failures: number;
  quote_delay_since: string | null;
  failure_threshold: number;
};

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const res = await fetch(`${API_BASE}/api/system/status`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch system status");
  }
  return (await res.json()) as SystemStatus;
}

export function getJobStatusSSEUrl(): string {
  return `${API_BASE}/api/jobs/daily-baseline/status/stream`;
}

export async function previewBuy(
  stock_code: string,
  qty: number,
  price: number,
): Promise<BuyPreview> {
  const params = new URLSearchParams({
    qty: String(qty),
    price: String(price),
  });
  const res = await fetch(
    `${API_BASE}/api/positions/${encodeURIComponent(stock_code)}/preview-buy?${params}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || "failed to preview buy");
  }
  return (await res.json()) as BuyPreview;
}

export async function registerBuy(
  stock_code: string,
  payload: PositionTradePayload,
): Promise<BuyResult> {
  const res = await fetch(
    `${API_BASE}/api/positions/${encodeURIComponent(stock_code)}/buys`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!res.ok) {
    let message = "登记买入失败";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return (await res.json()) as BuyResult;
}

export async function registerSell(
  stock_code: string,
  payload: PositionTradePayload,
): Promise<SellResult> {
  const res = await fetch(
    `${API_BASE}/api/positions/${encodeURIComponent(stock_code)}/sells`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!res.ok) {
    let message = "登记减持失败";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return (await res.json()) as SellResult;
}

export async function fetchLedgers(stock_code: string): Promise<LedgerItem[]> {
  const res = await fetch(
    `${API_BASE}/api/positions/${encodeURIComponent(stock_code)}/ledgers`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error("failed to fetch ledgers");
  }
  return (await res.json()) as LedgerItem[];
}
