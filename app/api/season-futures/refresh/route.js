import { NextResponse } from "next/server";
import { supabaseRest, invokeTrackerFunction } from "../../../../lib/supabase-server";

export const maxDuration = 300;

export async function POST() {
  try {
    const legs = await supabaseRest("bet_legs", {
      searchParams: {
        select: "id,selection,market",
        tracking_scope: "eq.SEASON",
        order: "id.asc"
      }
    });

    const results = [];
    for (const leg of legs || []) {
      const legId = Number(leg.id);
      try {
        const matchPlayers = await invokeTrackerFunction("match-players", { leg_id: legId });
        const updateLiveBets = await invokeTrackerFunction("update-live-bets", { leg_id: legId });
        results.push({ leg_id: legId, ok: Boolean(updateLiveBets?.ok ?? true), matchPlayers, updateLiveBets });
      } catch (error) {
        results.push({ leg_id: legId, ok: false, error: error.message });
      }
    }

    const successful = results.filter((row) => row.ok).length;
    return NextResponse.json({
      ok: results.every((row) => row.ok),
      processed: results.length,
      successful,
      failed: results.length - successful,
      results
    });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
