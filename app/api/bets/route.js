import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

export async function GET(request) {
  try {
    const { searchParams } = new URL(request.url);
    const status = searchParams.get("status");
    const limit = Math.min(Number(searchParams.get("limit") || 250), 1000);

    const query = {
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
      limit
    };

    if (status) query.status = `eq.${status}`;

    const rows = await supabaseRest("bets", { searchParams: query });
    return NextResponse.json({ rows });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
