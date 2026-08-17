"use client";

import { useState } from "react";

import SchedulerActivityPanel from "@/components/scheduler-activity-panel";
import StrategySettingsPanel, {
  StrategyDialogTarget,
} from "@/components/strategy-settings-panel";
import WatchlistPanel from "@/components/watchlist-panel";

export default function HomePage() {
  const [strategyTarget, setStrategyTarget] = useState<StrategyDialogTarget>(null);
  const [watchlistKey, setWatchlistKey] = useState(0);

  return (
    <main className="w-full px-4 py-8 md:px-6 md:py-10">
      <div className="grid min-w-0 gap-5">
        <WatchlistPanel
          key={watchlistKey}
          onOpenStrategy={(item) =>
            setStrategyTarget({ stock_code: item.stock_code, stock_name: item.stock_name })
          }
        />
        <SchedulerActivityPanel />
        <StrategySettingsPanel
          stockTarget={strategyTarget}
          onStockTargetClose={() => setStrategyTarget(null)}
          onSaved={() => setWatchlistKey((k) => k + 1)}
        />
      </div>
    </main>
  );
}
