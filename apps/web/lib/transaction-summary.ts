/** Exact decimal summaries of filtered evidence rows, kept separate by currency and direction. */
export interface SummaryRow { status?: unknown; currency?: unknown; direction?: unknown; amount?: unknown }
interface Total { currency: string; direction: string; coefficient: bigint; scale: number; count: number }

export function summarizePosted(rows: SummaryRow[]): { totals: { currency: string; direction: string; amount: string; count: number }[]; excluded: number; unrecognized: number } {
  const groups = new Map<string, Total>();
  let excluded = 0;
  let unrecognized = 0;
  for (const row of rows) {
    if (row.status === "PENDING" || row.status === "REVERSED") { excluded++; continue; }
    if (row.status !== "POSTED" || typeof row.currency !== "string" || !/^[A-Z]{3}$/.test(row.currency) ||
        (row.direction !== "CREDIT" && row.direction !== "DEBIT") || typeof row.amount !== "string" ||
        !/^-?\d{1,40}(?:\.\d{1,18})?$/.test(row.amount)) { unrecognized++; continue; }
    const negative = row.amount.startsWith("-");
    const [whole, fractional = ""] = (negative ? row.amount.slice(1) : row.amount).split(".");
    const value = BigInt(`${negative ? "-" : ""}${whole}${fractional}`);
    const key = `${row.currency}:${row.direction}`;
    const current = groups.get(key);
    if (!current) { groups.set(key, { currency: row.currency, direction: row.direction, coefficient: value, scale: fractional.length, count: 1 }); continue; }
    const scale = Math.max(current.scale, fractional.length);
    current.coefficient = current.coefficient * 10n ** BigInt(scale - current.scale) + value * 10n ** BigInt(scale - fractional.length);
    current.scale = scale;
    current.count++;
  }
  return { totals: [...groups.values()].map(({ currency, direction, coefficient, scale, count }) => {
    const negative = coefficient < 0n;
    const digits = (negative ? -coefficient : coefficient).toString().padStart(scale + 1, "0");
    const amount = `${negative ? "-" : ""}${scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits}`;
    return { currency, direction, amount, count };
  }).sort((a, b) => a.currency.localeCompare(b.currency) || a.direction.localeCompare(b.direction)), excluded, unrecognized };
}
