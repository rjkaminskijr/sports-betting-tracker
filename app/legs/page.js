"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchJsonWithRetry } from "../../lib/client-api";

const REFRESH_MS = 30000;

const upper = (value) => String(value || "").trim().toUpperCase();
const text = (value) => String(value || "").trim();

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
  const live = upper(row.live_state);
  const status = upper(row.leg_status || row.status);
  if (live === "LIVE" || ["LIVE", "IN_PROGRESS"].includes(status)) return "LIVE";
  return "UPCOMING";
}

function lineText(row) {
  const direction = upper(row.direction);
  const line = row.line_value;
  if (direction && line !== null && line !== undefined && line !== "") return `${direction} ${line}`;
  if (line !== null && line !== undefined && line !== "") return String(line);
  return "";
}

function liveValue(row) {
  const value = row.live_value;
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function uniqueKey(row) {
  return [
    sportOf(row),
    text(row.selection),
    text(row.market),
    String(row.line_value ?? ""),
    upper(row.direction),
    row.espn_event_id ? `event:${row.espn_event_id}` : `game:${gameOf(row)}`,
    upper(row.leg_status),
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
    const betIds = [...new Set(group.map((r) => Number(r.bet_row_id)).filter(Number.isFinite))].sort((a,b) => a-b);
    const sportsbooks = [...new Set(group.map((r) => text(r.parent_sportsbook || r.sportsbook)).filter(Boolean))].sort();
    return {
      ...first,
      count: group.length,
      betIds,
      sportsbooks,
      groupRows: group,
      state: group.some((r) => stateOf(r) === "LIVE") ? "LIVE" : "UPCOMING"
    };
  });
}

function sortCombined(rows) {
  return [...rows].sort((a, b) => {
    const aLive = a.state === "LIVE" ? 0 : 1;
    const bLive = b.state === "LIVE" ? 0 : 1;
    if (aLive !== bLive) return aLive - bLive;
    const at = eventTime(a)?.getTime() ?? Number.POSITIVE_INFINITY;
    const bt = eventTime(b)?.getTime() ?? Number.POSITIVE_INFINITY;
    if (at !== bt) return at - bt;
    return `${sportOf(a)}|${gameOf(a)}|${a.selection || ""}`.localeCompare(`${sportOf(b)}|${gameOf(b)}|${b.selection || ""}`);
  });
}

function buildGameGroups(rows) {
  const map = new Map();

  for (const row of sortCombined(rows)) {
    const eventId = text(row.espn_event_id);
    const fallbackGame = gameOf(row);
    const key = eventId
      ? `${sportOf(row)}||event:${eventId}`
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

    return {
      ...group,
      game: preferredGame || group.game,
      gameLabels: undefined
    };
  });

  return groups.sort((a,b) => {
    if (a.isLive !== b.isLive) return a.isLive ? -1 : 1;
    const at = a.eventTime ? new Date(a.eventTime).getTime() : Number.POSITIVE_INFINITY;
    const bt = b.eventTime ? new Date(b.eventTime).getTime() : Number.POSITIVE_INFINITY;
    if (at !== bt) return at - bt;
    return `${a.sport}|${a.game}`.localeCompare(`${b.sport}|${b.game}`);
  });
}


function friendlyMarketLine(row) {
  const market = text(row.market);
  const direction = upper(row.direction);
  const line = row.line_value;
  if (direction && line !== null && line !== undefined && line !== "") {
    const dir = direction === "OVER" ? "Over" : direction === "UNDER" ? "Under" : direction;
    if (/passing tds?/i.test(market)) return `${dir} ${line} Passing TDs`;
    if (/passing yards?/i.test(market)) return `${dir} ${line} Passing Yards`;
    if (/rushing yards?/i.test(market)) return `${dir} ${line} Rushing Yards`;
    if (/receiving yards?/i.test(market)) return `${dir} ${line} Receiving Yards`;
    if (/receptions?/i.test(market)) return `${dir} ${line} Receptions`;
    if (/total/i.test(market)) return `${dir} ${line} ${market}`;
    return `${dir} ${line}${market ? ` ${market}` : ""}`;
  }
  return market || "Market unavailable";
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

  // Game-total labels used by the sportsbooks in this tracker. Keep this
  // intentionally narrow so player/stat totals do not receive the check.
  return (
    market === "TOTAL" ||
    market === "GAME TOTAL" ||
    market === "TOTAL ALTERNATE" ||
    market === "ALTERNATE TOTAL" ||
    market === "TOTAL POINTS" ||
    market === "GAME TOTAL POINTS"
  );
}

