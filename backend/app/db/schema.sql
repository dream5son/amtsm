PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code VARCHAR(10) NOT NULL UNIQUE,
    stock_name VARCHAR(50) NOT NULL,
    status VARCHAR(10) DEFAULT 'NORMAL',
    custom_n INTEGER DEFAULT NULL,
    custom_x REAL DEFAULT NULL,
    custom_y REAL DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    global_buy_n INTEGER DEFAULT 60,
    global_buy_x REAL DEFAULT 1.10,
    global_sell_n INTEGER DEFAULT 60,
    global_sell_y REAL DEFAULT 0.90,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_baselines (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    low_min REAL NOT NULL,
    high_max REAL NOT NULL,
    actual_n INTEGER NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS daily_market_snapshots (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    close_price REAL NOT NULL,
    volume REAL NOT NULL,
    turnover_rate REAL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS alert_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    signal_type VARCHAR(5) NOT NULL,
    trigger_price REAL NOT NULL,
    baseline_price REAL NOT NULL,
    used_coeff REAL NOT NULL,
    sent_status VARCHAR(10) NOT NULL,
    sent_time DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_freq_limit
ON alert_logs(stock_code, trade_date, signal_type);

INSERT OR IGNORE INTO strategy_config (
    id,
    global_buy_n,
    global_buy_x,
    global_sell_n,
    global_sell_y
)
VALUES (1, 60, 1.10, 60, 0.90);
