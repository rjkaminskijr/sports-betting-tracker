"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchJsonWithRetry } from "../../lib/client-api";

const ACTIVE_STATUSES = new Set(["PENDING", "OPEN", "LIVE", "IN_PROGRESS"]);
const SETTLED_STATUSES = new Set(["WON", "LOST", "PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED", "CASHED_OUT"]);
const NEUTRAL_STATUSES = new Set(["PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED"]);

const numberOrZero = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

const money = (value) => new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD"
}).format(numberOrZero(value));

function odds(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return `${n >= 0 ? "+" : ""}${Math.trunc(n)}`;
}

function statusOf(row) {
  return String(row?.status || "PENDING").trim().toUpperCase();
}

function isBonusBet(bet) {
  const promo = String(bet?.promo || "").trim().toUpperCase();
  return ["BONUS BET", "FREE BET", "FREEBET"].some((marker) => promo.includes(marker));
}

function cashAtRisk(bet) {
  return isBonusBet(bet) ? 0 : numberOrZero(bet?.stake);
}

function profitLoss(bet) {
  const status = statusOf(bet);
  const paidPresent = bet?.paid !== null && bet?.paid !== undefined && bet?.paid !== "";
  const risk = cashAtRisk(bet);
  if (status === "LOST") return -risk;
  if (NEUTRAL_STATUSES.has(status)) return 0;
  if (SETTLED_STATUSES.has(status) && paidPresent) return numberOrZero(bet.paid) - risk;
  return null;
}

function displaySport(bet) {
  const sport = String(bet?.sport || "").trim();
  if (sport) {
    const upper = sport.toUpperCase();
    if (["NCAAF", "CFB", "COLLEGE FOOTBALL"].includes(upper)) return "CFB";
    return upper;
  }
  if ((bet?.legs || []).some((leg) => leg.espn_athlete_id)) return "NFL";
  return "Football";
}

function cleanMarket(value) {
  const v = String(value || "").trim();
  return v || "Bet";
}

function displayBetType(bet) {
  const type = String(bet?.bet_type || "").trim().toUpperCase();
  if (bet?.round_robin_size != null || bet?.round_robin_combinations != null || type.includes("ROUND ROBIN")) return "Round Robin";
  const aliases = {
    STRAIGHT: "Straight",
    SINGLE: "Straight",
    PARLAY: "Parlay",
    SGP: "SGP",
    SGPX: "SGPx",
    TEASER: "Teaser",
    "FUTURES STRAIGHT": "Futures Straight",
    "FUTURES PARLAY": "Futures Parlay"
  };
  return aliases[type] || (type ? type.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "Other");
}

function betDescription(bet) {
  const sport = displaySport(bet);
  const legs = bet.legs || [];
  const markets = [...new Set(legs.map((leg) => cleanMarket(leg.market)).filter(Boolean))];
  const type = String(bet?.bet_type || "").trim().toUpperCase();

  if (type.includes("TEASER")) {
    const source = `${bet.headline || ""} ${bet.subtitle || ""}`;
    const match = source.match(/(\d+(?:\.\d+)?)\s*[- ]?Point\s+Teaser/i);
    return `${sport} ${match ? `${match[1]}-Point Teaser` : "Teaser"}`;
  }
  if (type.includes("SGPX")) return `${sport} SGPx ${legs.length}-Pick Parlay`;
  if (bet?.round_robin_size || bet?.round_robin_combinations || type.includes("ROUND ROBIN")) {
    return markets.length === 1 ? `${sport} ${markets[0]} Round Robin` : `${sport} Round Robin`;
  }
  if (legs.length === 1) return `${sport} ${markets[0] || "Straight Bet"}`;
  if (markets.length === 1 && legs.length > 1) return `${sport} ${markets[0]} Parlay`;
  if (type.includes("SGP") || type.includes("SAME GAME")) return `${sport} Same Game Parlay`;
  if (legs.length > 1) return `${sport} ${legs.length}-Leg Parlay`;
  return bet.headline || `${sport} Bet`;
}

