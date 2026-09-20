"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchJsonWithRetry } from "../../lib/client-api";
import { isEarlyWinLive } from "../../lib/early-win";

const REFRESH_MS = 30000;
const FINAL_REVIEW_BUFFER_MS = 15 * 60 * 1000;
const FINAL_FALLBACK_MAX_GAME_AGE_MS = 8 * 60 * 60 * 1000;
const FINAL_SEEN_STORAGE_KEY = "sports-bet-tracker-final-seen-v1";
const SETTLED = new Set(["WON", "LOST", "PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED", "CASHED_OUT"]);

const upper = (value) => String(value || "").trim().toUpperCase();
const text = (value) => String(value || "").trim();

function statusOf(row) {
  return upper(row.leg_status || row.status || "PENDING");
}

function isSettled(row) {
  return Boolean(row.is_settled) || SETTLED.has(statusOf(row));
}

function isUndecided(row) {
  return !isSettled(row);
}

function isFinalGameStatus(gameStatus) {
  if (!gameStatus) return false;
  const state = String(gameStatus.state || "").trim().toLowerCase();
  if (state === "post") return true;
  const readout = upper(gameStatus.readout);
  const detail = upper(gameStatus.detail);
  return readout.includes("FINAL") || detail.includes("FINAL");
}

function isLiveGameStatus(gameStatus) {
  if (!gameStatus || isFinalGameStatus(gameStatus)) return false;
  return String(gameStatus.state || "").trim().toLowerCase() === "in";
}

function sportOf(row) {
  const raw = upper(row.parent_sport || row.sport);
  if (["NCAAF", "CFB", "COLLEGE FOOTBALL"].includes(raw)) return "CFB";
  return raw || "Football";
}

function gameOf(row) {
  const a = text(row.event_team_a);
  const b = text(row.event_team_b);
  if (a && b) return `${a} @ ${b}`;
  return a || b || text(row.parent_event_name) || `Event ${row.espn_event_id || ""}`.trim() || "Game TBD";
}

function eventTime(row) {
  const value = row.event_time;
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dateKey(row) {
  const d = eventTime(row);
  if (!d) return "";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatGameTime(value) {
  if (!value) return "Time TBD";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "Time TBD";
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
  }).format(d);
}

function stateOf(row) {
  if (isSettled(row)) return "SETTLED";
  const live = upper(row.live_state);
  const status = statusOf(row);
  if (live === "LIVE" || ["LIVE", "IN_PROGRESS"].includes(status)) return "LIVE";
  return "UPCOMING";
}

function liveValue(row) {
  const raw = row?.live_value;
  if (raw === null || raw === undefined || raw === "") return "—";

  const value = String(raw).trim();
  const market = upper(row?.market);

  if (market.includes("IN EACH QUARTER")) {
    const matches = [...value.matchAll(/\bQ([1-4])\s*(-?\d+(?:\.\d+)?)/gi)];
    if (matches.length) {
      const quarters = new Map(matches.map((m) => [Number(m[1]), m[2]]));
      if ([1, 2, 3, 4].every((q) => quarters.has(q))) {
        return [1, 2, 3, 4].map((q) => quarters.get(q)).join("/");
      }
    }
  }

  return value;
}

function isAnytimeTdMarket(row) {
  // Sportsbooks and parsers use multiple labels for the same anytime TD prop.
  // Do not combine first/last scorer, passing TD or other distinct markets.
  const market = upper(row?.market).replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
  if (/\b(?:FIRST|LAST)\b/.test(market)) return false;
  return market === "ATD" ||
    market === "TOUCHDOWN SCORER" ||
    /\bANY\s*TIME\s+(?:TD|TOUCHDOWN)(?:\s+SCORER)?\b/.test(market) ||
    /\bTO SCORE (?:A |AN )?(?:TD|TOUCHDOWN)\b/.test(market);
}

function uniqueKey(row) {
  const anytimeTd = isAnytimeTdMarket(row);

  return [
    sportOf(row),
    upper(row.selection),
    anytimeTd ? "ATD" : upper(row.market),
    anytimeTd ? "" : String(row.line_value ?? ""),
    anytimeTd ? "" : upper(row.direction),
    row.espn_event_id ? `event:${row.espn_event_id}` : `game:${gameOf(row)}`,
    statusOf(row),
    liveValue(row)
  ].join("||");
}

function combineRows(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = uniqueKey(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }

  return [...groups.values()].map((group) => {
    const first = group[0];
    const betIds = [...new Set(group.map((r) => Number(r.bet_row_id)).filter(Number.isFinite))].sort((a, b) => a - b);
    const sportsbooks = [...new Set(group.map((r) => text(r.parent_sportsbook || r.sportsbook)).filter(Boolean))].sort();
    const settled = group.every(isSettled);
    const live = group.some((r) => stateOf(r) === "LIVE");
    return {
      ...first,
      count: group.length,
      betIds,
      sportsbooks,
      groupRows: group,
      settled,
      state: settled ? "SETTLED" : live ? "LIVE" : "UPCOMING"
    };
  });
}

function urgencyRank(row) {
  const status = statusOf(row);
  if (row.state === "LIVE" && !isSettled(row)) return 0;
  if (!isSettled(row)) return 1;
  if (status === "WON") return 2;
  if (["PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED"].includes(status)) return 3;
  return 4;
}

