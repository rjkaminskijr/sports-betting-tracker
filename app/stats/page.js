import Link from "next/link";
import { supabaseRest } from "../../lib/supabase-server";

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
const NEUTRAL_STATUSES = new Set([
  "PUSH",
  "VOID",
  "VOIDED",
  "CANCELLED",
  "CANCELED"
]);

const numberOrZero = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
};

const money = (value) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD"
  }).format(numberOrZero(value));

const percent = (value) =>
  `${numberOrZero(value).toFixed(1)}%`;

const upper = (value) =>
  String(value ?? "")
    .trim()
    .toUpperCase();

const cleanLabel = (value, fallback = "Unknown") => {
  const text = String(value ?? "").trim();
  return text || fallback;
};

function statusOf(bet) {
  return upper(bet?.status || "PENDING");
}

function isBonusBet(bet) {
  const promo = upper(bet?.promo);
  return ["BONUS BET", "FREE BET", "FREEBET"].some((marker) =>
    promo.includes(marker)
  );
}

function cashAtRisk(bet) {
  return isBonusBet(bet)
    ? 0
    : numberOrZero(bet?.stake);
}

function returnedAmount(bet) {
  const status = statusOf(bet);
  const stake = numberOrZero(bet?.stake);
  const paidIsPresent =
    bet?.paid !== null &&
    bet?.paid !== undefined &&
    bet?.paid !== "";

  if (!SETTLED_STATUSES.has(status)) return 0;
  if (paidIsPresent) return numberOrZero(bet.paid);
  if (NEUTRAL_STATUSES.has(status)) return stake;
  return 0;
}

function profitLoss(bet) {
  const status = statusOf(bet);
  const cashRisk = cashAtRisk(bet);
  const paidIsPresent =
    bet?.paid !== null &&
    bet?.paid !== undefined &&
    bet?.paid !== "";

  if (status === "LOST") return -cashRisk;
  if (NEUTRAL_STATUSES.has(status)) return 0;
  if (SETTLED_STATUSES.has(status) && paidIsPresent) {
    return numberOrZero(bet.paid) - cashRisk;
  }
  return 0;
}

function summarize(bets) {
  const settled =
    bets.filter((bet) =>
      SETTLED_STATUSES.has(statusOf(bet))
    );

  const active =
    bets.filter((bet) =>
      ACTIVE_STATUSES.has(statusOf(bet))
    );

  const settledWagered =
    settled.reduce(
      (sum, bet) =>
        sum + cashAtRisk(bet),
      0
    );

  const totalReturned =
    settled.reduce(
      (sum, bet) =>
        sum + returnedAmount(bet),
      0
    );

  const netPnl =
    settled.reduce(
      (sum, bet) =>
        sum + profitLoss(bet),
      0
    );

  const openExposure =
    active.reduce(
      (sum, bet) =>
        sum + cashAtRisk(bet),
      0
    );

  const totalCashPlaced =
    bets.reduce(
      (sum, bet) =>
        sum + cashAtRisk(bet),
      0
    );

  const avgStake =
    bets.length
      ? bets.reduce(
          (sum, bet) =>
            sum + cashAtRisk(bet),
          0
        ) / bets.length
      : 0;

  const settledOdds =
    settled
      .map((bet) =>
        Number(
          bet?.boosted_odds ??
          bet?.current_odds ??
          bet?.original_odds
        )
      )
      .filter(Number.isFinite);

  const avgOdds =
    settledOdds.length
      ? settledOdds.reduce(
          (sum, value) =>
            sum + value,
          0
        ) / settledOdds.length
      : null;

  return {
    bets: bets.length,
    settled: settled.length,
    active: active.length,
    wins: settled.filter(
      (bet) => statusOf(bet) === "WON"
    ).length,
    losses: settled.filter(
      (bet) => statusOf(bet) === "LOST"
    ).length,
    pushes: settled.filter(
      (bet) => NEUTRAL_STATUSES.has(statusOf(bet))
    ).length,
    settledWagered,
    totalReturned,
    netPnl,
    roi:
      settledWagered
        ? (netPnl / settledWagered) * 100
        : 0,
    openExposure,
    totalCashPlaced,
    avgStake,
    avgOdds
  };
}

