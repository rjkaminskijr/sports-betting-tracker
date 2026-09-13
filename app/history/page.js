"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchJsonWithRetry } from "../../lib/client-api";

const SETTLED = new Set(["WON", "LOST", "PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED", "CASHED_OUT"]);
const NEUTRAL = new Set(["PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED"]);

const n = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
const cleanZero = (v) => Math.abs(n(v)) < 0.005 ? 0 : n(v);
const money = (v) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cleanZero(v));
const statusOf = (row) => String(row?.status || "").trim().toUpperCase();

function isBonusBet(bet) {
  const promo = String(bet?.promo || "").toUpperCase();
  return ["BONUS BET", "FREE BET", "FREEBET"].some((x) => promo.includes(x));
}

function cashAtRisk(bet) { return isBonusBet(bet) ? 0 : n(bet?.stake); }
function pnl(bet) {
  const s = statusOf(bet);
  if (s === "LOST") return -cashAtRisk(bet);
  if (NEUTRAL.has(s)) return 0;
  if (SETTLED.has(s) && bet?.paid !== null && bet?.paid !== undefined && bet?.paid !== "") return n(bet.paid) - cashAtRisk(bet);
  return 0;
}

function odds(value) {
  if (value === null || value === undefined || value === "") return "—";
  const x = Number(value);
  return Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${Math.trunc(x)}` : String(value);
}

function displaySport(bet) {
  const s = String(bet?.sport || "").trim().toUpperCase();
  if (["NCAAF", "CFB", "COLLEGE FOOTBALL"].includes(s)) return "CFB";
  return s || "Football";
}

function displayBetType(bet) {
  const t = String(bet?.bet_type || "").trim().toUpperCase();
  const aliases = { STRAIGHT: "Straight", SINGLE: "Straight", PARLAY: "Parlay", SGP: "SGP", SGPX: "SGPx", TEASER: "Teaser" };
  return aliases[t] || (t ? t.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "Other");
}

function description(bet) {
  const legs = bet.legs || [];
  const sport = displaySport(bet);
  const markets = [...new Set(legs.map((x) => String(x.market || "").trim()).filter(Boolean))];
  const type = String(bet.bet_type || "").toUpperCase();
  if (legs.length === 1) return `${sport} ${markets[0] || "Straight Bet"}`;
  if (type.includes("SGP") || type.includes("SAME GAME")) return `${sport} Same Game Parlay`;
  if (markets.length === 1 && legs.length > 1) return `${sport} ${markets[0]} Parlay`;
  return legs.length > 1 ? `${sport} ${legs.length}-Leg Parlay` : (bet.headline || `${sport} Bet`);
}

function gameName(leg) {
  const a = String(leg.event_team_a || "").trim();
  const b = String(leg.event_team_b || "").trim();
  return a && b ? `${a} @ ${b}` : (a || b || leg.event_name || "");
}

function fmtDate(value) {
  if (!value) return "Settlement time unavailable";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(d);
}

function Status({ value }) {
  const s = statusOf({ status: value });
  return <span className={`historyStatus history-${s.toLowerCase().replaceAll("_", "-")}`}>{s || "SETTLED"}</span>;
}

function LegRow({ leg, onAction, busy }) {
  const s = statusOf(leg) || "PENDING";
  const current = leg.live_value ?? leg.current_value ?? "";
  return (
    <div className="historyLegRow">
      <div>
        <strong>{leg.selection || "Leg"}</strong>
        <small>{gameName(leg)}</small>
        <span>{leg.market || "Market"}{leg.line_value != null ? ` · ${leg.direction || ""} ${leg.line_value}` : ""}</span>
      </div>
      <div className="historyLegRight">
        <Status value={s} />
        {current !== "" && <b>{String(current)}</b>}
        <div className="historyLegActions">
          <button disabled={busy} onClick={() => onAction("recheck", leg)}>Recheck</button>
          {s !== "VOID" && s !== "VOIDED" && (
            <button className="dangerGhost" disabled={busy} onClick={() => onAction("void", leg)}>Mark VOID</button>
          )}
        </div>
      </div>
    </div>
  );
}

function HistoryCard({ bet, onAction, busyId }) {
  const profit = cleanZero(pnl(bet));
  const displayedOdds = bet.current_odds ?? bet.original_odds ?? bet.boosted_odds;
  const tone = profit > 0 ? "win" : profit < 0 ? "loss" : "neutral";
  return (
    <details className={`historyCard historyCard-${tone}`}>
      <summary>
        <div className="historySummaryMain">
          <div className="historyTitle"><strong>{description(bet)}</strong><Status value={bet.status} /></div>
          <small>{bet.sportsbook || "Sportsbook"} · {displaySport(bet)} · {displayBetType(bet)} · Bet {bet.id}</small>
          <small>Settled {fmtDate(bet.settled_at || bet.settled_reference)}</small>
        </div>
        <div className="historyMoney">
          <strong className={tone === "win" ? "positiveText" : tone === "loss" ? "negativeText" : ""}>{profit >= 0 ? "+" : ""}{money(profit)}</strong>
          <span>Wager {money(bet.stake)} · {odds(displayedOdds)}</span>
          <span>Paid {money(bet.paid)}</span>
        </div>
      </summary>
      <div className="historyExpanded">
        <div className="historyMetricRow">
          <div><span>Cash at Risk</span><b>{money(cashAtRisk(bet))}</b></div>
          <div><span>To Pay</span><b>{money(bet.to_pay)}</b></div>
          <div><span>Paid</span><b>{money(bet.paid)}</b></div>
          <div><span>P/L</span><b>{profit >= 0 ? "+" : ""}{money(profit)}</b></div>
        </div>
        <h4>Legs</h4>
        {(bet.legs || []).map((leg) => (
          <LegRow key={leg.id} leg={leg} busy={busyId === Number(leg.id)} onAction={onAction} />
        ))}
        <p className="historyToolNote">Settlement tools are intended for sportsbook corrections such as a later VOID. “Mark VOID” updates the leg in Supabase and recalculates the parent bet from stored leg statuses.</p>
      </div>
    </details>
  );
}

export default function HistoryPage() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [windowDays, setWindowDays] = useState("2");
  const [search, setSearch] = useState("");
  const [sportsbook, setSportsbook] = useState("ALL");
  const [sport, setSport] = useState("ALL");
  const [result, setResult] = useState("ALL");
  const [busyId, setBusyId] = useState(null);
  const [notice, setNotice] = useState("");

  async function load() {
    setError("");
    try {
      const { data } = await fetchJsonWithRetry(
        "/api/history",
        { cache: "no-store" },
        { fallbackMessage: "Unable to load history" }
      );
      setRows(data.rows || []);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function doAction(action, leg) {
    const verb = action === "void" ? `mark ${leg.selection || "this leg"} VOID` : `recheck ${leg.selection || "this leg"}`;
    if (!window.confirm(`Are you sure you want to ${verb}?`)) return;
    setBusyId(Number(leg.id));
    setNotice("");
    try {
      const r = await fetch("/api/history/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, legId: Number(leg.id) })
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Action failed");
      setNotice(action === "void" ? "VOID saved and parent settlement recalculated." : "Leg rechecked successfully.");
      await load();
    } catch (e) { setNotice(`Error: ${e.message}`); }
    finally { setBusyId(null); }
  }

  const filtered = useMemo(() => {
    const now = Date.now();
    const cutoff = windowDays === "ALL" ? null : now - Number(windowDays) * 86400000;
    const q = search.trim().toLowerCase();
    return rows.filter((bet) => {
      if (cutoff) {
        const d = new Date(bet.settled_at || bet.history_window_reference);
        // If absolutely no usable reference exists, keep the row visible rather
        // than silently hiding a settled bet from the correction window.
        if (!Number.isNaN(d.getTime()) && d.getTime() < cutoff) return false;
      }
      if (sportsbook !== "ALL" && String(bet.sportsbook || "") !== sportsbook) return false;
      if (sport !== "ALL" && displaySport(bet) !== sport) return false;
      if (result !== "ALL" && statusOf(bet) !== result) return false;
      if (q) {
        const hay = [bet.id, bet.sportsbook, bet.headline, bet.subtitle, bet.event_name, displaySport(bet), displayBetType(bet), ...(bet.legs || []).flatMap((l) => [l.selection, l.market, gameName(l)])].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }).sort((a, b) => new Date(b.settled_at || b.history_window_reference || b.settled_reference || 0) - new Date(a.settled_at || a.history_window_reference || a.settled_reference || 0));
  }, [rows, windowDays, search, sportsbook, sport, result]);

  const books = [...new Set(rows.map((x) => x.sportsbook).filter(Boolean))].sort();
  const sports = [...new Set(rows.map(displaySport).filter(Boolean))].sort();
  const wins = filtered.filter((x) => statusOf(x) === "WON").length;
  const losses = filtered.filter((x) => statusOf(x) === "LOST").length;
  const net = filtered.reduce((sum, x) => sum + pnl(x), 0);

  return (
    <>
      <header className="header activeHeader">
        <div><small>Recently settled bets</small><h1>History</h1></div>
        <button className="refreshButton" onClick={load}>↻ Refresh</button>
      </header>

      <section className="historySummaryGrid">
        <div><span>Showing</span><strong>{filtered.length}</strong></div>
        <div><span>Record</span><strong>{wins}-{losses}</strong></div>
        <div><span>Net P/L</span><strong className={net >= 0 ? "positiveText" : "negativeText"}>{net >= 0 ? "+" : ""}{money(net)}</strong></div>
      </section>

      <section className="panel historyFilters">
        <div className="filterTopLine"><h3>History Window</h3><small>Default: last 2 days</small></div>
        <div className="historyFilterGrid">
          <label>Window<select value={windowDays} onChange={(e) => setWindowDays(e.target.value)}><option value="2">2 Days</option><option value="7">7 Days</option><option value="14">14 Days</option><option value="ALL">All</option></select></label>
          <label>Search<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Player, team, bet ID..." /></label>
          <label>Sportsbook<select value={sportsbook} onChange={(e) => setSportsbook(e.target.value)}><option>ALL</option>{books.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Sport<select value={sport} onChange={(e) => setSport(e.target.value)}><option>ALL</option>{sports.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Result<select value={result} onChange={(e) => setResult(e.target.value)}><option>ALL</option><option>WON</option><option>LOST</option><option>PUSH</option><option>VOID</option><option>CASHED_OUT</option></select></label>
        </div>
      </section>

      {notice && <div className={`notice ${notice.startsWith("Error") ? "noticeError" : "noticeSuccess"}`}>{notice}</div>}
      {loading && <p>Loading recent settlements…</p>}
      {error && <div className="notice noticeError">{error}</div>}
      {!loading && !error && filtered.length === 0 && <section className="panel"><p>No settled bets match the selected window and filters.</p></section>}
      <section className="historyList">
        {filtered.map((bet) => <HistoryCard key={bet.id} bet={bet} onAction={doAction} busyId={busyId} />)}
      </section>
    </>
  );
}
