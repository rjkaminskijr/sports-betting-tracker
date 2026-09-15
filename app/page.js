import { supabaseRest } from "../lib/supabase-server";

export const dynamic = "force-dynamic";

const ACTIVE_STATUSES = new Set(["PENDING", "OPEN", "LIVE", "IN_PROGRESS"]);
const SETTLED_STATUSES = new Set([
  "WON",
  "LOST",
  "PUSH",
  "VOID",
  "VOIDED",
  "CANCELLED",
  "CANCELED",
  "CASHED_OUT"
]);
const NEUTRAL_STATUSES = new Set(["PUSH", "VOID", "VOIDED", "CANCELLED", "CANCELED"]);

async function getDashboardData() {
  try {
    const [bets, futureRows] = await Promise.all([
      supabaseRest("bets", {
        searchParams: {
          select: [
            "id",
            "sportsbook",
            "bet_type",
            "status",
            "stake",
            "to_pay",
            "paid",
            "promo",
            "placed_at",
            "source_captured_at",
            "sport",
            "headline",
            "subtitle",
            "event_name",
            "current_odds",
            "boosted_odds",
            "original_odds",
            "leg_count"
          ].join(","),
          order: "placed_at.desc",
          limit: 1000
        }
      }),

      supabaseRest("bet_legs", {
        searchParams: {
          select: "bet_row_id",
          tracking_scope: "eq.SEASON",
          order: "bet_row_id.asc",
          limit: 1000
        }
      })
    ]);

    const futureBetIds =
      new Set(
        (futureRows || [])
          .map((row) =>
            Number(
              row.bet_row_id,
            ),
          )
          .filter(
            (id) =>
              Number.isFinite(
                id,
              ),
          ),
      );

    return {
      bets:
        bets || [],
      futureBetIds,
      loadError:
        null,
    };
  } catch (error) {
    console.error(
      "Dashboard load failed:",
      error,
    );

    return {
      bets: [],
      futureBetIds:
        new Set(),
      loadError:
        error instanceof Error
          ? error.message
          : "Unable to load dashboard data.",
    };
  }
}

const numberOrZero = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
};

const money = (value) => new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD"
}).format(numberOrZero(value));

function statusOf(bet) {
  return String(bet?.status || "PENDING").trim().toUpperCase();
}

function isBonusBet(bet) {
  // Match Streamlit v38 exactly: only explicit bonus/free-bet markers
  // make the user's cash-at-risk $0. A generic promo does NOT.
  const promo = String(bet?.promo || "").trim().toUpperCase();
  return ["BONUS BET", "FREE BET", "FREEBET"].some((marker) => promo.includes(marker));
}

function cashAtRisk(bet) {
  return isBonusBet(bet) ? 0 : numberOrZero(bet?.stake);
}

function returnedAmount(bet) {
  const status = statusOf(bet);
  const stake = numberOrZero(bet?.stake);
  const paidIsPresent = bet?.paid !== null && bet?.paid !== undefined && bet?.paid !== "";

  if (!SETTLED_STATUSES.has(status)) return 0;
  if (paidIsPresent) return numberOrZero(bet.paid);
  if (NEUTRAL_STATUSES.has(status)) return stake;
  return 0;
}

function profitLoss(bet) {
  const status = statusOf(bet);
  const cashRisk = cashAtRisk(bet);
  const paidIsPresent = bet?.paid !== null && bet?.paid !== undefined && bet?.paid !== "";

  if (status === "LOST") return -cashRisk;
  if (NEUTRAL_STATUSES.has(status)) return 0;
  if (SETTLED_STATUSES.has(status) && paidIsPresent) {
    // v38 behavior: for a winning bonus/free bet, the full paid amount is
    // profit because promotional stake is not user cash and is not returned.
    return numberOrZero(bet.paid) - cashRisk;
  }
  return 0;
}

