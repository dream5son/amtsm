type Status = "NORMAL" | "HALT" | "DELISTED";

const map: Record<Status, { text: string; bg: string; color: string }> = {
  NORMAL: { text: "正常", bg: "#e8f6ee", color: "#1a7f37" },
  HALT: { text: "停牌", bg: "#fff4df", color: "#b26a00" },
  DELISTED: { text: "退市", bg: "#ffe8e8", color: "#bf2626" },
};

export default function StatusBadge({ status }: { status: string }) {
  const resolved = (status in map ? status : "NORMAL") as Status;
  const item = map[resolved];

  return (
    <span
      style={{
        padding: "4px 10px",
        borderRadius: 999,
        background: item.bg,
        color: item.color,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {item.text}
    </span>
  );
}