function summarizeGroups(bets, keyFn) {
  const groups = new Map();

  for (const bet of bets) {
    const key =
      cleanLabel(
        keyFn(bet)
      );

    if (!groups.has(key)) {
      groups.set(key, []);
    }

    groups.get(key).push(bet);
  }

  return [...groups.entries()]
    .map(([label, rows]) => ({
      label,
      ...summarize(rows)
    }))
    .sort((a, b) =>
      b.settledWagered - a.settledWagered ||
      b.bets - a.bets ||
      a.label.localeCompare(b.label)
    );
}

function promoCategory(bet) {
  if (isBonusBet(bet)) {
    return "Bonus / Free Bet";
  }

  const promo =
    String(bet?.promo ?? "")
      .trim();

  return promo
    ? "Boost / Promo"
    : "Standard Cash";
}

function marketLabel(market) {
  const value =
    upper(market);

  if (
    value.includes("ANYTIME") &&
    value.includes("TD")
  ) {
    return "Anytime TD";
  }

  if (
    value.includes("FIRST") &&
    value.includes("TD")
  ) {
    return "First TD";
  }

  if (
    value.includes("RUSHING YARDS")
  ) {
    return value.includes("EACH QUARTER")
      ? "Rushing Yards — Each Quarter"
      : "Rushing Yards";
  }

  if (
    value.includes("RECEIVING YARDS")
  ) {
    return "Receiving Yards";
  }

  if (
    value.includes("PASSING YARDS")
  ) {
    return "Passing Yards";
  }

  if (
    value.includes("RECEPTIONS")
  ) {
    return value.includes("QTR") ||
      value.includes("QUARTER")
      ? "Quarter Receptions"
      : "Receptions";
  }

  if (value.includes("MONEYLINE")) {
    return "Moneyline";
  }

  if (value.includes("SPREAD")) {
    return "Spread";
  }

  if (value.includes("TOTAL")) {
    return "Totals";
  }

  return cleanLabel(market);
}

function legStatus(leg) {
  return upper(
    leg?.leg_status ??
    leg?.status ??
    "PENDING"
  );
}

function summarizeMarkets(legs) {
  const groups = new Map();

  for (const leg of legs) {
    const label =
      marketLabel(
        leg?.market
      );

    if (!groups.has(label)) {
      groups.set(label, {
        label,
        occurrences: 0,
        tickets: new Set(),
        won: 0,
        lost: 0,
        neutral: 0,
        active: 0
      });
    }

    const group =
      groups.get(label);

    group.occurrences += 1;

    const betId =
      Number(
        leg?.bet_row_id
      );

    if (
      Number.isFinite(betId)
    ) {
      group.tickets.add(
        betId
      );
    }

    const status =
      legStatus(leg);

    if (status === "WON") {
      group.won += 1;
    } else if (status === "LOST") {
      group.lost += 1;
    } else if (
      NEUTRAL_STATUSES.has(status)
    ) {
      group.neutral += 1;
    } else {
      group.active += 1;
    }
  }

  return [...groups.values()]
    .map((group) => {
      const graded =
        group.won +
        group.lost;

      return {
        ...group,
        tickets: group.tickets.size,
        hitRate:
          graded
            ? (group.won / graded) * 100
            : null
      };
    })
    .sort((a, b) =>
      b.occurrences - a.occurrences ||
      a.label.localeCompare(b.label)
    );
}

function dateForTrend(bet) {
  return (
    bet?.settled_at ||
    bet?.placed_at ||
    bet?.source_captured_at ||
    null
  );
}

function mondayStart(date) {
  const d =
    new Date(date);

  if (
    Number.isNaN(
      d.getTime()
    )
  ) {
    return null;
  }

  const day =
    d.getDay();

  const diff =
    day === 0
      ? -6
      : 1 - day;

  d.setDate(
    d.getDate() + diff
  );

  d.setHours(
    0,
    0,
    0,
    0
  );

  return d;
}

function periodKey(date, mode) {
  const parsed =
    new Date(date);

  if (
    Number.isNaN(
      parsed.getTime()
    )
  ) {
    return null;
  }

  if (mode === "month") {
    return `${parsed.getFullYear()}-${String(
      parsed.getMonth() + 1
    ).padStart(2, "0")}`;
  }

  const monday =
    mondayStart(parsed);

  if (!monday) return null;

  return monday
    .toISOString()
    .slice(0, 10);
}

