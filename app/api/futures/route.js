import { NextResponse } from "next/server";
import { supabaseRest } from "../../../lib/supabase-server";

export async function GET() {
  try {
    // v38 identifies a parent as a season future when at least one child leg
    // has tracking_scope=SEASON. We only need the parent IDs on Dashboard.
    const rows = await supabaseRest("bet_legs", {
      searchParams: {
        select: "bet_row_id",
        tracking_scope: "eq.SEASON",
        order: "bet_row_id.asc",
        limit: 1000
      }
    });

    const betIds = [...new Set(
      (rows || [])
        .map((row) => Number(row.bet_row_id))
        .filter((id) => Number.isFinite(id))
    )];

    return NextResponse.json({ betIds });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
