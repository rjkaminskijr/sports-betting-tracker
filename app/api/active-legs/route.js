import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

const ACTIVE_FILTER = "in.(PENDING,OPEN,LIVE,IN_PROGRESS)";
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
          status: ACTIVE_FILTER,
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

    const betMap = new Map(gameBets.map((bet) => [Number(bet.id), bet]));
    const rows = [];

    for (const leg of legs || []) {
      const betId = Number(leg.bet_row_id);
      const bet = betMap.get(betId);
      if (!bet) continue;
      if (upper(leg.tracking_scope) === "SEASON") continue;

      const legStatus = upper(leg.status || leg.leg_status || "PENDING");
      if (SETTLED.has(legStatus)) continue;

      rows.push({
        ...leg,
        leg_status: legStatus,
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
