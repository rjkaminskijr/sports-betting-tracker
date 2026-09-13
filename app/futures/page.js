"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchJsonWithRetry } from "../../lib/client-api";

const money = (value) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(value || 0));
const number = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

function formatMarket(market) {
  return String(market || "Season Prop")
    .replace(/^regular season\s+/i, "")
    .replace(/\btds\b/gi, "TDs")
    .replace(/\btd\b/gi, "TD");
}

function stateTone(state) {
  const value = String(state || "").toUpperCase();
  if (["WON", "OVER", "HIT"].includes(value)) return "futureStateGood";
  if (["LOST", "UNDER", "MISSED"].includes(value)) return "futureStateBad";
  if (["LIVE", "TRACKING", "ACTIVE"].includes(value)) return "futureStateLive";
  return "futureStateNeutral";
}

function displayState(row) {
  const games = number(row.future_games_played) || 0;
  const current = number(row.future_current);
  const line = number(row.line_value);
  const pace = number(row.future_pace);
  const raw = String(row.future_state || "").toUpperCase();

  if (["WON", "LOST", "HIT", "MISSED"].includes(raw)) return raw;
  if (games <= 0) return "NOT STARTED";
  if (pace !== null && line !== null) {
    return pace >= line ? "ON PACE" : "BELOW PACE";
  }
  if (current !== null) return "TRACKING";
  return raw || "TRACKING";
}

function FutureRow({ row }) {
  const current = number(row.future_current);
  const line = number(row.line_value);
  const pace = number(row.future_pace);
  const games = number(row.future_games_played);
  const direction = String(row.direction || "OVER").toUpperCase();
  const progress = current !== null && line && line > 0 ? Math.max(0, Math.min(100, (current / line) * 100)) : null;
  const season = row.future_season_year || row.espn_season_year || "—";
  const state = displayState(row);
  const bet = row.bet || {};

  return (
    <div className="futurePlayerLeg">
      <div className="futurePlayerLegHead">
        <div className="futurePlayerLegTitle">
          <strong>{direction} {line ?? "—"} {formatMarket(row.market)}</strong>
          <span className={`futureState ${stateTone(state)}`}>{state}</span>
          <small>{season} regular season · Bet {row.bet_row_id}{bet.sportsbook ? ` · ${bet.sportsbook}` : ""}</small>
        </div>
        <div className="futureParentMoney">
          <small className="futureTicketLabel">Ticket</small>
          <strong>{money(bet.stake)}</strong>
          <small>{bet.current_odds ? `${Number(bet.current_odds) > 0 ? "+" : ""}${bet.current_odds}` : ""}</small>
          {bet.to_pay !== null && bet.to_pay !== undefined ? <span>Pays {money(bet.to_pay)}</span> : null}
        </div>
      </div>

      <div className="futureStatsGrid compact">
        <div><span>Current</span><strong>{current ?? "—"}</strong></div>
        <div><span>Games</span><strong>{games ?? "—"}</strong></div>
        <div><span>17-Game Pace</span><strong>{pace !== null ? pace.toFixed(1) : "—"}</strong></div>
        <div><span>Line</span><strong>{line ?? "—"}</strong></div>
      </div>

      {progress !== null && games > 0 ? (
        <div className="futureProgressWrap">
          <div className="futureProgressLabel"><span>Progress to line</span><strong>{progress.toFixed(0)}%</strong></div>
          <div className="futureProgress"><span style={{ width: `${progress}%` }} /></div>
        </div>
      ) : null}

      <div className="futureFooter">
        <span>ESPN athlete: {row.espn_athlete_id || "not matched"}</span>
        <span>{row.future_updated_at ? `Updated ${new Date(row.future_updated_at).toLocaleString()}` : "Not refreshed yet"}</span>
      </div>
    </div>
  );
}

