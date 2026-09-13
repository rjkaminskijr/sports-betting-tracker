import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

export async function GET() {
  try {
    const trackedLegs = await supabaseRest("bet_legs", {
      searchParams: {
        select: "*",
        tracking_scope: "eq.SEASON",
        order: "bet_row_id.desc,leg_index.asc.nullslast,id.asc"
      }
    });

    const betIds = [...new Set(
      (trackedLegs || [])
        .map((leg) => Number(leg.bet_row_id))
        .filter(Number.isFinite)
    )];

    let parentBets = [];
    if (betIds.length) {
      parentBets = await supabaseRest("bets", {
        searchParams: {
          select: "*",
          id: `in.(${betIds.join(",")})`,
          order: "id.desc"
        }
      });
    }

    const betMap = new Map((parentBets || []).map((bet) => [Number(bet.id), bet]));
    const tracked = (trackedLegs || []).map((leg) => ({
      ...leg,
      bet: betMap.get(Number(leg.bet_row_id)) || null
    }));

    return NextResponse.json({ tracked });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
