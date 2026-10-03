import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

// Active Legs should only contain tickets that are actually still active.
// LOST tickets may continue to be processed by update-live-bets for historical
// tracking, but they do not belong in the Active Legs UI.
const TRACKABLE_PARENT_FILTER = "in.(PENDING,OPEN,LIVE,IN_PROGRESS)";

const SETTLED = new Set([
  "WON",
  "LOST",
  "PUSH",
  "VOID",
  "VOIDED",
  "CANCELLED",
  "CANCELED",
  "CASHED_OUT"
]);

function upper(value) {
  return String(value || "").trim().toUpperCase();
}

export async function GET() {
  try {
    const [bets, futureLegs] = await Promise.all([
      supabaseRest("bets", {
        searchParams: {
          select:
            "id,sportsbook,bet_type,sport,headline,subtitle,event_name,status,stake,to_pay,placed_at,source_captured_at",
          status: TRACKABLE_PARENT_FILTER,
          order: "placed_at.desc.nullslast,id.desc"
        }
      }),

      // Identify season-future tickets so they stay off the game-day
      // Active Legs screen.
      supabaseRest("bet_legs", {
        searchParams: {
          select: "bet_row_id",
          tracking_scope: "eq.SEASON"
        }
      })
    ]);

    /*
     * ------------------------------------------------------------
     * EXCLUDE SEASON FUTURES
     * ------------------------------------------------------------
     */

    const futureBetIds = new Set(
      (futureLegs || [])
        .map((row) => Number(row.bet_row_id))
        .filter(Number.isFinite)
    );

    const gameBets = (bets || []).filter(
      (bet) => !futureBetIds.has(Number(bet.id))
    );

    const betIds = gameBets
      .map((bet) => Number(bet.id))
      .filter(Number.isFinite);

    /*
     * ------------------------------------------------------------
     * LOAD LEGS FOR ACTIVE GAME-DAY BETS
     * ------------------------------------------------------------
     */

    let legs = [];

    if (betIds.length) {
      legs = await supabaseRest("bet_legs", {
        searchParams: {
          select: "*",
          bet_row_id: `in.(${betIds.join(",")})`,
          order:
            "event_time.asc.nullslast,bet_row_id.desc,leg_index.asc.nullslast,id.asc"
        }
      });
    }

    /*
     * ------------------------------------------------------------
     * ACTIVE PARENT MAP
     * ------------------------------------------------------------
     *
     * Only PENDING / OPEN / LIVE / IN_PROGRESS parents ever reached
     * this point, so there is no reason to perform additional LOST
     * parent handling here.
     */

    const betMap = new Map();

    for (const bet of gameBets) {
      const betId = Number(bet.id);

      if (!Number.isFinite(betId)) continue;

      betMap.set(betId, bet);
    }

    /*
     * ------------------------------------------------------------
     * BUILD ACTIVE-LEG RESPONSE
     * ------------------------------------------------------------
     */

    const rows = [];

    for (const leg of legs || []) {
      const betId = Number(leg.bet_row_id);
      const bet = betMap.get(betId);

      if (!bet) continue;

      // Season futures belong on the Futures screen, not Active Legs.
      if (upper(leg.tracking_scope) === "SEASON") continue;

      const legStatus = upper(
        leg.status || leg.leg_status || "PENDING"
      );

      rows.push({
        ...leg,

        // Normalize the status fields consumed by the Legs frontend.
        leg_status: legStatus,
        is_settled: SETTLED.has(legStatus),

        // Parent ticket information.
        parent_status: upper(bet.status),
        parent_sportsbook: bet.sportsbook || leg.sportsbook || "",
        parent_bet_type: bet.bet_type || "",
        parent_sport: bet.sport || "",
        parent_headline: bet.headline || "",
        parent_subtitle: bet.subtitle || "",
        parent_event_name: bet.event_name || "",
        parent_stake: bet.stake,
        parent_to_pay: bet.to_pay,
        parent_placed_at:
          bet.placed_at || bet.source_captured_at || null
      });
    }

    return NextResponse.json({ rows });
  } catch (error) {
    console.error("GET /api/active-legs failed:", error);

    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : String(error)
      },
      { status: 500 }
    );
  }
}
