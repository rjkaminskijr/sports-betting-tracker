import { NextResponse } from "next/server";
import { supabaseRest, invokeTrackerFunction } from "../../../../lib/supabase-server";

export const maxDuration = 300;

export async function POST(request) {
  try {
    const body = await request.json();
    const action = String(body?.action || "").trim().toLowerCase();
    const legId = Number(body?.legId);

    if (!Number.isFinite(legId)) {
      return NextResponse.json({ error: "A valid legId is required." }, { status: 400 });
    }

    if (action === "void") {
      const updated = await supabaseRest("bet_legs", {
        method: "PATCH",
        searchParams: { id: `eq.${legId}` },
        body: { status: "VOID" },
        prefer: "return=representation"
      });

      const settlement = await invokeTrackerFunction("update-live-bets", {
        settlement_only_leg_id: legId
      });

      return NextResponse.json({ ok: true, action, updated, settlement });
    }

    if (action === "recheck") {
      const result = await invokeTrackerFunction("update-live-bets", { leg_id: legId });
      return NextResponse.json({ ok: true, action, result });
    }

    return NextResponse.json({ error: "Unsupported action." }, { status: 400 });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