function overThresholdMet(row) {
  if (!isGameOrTeamTotal(row)) return false;

  const direction = upper(row.direction);
  const selection = upper(row.selection);
  const isOver = direction === "OVER" || /^OVER\b/.test(selection);
  if (!isOver) return false;

  let line = Number(row.line_value);
  if (!Number.isFinite(line)) {
    const match = text(row.selection).match(/\bOver\s*\(?([0-9]+(?:\.[0-9]+)?)\)?/i);
    line = match ? Number(match[1]) : NaN;
  }

  const current = Number(row.live_value);
  return Number.isFinite(line) && Number.isFinite(current) && current > line;
}

function urgencyRank(row) {
  const status = upper(row.leg_status || row.status);
  if (row.state === "LIVE" && !["WON","LOST","PUSH","VOID","VOIDED"].includes(status)) return 0;
  if (status === "WON") return 1;
  if (row.state === "LIVE") return 2;
  return 3;
}

function sortGameRows(rows) {
  return [...rows].sort((a,b) => {
    const ar = urgencyRank(a);
    const br = urgencyRank(b);
    if (ar !== br) return ar - br;
    return String(a.selection || "").localeCompare(String(b.selection || ""));
  });
}

function GameGroup({ group, open, onToggle, gameStatus }) {
  const sortedRows = sortGameRows(group.rows);
  return (
    <section className={`gameGroup ${group.isLive ? "liveGameGroup" : ""}`}>
      <button className="gameGroupHeader gameGroupToggle" type="button" onClick={onToggle} aria-expanded={open}>
        <div>
          <div className="gameGroupTitleLine"><span>{group.sport}</span><h2>{group.game}</h2>{group.isLive && <span className="liveBadge">LIVE</span>}</div>
          <small>{formatGameTime(group.eventTime)}</small>{group.isLive && gameStatus?.readout && <div className="gameLiveReadout">{gameStatus.readout}</div>}
        </div>
        <div className="gameGroupHeaderRight">
          <span className="gameLegCount">{group.rows.length} leg{group.rows.length === 1 ? "" : "s"}</span>
          <span className={`gameChevron ${open ? "gameChevronOpen" : ""}`}>⌄</span>
        </div>
      </button>
      {open && <div className="gameLegList">{sortedRows.map((row, index) => <LegCard key={`${uniqueKey(row)}-${index}`} row={row} />)}</div>}
    </section>
  );
}

