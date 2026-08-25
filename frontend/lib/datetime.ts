/** Display timezone for A-share market local time (北京时间). */
export const DISPLAY_TIME_ZONE = "Asia/Shanghai";

/**
 * Parse an API datetime string into a Date.
 * Naive ISO strings (no offset) are treated as UTC, matching SQLite storage.
 */
export function parseApiDateTime(value: string): Date {
  const trimmed = value.trim();
  if (!trimmed) {
    return new Date(Number.NaN);
  }
  const hasOffset = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(trimmed);
  if (hasOffset) {
    return new Date(trimmed);
  }
  // Date-only YYYY-MM-DD → keep as calendar date in Shanghai via noon UTC trick avoided;
  // treat bare date as UTC midnight so formatters with DISPLAY_TIME_ZONE stay stable.
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    return new Date(`${trimmed}T00:00:00Z`);
  }
  return new Date(`${trimmed}Z`);
}

function isValidDate(date: Date): boolean {
  return !Number.isNaN(date.getTime());
}

export function formatDateTime(value: string): string {
  const date = parseApiDateTime(value);
  if (!isValidDate(date)) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    hour12: false,
    timeZone: DISPLAY_TIME_ZONE,
  });
}

export function formatTime(value: string): string {
  const date = parseApiDateTime(value);
  if (!isValidDate(date)) {
    return value;
  }
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    timeZone: DISPLAY_TIME_ZONE,
  });
}

/** Time only when same Shanghai calendar day; otherwise `M/D HH:MM:SS`. */
export function formatDateTimeCompact(value: string): string {
  const date = parseApiDateTime(value);
  if (!isValidDate(date)) {
    return value;
  }
  const time = date.toLocaleTimeString("zh-CN", {
    hour12: false,
    timeZone: DISPLAY_TIME_ZONE,
  });
  const day = date.toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
    timeZone: DISPLAY_TIME_ZONE,
  });
  const today = new Date().toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
    timeZone: DISPLAY_TIME_ZONE,
  });
  if (day === today) {
    return time;
  }
  return `${day} ${time}`;
}

/** Today's calendar date in Asia/Shanghai as `YYYY-MM-DD`. */
export function todayISO(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
