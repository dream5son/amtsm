const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type SignalType =
  | "BUY"
  | "SELL"
  | "STOP_LOSS"
  | "TAKE_PROFIT"
  | "PARTIAL_TP"
  | "ADDON";

export type BacktestStatus = "NONE" | "PENDING" | "RUNNING" | "SUCCESS" | "FAILED";

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
  signal_type: SignalType | null;
  signal_t1_note: boolean;
  signal_limit_board: boolean;
  position_status: "EMPTY" | "HOLDING" | "PARTIAL";
  position_qty: number;
  avg_cost: number | null;
  stop_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  stop_distance_pct: number | null;
  backtest_status: BacktestStatus;
  backtest_job_id: number | null;
  backtest_win_rate: number | null;
  backtest_trade_count: number | null;
  backtest_sample_insufficient: boolean;
  backtest_stale: boolean;
  backtest_error_message: string | null;
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

export type TrailingLadderLevel = {
  min_pnl: number;
  max_pnl: number | null;
  drawdown: number;
};

export type StrategyConfig = {
  global_buy_n: number;
  global_buy_x: number;
  global_sell_n: number;
  global_sell_y: number;
  stop_loss_pct: number;
  break_even_trigger_pct: number;
  break_even_buffer_pct: number;
  trailing_ladder: TrailingLadderLevel[];
  enable_partial_take_profit: boolean;
  enable_addon_alert: boolean;
  enable_tech_sell_while_holding: boolean;
};

export type StockStrategyOverride = {
  stock_code?: string;
  custom_n: number | null;
  custom_x: number | null;
  custom_y: number | null;
  stop_loss_pct: number | null;
  break_even_trigger_pct: number | null;
  break_even_buffer_pct: number | null;
  trailing_ladder: TrailingLadderLevel[] | null;
  enable_partial_take_profit: boolean | null;
  enable_addon_alert: boolean | null;
  enable_tech_sell_while_holding: boolean | null;
  has_override?: boolean;
  resolved?: StrategyConfig & {
    n: number;
    x: number;
    y: number;
  };
};

export const DEFAULT_TRAILING_LADDER: TrailingLadderLevel[] = [
  { min_pnl: 0.1, max_pnl: 0.2, drawdown: 0.15 },
  { min_pnl: 0.2, max_pnl: 0.5, drawdown: 0.1 },
  { min_pnl: 0.5, max_pnl: null, drawdown: 0.06 },
];