function PlayerGroup({ player, rows, open, onToggle }) {
  const bestUpdated = rows
    .map((r) => r.future_updated_at ? new Date(r.future_updated_at).getTime() : 0)
    .reduce((a, b) => Math.max(a, b), 0);
  const games = Math.max(...rows.map((r) => number(r.future_games_played) || 0));

  const stateCounts = rows.reduce((acc, row) => {
    const state = displayState(row);
    acc[state] = (acc[state] || 0) + 1;
    return acc;
  }, {});

  const futureLabel = `${rows.length} future${rows.length === 1 ? "" : "s"}`;
  let seasonSummary = "Season not started";

  if (games > 0) {
    const parts = [`${games} game${games === 1 ? "" : "s"} played`];
    if (stateCounts["ON PACE"]) parts.push(`${stateCounts["ON PACE"]} on pace`);
    if (stateCounts["BELOW PACE"]) parts.push(`${stateCounts["BELOW PACE"]} below pace`);
    if (stateCounts.WON || stateCounts.HIT) parts.push(`${(stateCounts.WON || 0) + (stateCounts.HIT || 0)} won`);
    if (stateCounts.LOST || stateCounts.MISSED) parts.push(`${(stateCounts.LOST || 0) + (stateCounts.MISSED || 0)} lost`);
    if (stateCounts.TRACKING) parts.push(`${stateCounts.TRACKING} tracking`);
    seasonSummary = parts.join(" · ");
  }

  return (
    <article className={`futurePlayerGroup ${open ? "open" : ""}`}>
      <button type="button" className="futurePlayerHeader" onClick={onToggle} aria-expanded={open}>
        <div className="futurePlayerNameBlock">
          <h2>{player}</h2>
          <div className="futurePlayerMeta">
            <span>{futureLabel}</span>
            <span>{seasonSummary}</span>
          </div>
        </div>
        <div className="futurePlayerHeaderRight">
          {bestUpdated ? <small>Updated {new Date(bestUpdated).toLocaleString()}</small> : null}
          <span className="futureChevron">{open ? "⌃" : "⌄"}</span>
        </div>
      </button>
      {open ? <div className="futurePlayerBody">{rows.map((row) => <FutureRow row={row} key={row.id} />)}</div> : null}
    </article>
  );
}

