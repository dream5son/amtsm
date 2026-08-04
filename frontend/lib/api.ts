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