function parlayProgress(legs = []) {
  let won = 0, lost = 0, neutral = 0, live = 0, pending = 0;
  for (const leg of legs) {
    const status = statusOf(leg);
    const liveState = String(leg.live_state || "").trim().toUpperCase();
    if (status === "WON") won += 1;
    else if (status === "LOST") lost += 1;
    else if (NEUTRAL_STATUSES.has(status)) neutral += 1;
    else if (!SETTLED_STATUSES.has(status) && liveState === "LIVE") live += 1;
    else pending += 1;
  }
  const parts = [`✓ ${won}/${legs.length}`];
  if (lost) parts.push(`✕ ${lost}`);
  if (neutral) parts.push(`↔ ${neutral}`);
  if (live) parts.push(`● ${live} live`);
  if (pending) parts.push(`${pending} pending`);
  return parts.join(" · ");
}

function gameDetail(leg) {
  const a = String(leg.event_team_a || "").trim();
  const b = String(leg.event_team_b || "").trim();
  if (a && b) return `${a} @ ${b}`;
  return a || b || String(leg.event_name || "").trim() || "—";
}

function formatDate(value) {
  if (!value) return "Placed time unavailable";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit"
  }).format(d);
}

function isoDate(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}


function parseEventTime(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatGameTime(value) {
  const d = parseEventTime(value);
  if (!d) return "Time TBD";
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(d);
}

function betGameSummary(bet) {
  const legs = bet.legs || [];
  const games = [];
  for (const leg of legs) {
    const game = gameDetail(leg);
    if (game && game !== "—" && !games.includes(game)) games.push(game);
  }

  const timedLegs = legs
    .map((leg) => ({ leg, time: parseEventTime(leg.event_time) }))
    .filter((x) => x.time)
    .sort((a, b) => a.time - b.time);

  const anyLive = legs.some((leg) => String(leg.live_state || "").trim().toUpperCase() === "LIVE");
  const next = timedLegs[0] || null;

  if (games.length === 1) {
    return {
      label: games[0],
      timeLabel: next ? formatGameTime(next.leg.event_time) : "Time TBD",
      anyLive,
      sortTime: next?.time?.getTime() ?? Number.POSITIVE_INFINITY
    };
  }

  if (games.length > 1) {
    return {
      label: `${games.length} games${next ? ` · Next: ${gameDetail(next.leg)}` : ""}`,
      timeLabel: next ? formatGameTime(next.leg.event_time) : "Times TBD",
      anyLive,
      sortTime: next?.time?.getTime() ?? Number.POSITIVE_INFINITY
    };
  }

  return {
    label: String(bet.event_name || "").trim() || "Game details unavailable",
    timeLabel: next ? formatGameTime(next.leg.event_time) : "Time TBD",
    anyLive,
    sortTime: next?.time?.getTime() ?? Number.POSITIVE_INFINITY
  };
}

function legStatusVisual(leg) {
  const status = statusOf(leg);
  const liveState = String(leg.live_state || "").trim().toUpperCase();
  if (status === "WON") return { icon: "✓", label: "WON", tone: "won" };
  if (status === "LOST") return { icon: "✕", label: "LOST", tone: "lost" };
  if (NEUTRAL_STATUSES.has(status)) return { icon: "↔", label: status, tone: "neutral" };
  if (liveState === "LIVE" || ["LIVE", "IN_PROGRESS"].includes(status)) return { icon: "●", label: "LIVE", tone: "live" };
  return { icon: "○", label: "PENDING", tone: "pending" };
}

function readableProgress(legs = []) {
  let won = 0, lost = 0, neutral = 0, live = 0, pending = 0;
  for (const leg of legs) {
    const v = legStatusVisual(leg);
    if (v.tone === "won") won += 1;
    else if (v.tone === "lost") lost += 1;
    else if (v.tone === "neutral") neutral += 1;
    else if (v.tone === "live") live += 1;
    else pending += 1;
  }
  const parts = [];
  if (won) parts.push(`${won} Won`);
  if (lost) parts.push(`${lost} Lost`);
  if (live) parts.push(`${live} Live`);
  if (pending) parts.push(`${pending} Pending`);
  if (neutral) parts.push(`${neutral} Void/Push`);
  return parts.length ? parts.join(" · ") : `${legs.length} leg${legs.length === 1 ? "" : "s"}`;
}

function qualityIssues(bet) {
  const issues = [];
  const legs = bet.legs || [];
  if (!String(bet.sportsbook || "").trim()) issues.push("Sportsbook missing");
  if (displaySport(bet) === "Football") issues.push("Sport missing");
  if (bet.stake == null) issues.push("Wager missing");
  if (bet.current_odds == null && bet.original_odds == null && bet.boosted_odds == null) issues.push("Odds missing");
  if (bet.to_pay == null && bet.cash_out == null) issues.push("Potential payout missing");
  if (bet.leg_count != null && Number(bet.leg_count) !== legs.length) issues.push(`Leg count says ${bet.leg_count}, stored ${legs.length}`);
  return issues;
}

function StatusPill({ status }) {
  const normalized = String(status || "PENDING").toUpperCase();
  return <span className={`statusPill status-${normalized.toLowerCase().replaceAll("_", "-")}`}>{normalized}</span>;
}

function BetCard({ bet, onCashOut }) {
  const legs = bet.legs || [];
  const displayedOdds = bet.current_odds ?? bet.original_odds ?? bet.boosted_odds;
  const pnl = profitLoss(bet);
  const issues = qualityIssues(bet);
  const description = betDescription(bet);
  const game = betGameSummary(bet);
  const [cashOutOpen, setCashOutOpen] = useState(false);
  const [cashOutAmount, setCashOutAmount] = useState("");
  const [cashOutBusy, setCashOutBusy] = useState(false);
  const [cashOutError, setCashOutError] = useState("");

  async function submitCashOut() {
    const amount = Number(cashOutAmount);
    if (!Number.isFinite(amount) || amount < 0) {
      setCashOutError("Enter the actual amount returned by the sportsbook.");
      return;
    }

    const confirmed = window.confirm(
      `Mark Bet ${bet.id} as CASHED_OUT with ${money(amount)} returned?`
    );
    if (!confirmed) return;

    setCashOutBusy(true);
    setCashOutError("");
    try {
      await onCashOut?.(bet, amount);
    } catch (error) {
      setCashOutError(error?.message || "Unable to save cash out.");
      setCashOutBusy(false);
    }
  }

  return (
    <details className={`betCard activeBetCard${game.anyLive ? " hasLiveBet" : ""}`}>
      <summary>
        <div className="betSummaryMain">
          <div className="betTitleLine">
            {issues.length > 0 && <span className="warningIcon" title={issues.join(" • ")}>⚠</span>}
            <strong>{description}</strong>
            {game.anyLive && <span className="liveBadge">LIVE</span>}
          </div>
          <div className="gameSummaryLine">
            <span>{game.label}</span>
            <small>{game.timeLabel}</small>
          </div>
          <div className="betSummaryMeta">
            <StatusPill status={bet.status} />
            {legs.length > 1 && <span>{readableProgress(legs)}</span>}
          </div>
          <small>{bet.sportsbook || "Sportsbook"} · {displaySport(bet)} · {legs.length} leg{legs.length === 1 ? "" : "s"}</small>
        </div>
        <div className="amount betSummaryAmount">
          <strong>{money(bet.stake)}</strong>
          <small>{odds(displayedOdds)}</small>
          <small className="payoutLine">Pays {bet.to_pay == null ? "—" : money(bet.to_pay)}</small>
          <span className="expandHint">⌄</span>
        </div>
      </summary>

      <div className="betBody activeBetBody">
        <div className="betMetricGrid">
          <div><span>Wager</span><strong>{money(bet.stake)}</strong></div>
          <div><span>Odds</span><strong>{odds(displayedOdds)}</strong></div>
          <div><span>To Pay</span><strong>{money(bet.to_pay)}</strong></div>
          <div><span>Paid</span><strong>{bet.paid == null ? "—" : money(bet.paid)}</strong></div>
          <div><span>P/L</span><strong>{pnl == null ? "—" : money(pnl)}</strong></div>
        </div>

        <p className="betCaption">{bet.sportsbook || ""} · {displaySport(bet)} · {legs.length} leg{legs.length === 1 ? "" : "s"} · {formatDate(bet.placed_at || bet.source_captured_at)}</p>

        {issues.length > 0 && (
          <div className="issueBox"><strong>Review:</strong> {issues.join(" • ")}</div>
        )}

        <div className="sectionLabel">Legs</div>
        {legs.length ? (
          <div className="legList sportsbookLegList">
            {legs.map((leg) => {
              const visual = legStatusVisual(leg);
              return (
                <div className={`activeLegRow legTone-${visual.tone}`} key={leg.id}>
                  <div className={`legStatusIcon legStatusIcon-${visual.tone}`} title={visual.label}>{visual.icon}</div>
                  <div className="activeLegMain">
                    <strong>{leg.selection || "Selection unavailable"}</strong>
                    <small>{gameDetail(leg)}{leg.event_time ? ` · ${formatGameTime(leg.event_time)}` : ""}</small>
                    <span>{leg.market || "Market unavailable"}{leg.line_value != null ? ` · ${leg.line_value}` : ""}</span>
                  </div>
                  <div className="activeLegState">
                    <span className={`legStateText legState-${visual.tone}`}>{visual.label}</span>
                    {leg.live_value != null && leg.live_value !== "" && <strong>{String(leg.live_value)}</strong>}
                    {legs.length > 1 && leg.odds != null && <small>{odds(leg.odds)}</small>}
                  </div>
                </div>
              );
            })}
          </div>
        ) : <div className="emptyInline">No legs stored for this bet.</div>}

        <div className="cashOutSection">
          {!cashOutOpen ? (
            <button className="cashOutButton" type="button" onClick={() => setCashOutOpen(true)}>Cash Out</button>
          ) : (
            <div className="cashOutPanel">
              <div>
                <strong>Cash Out</strong>
                <small>Enter the actual amount returned by the sportsbook.</small>
              </div>
              <label>
                Returned
                <input
                  inputMode="decimal"
                  type="number"
                  min="0"
                  step="0.01"
                  placeholder="0.00"
                  value={cashOutAmount}
                  onChange={(event) => setCashOutAmount(event.target.value)}
                  disabled={cashOutBusy}
                />
              </label>
              <div className="cashOutActions">
                <button className="textButton" type="button" onClick={() => { setCashOutOpen(false); setCashOutError(""); }} disabled={cashOutBusy}>Cancel</button>
                <button className="cashOutConfirmButton" type="button" onClick={submitCashOut} disabled={cashOutBusy}>
                  {cashOutBusy ? "Saving…" : "Confirm Cash Out"}
                </button>
              </div>
              {cashOutError && <div className="cashOutError">{cashOutError}</div>}
            </div>
          )}
        </div>

        <details className="innerDetails">
          <summary>Bet details</summary>
          <div className="detailsGrid">
            <span>Bet ID</span><strong>{bet.id}</strong>
            <span>Sportsbook Bet ID</span><strong>{bet.sportsbook_bet_id || "—"}</strong>
            <span>Bet Type</span><strong>{displayBetType(bet)}</strong>
            <span>Status</span><strong>{statusOf(bet)}</strong>
            <span>Sport</span><strong>{displaySport(bet)}</strong>
            <span>Leg Count</span><strong>{bet.leg_count ?? legs.length}</strong>
            <span>Placed At</span><strong>{formatDate(bet.placed_at)}</strong>
            <span>Screenshot Captured</span><strong>{formatDate(bet.source_captured_at)}</strong>
            {bet.promo && <><span>Promo</span><strong>{bet.promo}</strong></>}
          </div>
        </details>
      </div>
    </details>
  );
}

export default function BetsPage() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);
  const [notice, setNotice] = useState("");

  const [search, setSearch] = useState("");
  const [sportsbook, setSportsbook] = useState("");
  const [sport, setSport] = useState("");
  const [betType, setBetType] = useState("");
  const [status, setStatus] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);

  async function load({ initial = false } = {}) {
    setError("");
    initial ? setLoading(true) : setRefreshing(true);
    try {
      const { data } = await fetchJsonWithRetry(
        "/api/active-bets",
        { cache: "no-store" },
        { fallbackMessage: "Unable to load active bets" }
      );
      setRows(data.rows || []);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => { load({ initial: true }); }, []);

  async function cashOutBet(bet, amount) {
    setError("");
    setNotice("");
    const response = await fetch("/api/active-bets/cash-out", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ betId: Number(bet.id), paid: amount })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Unable to save cash out");

    setRows((current) => current.filter((row) => Number(row.id) !== Number(bet.id)));
    setNotice(`Bet ${bet.id} cashed out for ${money(amount)}. It has moved to History.`);
    setLastUpdated(new Date());
  }

  const options = useMemo(() => ({
    sportsbooks: [...new Set(rows.map((b) => b.sportsbook).filter(Boolean))].sort(),
    sports: [...new Set(rows.map(displaySport))].sort(),
    betTypes: [...new Set(rows.map(displayBetType))].sort(),
    statuses: [...new Set(rows.map(statusOf))].sort()
  }), [rows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matches = rows.filter((bet) => {
      if (sportsbook && bet.sportsbook !== sportsbook) return false;
      if (sport && displaySport(bet) !== sport) return false;
      if (betType && displayBetType(bet) !== betType) return false;
      if (status && statusOf(bet) !== status) return false;

      const date = isoDate(bet.placed_at || bet.source_captured_at || bet.created_at);
      if (fromDate && date && date < fromDate) return false;
      if (toDate && date && date > toDate) return false;

      if (q) {
        const searchable = [
          bet.id, bet.sportsbook, bet.sportsbook_bet_id, bet.bet_type, bet.status,
          bet.sport, bet.headline, bet.subtitle, bet.event_name, bet.promo,
          ...(bet.legs || []).flatMap((leg) => [
            leg.id, leg.selection, leg.market, leg.event_team_a, leg.event_team_b,
            leg.event_name, leg.live_value, leg.status
          ])
        ].filter((x) => x !== null && x !== undefined).join(" ").toLowerCase();
        if (!searchable.includes(q)) return false;
      }
      return true;
    });

    return matches.sort((a, b) => {
      const ga = betGameSummary(a);
      const gb = betGameSummary(b);
      if (ga.anyLive !== gb.anyLive) return ga.anyLive ? -1 : 1;
      if (ga.sortTime !== gb.sortTime) return ga.sortTime - gb.sortTime;
      return Number(b.id || 0) - Number(a.id || 0);
    });
  }, [rows, search, sportsbook, sport, betType, status, fromDate, toDate]);

  const exposure = filtered.reduce((sum, bet) => sum + cashAtRisk(bet), 0);
  const potential = filtered.reduce((sum, bet) => sum + numberOrZero(bet.to_pay), 0);
  const hasFilters = search || sportsbook || sport || betType || status || fromDate || toDate;

  function clearFilters() {
    setSearch(""); setSportsbook(""); setSport(""); setBetType(""); setStatus(""); setFromDate(""); setToDate("");
  }

  return (
    <>
      <header className="header activeHeader">
        <div>
          <span className="eyebrow">Game bets only · season futures excluded</span>
          <h1>Active Bets</h1>
        </div>
        <button className="refreshButton" onClick={() => load()} disabled={refreshing || loading}>
          {refreshing ? "Refreshing…" : "↻ Refresh"}
        </button>
      </header>

      <section className="activeSummaryGrid">
        <div><span>Showing</span><strong>{filtered.length}</strong></div>
        <div><span>At Risk</span><strong>{money(exposure)}</strong></div>
        <div><span>Potential Return</span><strong>{money(potential)}</strong></div>
      </section>
      {lastUpdated && <p className="lastUpdated">Database view updated {lastUpdated.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</p>}
      {notice && <div className="successBox">{notice}</div>}

      <div className="mobileFilterBar">
        <button className="mobileFilterButton" onClick={() => setFiltersOpen((v) => !v)}>
          {filtersOpen ? "Hide Filters" : "Filters"}{hasFilters ? " • Active" : ""}
        </button>
        {hasFilters && <button className="textButton" onClick={clearFilters}>Clear</button>}
      </div>

      <section className={`filterPanel ${filtersOpen ? "filtersOpen" : ""}`}>
        <div className="filterTopLine">
          <h2>Filters</h2>
          {hasFilters && <button className="textButton" onClick={clearFilters}>Clear</button>}
        </div>
        <div className="filterGrid">
          <label className="searchField">Search<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Player, team, market, bet ID…" /></label>
          <label>Sportsbook<select value={sportsbook} onChange={(e) => setSportsbook(e.target.value)}><option value="">All</option>{options.sportsbooks.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Sport<select value={sport} onChange={(e) => setSport(e.target.value)}><option value="">All</option>{options.sports.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Bet Type<select value={betType} onChange={(e) => setBetType(e.target.value)}><option value="">All</option>{options.betTypes.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Status<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{options.statuses.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>From<input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} /></label>
          <label>To<input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} /></label>
        </div>
      </section>
      {loading && <div className="skeleton">Loading active bets…</div>}
      {error && <div className="error">{error}</div>}

      {!loading && !error && (
        <section className="cardList activeBetList">
          {filtered.map((bet) => <BetCard bet={bet} key={bet.id} onCashOut={cashOutBet} />)}
        </section>
      )}

      {!loading && !filtered.length && !error && <div className="empty">No active bets match the selected filters.</div>}
    </>
  );
}
