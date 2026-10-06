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
  const hasYardage = /\bY(?:ARDS?|DS?)\b/.test(market);
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
  const explicitOver =
    direction === "OVER" ||
    /^OVER\b/.test(selection) ||
    /\bOVER\s*\(?[0-9]/i.test(raw) ||
    /(?:^|\s)O\s*\(?[0-9]/i.test(raw);

  // Some imported alt-player props have a valid line_value but no direction and
  // no raw "60+" text left on the grouped UI row. In that case, whole-number
  // player thresholds are the sportsbook's "X+" form. Fractional lines (29.5,
  // 74.5, etc.) remain OVER lines unless the source explicitly says otherwise.
  const thresholdPlayerMarket =
    (market.includes("RECEPTION") && !market.includes("LONGEST")) ||
    market.includes("COMPLETION") ||
    (hasYardage &&
      !market.includes("LONGEST") &&
      !market.includes("IN EACH QUARTER") &&
      !market.includes("IN EACH HALF"));
  const inferredPlus =
    !direction &&
    !explicitPlus &&
    !explicitOver &&
    thresholdPlayerMarket &&
    Number.isInteger(line);

  const atLeast = direction === "AT_LEAST" || explicitPlus || inferredPlus;
  const over = !atLeast && explicitOver;
  if (!atLeast && !over) return false;

  if (market.includes("RECEPTION") && !market.includes("LONGEST")) {
    return atLeast ? current >= line : current > line;
  }

  // Passing completions are a reversible display-only live hit.
  // If ESPN later corrects the total back below the threshold, WON (LIVE)
  // disappears automatically because this function is recalculated each render.
  if (market.includes("COMPLETION")) {
    return atLeast ? current >= line : current > line;
  }

  // Yardage markets no longer use the old +10 buffer. Treat the current ESPN
  // game value exactly like the wager threshold, including combined yardage.
  // This remains display-only and reversible until official final settlement.
  const isYardageMarket =
    hasYardage &&
    !market.includes("LONGEST") &&
    !market.includes("IN EACH QUARTER") &&
    !market.includes("IN EACH HALF");

  if (isYardageMarket) {
    return atLeast ? current >= line : current > line;
  }

  // Restrict totals to game/team points, not player points or other total markets.
  const totalMarket = market.includes("TEAM TOTAL") || [
    "TOTAL", "GAME TOTAL", "TOTAL ALTERNATE", "ALTERNATE TOTAL",
    "TOTAL POINTS", "GAME TOTAL POINTS"
  ].includes(market);
  const playerLinked = Boolean(row.espn_athlete_id || row.player_id || row.athlete_id);
  return totalMarket && !playerLinked && over && current > line;
}
