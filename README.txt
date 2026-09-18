WON (LIVE) display-only update

Copy app/legs/page.js, app/bets/page.js and lib/early-win.js into the matching paths in your Next.js repository.
No Supabase function, API, settlement, payout or refresh changes are included.
WON (LIVE) requires a live leg and a numeric live value. Official settled statuses take precedence.
Receptions: OVER > line; X+ / AT_LEAST >= line.
Individual passing/rushing/receiving yards: >= line + 10.
Anytime TD: >= 1. OVER game/team point totals: > line.
Other markets are deliberately unchanged.
