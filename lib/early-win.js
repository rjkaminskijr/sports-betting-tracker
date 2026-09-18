// Display-only early-win indicator. Never write this result to bet_legs.status.
const upper = (value) => String(value ?? "").trim().toUpperCase();
const presentNumber = (value) => {
  if (value === null || value === undefined || value === "") return NaN;
  const n = Number(value);
  return Number.isFinite(n) ? n : NaN;
};
const FINAL = new Set(["WON", "LOST", "PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED", "CASHED_OUT"]);

export function isEarlyWinLive(row) {
  if (!row || row.is_settled || FINAL.has(upper(row.leg_status || row.status))) return false;
  const liveState = upper(row.live_state);
  const status = upper(row.leg_status || row.status);
  if (liveState !== "LIVE" && status !== "LIVE" && status !== "IN_PROGRESS") return false;

  const market = upper(row.market);
  const selection = upper(row.selection);
  const raw = [row.raw_leg_text, row.raw_text, row.market, row.selection].filter(Boolean).join(" ");
  const current = presentNumber(row.live_value ?? row.current_value);
  if (!Number.isFinite(current)) return false;

  // First/last TD and passing-TD markets are deliberately excluded.
  if (market.includes("ANYTIME TD") || market === "TOUCHDOWN SCORER" || market.includes("TO SCORE A TOUCHDOWN")) {
    return current >= 1;
  }

  let line = presentNumber(row.line_value);
  if (!Number.isFinite(line)) {
    const match = raw.match(/\b(?:OVER|O)\s*\(?([0-9]+(?:\.[0-9]+)?)\)?/i);
    if (match) line = Number(match[1]);
  }
  if (!Number.isFinite(line)) return false;

  const explicitPlus = [...raw.matchAll(/(-?\d+(?:\.\d+)?)\s*\+/g)]
    .some((match) => Number(match[1]) === line);
  const direction = upper(row.direction);
  const atLeast = direction === "AT_LEAST" || explicitPlus;
  const over = !atLeast && (direction === "OVER" || /^OVER\b/.test(selection) || /\bOVER\s*\(?[0-9]/i.test(raw));
  if (!atLeast && !over) return false;

  if (market.includes("RECEPTION") && !market.includes("LONGEST")) {
    return atLeast ? current >= line : current > line;
  }

  // Only the three individual yardage markets get the ten-yard buffer.
  const yardMarkets = ["RECEIVING YARD", "RUSHING YARD", "PASSING YARD"];
  const yardCount = yardMarkets.filter((name) => market.includes(name)).length;
  if (yardCount === 1 && !market.includes("LONGEST") && !market.includes("IN EACH QUARTER")) {
    return current >= line + 10;
  }

  // Restrict totals to game/team points, not player points or other total markets.
  const totalMarket = market.includes("TEAM TOTAL") || [
    "TOTAL", "GAME TOTAL", "TOTAL ALTERNATE", "ALTERNATE TOTAL",
    "TOTAL POINTS", "GAME TOTAL POINTS"
  ].includes(market);
  const playerLinked = Boolean(row.espn_athlete_id || row.player_id || row.athlete_id);
  return totalMarket && !playerLinked && over && current > line;
}