function summarize(bets) {
  const settled = bets.filter((bet) => SETTLED_STATUSES.has(statusOf(bet)));
  const active = bets.filter((bet) => ACTIVE_STATUSES.has(statusOf(bet)));

  const totalWagered = bets.reduce((sum, bet) => sum + cashAtRisk(bet), 0);
  const totalReturned = settled.reduce((sum, bet) => sum + returnedAmount(bet), 0);
  const settledWagered = settled.reduce((sum, bet) => sum + cashAtRisk(bet), 0);
  const netPnl = settled.reduce((sum, bet) => sum + profitLoss(bet), 0);
  const roi = settledWagered ? (netPnl / settledWagered) * 100 : 0;
  const openExposure = active.reduce((sum, bet) => sum + cashAtRisk(bet), 0);
  const potentialReturn = active.reduce((sum, bet) => sum + numberOrZero(bet.to_pay), 0);
  const wins = settled.filter((bet) => statusOf(bet) === "WON").length;
  const losses = settled.filter((bet) => statusOf(bet) === "LOST").length;
  const pushes = settled.filter((bet) => NEUTRAL_STATUSES.has(statusOf(bet))).length;

  return {
    totalWagered,
    settledWagered,
    totalReturned,
    netPnl,
    roi,
    openExposure,
    potentialReturn,
    activeBets: active.length,
    wins,
    losses,
    pushes
  };
}

function Metric({ label, value, tone = "" }) {
  return (
    <article className={`metric ${tone}`.trim()}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

export default async function Dashboard() {
  const { bets, futureBetIds, loadError } = await getDashboardData();
  const gameBets = bets.filter((bet) => !futureBetIds.has(Number(bet.id)));
  const futureBets = bets.filter((bet) => futureBetIds.has(Number(bet.id)));

  const performance = summarize(gameBets);
  const futures = summarize(futureBets);

  return (
    <>
      <header className="header">
        <div>
          <span className="eyebrow">Performance & exposure</span>
          <h1>Sports Bet Tracker</h1>
        </div>
      </header>

      {loadError ? (
        <section className="panel">
          <div className="panelTitle"><h2>Dashboard unavailable</h2></div>
          <p className="muted dashboardNote">
            Unable to load Supabase data right now. Refresh the page in a moment.
          </p>
        </section>
      ) : null}

      <section className="panel">
        <div className="panelTitle"><h2>Performance Overview</h2></div>
        <p className="muted dashboardNote">Season futures are excluded from these totals.</p>
        <section className="metricGrid dashboardMetrics">
          <Metric label="Settled Wagered" value={money(performance.settledWagered)} />
          <Metric label="Total Returned" value={money(performance.totalReturned)} />
          <Metric label="Net P/L" value={money(performance.netPnl)} tone={performance.netPnl > 0 ? "metricPositive" : performance.netPnl < 0 ? "metricNegative" : ""} />
          <Metric label="ROI" value={`${performance.roi.toFixed(1)}%`} tone={performance.roi > 0 ? "metricPositive" : performance.roi < 0 ? "metricNegative" : ""} />
          <Metric label="Open Exposure" value={money(performance.openExposure)} />
          <Metric label="Active Bets" value={performance.activeBets} />
        </section>
        <p className="dashboardCaption">
          Settled record: {performance.wins}-{performance.losses}
          {performance.pushes ? `-${performance.pushes} push/void` : ""}
          {` • Total cash placed: ${money(performance.totalWagered)}`}
          {` • Active potential return: ${money(performance.potentialReturn)}`}
        </p>
      </section>

      <section className="panel">
        <div className="panelTitle"><h2>Season Futures</h2></div>
        <section className="metricGrid dashboardMetrics">
          <Metric label="Future Wagered" value={money(futures.totalWagered)} />
          <Metric label="Future Returned" value={money(futures.totalReturned)} />
          <Metric label="Future Net P/L" value={money(futures.netPnl)} />
          <Metric label="Future Exposure" value={money(futures.openExposure)} />
          <Metric label="Future Potential Return" value={money(futures.potentialReturn)} />
          <Metric label="Active Futures" value={futures.activeBets} />
        </section>
      </section>
    </>
  );
}
