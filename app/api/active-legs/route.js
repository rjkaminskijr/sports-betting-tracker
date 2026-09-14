import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

const TRACKABLE_PARENT_FILTER = "in.(PENDING,OPEN,LIVE,IN_PROGRESS,LOST)";
const SETTLED = new Set(["WON","LOST","PUSH","VOID","VOIDED","CANCELLED","CANCELED","CASHED_OUT"]);

function upper(value) {
  return String(value || "").trim().toUpperCase();
}

export async function GET() {
  try {
    const [bets, futureLegs] = await Promise.all([
      supabaseRest("bets", {
        searchParams: {
          select: "id,sportsbook,bet_type,sport,headline,subtitle,event_name,status,stake,to_pay,placed_at,source_captured_at",
          status: TRACKABLE_PARENT_FILTER,
          order: "placed_at.desc.nullslast,id.desc"
        }
      }),
      supabaseRest("bet_legs", {
        searchParams: {
          select: "bet_row_id",
          tracking_scope: "eq.SEASON"
        }
      })
    ]);

    const futureBetIds = new Set(
      (futureLegs || []).map((row) => Number(row.bet_row_id)).filter(Number.isFinite)
    );

    const gameBets = (bets || []).filter((bet) => !futureBetIds.has(Number(bet.id)));
    const betIds = gameBets.map((bet) => Number(bet.id)).filter(Number.isFinite);

    let legs = [];
    if (betIds.length) {
      legs = await supabaseRest("bet_legs", {
        searchParams: {
          select: "*",
          bet_row_id: `in.(${betIds.join(",")})`,
          order: "event_time.asc.nullslast,bet_row_id.desc,leg_index.asc.nullslast,id.asc"
        }
      });
    }

    const legsByBet = new Map();
    for (const leg of legs || []) {
      const betId = Number(leg.bet_row_id);
      if (!legsByBet.has(betId)) legsByBet.set(betId, []);
      legsByBet.get(betId).push(leg);
    }

    const betMap = new Map();
    for (const bet of gameBets) {
      const betId = Number(bet.id);
      const betLegs = (legsByBet.get(betId) || []).filter((leg) => upper(leg.tracking_scope) !== "SEASON");
      const hasUnsettledLeg = betLegs.some((leg) => !SETTLED.has(upper(leg.status || leg.leg_status || "PENDING")));

      // A LOST parlay stays on Active Legs only while another child leg still
      // needs tracking. This mirrors update-live-bets and keeps game-day action
      // visible without dragging completed tickets back into the screen.
      if (upper(bet.status) === "LOST" && !hasUnsettledLeg) continue;
      betMap.set(betId, bet);
    }

    const rows = [];

    for (const leg of legs || []) {
      const betId = Number(leg.bet_row_id);
      const bet = betMap.get(betId);
      if (!bet) continue;
      if (upper(leg.tracking_scope) === "SEASON") continue;

      const legStatus = upper(leg.status || leg.leg_status || "PENDING");

      // Include settled child legs while their ticket is still relevant. The
      // client collapses/hides them by default in Sweat mode, but they remain
      // available for the compact game/player context when needed.
      rows.push({
        ...leg,
        leg_status: legStatus,
        is_settled: SETTLED.has(legStatus),
        parent_status: upper(bet.status),
        parent_sportsbook: bet.sportsbook || leg.sportsbook || "",
        parent_bet_type: bet.bet_type || "",
        parent_sport: bet.sport || "",
        parent_headline: bet.headline || "",
        parent_subtitle: bet.subtitle || "",
        parent_event_name: bet.event_name || "",
        parent_stake: bet.stake,
        parent_to_pay: bet.to_pay,
        parent_placed_at: bet.placed_at || bet.source_captured_at || null
      });
    }

    return NextResponse.json({ rows });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
