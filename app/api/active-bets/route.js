import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

const ACTIVE_FILTER = "in.(PENDING,OPEN,LIVE,IN_PROGRESS)";

export async function GET() {
  try {
    const [bets, futureLegs] = await Promise.all([
      supabaseRest("bets", {
        searchParams: {
          select: "*",
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
      (futureLegs || [])
        .map((row) => Number(row.bet_row_id))
        .filter(Number.isFinite)
    );

    // Keep Season Futures on their own screen, matching the dashboard split.
    const gameBets = (bets || []).filter((bet) => !futureBetIds.has(Number(bet.id)));
    const betIds = gameBets.map((bet) => Number(bet.id)).filter(Number.isFinite);

    let legs = [];
    if (betIds.length) {
      legs = await supabaseRest("bet_legs", {
        searchParams: {
          select: "*",
          bet_row_id: `in.(${betIds.join(",")})`,
          order: "bet_row_id.desc,leg_index.asc.nullslast,id.asc"
        }
      });
    }

    const legsByBet = new Map();
    for (const leg of legs || []) {
      const id = Number(leg.bet_row_id);
      if (!legsByBet.has(id)) legsByBet.set(id, []);
      legsByBet.get(id).push(leg);
    }

    const rows = gameBets.map((bet) => ({
      ...bet,
      legs: legsByBet.get(Number(bet.id)) || []
    }));

    return NextResponse.json({ rows });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
