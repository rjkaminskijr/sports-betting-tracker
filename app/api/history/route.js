import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

const SETTLED = "in.(WON,LOST,PUSH,VOID,VOIDED,CANCELLED,CANCELED,CASHED_OUT)";

function timestampMs(value) {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : ms;
}

function sportPath(sport) {
  const s = String(sport || "").trim().toUpperCase();
  if (["CFB", "NCAAF", "COLLEGE FOOTBALL"].includes(s)) return "college-football";
  return "nfl";
}

async function resolveEventStarts(legs, betMap, missingSettledIds) {
  // This fallback now runs ONLY for legacy rows that do not have bets.settled_at.
  // New/current rows use the authoritative parent settlement timestamp and never
  // need an ESPN lookup just to render History.
  const targets = new Map();

  for (const leg of legs || []) {
    const betId = Number(leg.bet_row_id);
    if (!missingSettledIds.has(betId)) continue;
    if (timestampMs(leg.event_time) !== null) continue;

    const eventId = String(leg.espn_event_id || "").trim();
    if (!/^\d+$/.test(eventId)) continue;

    const bet = betMap.get(betId) || {};
    const sport = String(bet.sport || "NFL");
    const key = `${eventId}|${sportPath(sport)}`;
    if (!targets.has(key)) targets.set(key, { eventId, sport });
  }

  const resolved = new Map();
  await Promise.all([...targets.entries()].map(async ([key, { eventId, sport }]) => {
    try {
      const endpoint = `https://site.api.espn.com/apis/site/v2/sports/football/${sportPath(sport)}/summary?event=${encodeURIComponent(eventId)}`;
      const response = await fetch(endpoint, { next: { revalidate: 86400 } });
      if (!response.ok) return;

      const data = await response.json();
      const competition = data?.header?.competitions?.[0];
      const value = competition?.date || data?.header?.season?.date || null;
      if (timestampMs(value) !== null) resolved.set(key, value);
    } catch {
      // Legacy fallback only. Missing ESPN data must never break History.
    }
  }));

  return resolved;
}

function legacySettlementReference(bet, betLegs, resolvedStarts) {
  const trustworthyLegTimes = [];
  const gameStarts = [];

  for (const leg of betLegs || []) {
    let eventValue = leg.event_time;
    let eventMs = timestampMs(eventValue);

    if (eventMs === null) {
      const eventId = String(leg.espn_event_id || "").trim();
      const key = `${eventId}|${sportPath(bet.sport || "NFL")}`;
      const recovered = resolvedStarts.get(key);
      if (recovered) {
        eventValue = recovered;
        eventMs = timestampMs(recovered);
      }
    }

    if (eventMs !== null) gameStarts.push({ ms: eventMs, value: eventValue });

    // Legacy approximation only: use a leg update when it occurred at/after
    // the recorded game start so import/matching timestamps are not mistaken
    // for settlement timestamps.
    const candidates = [
      leg.settled_at,
      leg.status_updated_at,
      leg.live_updated_at,
      leg.future_updated_at
    ];

    for (const value of candidates) {
      const ms = timestampMs(value);
      if (ms === null) continue;
      if (eventMs !== null && ms >= eventMs) trustworthyLegTimes.push({ ms, value });
    }
  }

  if (trustworthyLegTimes.length) {
    trustworthyLegTimes.sort((a, b) => b.ms - a.ms);
    return { value: trustworthyLegTimes[0].value, source: "LEG_UPDATE_FALLBACK" };
  }

  if (gameStarts.length) {
    gameStarts.sort((a, b) => b.ms - a.ms);
    return { value: gameStarts[0].value, source: "LATEST_GAME_START_FALLBACK" };
  }

  return { value: null, source: "UNAVAILABLE" };
}

function historyWindowReference(bet, settledReference) {
  // Authoritative source first. This is now what controls 2/7/14-day History.
  if (timestampMs(bet.settled_at) !== null) return bet.settled_at;

  // Keep old rows usable if a legacy/import edge case ever lacks settled_at.
  const fallbacks = [
    settledReference,
    bet.updated_at,
    bet.modified_at,
    bet.created_at,
    bet.source_captured_at,
    bet.placed_at
  ];

  for (const value of fallbacks) {
    if (timestampMs(value) !== null) return value;
  }

  return null;
}

export async function GET() {
  try {
    const bets = await supabaseRest("bets", {
      searchParams: {
        select: "*",
        status: SETTLED,
        order: "settled_at.desc.nullslast,id.desc"
      }
    });

    const ids = (bets || []).map((b) => Number(b.id)).filter(Number.isFinite);
    let legs = [];

    if (ids.length) {
      legs = await supabaseRest("bet_legs", {
        searchParams: {
          select: "*",
          bet_row_id: `in.(${ids.join(",")})`,
          order: "bet_row_id.desc,leg_index.asc.nullslast,id.asc"
        }
      });
    }

    const betMap = new Map((bets || []).map((bet) => [Number(bet.id), bet]));
    const missingSettledIds = new Set(
      (bets || [])
        .filter((bet) => timestampMs(bet.settled_at) === null)
        .map((bet) => Number(bet.id))
    );

    const recoveredStarts = missingSettledIds.size
      ? await resolveEventStarts(legs || [], betMap, missingSettledIds)
      : new Map();

    const legsByBet = new Map();
    for (const leg of legs || []) {
      const id = Number(leg.bet_row_id);
      if (!legsByBet.has(id)) legsByBet.set(id, []);
      legsByBet.get(id).push(leg);
    }

    const rows = (bets || []).map((bet) => {
      const betLegs = legsByBet.get(Number(bet.id)) || [];

      let reference;
      if (timestampMs(bet.settled_at) !== null) {
        reference = { value: bet.settled_at, source: "BET_SETTLED_AT" };
      } else {
        reference = legacySettlementReference(bet, betLegs, recoveredStarts);
      }

      return {
        ...bet,
        legs: betLegs,
        settled_reference: reference.value,
        settled_reference_source: reference.source,
        history_window_reference: historyWindowReference(bet, reference.value)
      };
    });

    return NextResponse.json({ rows });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