export default function FuturesPage() {
  const [tracked, setTracked] = useState([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [openPlayers, setOpenPlayers] = useState({});
  const [search, setSearch] = useState("");
  const [sportsbookFilter, setSportsbookFilter] = useState("ALL");
  const [statusFilter, setStatusFilter] = useState("ALL");

  async function load() {
    setError("");
    try {
      const { data } = await fetchJsonWithRetry(
        "/api/season-futures",
        { cache: "no-store" },
        { fallbackMessage: "Could not load season futures." }
      );
      setTracked(data.tracked || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const summary = useMemo(() => {
    const betMap = new Map();
    for (const row of tracked) {
      if (row.bet) betMap.set(Number(row.bet.id), row.bet);
    }
    const bets = [...betMap.values()];
    const activeStatuses = new Set(["PENDING", "OPEN", "LIVE", "IN_PROGRESS"]);
    const active = bets.filter((b) => activeStatuses.has(String(b.status || "").toUpperCase()));
    return {
      futureLegs: tracked.length,
      parentBets: bets.length,
      exposure: active.reduce((s, b) => s + Number(b.stake || 0), 0),
      potential: active.reduce((s, b) => s + Number(b.to_pay || 0), 0)
    };
  }, [tracked]);

  const groups = useMemo(() => {
    const map = new Map();
    for (const row of tracked) {
      const player = String(row.selection || "Unknown Player").trim() || "Unknown Player";
      if (!map.has(player)) map.set(player, []);
      map.get(player).push(row);
    }
    return [...map.entries()]
      .map(([player, rows]) => ({ player, rows: [...rows].sort((a, b) => {
        const aMarket = `${a.market || ""} ${a.line_value || ""}`;
        const bMarket = `${b.market || ""} ${b.line_value || ""}`;
        return aMarket.localeCompare(bMarket);
      }) }))
      .sort((a, b) => a.player.localeCompare(b.player));
  }, [tracked]);

  const sportsbooks = useMemo(() => {
    return [...new Set(tracked.map((row) => String(row.bet?.sportsbook || "").trim()).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b));
  }, [tracked]);

  const filteredGroups = useMemo(() => {
    const query = search.trim().toLowerCase();
    return groups
      .map(({ player, rows }) => {
        const matchingRows = rows.filter((row) => {
          const state = displayState(row);
          const sportsbook = String(row.bet?.sportsbook || "");
          const haystack = [player, sportsbook, row.market, state, row.bet_row_id]
            .map((value) => String(value || "").toLowerCase())
            .join(" ");
          if (query && !haystack.includes(query)) return false;
          if (sportsbookFilter !== "ALL" && sportsbook !== sportsbookFilter) return false;
          if (statusFilter !== "ALL" && state !== statusFilter) return false;
          return true;
        });
        return { player, rows: matchingRows };
      })
      .filter((group) => group.rows.length);
  }, [groups, search, sportsbookFilter, statusFilter]);

  const statusOptions = useMemo(() => {
    return [...new Set(tracked.map((row) => displayState(row)))].sort((a, b) => a.localeCompare(b));
  }, [tracked]);

  useEffect(() => {
    if (!groups.length) return;
    setOpenPlayers((prev) => {
      if (Object.keys(prev).length) return prev;
      // Keep the page compact by default, but open the first player so the layout is obvious.
      return { [groups[0].player]: true };
    });
  }, [groups]);

  function togglePlayer(player) {
    setOpenPlayers((prev) => ({ ...prev, [player]: !prev[player] }));
  }

  function setAllPlayers(value) {
    const next = {};
    for (const group of filteredGroups) next[group.player] = value;
    setOpenPlayers(next);
  }

  async function refreshStats() {
    setRefreshing(true); setMessage(""); setError("");
    try {
      const res = await fetch("/api/season-futures/refresh", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Season refresh failed.");
      setMessage(`Refreshed ${data.successful || 0} season-future leg(s)${data.failed ? ` · ${data.failed} failed` : ""}.`);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <>
      <header className="header futuresHeader">
        <div>
          <span className="eyebrow">NFL season-long tracking</span>
          <h1>Season Futures</h1>
        </div>
        <button onClick={refreshStats} disabled={refreshing || !tracked.length}>{refreshing ? "Refreshing…" : "↻ Refresh Stats"}</button>
      </header>

      <section className="futuresSummaryGrid">
        <div><span>Future Legs</span><strong>{summary.futureLegs}</strong></div>
        <div><span>Future Bets</span><strong>{summary.parentBets}</strong></div>
        <div><span>Exposure</span><strong>{money(summary.exposure)}</strong></div>
        <div><span>Potential Return</span><strong>{money(summary.potential)}</strong></div>
      </section>

      {message ? <div className="successBox">{message}</div> : null}
      {error ? <div className="error">{error}</div> : null}

      <section className="futureFilters" aria-label="Future filters">
        <label className="futureSearchField">
          <span>Search</span>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Player, market, bet ID…"
          />
        </label>
        <label>
          <span>Sportsbook</span>
          <select value={sportsbookFilter} onChange={(e) => setSportsbookFilter(e.target.value)}>
            <option value="ALL">All sportsbooks</option>
            {sportsbooks.map((sportsbook) => <option key={sportsbook} value={sportsbook}>{sportsbook}</option>)}
          </select>
        </label>
        <label>
          <span>Status</span>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="ALL">All statuses</option>
            {statusOptions.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        {(search || sportsbookFilter !== "ALL" || statusFilter !== "ALL") ? (
          <button
            type="button"
            className="futureClearFilters"
            onClick={() => { setSearch(""); setSportsbookFilter("ALL"); setStatusFilter("ALL"); }}
          >
            Clear
          </button>
        ) : null}
      </section>

      <div className="futureSectionHeader playerGroupedHeader">
        <div>
          <h2>Players</h2>
          <small>{filteredGroups.length} of {groups.length} player{groups.length === 1 ? "" : "s"} · {filteredGroups.reduce((sum, group) => sum + group.rows.length, 0)} future leg{filteredGroups.reduce((sum, group) => sum + group.rows.length, 0) === 1 ? "" : "s"}</small>
        </div>
        <div className="futureExpandControls">
          <button type="button" onClick={() => setAllPlayers(true)}>Expand all</button>
          <button type="button" onClick={() => setAllPlayers(false)}>Collapse all</button>
        </div>
      </div>

      {loading ? <div className="skeleton">Loading season futures…</div> : null}
      {!loading && !tracked.length ? <div className="empty">No season futures are currently being tracked.</div> : null}
      <section className="futurePlayerList">
        {filteredGroups.map(({ player, rows }) => (
          <PlayerGroup key={player} player={player} rows={rows} open={!!openPlayers[player]} onToggle={() => togglePlayer(player)} />
        ))}
      </section>
      {!loading && tracked.length && !filteredGroups.length ? <div className="empty">No futures match those filters.</div> : null}

      <p className="legsFootnote">Pace = current stat ÷ games played × 17. Official sportsbook settlement rules still control voids, injuries, pushes, and special minimum-game conditions.</p>
    </>
  );
}