export const DEFAULT_STRATEGY: StrategyConfig = {
  global_buy_n: 60,
  global_buy_x: 1.1,
  global_sell_n: 60,
  global_sell_y: 0.9,
  stop_loss_pct: 0.08,
  break_even_trigger_pct: 0.1,
  break_even_buffer_pct: 0.005,
  trailing_ladder: DEFAULT_TRAILING_LADDER,
  enable_partial_take_profit: false,
  enable_addon_alert: false,
  enable_tech_sell_while_holding: false,
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

export async function fetchStrategyOverride(stockCode: string): Promise<StockStrategyOverride> {
  const res = await fetch(
    `${API_BASE}/api/strategy/overrides/${encodeURIComponent(stockCode)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error("failed to fetch strategy override");
  }
  return (await res.json()) as StockStrategyOverride;
}

export async function updateStrategyOverride(
  stockCode: string,
  payload: Omit<StockStrategyOverride, "stock_code" | "has_override" | "resolved">,
): Promise<StockStrategyOverride> {
  const res = await fetch(
    `${API_BASE}/api/strategy/overrides/${encodeURIComponent(stockCode)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!res.ok) {
    throw new Error("failed to update strategy override");
  }
  return (await res.json()) as StockStrategyOverride;
}

export async function clearStrategyOverride(stockCode: string): Promise<StockStrategyOverride> {
  const res = await fetch(
    `${API_BASE}/api/strategy/overrides/${encodeURIComponent(stockCode)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new Error("failed to clear strategy override");
  }
  return (await res.json()) as StockStrategyOverride;
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

export function getSystemStatusSSEUrl(): string {
  return `${API_BASE}/api/system/status/stream`;
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

// ---------------------------------------------------------------------------
// Backtest (user stories 08-10)
// ---------------------------------------------------------------------------

export type BacktestParamsOverride = {
  n?: number;
  x?: number;
  y?: number;
  stop_loss_pct?: number;
  break_even_trigger_pct?: number;
  break_even_buffer_pct?: number;
  trailing_ladder?: TrailingLadderLevel[];
};

export type BacktestCreateRequest = {
  stock_code: string;
  start_date?: string;
  end_date?: string;
  params?: BacktestParamsOverride[];
};

export type BacktestJob = {
  id: number;
  stock_code: string;
  status: "PENDING" | "RUNNING" | "SUCCESS" | "FAILED";
  start_date: string;
  end_date: string;
  params_json: string;
  params_hash: string;
  compare_group_id: string;
  win_rate: number | null;
  avg_win_loss_ratio: number | null;
  max_drawdown: number | null;
  trade_count: number | null;
  total_return: number | null;
  annual_return: number | null;
  sample_insufficient: boolean;
  error_message: string | null;
  created_at: string | null;
  finished_at: string | null;
};

export type BacktestCreateResponse = {
  compare_group_id: string;
  jobs: BacktestJob[];
};

export type BacktestTrade = {
  id: number;
  entry_date: string;
  entry_price: number;
  exit_date: string;
  exit_price: number;
  hold_days: number;
  pnl_pct: number;
  pnl_amount: number | null;
  exit_reason: "STOP_LOSS" | "TAKE_PROFIT" | "PERIOD_END";
};

export type BacktestKlineBar = {
  trade_date: string;
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
  volume: number;
};

export type BacktestKlineResponse = {
  job: BacktestJob;
  bars: BacktestKlineBar[];
  trades: BacktestTrade[];
};

export async function createBacktest(
  payload: BacktestCreateRequest,
): Promise<BacktestCreateResponse> {
  const res = await fetch(`${API_BASE}/api/backtests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let message = "发起回测失败";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return (await res.json()) as BacktestCreateResponse;
}

export async function fetchBacktest(jobId: number): Promise<BacktestJob> {
  const res = await fetch(`${API_BASE}/api/backtests/${jobId}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch backtest job");
  }
  return (await res.json()) as BacktestJob;
}

export async function fetchBacktestsByStock(stockCode: string): Promise<BacktestJob[]> {
  const params = new URLSearchParams({ stock: stockCode });
  const res = await fetch(`${API_BASE}/api/backtests?${params.toString()}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch backtests");
  }
  return (await res.json()) as BacktestJob[];
}

export async function fetchBacktestsByCompareGroup(compareGroupId: string): Promise<BacktestJob[]> {
  const params = new URLSearchParams({ compare_group: compareGroupId });
  const res = await fetch(`${API_BASE}/api/backtests?${params.toString()}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch backtests");
  }
  return (await res.json()) as BacktestJob[];
}

export async function fetchBacktestKline(jobId: number): Promise<BacktestKlineResponse> {
  const res = await fetch(`${API_BASE}/api/backtests/${jobId}/kline`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch backtest kline");
  }
  return (await res.json()) as BacktestKlineResponse;
}

export type BacktestApplyResponse = {
  stock_code: string;
  override: StockStrategyOverride;
  updated_global: boolean;
  global_strategy: StrategyConfig | null;
};

export async function applyBacktest(
  jobId: number,
  options?: { update_global?: boolean },
): Promise<BacktestApplyResponse> {
  const res = await fetch(`${API_BASE}/api/backtests/${jobId}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ update_global: options?.update_global ?? false }),
  });
  if (!res.ok) {
    let message = "应用回测参数失败";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return (await res.json()) as BacktestApplyResponse;
}

// ---------------------------------------------------------------------------
// WeChat notify (admin test page)
// ---------------------------------------------------------------------------

export type WeChatChannelStatusValue = "not_ready" | "available" | "failed";

export type WeChatChannelStatus = {
  status: WeChatChannelStatusValue;
  configured: boolean;
  missing_fields: string[];
  last_error: string | null;
  last_error_category: string | null;
  last_errcode: number | null;
  corp_id_masked: string;
  agent_id: string;
  to_user_masked: string;
  available: boolean;
};

export type WeChatSendResult = {
  ok: boolean;
  status: WeChatChannelStatusValue;
  message: string;
  errcode: number | null;
  errmsg: string | null;
  invalid_user: string | null;
  category: string | null;
  channel: WeChatChannelStatus;
};

export async function fetchWeChatStatus(): Promise<WeChatChannelStatus> {
  const res = await fetch(`${API_BASE}/api/notify/wechat/status`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("failed to fetch wechat status");
  }
  return (await res.json()) as WeChatChannelStatus;
}

export async function sendWeChatTest(to_user: string, content: string): Promise<WeChatSendResult> {
  const res = await fetch(`${API_BASE}/api/notify/wechat/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to_user, content }),
  });
  if (!res.ok) {
    let message = "发送失败";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return (await res.json()) as WeChatSendResult;
}
