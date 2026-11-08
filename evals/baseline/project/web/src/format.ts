// Formatting for the shop front end: money, quantities and dates, in the shop's own style.

const THOUSANDS = /\B(?=(\d{3})+(?!\d))/g;

/** 123456 -> "€1,234.56". */
export function formatMoney(cents: number): string {
  if (!Number.isInteger(cents)) {
    throw new Error(`formatMoney expects whole cents, got ${cents}`);
  }
  const euros = Math.trunc(cents / 100);
  const rest = Math.abs(cents % 100);
  return `€${String(euros).replace(THOUSANDS, ",")}.${String(rest).padStart(2, "0")}`;
}

/** 1 -> "1 item", 3 -> "3 items". */
export function formatCount(count: number, singular = "item", plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

/** A percentage with at most one decimal: 0.215 -> "21.5%". */
export function formatShare(fraction: number): string {
  const percent = Math.round(fraction * 1000) / 10;
  return `${percent}%`;
}

/** A delivery date as the shop writes it: "Tue 3 Mar". */
export function formatDeliveryDate(date: Date): string {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${days[date.getUTCDay()]} ${date.getUTCDate()} ${months[date.getUTCMonth()]}`;
}