function sortCombined(rows) {
  return [...rows].sort((a, b) => {
    const ar = urgencyRank(a);
    const br = urgencyRank(b);
    if (ar !== br) return ar - br;
    const at = eventTime(a)?.getTime() ?? Number.POSITIVE_INFINITY;
    const bt = eventTime(b)?.getTime() ?? Number.POSITIVE_INFINITY;
    if (at !== bt) return at - bt;
    return `${sportOf(a)}|${gameOf(a)}|${a.selection || ""}`.localeCompare(`${sportOf(b)}|${gameOf(b)}|${b.selection || ""}`);
  });
}

function buildGameGroups(rows, gameStatuses = {}) {
  const map = new Map();

  for (const row of sortCombined(rows)) {
    const eventId = text(row.espn_event_id);
    const fallbackGame = gameOf(row);
    // ESPN event_id is the canonical game identity. Do not split one game
    // into separate cards because imported legs use different sport labels
    // such as "NFL" versus "Football".
    const key = eventId
      ? `event:${eventId}`
      : `${sportOf(row)}||game:${fallbackGame}||${row.event_time || ""}`;

    if (!map.has(key)) {
      map.set(key, {
        key,
        eventId: eventId || null,
        sport: sportOf(row),
        game: fallbackGame,
        eventTime: row.event_time,
        isLive: false,
        rows: [],
        gameLabels: new Map()
      });
    }

    const group = map.get(key);
    group.rows.push(row);
    group.gameLabels.set(fallbackGame, (group.gameLabels.get(fallbackGame) || 0) + 1);

    if (!group.eventTime && row.event_time) group.eventTime = row.event_time;
    if (row.state === "LIVE") group.isLive = true;
  }

  const groups = [...map.values()].map((group) => {
    const preferredGame = [...group.gameLabels.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0];
    const undecidedCount = group.rows.filter(isUndecided).length;
    const settledCount = group.rows.length - undecidedCount;

    return {
      ...group,
      game: preferredGame || group.game,
      // Some imported legs have no event_time even after ESPN has matched the
      // event. Use ESPN's scheduled kickoff for both the card and its order.
      eventTime: [group.eventTime, gameStatuses[String(group.eventId || "")]?.startTime]
        .find((value) => value && !Number.isNaN(new Date(value).getTime())) || null,
      gameLabels: undefined,
      undecidedCount,
      settledCount,
      hasUndecided: undecidedCount > 0
    };
  });

  return groups.sort((a, b) => {
    if (a.isLive !== b.isLive) return a.isLive ? -1 : 1;
    if (a.hasUndecided !== b.hasUndecided) return a.hasUndecided ? -1 : 1;
    const at = a.eventTime ? new Date(a.eventTime).getTime() : Number.POSITIVE_INFINITY;
    const bt = b.eventTime ? new Date(b.eventTime).getTime() : Number.POSITIVE_INFINITY;
    if (at !== bt) return at - bt;
    return `${a.sport}|${a.game}`.localeCompare(`${b.sport}|${b.game}`);
  });
}

function friendlyLiveValue(row) {
  const raw = liveValue(row);
  if (raw === "—") return raw;
  const market = upper(row.market);
  if (market.includes("ANYTIME TD") || market.includes("TOUCHDOWN") || market.includes("PASSING TD")) {
    const n = Number(raw);
    if (Number.isFinite(n)) return `${n} TD${n === 1 ? "" : "s"}`;
  }
  if (market.includes("PASSING YARD") || market.includes("RUSHING YARD") || market.includes("RECEIVING YARD")) {
    const n = Number(raw);
    if (Number.isFinite(n)) return `${n} yds`;
  }
  if (market.includes("RECEPTION")) {
    const n = Number(raw);
    if (Number.isFinite(n)) return `${n} rec`;
  }
  return raw;
}

function isGameOrTeamTotal(row) {
  const market = upper(row.market);
  if (!market) return false;
  if (market.includes("TEAM TOTAL")) return true;
  return (
    market === "TOTAL" ||
    market === "GAME TOTAL" ||
    market === "TOTAL ALTERNATE" ||
    market === "ALTERNATE TOTAL" ||
    market === "TOTAL POINTS" ||
    market === "GAME TOTAL POINTS"
  );
}

function isPlayerLeg(row) {
  if (upper(row.market) === "PLAYER QUARTER SPECIALS") return false;
  if (text(row.espn_athlete_id) || text(row.player_id) || text(row.athlete_id)) return true;
  const market = text(row.market);
  return /(receiving|rushing|passing|receptions?|touchdown|\btd\b|first to score|last to score|completions?|interceptions?|longest reception|longest rush)/i.test(market);
}

function rawLegText(row) {
  return text(row.raw_leg_text || row.raw_text || "");
}

function hasExplicitPlusThreshold(row) {
  const line = Number(row.line_value);
  if (!Number.isFinite(line)) return false;

  const sources = [
    rawLegText(row),
    text(row.market),
    text(row.selection)
  ].filter(Boolean);

  return sources.some((source) => {
    const matches = [...String(source).matchAll(/(-?\d+(?:\.\d+)?)\s*\+/g)];
    return matches.some((match) => Number(match[1]) === line);
  });
}