function LegCard({ row }) {
  const live = row.state === "LIVE";
  return (
    <article className={`gameLegCard ${live ? "gameLegLive" : ""}`}>
      <div className="gameLegMain">
        <div className="gameLegTitleLine">
          <strong>{row.selection || "Unnamed selection"}</strong>
          {row.count > 1 && <span className="countBadge">×{row.count}</span>}
        </div>
        <span>{friendlyMarketLine(row)}</span>
        <small>{row.sportsbooks.join(", ") || "Sportsbook unavailable"}</small>
        {row.count > 1 && (
          <details className="duplicateDetails">
            <summary>Used in {row.count} bets</summary>
            <div>{row.betIds.map((id) => <span key={id}>Bet {id}</span>)}</div>
          </details>
        )}
      </div>
      <div className="gameLegValue">
        {live && <span className="liveBadge">LIVE</span>}
        <div className="liveValueLine">
          {overThresholdMet(row) && <span className="thresholdMet" title="Over threshold reached" aria-label="Over threshold reached">✓</span>}
          <strong>{friendlyLiveValue(row)}</strong>
        </div>
      </div>
    </article>
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
  const [openGames, setOpenGames] = useState({});
  const [gameStatuses, setGameStatuses] = useState({});

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
        const haystack = [row.selection,row.market,row.event_team_a,row.event_team_b,row.parent_event_name,row.bet_row_id,book,sportOf(row)].join(" ").toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [rows, search, sportsbook, sport, gameState, date]);

  const combined = useMemo(() => sortCombined(combineRows(filteredOccurrences)), [filteredOccurrences]);
  const games = useMemo(() => buildGameGroups(combined), [combined]);

  useEffect(() => {
    setOpenGames((current) => {
      const next = { ...current };
      for (const game of games) {
        if (!(game.key in next)) next[game.key] = game.isLive;
      }
      for (const key of Object.keys(next)) {
        if (!games.some((game) => game.key === key)) delete next[key];
      }
      return next;
    });
  }, [games]);

  const liveGameRefs = useMemo(() => games.filter((g) => g.isLive && g.eventId).map((g) => ({ eventId: String(g.eventId), sport: g.sport })), [games]);

  useEffect(() => {
    if (!liveGameRefs.length) { setGameStatuses({}); return; }
    let cancelled = false;
    async function loadGameStatuses() {
      try {
        const params = new URLSearchParams();
        for (const game of liveGameRefs) params.append("game", `${game.eventId}|${game.sport}`);
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
        if (!cancelled) setGameStatuses({});
      }
    }
    loadGameStatuses();
    const id = setInterval(loadGameStatuses, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, [liveGameRefs.map((g) => `${g.eventId}|${g.sport}`).join(",")]);

  const liveCount = combined.filter((r) => r.state === "LIVE").length;
  const upcomingCount = combined.length - liveCount;
  const betCount = new Set(filteredOccurrences.map((r) => Number(r.bet_row_id)).filter(Number.isFinite)).size;

  function clearFilters() {
    setSearch(""); setSportsbook("ALL"); setSport("ALL"); setGameState("ALL"); setDate("");
  }

  return (
    <>
      <header className="header activeHeader">
        <div><span className="eyebrow">Game bets only · auto-refresh 30 sec</span><h1>Active Legs</h1></div>
        <button className="refreshButton" onClick={load}>↻ Refresh</button>
      </header>

      <section className="legsSummaryGrid">
        <div><span>Unique Legs</span><strong>{combined.length}</strong></div>
        <div><span>Live</span><strong>{liveCount}</strong></div>
        <div><span>Upcoming</span><strong>{upcomingCount}</strong></div>
        <div><span>Active Bets</span><strong>{betCount}</strong></div>
      </section>

      <p className="lastUpdated">{updated ? `Database view updated ${updated.toLocaleTimeString([], {hour:"numeric",minute:"2-digit"})}` : "Loading database view…"}</p>

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
          <label>State<select value={gameState} onChange={(e) => setGameState(e.target.value)}><option value="ALL">All</option><option value="LIVE">Live</option><option value="UPCOMING">Upcoming</option></select></label>
          <label>Game Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        </div>
      </section>

      {error && <div className="error">{error}</div>}
      {loading && !rows.length && <div className="skeleton">Loading active legs…</div>}
      {!loading && !error && !combined.length && <div className="empty">No active non-futures legs match the selected filters.</div>}

      {!!games.length && <div className="gameViewControls">
        <button className="textButton" onClick={() => setOpenGames(Object.fromEntries(games.map((g) => [g.key, true])))}>Expand all</button>
        <span>•</span>
        <button className="textButton" onClick={() => setOpenGames(Object.fromEntries(games.map((g) => [g.key, false])))}>Collapse all</button>
      </div>}

      <div className="gameGroupList">
        {games.map((group) => <GameGroup key={group.key} group={group} open={Boolean(openGames[group.key])} onToggle={() => setOpenGames((current) => ({ ...current, [group.key]: !current[group.key] }))} gameStatus={gameStatuses[String(group.eventId || "")]} />)}
      </div>

      {!!combined.length && <p className="legsFootnote">{filteredOccurrences.length} active leg occurrence{filteredOccurrences.length === 1 ? "" : "s"} combined into {combined.length} unique leg{combined.length === 1 ? "" : "s"} across {betCount} bet{betCount === 1 ? "" : "s"}.</p>}
    </>
  );
}
