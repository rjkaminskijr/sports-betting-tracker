import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

export async function GET(request) {
  try {
    const { searchParams } = new URL(request.url);
    const limit = Math.min(Number(searchParams.get("limit") || 250), 500);

    const rows = await supabaseRest("bet_legs", {
      searchParams: {
        select: "leg_id,bet_row_id,sportsbook,selection,market,event_team_a,event_team_b,live_state,live_value,tracking_scope,espn_event_id,event_time,leg_status",
        order: "event_time.asc.nullslast",
        limit
      }
    });

    return NextResponse.json({ rows });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