function effectiveDirection(row) {
  const direction = upper(row.direction);
  if (direction === "AT_LEAST" || hasExplicitPlusThreshold(row)) return "AT_LEAST";
  if (direction === "OVER") return "OVER";
  if (direction === "UNDER") return "UNDER";
  return direction;
}

function directionShort(row) {
  const direction = effectiveDirection(row);
  if (direction === "OVER") return "O";
  if (direction === "UNDER") return "U";
  if (direction === "AT_LEAST") return "+";
  return direction;
}

// Both standard and alternate receiving-yard markets describe the same stat.
// Some feeds abbreviate yards as "Yds" rather than spelling out "Yards".
function isReceivingYardsMarket(market) {
  return /\bRECEIVING\s+Y(?:ARDS?|DS?)\b/.test(upper(market));
}

function compactMarketLabel(row) {
  const market = text(row.market);
  const m = upper(market);
  const line = row.line_value;
  const dir = directionShort(row);
  const hasLine = line !== null && line !== undefined && line !== "";
  const prefix = !hasLine || !dir
    ? ""
    : dir === "+"
      ? `${line}+ `
      : `${dir}${line} `;

  if (m.includes("FIRST TD") || m.includes("FIRST TOUCHDOWN") || m.includes("FIRST TO SCORE")) return "FTD";
  if (m.includes("LAST TD") || m.includes("LAST TOUCHDOWN") || m.includes("LAST TO SCORE")) return "LTD";
  if (isAnytimeTdMarket(row)) return "ATD";
  if (m.includes("RUSHING") && m.includes("RECEIVING YARD")) return `${prefix}Rush + Rec Yds`.trim();
  if (m.includes("PASSING") && m.includes("RUSHING YARD")) return `${prefix}Pass + Rush Yds`.trim();
  if (isReceivingYardsMarket(m)) return `${prefix}Rec Yds`.trim();
  if (m.includes("RUSHING YARD")) return `${prefix}Rush Yds`.trim();
  if (m.includes("PASSING YARD")) return `${prefix}Pass Yds`.trim();
  if (m.includes("RECEPTION")) return `${prefix}Rec`.trim();
  if (m.includes("PASSING TD")) return `${prefix}Pass TDs`.trim();
  if (m.includes("INTERCEPTION")) return `${prefix}INT`.trim();
  if (m.includes("MONEYLINE") || m === "ML") return "ML";
  if (m.includes("SPREAD")) return text(row.selection) || `${line}`;
  if (m.includes("TEAM TOTAL")) return `${prefix}Team Total`.trim();
  if (isGameOrTeamTotal(row)) return `${prefix}Total`.trim();
  if (prefix) return `${prefix}${market}`.trim();
  return market || text(row.selection) || "Market";
}

function teamGamePillLabel(row) {
  const selection = text(row.selection);
  const market = text(row.market);
  const normalizedMarket = upper(market);
  const hasLine = row.line_value !== null && row.line_value !== undefined && row.line_value !== "";
  const direction = effectiveDirection(row);
  const threshold = hasLine
    ? `${direction === "OVER" ? "Over" : direction === "UNDER" ? "Under" : direction === "AT_LEAST" ? "At least" : ""} ${row.line_value}`.trim()
    : "";

  if (normalizedMarket.includes("SPREAD")) return selection || compactMarketLabel(row);
  if (normalizedMarket.includes("TEAM TOTAL")) {
    const team = market.replace(/\s+TEAM\s+(?:ALT(?:ERNATE)?\s+)?TOTAL(?:\s+.*)?$/i, "").trim();
    const name = team.replace(/\b[A-Z]{5,}\b/g, (word) => word[0] + word.slice(1).toLowerCase());
    return `${name || "Team"} team total${threshold ? ` · ${threshold}` : ""}`;
  }
  if (isGameOrTeamTotal(row)) return `Game total${threshold ? ` · ${threshold}` : ""}`;
  if (normalizedMarket.includes("MONEYLINE") || normalizedMarket === "ML") {
    return [selection, "moneyline"].filter(Boolean).join(" ");
  }
  const detail = compactMarketLabel(row);
  return selection && selection !== detail ? `${selection} · ${detail}` : selection || detail;
}

function badgeStatusClass(row, gameStatus) {
  const status = statusOf(row);
  if (status === "WON") return "marketPillWon";
  if (status === "LOST") return "marketPillLost";
  if (["PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED"].includes(status)) return "marketPillNeutral";
  if (isEarlyWinLive(row)) return "marketPillHit";
  if (row.state === "LIVE" || (isLiveGameStatus(gameStatus) && !isSettled(row))) return "marketPillLive";
  return "marketPillUpcoming";
}

