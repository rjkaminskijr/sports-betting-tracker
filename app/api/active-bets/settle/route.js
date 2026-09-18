import { NextResponse } from "next/server";
import { supabaseRest } from "../../../../lib/supabase-server";

const ACTIVE = new Set(["PENDING", "OPEN", "LIVE", "IN_PROGRESS"]);
const RESULTS = new Set(["WON", "LOST", "VOID"]);

export async function POST(request) {
  try {
    const body = await request.json();
    const betId = Number(body?.betId);
    const status = String(body?.status || "").trim().toUpperCase();
    const paid = Number(body?.paid);
    if (!Number.isSafeInteger(betId) || betId <= 0) {
      return NextResponse.json({ error: "A valid bet ID is required." }, { status: 400 });
    }
    if (!RESULTS.has(status)) {
      return NextResponse.json({ error: "Result must be WON, LOST, or VOID." }, { status: 400 });
    }
    if (body?.paid === "" || body?.paid === null || body?.paid === undefined || !Number.isFinite(paid) || paid < 0) {
      return NextResponse.json({ error: "Enter the actual total amount returned by the sportsbook (zero is allowed)." }, { status: 400 });
    }
    const rows = await supabaseRest("bets", {
      searchParams: { select: "id,status", id: `eq.${betId}`, limit: "1" }
    });
    const bet = rows?.[0];
    if (!bet) return NextResponse.json({ error: `Bet ${betId} was not found.` }, { status: 404 });
    const currentStatus = String(bet.status || "").trim().toUpperCase();
    if (!ACTIVE.has(currentStatus)) {
      return NextResponse.json({ error: `Bet ${betId} is no longer active (${currentStatus || "unknown"}).` }, { status: 409 });
    }
    const updated = await supabaseRest("bets", {
      method: "PATCH",
      searchParams: {
        id: `eq.${betId}`,
        status: "in.(PENDING,OPEN,LIVE,IN_PROGRESS)",
        select: "id,status,paid,settled_at"
      },
      body: { status, paid, settled_at: new Date().toISOString() },
      prefer: "return=representation"
    });
    if (!updated?.length) {
      return NextResponse.json({ error: "Bet changed before settlement could be saved. Refresh and retry." }, { status: 409 });
    }
    return NextResponse.json({ ok: true, bet: updated[0] });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
