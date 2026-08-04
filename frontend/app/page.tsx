import StrategySettingsPanel from "@/components/strategy-settings-panel";
import WatchlistPanel from "@/components/watchlist-panel";

export default function HomePage() {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 md:px-6 md:py-10">
      <div className="grid gap-5">
        <WatchlistPanel />
        <StrategySettingsPanel />
      </div>
    </main>
  );
}