function MarketPill({ row, includeSelection = false, gameStatus }) {
  const value = friendlyLiveValue(row);
  const status = statusOf(row);
  const label = includeSelection ? teamGamePillLabel(row) : compactMarketLabel(row);
  // Player live values are shown once in the inline player stats bar.
  const showValue = false;
  const title = [
    row.selection,
    row.market,
    row.sportsbooks?.join(", "),
    row.betIds?.length ? `Bet${row.betIds.length === 1 ? "" : "s"} ${row.betIds.join(", ")}` : ""
  ].filter(Boolean).join(" • ");

  return (
    <span className={`marketPill ${badgeStatusClass(row, gameStatus)}`} title={title}>
      <span className="marketPillLabel">{label}</span>
      {row.count > 1 && <span className="marketPillCount">×{row.count}</span>}
      {isEarlyWinLive(row) && <span className="marketPillResult">✓ WON (LIVE)</span>}
      {status === "WON" && <span className="marketPillResult">✓</span>}
      {status === "LOST" && <span className="marketPillResult">✕</span>}
      {showValue && <span className="marketPillValue">{value}</span>}
    </span>
  );
}

// Shared classification drives both the inline stats bar and the bet-pill order.
// Do not assume zero if ESPN has not supplied a live value yet.
function playerStat(row) {
  const market = upper(row.market);
  if (market.includes("RUSHING") && market.includes("RECEIVING YARD")) return { key: "rushRecYds", label: "Rush + Rec Yds", order: 45 };
  if (market.includes("PASSING") && market.includes("RUSHING YARD")) return { key: "passRushYds", label: "Pass + Rush Yds", order: 46 };
  if (market.includes("RECEPTION") && !market.includes("LONGEST")) return { key: "rec", label: "Rec", order: 10 };
  if (isReceivingYardsMarket(market) && !market.includes("LONGEST")) return { key: "recYds", label: "Rec Yds", order: 20 };
  if (market.includes("RUSHING YARD") && !market.includes("LONGEST")) return { key: "rushYds", label: "Rush Yds", order: 30 };
  if (market.includes("PASSING YARD")) return { key: "passYds", label: "Pass Yds", order: 40 };
  if (market.includes("PASSING TD")) return { key: "passTd", label: "Pass TD", order: 47 };
  if (isAnytimeTdMarket(row) || /\b(?:FIRST|LAST) (?:TD|TOUCHDOWN|TO SCORE)\b/.test(market)) return { key: "td", label: "TD", order: 50 };
  if (market.includes("COMPLETION")) return { key: "completions", label: "Comp", order: 41 };
  if (market.includes("INTERCEPTION")) return { key: "int", label: "INT", order: 42 };
  // Other supported markets retain their own stat without inventing a metric.
  return { key: `market:${market}`, label: compactMarketLabel({ ...row, line_value: null, direction: "" }), order: 90 };
}

function playerPillCompare(a, b) {
  const statA = playerStat(a);
  const statB = playerStat(b);
  if (statA.order !== statB.order) return statA.order - statB.order;
  if (statA.key !== statB.key) return statA.key.localeCompare(statB.key);
  const thresholdA = Number(a.line_value);
  const thresholdB = Number(b.line_value);
  if (Number.isFinite(thresholdA) && Number.isFinite(thresholdB) && thresholdA !== thresholdB) return thresholdA - thresholdB;
  return compactMarketLabel(a).localeCompare(compactMarketLabel(b), undefined, { numeric: true }) || urgencyRank(a) - urgencyRank(b);
}

function playerStatSummary(rows) {
  const stats = new Map();
  for (const row of rows) {
    const stat = playerStat(row);
    const value = liveValue(row);
    const current = stats.get(stat.key);
    // Prefer a known LIVE reading over unknown or outdated values. A settled
    // occurrence may lack a current value while other bets on this player run.
    const priority = (value !== "—" ? 2 : 0) + (row.state === "LIVE" ? 1 : 0);
    if (!current || priority > current.priority) {
      stats.set(stat.key, { ...stat, value, priority });
    }
  }
  return [...stats.values()].sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
}

