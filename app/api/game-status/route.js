import { NextResponse } from "next/server";

function sportPath(sport) {
  const s = String(sport || "").trim().toUpperCase();
  if (["CFB", "NCAAF", "COLLEGE FOOTBALL"].includes(s)) return "college-football";
  return "nfl";
}

function quarterLabel(period) {
  const n = Number(period);
  if (!Number.isFinite(n) || n <= 0) return "";
  if (n <= 4) return `Q${n}`;
  return `OT${n === 5 ? "" : n - 4}`;
}

function parseSummary(data, eventId) {
  const header = data?.header;
  const competition = header?.competitions?.[0];
  const status = competition?.status || header?.competitions?.[0]?.status || {};
  const competitors = competition?.competitors || [];
  const away = competitors.find((c) => c.homeAway === "away") || competitors[0];
  const home = competitors.find((c) => c.homeAway === "home") || competitors[1];

  const teamLabel = (c) => c?.team?.abbreviation || c?.team?.shortDisplayName || c?.team?.displayName || "";
  const score = (c) => c?.score ?? "0";
  const state = String(status?.type?.state || "").toLowerCase();
  const clock = status?.displayClock || "";
  const period = quarterLabel(status?.period);
  const detail = status?.type?.shortDetail || status?.type?.detail || "";
  const scheduledDate = competition?.date || header?.date || data?.gameInfo?.date;
  const startTime = scheduledDate && !Number.isNaN(Date.parse(scheduledDate))
    ? new Date(scheduledDate).toISOString()
    : null;

  let readout = "";
  if (away && home) {
    readout = `${teamLabel(away)} ${score(away)} – ${teamLabel(home)} ${score(home)}`;
  }
  if (state === "in") {
    const timePart = [period, clock].filter(Boolean).join(" ");
    if (timePart) readout += `${readout ? " • " : ""}${timePart}`;
  } else if (state === "post") {
    readout += `${readout ? " • " : ""}FINAL`;
  } else if (detail) {
    readout += `${readout ? " • " : ""}${detail}`;
  }

  return {
    eventId: String(eventId),
    startTime,
    state,
    readout,
    away: away ? { name: teamLabel(away), score: String(score(away)) } : null,
    home: home ? { name: teamLabel(home), score: String(score(home)) } : null,
    period: status?.period ?? null,
    clock,
    detail
  };
}

export async function GET(request) {
  try {
    const url = new URL(request.url);
    const rawGames = url.searchParams.getAll("game").slice(0, 20);
    const games = rawGames.map((raw) => {
      const [eventId, sport = "NFL"] = raw.split("|");
      return { eventId: String(eventId || "").trim(), sport };
    }).filter((g) => /^\d+$/.test(g.eventId));

    const results = await Promise.all(games.map(async ({ eventId, sport }) => {
      try {
        const endpoint = `https://site.api.espn.com/apis/site/v2/sports/football/${sportPath(sport)}/summary?event=${encodeURIComponent(eventId)}`;
        const res = await fetch(endpoint, { next: { revalidate: 10 } });
        if (!res.ok) throw new Error(`ESPN ${res.status}`);
        const data = await res.json();
        return parseSummary(data, eventId);
      } catch (error) {
        return { eventId, error: error.message };
      }
    }));

    return NextResponse.json({ games: results });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