function periodLabel(key, mode) {
  if (mode === "month") {
    const [year, month] =
      key.split("-");

    return new Intl.DateTimeFormat(
      "en-US",
      {
        month: "short",
        year: "numeric"
      }
    ).format(
      new Date(
        Number(year),
        Number(month) - 1,
        1
      )
    );
  }

  return new Intl.DateTimeFormat(
    "en-US",
    {
      month: "short",
      day: "numeric"
    }
  ).format(
    new Date(
      `${key}T12:00:00`
    )
  );
}

function buildTrend(bets, mode) {
  const groups =
    new Map();

  for (
    const bet
    of bets
  ) {
    if (
      !SETTLED_STATUSES.has(
        statusOf(bet)
      )
    ) {
      continue;
    }

    const date =
      dateForTrend(bet);

    const key =
      periodKey(
        date,
        mode
      );

    if (!key) continue;

    if (
      !groups.has(key)
    ) {
      groups.set(
        key,
        {
          key,
          bets: 0,
          wagered: 0,
          pnl: 0
        }
      );
    }

    const row =
      groups.get(key);

    row.bets += 1;
    row.wagered +=
      cashAtRisk(bet);
    row.pnl +=
      profitLoss(bet);
  }

  let cumulative =
    0;

  return [...groups.values()]
    .sort((a, b) =>
      a.key.localeCompare(
        b.key
      )
    )
    .map((row) => {
      cumulative +=
        row.pnl;

      return {
        ...row,
        label:
          periodLabel(
            row.key,
            mode
          ),
        cumulative
      };
    });
}

function exposureRows(bets, keyFn) {
  const active =
    bets.filter(
      (bet) =>
        ACTIVE_STATUSES.has(
          statusOf(bet)
        )
    );

  const groups =
    new Map();

  for (
    const bet
    of active
  ) {
    const key =
      cleanLabel(
        keyFn(bet)
      );

    if (
      !groups.has(key)
    ) {
      groups.set(
        key,
        {
          label: key,
          bets: 0,
          exposure: 0,
          potentialReturn: 0
        }
      );
    }

    const row =
      groups.get(key);

    row.bets += 1;
    row.exposure +=
      cashAtRisk(bet);
    row.potentialReturn +=
      numberOrZero(
        bet?.to_pay
      );
  }

  return [...groups.values()]
    .sort(
      (a, b) =>
        b.exposure -
        a.exposure
    );
}

function formatOdds(value) {
  if (
    value === null ||
    value === undefined ||
    !Number.isFinite(
      Number(value)
    )
  ) {
    return "—";
  }

  const number =
    Number(value);

  return number > 0
    ? `+${Math.round(number)}`
    : `${Math.round(number)}`;
}

async function getStatsData() {
  try {
    const [bets, legs, futureRows] =
      await Promise.all([
        supabaseRest(
          "bets",
          {
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
                "settled_at",
                "sport",
                "headline",
                "subtitle",
                "event_name",
                "current_odds",
                "boosted_odds",
                "original_odds",
                "leg_count"
              ].join(","),
              order:
                "placed_at.desc.nullslast,id.desc",
              limit:
                5000
            }
          }
        ),

        supabaseRest(
          "bet_legs",
          {
            searchParams: {
              select: [
                "id",
                "bet_row_id",
                "selection",
                "market",
                "leg_status",
                "status",
                "tracking_scope"
              ].join(","),
              order:
                "bet_row_id.desc,id.asc",
              limit:
                5000
            }
          }
        ),

        supabaseRest(
          "bet_legs",
          {
            searchParams: {
              select:
                "bet_row_id",
              tracking_scope:
                "eq.SEASON",
              limit:
                5000
            }
          }
        )
      ]);

    return {
      bets:
        bets || [],
      legs:
        legs || [],
      futureBetIds:
        new Set(
          (futureRows || [])
            .map(
              (row) =>
                Number(
                  row.bet_row_id
                )
            )
            .filter(
              Number.isFinite
            )
        ),
      loadError:
        null
    };
  } catch (error) {
    console.error(
      "Stats load failed:",
      error
    );

    return {
      bets: [],
      legs: [],
      futureBetIds:
        new Set(),
      loadError:
        error instanceof Error
          ? error.message
          : "Unable to load statistics."
    };
  }
}