function buildPlayerGroups(rows) {
  const map = new Map();
  for (const row of rows.filter(isPlayerLeg)) {
    const name = text(row.selection) || "Unnamed player";
    // Group by player name *within this game*. Imported alternate markets
    // sometimes lack ESPN athlete IDs or use a different identifier.
    const key = name.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
    if (!map.has(key)) map.set(key, { key, name, rows: [] });
    map.get(key).rows.push(row);
  }

  return [...map.values()].map((group) => {
    const rowsSorted = [...group.rows].sort(playerPillCompare);
    const activeRows = rowsSorted.filter(isUndecided);
    const settledRows = rowsSorted.filter(isSettled);
    const betIds = [...new Set(rowsSorted.flatMap((row) => row.betIds || []))];
    const live = activeRows.some((row) => row.state === "LIVE");
    return { ...group, rows: rowsSorted, activeRows, settledRows, betIds, live, stats: playerStatSummary(activeRows.length ? activeRows : rowsSorted) };
  }).sort((a, b) => {
    if (a.live !== b.live) return a.live ? -1 : 1;
    if (Boolean(a.activeRows.length) !== Boolean(b.activeRows.length)) return a.activeRows.length ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

function PlayerMarketGroup({ group, showSettled, gameStatus }) {
  return (
    <div className={`playerMarketGroup ${group.live ? "playerMarketGroupLive" : ""}`}>
      <div className="playerMarketHead" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", justifyContent: "flex-start", columnGap: 10, rowGap: 3 }}>
        <span style={{ display: "inline-flex", alignItems: "baseline", flexWrap: "wrap", columnGap: 10, rowGap: 3, minWidth: 0 }}>
          <strong>{group.name}</strong>
          {!!group.stats.length && (
            <span className="playerInlineStats" aria-label="Player stats" style={{ display: "inline-flex", alignItems: "baseline", flexWrap: "wrap", gap: 0, color: "#8edcc5", fontSize: "0.85em", whiteSpace: "normal" }}>
              {group.stats.map((stat, index) => (
                <span key={stat.key} className="playerInlineStat" style={{ whiteSpace: "nowrap" }}>
                  {index > 0 && <span aria-hidden="true" style={{ margin: "0 6px", opacity: 0.65 }}>·</span>}
                  {stat.value} {stat.label}
                </span>
              ))}
            </span>
          )}
        </span>
        <span className="playerBetCount" style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>{group.betIds.length} bet{group.betIds.length === 1 ? "" : "s"}</span>
      </div>
      {!!group.activeRows.length && <div className="marketPillRow">{group.activeRows.map((row, index) => <MarketPill key={`${uniqueKey(row)}-${index}`} row={row} gameStatus={gameStatus} />)}</div>}
      {showSettled && !!group.settledRows.length && (
        <details className="settledMarketDetails">
          <summary>{group.settledRows.length} settled</summary>
          <div className="marketPillRow">{group.settledRows.map((row, index) => <MarketPill key={`${uniqueKey(row)}-settled-${index}`} row={row} gameStatus={gameStatus} />)}</div>
        </details>
      )}
    </div>
  );
}

// The backend sends one string containing both players' Q1-Q4 values.
// Render only known quarters; a zero in a future quarter is not a played zero.
function EachQuarterProgress({ row, gameStatus }) {
  const match = text(row.selection).match(/^(.+?)\s+(?:&|and)\s+(.+?)\s+to\s+Each\s+(?:Record|Have)\s+(\d+(?:\.\d+)?)\+\s+(Rushing|Receiving|Passing)\s+Yards\s+in\s+Each\s+Quarter$/i);
  if (!match || upper(row.market) !== "PLAYER QUARTER SPECIALS") return null;
  const players = [match[1], match[2]];
  const threshold = Number(match[3]);
  const currentQuarter = Number(String(gameStatus?.readout || gameStatus?.detail || "").match(/\bQ([1-4])\b/i)?.[1]) || null;
  const final = isFinalGameStatus(gameStatus);
  const parts = text(row.live_value).split(/\s+\|\s+/);
  const values = players.map((name) => {
    const part = parts.find((item) => item.toLowerCase().startsWith(`${name.toLowerCase()}:`));
    const quarters = [...(part || "").matchAll(/\bQ([1-4])\s+(-?\d+(?:\.\d+)?)/gi)];
    const result = [null, null, null, null];
    for (const q of quarters) result[Number(q[1]) - 1] = Number(q[2]);
    return result;
  });
  const hasProgress = values.some((v) => v.some((n) => n !== null));
  return (
    <div className="eachQuarterProgress" style={{ marginTop: 8, overflowX: "auto", fontSize: "0.85em" }} aria-label={`${threshold}+ ${match[4].toLowerCase()} yards for each player in each quarter`}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(95px,1.5fr) repeat(4,minmax(46px,1fr))", gap: "4px 8px", alignItems: "center", maxWidth: 490 }}>
        <span style={{ opacity: 0.75 }}>Player</span>{[1,2,3,4].map((q) => <strong key={q} style={{ textAlign: "center" }}>Q{q}</strong>)}
        {players.map((name, i) => [
          <strong key={`${i}-name`} style={{ overflowWrap: "anywhere" }}>{name}</strong>,
          ...[1,2,3,4].map((q) => {
            const n = values[i][q-1];
            const started = final || (currentQuarter !== null && q <= currentQuarter);
            const shown = hasProgress && started && n !== null;
            const hit = shown && n >= threshold;
            return <span key={`${i}-${q}`} style={{ textAlign: "center", color: hit ? "#8edcc5" : "inherit", opacity: shown ? 1 : 0.55 }} title={!shown ? "Quarter not started or no verified live value" : `${name}: ${n} yards in Q${q}`}>
              {shown ? `${n} yd${hit ? " ✓" : ""}` : "—"}
            </span>;
          })
        ])}
      </div>
      {!hasProgress && <span style={{ opacity: 0.75 }}>Quarter statistics not available yet.</span>}
    </div>
  );
}

function TeamGameMarkets({ rows, showSettled, gameStatus }) {
  const activeRows = rows.filter(isUndecided);
  const settledRows = rows.filter(isSettled);
  if (!activeRows.length && !(showSettled && settledRows.length)) return null;

  return (
    <div className="teamMarketSection">
      <div className="compactSectionLabel">Team / Game</div>
      {!!activeRows.length && <div className="marketPillRow">{activeRows.map((row, index) => <div key={`${uniqueKey(row)}-team-${index}`}><MarketPill row={row} includeSelection gameStatus={gameStatus} /><EachQuarterProgress row={row} gameStatus={gameStatus} /></div>)}</div>}
      {showSettled && !!settledRows.length && (
        <details className="settledMarketDetails">
          <summary>{settledRows.length} settled</summary>
          <div className="marketPillRow">{settledRows.map((row, index) => <MarketPill key={`${uniqueKey(row)}-team-settled-${index}`} row={row} includeSelection gameStatus={gameStatus} />)}</div>
        </details>
      )}
    </div>
  );
}

function GameGroup({ group, open, onToggle, gameStatus, showSettled }) {
  const playerGroups = buildPlayerGroups(group.rows);
  const teamRows = group.rows.filter((row) => !isPlayerLeg(row));
  const gameIsFinal = isFinalGameStatus(gameStatus);
  const gameIsLive = !gameIsFinal && (gameStatus?.state === "in" || group.isLive);

  return (
    <section className={`gameGroup ${gameIsLive ? "liveGameGroup" : ""} ${gameIsFinal || !group.hasUndecided ? "gameGroupSettled" : ""}`}>
      <button className="gameGroupHeader gameGroupToggle" type="button" onClick={onToggle} aria-expanded={open}>
        <div>
          <div className="gameGroupTitleLine"><span>{group.sport}</span><h2>{group.game}</h2>{gameIsLive && <span className="liveBadge">LIVE</span>}{gameIsFinal && <span className="settledBadge">FINAL</span>}</div>
          <small>{formatGameTime(group.eventTime)}</small>{gameStatus?.readout && <div className="gameLiveReadout">{gameStatus.readout}</div>}
        </div>
        <div className="gameGroupHeaderRight">
          <span className="gameLegCount">{group.undecidedCount} sweat{group.undecidedCount === 1 ? "" : "s"}{showSettled && group.settledCount ? ` · ${group.settledCount} settled` : ""}</span>
          <span className={`gameChevron ${open ? "gameChevronOpen" : ""}`}>⌄</span>
        </div>
      </button>
      {open && (
        <div className="gameCompactBody">
          {!!playerGroups.length && (
            <div className="playerMarketSection">
              <div className="compactSectionLabel">Players</div>
              <div className="playerMarketList">{playerGroups.map((player) => <PlayerMarketGroup key={player.key} group={player} showSettled={showSettled} gameStatus={gameStatus} />)}</div>
            </div>
          )}
          <TeamGameMarkets rows={teamRows} showSettled={showSettled} gameStatus={gameStatus} />
        </div>
      )}
    </section>
  );
}

export default function LegsPage() {
  const [rows, setRows] = useState([]);
  const [updated, setUpdated] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [sportsbook, setSportsbook] = useState("ALL");
  const [sport, setSport] = useState("ALL");
  const [gameState, setGameState] = useState("ALL");
  const [date, setDate] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [viewMode, setViewMode] = useState("SWEAT");
  const [openGames, setOpenGames] = useState({});
  const [gameStatuses, setGameStatuses] = useState({});
  const [finalSeenAt, setFinalSeenAt] = useState({});

  async function load() {
    try {
      const { data } = await fetchJsonWithRetry(
        "/api/active-legs",
        { cache: "no-store" },
        { fallbackMessage: "Unable to load active legs" }
      );
      setRows(data.rows || []);
      setUpdated(new Date());
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    try {
      const raw =
        window.localStorage.getItem(
          FINAL_SEEN_STORAGE_KEY,
        );

      if (!raw) return;

      const parsed =
        JSON.parse(
          raw,
        );

      if (
        parsed &&
        typeof parsed === "object"
      ) {
        setFinalSeenAt(
          parsed,
        );
      }
    } catch {
      // Local storage is only a convenience for preserving the review buffer.
    }
  }, []);

  const sportsbooks = useMemo(() => [...new Set(rows.map((r) => text(r.parent_sportsbook || r.sportsbook)).filter(Boolean))].sort(), [rows]);
  const sports = useMemo(() => [...new Set(rows.map(sportOf).filter(Boolean))].sort(), [rows]);

  const filteredOccurrences = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      const book = text(row.parent_sportsbook || row.sportsbook);
      const state = stateOf(row);
      if (sportsbook !== "ALL" && book !== sportsbook) return false;
      if (sport !== "ALL" && sportOf(row) !== sport) return false;
      if (gameState !== "ALL" && state !== gameState) return false;
      if (date && dateKey(row) !== date) return false;
      if (q) {
        const haystack = [row.selection, row.market, row.event_team_a, row.event_team_b, row.parent_event_name, row.bet_row_id, book, sportOf(row)].join(" ").toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [rows, search, sportsbook, sport, gameState, date]);

  const allCombined = useMemo(() => sortCombined(combineRows(filteredOccurrences)), [filteredOccurrences]);
  const allGames = useMemo(() => buildGameGroups(allCombined), [allCombined]);

  // ESPN game state is authoritative for Sweat. FINAL games disappear there
  // immediately, regardless of stale child-leg or parent-bet statuses.
  const finalEventIds = useMemo(() => new Set(
    Object.entries(gameStatuses)
      .filter(([, status]) => isFinalGameStatus(status))
      .map(([eventId]) => String(eventId))
  ), [gameStatuses]);

  /*
    All Legs gets a short postgame review window. The first time this device
    observes an ESPN event as FINAL, remember that timestamp in localStorage.
    That keeps the 15-minute buffer stable across refreshes.

    For an old FINAL game with no prior timestamp (for example after deploying
    this feature), do not resurrect it for 15 minutes merely because the page
    was opened today. If kickoff was more than 8 hours ago, treat its review
    window as already expired.
  */
  useEffect(() => {
    const now =
      Date.now();

    setFinalSeenAt((current) => {
      const next =
        { ...current };

      let changed =
        false;

      for (
        const game
        of allGames
      ) {
        const eventId =
          text(
            game.eventId,
          );

        if (
          !eventId ||
          !finalEventIds.has(
            eventId,
          ) ||
          Number.isFinite(
            Number(
              next[eventId],
            ),
          )
        ) {
          continue;
        }

        const kickoff =
          game.rows
            .map(
              (row) =>
                eventTime(
                  row,
                )?.getTime() ??
                null,
            )
            .find(
              (value) =>
                Number.isFinite(
                  value,
                ),
            );

        next[eventId] =
          kickoff &&
          now - kickoff >
            FINAL_FALLBACK_MAX_GAME_AGE_MS
            ? now -
              FINAL_REVIEW_BUFFER_MS -
              1
            : now;

        changed =
          true;
      }

      if (
        changed
      ) {
        try {
          window.localStorage.setItem(
            FINAL_SEEN_STORAGE_KEY,
            JSON.stringify(
              next,
            ),
          );
        } catch {
          // Ignore storage failures; in-memory timing still works.
        }

        return next;
      }

      return current;
    });
  }, [allGames, finalEventIds]);

  const finalEventsStillInReview = useMemo(() => {
    const now =
      Date.now();

    return new Set(
      [...finalEventIds]
        .filter(
          (eventId) => {
            const seenAt =
              Number(
                finalSeenAt[
                  eventId
                ],
              );

            return (
              Number.isFinite(
                seenAt,
              ) &&
              now - seenAt <
                FINAL_REVIEW_BUFFER_MS
            );
          },
        ),
    );
  }, [finalEventIds, finalSeenAt, gameStatuses]);

  const displayCombined = useMemo(() => {
    return allCombined.filter((row) => {
      const eventId =
        text(
          row.espn_event_id,
        );

      if (
        viewMode ===
        "SWEAT"
      ) {
        if (
          !isUndecided(
            row,
          )
        ) {
          return false;
        }

        // Once the parent ticket is LOST, its remaining child legs can keep
        // updating for history, but they are no longer a financial sweat.
        if (
          upper(
            row.parent_status,
          ) ===
          "LOST"
        ) {
          return false;
        }

        return (
          !eventId ||
          !finalEventIds.has(
            eventId,
          )
        );
      }

      // All Legs keeps FINAL games only during the 15-minute review buffer.
      if (
        eventId &&
        finalEventIds.has(
          eventId,
        )
      ) {
        return finalEventsStillInReview.has(
          eventId,
        );
      }

      return true;
    });
  }, [
    allCombined,
    viewMode,
    finalEventIds,
    finalEventsStillInReview,
  ]);

  const games = useMemo(() => buildGameGroups(displayCombined, gameStatuses), [displayCombined, gameStatuses]);

  useEffect(() => {
    const cutoff =
      Date.now() -
      7 * 24 * 60 * 60 * 1000;

    setFinalSeenAt((current) => {
      const entries =
        Object.entries(
          current,
        );

      const next =
        Object.fromEntries(
          entries.filter(
            ([, value]) =>
              Number(
                value,
              ) >=
              cutoff,
          ),
        );

      if (
        Object.keys(
          next,
        ).length ===
        entries.length
      ) {
        return current;
      }

      try {
        window.localStorage.setItem(
          FINAL_SEEN_STORAGE_KEY,
          JSON.stringify(
            next,
          ),
        );
      } catch {
        // Ignore storage failures.
      }

      return next;
    });
  }, [gameStatuses]);


  useEffect(() => {
    setOpenGames((current) => {
      const next = { ...current };
      for (const game of games) {
        if (!game.hasUndecided) {
          next[game.key] = false;
        } else if (!(game.key in next)) {
          next[game.key] = game.isLive;
        }
      }
      for (const key of Object.keys(next)) {
        if (!games.some((game) => game.key === key)) delete next[key];
      }
      return next;
    });
  }, [games]);

  const gameStatusRefs = useMemo(() => allGames
    .filter((g) => g.eventId)
    .map((g) => ({ eventId: String(g.eventId), sport: g.sport })), [allGames]);

  useEffect(() => {
    if (!gameStatusRefs.length) { setGameStatuses({}); return; }
    let cancelled = false;
    async function loadGameStatuses() {
      try {
        const params = new URLSearchParams();
        for (const game of gameStatusRefs) params.append("game", `${game.eventId}|${game.sport}`);
        const { data } = await fetchJsonWithRetry(
          `/api/game-status?${params.toString()}`,
          { cache: "no-store" },
          { fallbackMessage: "Unable to load game status" }
        );
        if (!cancelled) {
          const map = {};
          for (const game of data.games || []) if (game?.eventId) map[String(game.eventId)] = game;
          setGameStatuses(map);
        }
      } catch {
        // Keep the last known ESPN state rather than re-exposing FINAL games
        // because of one transient status request failure.
      }
    }
    loadGameStatuses();
    const id = setInterval(loadGameStatuses, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, [gameStatusRefs.map((g) => `${g.eventId}|${g.sport}`).join(",")]);

  const undecidedCount = allCombined.filter((row) => {
    if (!isUndecided(row)) return false;
    if (upper(row.parent_status) === "LOST") return false;
    const eventId = text(row.espn_event_id);
    return !eventId || !finalEventIds.has(eventId);
  }).length;

  const liveGameCount = allGames.filter((g) => {
    const status = g.eventId ? gameStatuses[String(g.eventId)] : null;
    if (isFinalGameStatus(status)) return false;

    // A live game counts only if at least one still-relevant child leg belongs
    // to a parent ticket that has not already lost.
    const hasRelevantSweat = g.rows.some((row) =>
      isUndecided(row) && upper(row.parent_status) !== "LOST"
    );
    if (!hasRelevantSweat) return false;

    return isLiveGameStatus(status) || (!status && g.isLive);
  }).length;
  const betCount = new Set(filteredOccurrences.map((r) => Number(r.bet_row_id)).filter(Number.isFinite)).size;

  function clearFilters() {
    setSearch(""); setSportsbook("ALL"); setSport("ALL"); setGameState("ALL"); setDate("");
  }

  return (
    <>
      <header className="header activeHeader">
        <div><span className="eyebrow">Game-day view · auto-refresh 30 sec</span><h1>Active Legs</h1></div>
        <button className="refreshButton" onClick={load}>↻ Refresh</button>
      </header>

      <section className="gamedaySummaryStrip gamedaySummaryStripCompact">
        <div><span>Games Live</span><strong>{liveGameCount}</strong></div>
        <div><span>Still Sweating</span><strong>{undecidedCount}</strong></div>
      </section>

      <div className="gamedayModeBar" role="group" aria-label="Active legs view">
        <button className={viewMode === "SWEAT" ? "gamedayModeActive" : ""} onClick={() => setViewMode("SWEAT")}>Sweat</button>
        <button className={viewMode === "ALL" ? "gamedayModeActive" : ""} onClick={() => setViewMode("ALL")}>All Legs</button>
        <span>{viewMode === "SWEAT" ? "Undecided action only" : "Final games stay 15 min for review"}</span>
      </div>

      <p className="lastUpdated">{updated ? `Database view updated ${updated.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "Loading database view…"}</p>

      <div className="mobileFilterBar">
        <button className="mobileFilterButton" onClick={() => setFiltersOpen((v) => !v)}>{filtersOpen ? "Hide Filters" : "Filters"}</button>
        {(search || sportsbook !== "ALL" || sport !== "ALL" || gameState !== "ALL" || date) && <button className="textButton" onClick={clearFilters}>Clear</button>}
      </div>

      <section className={`filterPanel ${filtersOpen ? "filtersOpen" : ""}`}>
        <div className="filterTopLine"><h2>Filters</h2><button className="textButton" onClick={clearFilters}>Clear</button></div>
        <div className="legsFilterGrid">
          <label className="searchField">Search<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Player, team, market, bet ID…" /></label>
          <label>Sportsbook<select value={sportsbook} onChange={(e) => setSportsbook(e.target.value)}><option value="ALL">All</option>{sportsbooks.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Sport<select value={sport} onChange={(e) => setSport(e.target.value)}><option value="ALL">All</option>{sports.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>State<select value={gameState} onChange={(e) => setGameState(e.target.value)}><option value="ALL">All</option><option value="LIVE">Live</option><option value="UPCOMING">Upcoming</option><option value="SETTLED">Settled</option></select></label>
          <label>Game Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        </div>
      </section>

      {error && <div className="error">{error}</div>}
      {loading && !rows.length && <div className="skeleton">Loading active legs…</div>}
      {!loading && !error && !displayCombined.length && <div className="empty">{viewMode === "SWEAT" && allCombined.length ? "Nothing left to sweat in the selected games. Switch to All Legs to review settled markets." : "No active non-futures legs match the selected filters."}</div>}

      {!!games.length && <div className="gameViewControls">
        <button className="textButton" onClick={() => setOpenGames(Object.fromEntries(games.map((g) => [g.key, true])))}>Expand all</button>
        <span>•</span>
        <button className="textButton" onClick={() => setOpenGames(Object.fromEntries(games.map((g) => [g.key, false])))}>Collapse all</button>
      </div>}

      <div className="gameGroupList">
        {games.map((group) => <GameGroup key={group.key} group={group} open={Boolean(openGames[group.key])} onToggle={() => setOpenGames((current) => ({ ...current, [group.key]: !current[group.key] }))} gameStatus={gameStatuses[String(group.eventId || "")]} showSettled={viewMode === "ALL"} />)}
      </div>

      {!!allCombined.length && <p className="legsFootnote">{filteredOccurrences.length} leg occurrence{filteredOccurrences.length === 1 ? "" : "s"} combined into {allCombined.length} unique leg{allCombined.length === 1 ? "" : "s"} across {betCount} bet{betCount === 1 ? "" : "s"}.</p>}
    </>
  );
}