function Metric({
  label,
  value,
  tone = ""
}) {
  return (
    <article
      className={`metric statsMetric ${tone}`.trim()}
    >
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function BreakdownTable({
  rows,
  showExposure = true
}) {
  if (!rows.length) {
    return (
      <div className="emptyInline">
        No data in this view.
      </div>
    );
  }

  return (
    <div className="statsTableWrap">
      <table className="statsTable">
        <thead>
          <tr>
            <th>Group</th>
            <th>Bets</th>
            <th>Record</th>
            <th>Wagered</th>
            <th>P/L</th>
            <th>ROI</th>
            {showExposure ? (
              <th>Open</th>
            ) : null}
          </tr>
        </thead>

        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td data-label="Group">
                <strong>
                  {row.label}
                </strong>
              </td>
              <td data-label="Bets">
                {row.bets}
              </td>
              <td data-label="Record">
                {row.wins}-{row.losses}
                {row.pushes
                  ? `-${row.pushes}`
                  : ""}
              </td>
              <td data-label="Wagered">
                {money(
                  row.settledWagered
                )}
              </td>
              <td
                data-label="P/L"
                className={
                  row.netPnl > 0
                    ? "statsPositive"
                    : row.netPnl < 0
                    ? "statsNegative"
                    : ""
                }
              >
                {money(
                  row.netPnl
                )}
              </td>
              <td data-label="ROI">
                {percent(
                  row.roi
                )}
              </td>
              {showExposure ? (
                <td data-label="Open">
                  {money(
                    row.openExposure
                  )}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MarketTable({
  rows
}) {
  if (!rows.length) {
    return (
      <div className="emptyInline">
        No leg data in this view.
      </div>
    );
  }

  return (
    <div className="statsTableWrap">
      <table className="statsTable statsMarketTable">
        <thead>
          <tr>
            <th>Market</th>
            <th>Legs</th>
            <th>Tickets</th>
            <th>W-L</th>
            <th>Hit Rate</th>
            <th>Active</th>
          </tr>
        </thead>

        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td data-label="Market">
                <strong>
                  {row.label}
                </strong>
              </td>
              <td data-label="Legs">
                {row.occurrences}
              </td>
              <td data-label="Tickets">
                {row.tickets}
              </td>
              <td data-label="W-L">
                {row.won}-{row.lost}
              </td>
              <td data-label="Hit Rate">
                {row.hitRate === null
                  ? "—"
                  : percent(
                      row.hitRate
                    )}
              </td>
              <td data-label="Active">
                {row.active}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExposureTable({
  rows
}) {
  if (!rows.length) {
    return (
      <div className="emptyInline">
        No active exposure.
      </div>
    );
  }

  return (
    <div className="statsTableWrap">
      <table className="statsTable">
        <thead>
          <tr>
            <th>Group</th>
            <th>Bets</th>
            <th>Exposure</th>
            <th>Potential Return</th>
          </tr>
        </thead>

        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td data-label="Group">
                <strong>
                  {row.label}
                </strong>
              </td>
              <td data-label="Bets">
                {row.bets}
              </td>
              <td data-label="Exposure">
                {money(
                  row.exposure
                )}
              </td>
              <td data-label="Potential Return">
                {money(
                  row.potentialReturn
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrendTable({
  rows,
  title
}) {
  if (!rows.length) {
    return (
      <div className="emptyInline">
        No settled history yet.
      </div>
    );
  }

  const recent =
    rows.slice(-12);

  return (
    <>
      <p className="statsSubnote">
        {title} · most recent {recent.length} periods
      </p>

      <div className="statsTableWrap">
        <table className="statsTable">
          <thead>
            <tr>
              <th>Period</th>
              <th>Bets</th>
              <th>Wagered</th>
              <th>P/L</th>
              <th>Cumulative</th>
            </tr>
          </thead>

          <tbody>
            {recent.map((row) => (
              <tr key={row.key}>
                <td data-label="Period">
                  <strong>
                    {row.label}
                  </strong>
                </td>
                <td data-label="Bets">
                  {row.bets}
                </td>
                <td data-label="Wagered">
                  {money(
                    row.wagered
                  )}
                </td>
                <td
                  data-label="P/L"
                  className={
                    row.pnl > 0
                      ? "statsPositive"
                      : row.pnl < 0
                      ? "statsNegative"
                      : ""
                  }
                >
                  {money(
                    row.pnl
                  )}
                </td>
                <td
                  data-label="Cumulative"
                  className={
                    row.cumulative > 0
                      ? "statsPositive"
                      : row.cumulative < 0
                      ? "statsNegative"
                      : ""
                  }
                >
                  {money(
                    row.cumulative
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function PnlExtremes({
  label,
  rows
}) {
  const settledRows =
    rows.filter(
      (row) =>
        row.settled > 0
    );

  if (!settledRows.length) {
    return null;
  }

  const highest =
    [...settledRows].sort(
      (a, b) =>
        b.netPnl -
        a.netPnl
    )[0];

  const lowest =
    [...settledRows].sort(
      (a, b) =>
        a.netPnl -
        b.netPnl
    )[0];

  return (
    <div className="statsExtremeRow">
      <span>{label}</span>

      <div>
        <small>Highest P/L</small>
        <strong className={
          highest.netPnl > 0
            ? "statsPositive"
            : highest.netPnl < 0
            ? "statsNegative"
            : ""
        }>
          {highest.label} · {money(highest.netPnl)}
        </strong>
      </div>

      <div>
        <small>Lowest P/L</small>
        <strong className={
          lowest.netPnl > 0
            ? "statsPositive"
            : lowest.netPnl < 0
            ? "statsNegative"
            : ""
        }>
          {lowest.label} · {money(lowest.netPnl)}
        </strong>
      </div>
    </div>
  );
}

export default async function StatsPage({
  searchParams
}) {
  const params =
    await searchParams;

  const includeFutures =
    params?.futures ===
    "1";

  const {
    bets,
    legs,
    futureBetIds,
    loadError
  } =
    await getStatsData();

  const selectedBets =
    includeFutures
      ? bets
      : bets.filter(
          (bet) =>
            !futureBetIds.has(
              Number(
                bet.id
              )
            )
        );

  const selectedIds =
    new Set(
      selectedBets
        .map(
          (bet) =>
            Number(
              bet.id
            )
        )
        .filter(
          Number.isFinite
        )
    );

  const selectedLegs =
    legs.filter(
      (leg) =>
        selectedIds.has(
          Number(
            leg.bet_row_id
          )
        )
    );

  const overall =
    summarize(
      selectedBets
    );

  const bySportsbook =
    summarizeGroups(
      selectedBets,
      (bet) =>
        bet.sportsbook
    );

  const byBetType =
    summarizeGroups(
      selectedBets,
      (bet) =>
        bet.bet_type
    );

  const bySport =
    summarizeGroups(
      selectedBets,
      (bet) =>
        bet.sport
    );

  const byPromo =
    summarizeGroups(
      selectedBets,
      promoCategory
    );

  const byMarket =
    summarizeMarkets(
      selectedLegs
    );

  const weekly =
    buildTrend(
      selectedBets,
      "week"
    );

  const monthly =
    buildTrend(
      selectedBets,
      "month"
    );

  const exposureSportsbook =
    exposureRows(
      selectedBets,
      (bet) =>
        bet.sportsbook
    );

  const exposureSport =
    exposureRows(
      selectedBets,
      (bet) =>
        bet.sport
    );

  const exposureType =
    exposureRows(
      selectedBets,
      (bet) =>
        bet.bet_type
    );

  return (
    <>
      <header className="header statsHeader">
        <div>
          <span className="eyebrow">
            Performance deep dive
          </span>
          <h1>Statistics</h1>
        </div>

        <div className="statsScopeToggle">
          <Link
            href="/stats"
            className={
              !includeFutures
                ? "isActive"
                : ""
            }
          >
            Regular Bets
          </Link>

          <Link
            href="/stats?futures=1"
            className={
              includeFutures
                ? "isActive"
                : ""
            }
          >
            Include Futures
          </Link>
        </div>
      </header>

      {loadError ? (
        <section className="panel">
          <div className="panelTitle">
            <h2>
              Statistics unavailable
            </h2>
          </div>

          <p className="muted">
            Unable to load Supabase statistics right now.
          </p>
        </section>
      ) : null}

      <section className="panel">
        <div className="panelTitle">
          <h2>
            Overall Performance
          </h2>

          <span>
            {includeFutures
              ? "Futures included"
              : "Futures excluded"}
          </span>
        </div>

        <section className="metricGrid statsMetricGrid">
          <Metric
            label="Settled Wagered"
            value={money(
              overall.settledWagered
            )}
          />

          <Metric
            label="Total Returned"
            value={money(
              overall.totalReturned
            )}
          />

          <Metric
            label="Net P/L"
            value={money(
              overall.netPnl
            )}
            tone={
              overall.netPnl > 0
                ? "metricPositive"
                : overall.netPnl < 0
                ? "metricNegative"
                : ""
            }
          />

          <Metric
            label="ROI"
            value={percent(
              overall.roi
            )}
            tone={
              overall.roi > 0
                ? "metricPositive"
                : overall.roi < 0
                ? "metricNegative"
                : ""
            }
          />

          <Metric
            label="Open Exposure"
            value={money(
              overall.openExposure
            )}
          />

          <Metric
            label="Active Bets"
            value={overall.active}
          />

          <Metric
            label="Average Stake"
            value={money(
              overall.avgStake
            )}
          />

          <Metric
            label="Average Odds"
            value={formatOdds(
              overall.avgOdds
            )}
          />
        </section>

        <p className="dashboardCaption">
          Record: {overall.wins}-{overall.losses}
          {overall.pushes
            ? `-${overall.pushes} push/void`
            : ""}
          {` • ${overall.settled} settled bets`}
          {` • ${money(overall.totalCashPlaced)} total cash placed`}
        </p>
      </section>

      <details
        className="panel statsDetails"
        open
      >
        <summary>
          <strong>
            By Sportsbook
          </strong>
          <span>
            {bySportsbook.length} groups
          </span>
        </summary>

        <BreakdownTable
          rows={
            bySportsbook
          }
        />
      </details>

      <details
        className="panel statsDetails"
        open
      >
        <summary>
          <strong>
            By Bet Type
          </strong>
          <span>
            {byBetType.length} groups
          </span>
        </summary>

        <BreakdownTable
          rows={
            byBetType
          }
        />
      </details>

      <details
        className="panel statsDetails"
        open
      >
        <summary>
          <strong>
            By Sport
          </strong>
          <span>
            {bySport.length} groups
          </span>
        </summary>

        <BreakdownTable
          rows={
            bySport
          }
        />
      </details>

      <details
        className="panel statsDetails"
      >
        <summary>
          <strong>
            By Market
          </strong>
          <span>
            leg-level
          </span>
        </summary>

        <p className="statsSubnote">
          Market stats are leg-level counts and hit rates. Cash P/L is not assigned to individual parlay legs, which avoids double-counting a ticket across multiple markets.
        </p>

        <MarketTable
          rows={
            byMarket
          }
        />
      </details>

      <details
        className="panel statsDetails"
      >
        <summary>
          <strong>
            Promo / Stake Type
          </strong>
          <span>
            cash vs promo
          </span>
        </summary>

        <p className="statsSubnote">
          Bonus/free-bet stakes remain excluded from cash wagered and ROI.
        </p>

        <BreakdownTable
          rows={
            byPromo
          }
        />
      </details>

      <details
        className="panel statsDetails"
      >
        <summary>
          <strong>
            Time Trends
          </strong>
          <span>
            weekly + monthly
          </span>
        </summary>

        <div className="statsTrendStack">
          <TrendTable
            rows={weekly}
            title="Weekly P/L"
          />

          <TrendTable
            rows={monthly}
            title="Monthly P/L"
          />
        </div>
      </details>

      <details
        className="panel statsDetails"
      >
        <summary>
          <strong>
            P/L Extremes
          </strong>
          <span>
            settled results
          </span>
        </summary>

        <p className="statsSubnote">
          Highest and lowest realized P/L by category. These are descriptive totals, not ratings.
        </p>

        <div className="statsExtremeList">
          <PnlExtremes
            label="Sportsbook"
            rows={bySportsbook}
          />

          <PnlExtremes
            label="Bet Type"
            rows={byBetType}
          />

          <PnlExtremes
            label="Sport"
            rows={bySport}
          />
        </div>
      </details>

      <details
        className="panel statsDetails"
      >
        <summary>
          <strong>
            Active Exposure
          </strong>
          <span>
            current risk
          </span>
        </summary>

        <div className="statsExposureStack">
          <div>
            <h3>
              By Sportsbook
            </h3>
            <ExposureTable
              rows={
                exposureSportsbook
              }
            />
          </div>

          <div>
            <h3>
              By Sport
            </h3>
            <ExposureTable
              rows={
                exposureSport
              }
            />
          </div>

          <div>
            <h3>
              By Bet Type
            </h3>
            <ExposureTable
              rows={
                exposureType
              }
            />
          </div>
        </div>
      </details>
    </>
  );
}
