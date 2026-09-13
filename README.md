
## v6.6 mobile + Active Legs grouping polish

- Active Legs now uses `espn_event_id` as the primary game grouping key whenever it is available. This prevents a leg with a resolved ESPN event from being displayed under another matchup that happens to share similar timing/context.
- Duplicate-leg combination keys also include the resolved ESPN event ID.
- Live game-status lookups use the group event ID directly.
- Added iPhone safe-area support (`viewport-fit=cover`, safe-area-aware bottom nav placement, and extra bottom content clearance).
- Tightened mobile header/nav spacing while preserving readable type and 44px touch targets.

# Sports Bet Tracker — Next.js Prototype

This is a parallel front-end experiment based on the current Streamlit v38 workflow. It does not replace or modify the Streamlit app.

## What is included

- Dashboard
- Active Bets
- Active Legs
- Mobile bottom navigation
- Active Legs browser refresh every 30 seconds
- Server-side Supabase REST proxy so the service-role key is never sent to the browser

## Important security rule

`SUPABASE_SERVICE_ROLE_KEY` is server-only. Never rename it to start with `NEXT_PUBLIC_`.

## Local setup

1. Install Node.js 20+.
2. Copy `.env.example` to `.env.local`.
3. Fill in your Supabase project URL and service-role key.
4. Run:

```bash
npm install
npm run dev
```

5. Open `http://localhost:3000`.

## Vercel setup

Create a new Vercel project for this folder/repository and add these Environment Variables:

- `NEXT_PUBLIC_SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Do not remove or change anything from the existing Streamlit deployment while testing this prototype.

## Prototype note

The REST field lists are intentionally small to reduce Supabase egress. If a database column name differs from the current production schema, update only the `select` strings in `app/api/bets/route.js` and `app/api/legs/route.js`.

## Dashboard v38 parity update

The Dashboard now mirrors Streamlit v38 for the top performance calculations:

- Season futures are separated using `bet_legs.tracking_scope = SEASON`.
- Bonus/free bets count as $0 cash wager/exposure only when `promo` contains `BONUS BET`, `FREE BET`, or `FREEBET`.
- Total Returned, Net P/L, ROI, Open Exposure, Active Bets, settled record, active potential return, and the six Season Futures summary values use the same definitions as v38.

## Active Bets v2 update

The Active Bets screen now:

- excludes Season Futures (`tracking_scope = SEASON` parent bets),
- loads all active parent bets and their legs in two Supabase reads,
- supports search + sportsbook + sport + bet type + status + date filters,
- matches v38 bet descriptions, wager/odds/payout details, parlay progress, and leg details,
- keeps card expansion entirely in the browser (no Streamlit-style full app rerun),
- refreshes the database view only when you tap Refresh.

The Refresh button currently reloads values already stored in Supabase. It does not invoke the ESPN/update-live-bets pipeline yet; that will be wired separately so the UI remains responsive.

## Active Bets polish pass

This build adds live-first/game-time sorting, matchup and kickoff context on collapsed cards, payout display, readable leg progress, sportsbook-style expanded legs, LIVE badges, and collapsible mobile filters.

## v4 Active Legs experiment

The Active Legs screen now mirrors the v38 non-futures logic, combines duplicate legs, groups by matchup, sorts live games first and upcoming games by kickoff, auto-refreshes the stored Supabase view every 30 seconds, and includes sportsbook/sport/state/date/search filters.

### Active Legs v4.2
- Matchup cards preserve expanded/collapsed state during 30-second database refreshes.
- Expand all / Collapse all controls are included.
- Live matchup headers request a lightweight ESPN score/clock readout every 15 seconds through a server-side Next.js route. This does not add Supabase database or egress usage for the score lookup.

## Season Futures refresh
The Season Futures page is monitoring-only. Supabase remains the source of truth for identifying and configuring season futures. To use the optional **Refresh Stats** button, also add the same custom Edge Function token used by the existing tracker:

```env
BET_UPLOAD_TOKEN=your_existing_bet_upload_token
```

Keep this server-side in `.env.local`; do not expose it with a `NEXT_PUBLIC_` prefix.

## v6 History
Adds a compact History screen. Default window is settled bets from the last 2 days, with optional 7-day, 14-day, or All views. Expanded settled bets include leg-level Recheck and Mark VOID correction tools. BET_UPLOAD_TOKEN is required for those correction actions.


## v6.1 History timestamp fallback

History now prefers `bets.settled_at`. Until Supabase writes that field reliably, it uses a trustworthy post-kickoff leg settlement/update timestamp when available; otherwise it falls back to the **latest game start time** among the bet's legs. It no longer uses bet placement/import timestamps as settlement times.


## v6.2 History window fallback
- Display settlement timestamp: parent settled_at -> trusted post-kickoff leg update -> recovered/latest game kickoff -> unavailable.
- 2/7/14-day inclusion uses the display reference first, then a database update timestamp if historical rows lack settlement/game metadata.
- Missing event_time values with an ESPN event id are resolved server-side from ESPN and cached for a day.
- Database update timestamps are never shown as settlement timestamps; they are used only to keep recent settled bets in the History window.


## v6.4 cash out
Active Bets now includes a parent-level Cash Out action. Enter the actual sportsbook amount returned; the app writes `status=CASHED_OUT` and `paid=<returned amount>`. Ensure the `settled_at` database trigger treats `CASHED_OUT` as a settled status.

## v6.5 Futures polish

- Player headers now show games played once the season has started, alongside pace-status counts.
- Futures ticket stake/odds/payout are labeled as ticket-level information.
- Added compact player/market/bet search plus sportsbook and future-status filters.
- Expand/Collapse all applies to the currently filtered player list.

## v6.7 UI polish
- Active Legs: game/team-total OVER legs show a green check once the live total is strictly greater than the selected line.
- Mobile Safari: bottom navigation no longer double-counts the iPhone safe-area inset; standalone/PWA mode still honors it.
- Mobile page bottom padding adjusted so final cards/actions can scroll above the fixed navigation.

## v6.8 production/mobile cleanup

- Removed prototype/framework labels from the Home header.
- Home Net P/L and ROI now use positive/negative color cues.
- Bottom navigation now uses the current route for a stable active-tab highlight.
- Read-only page loads retry one transient network/Supabase gateway failure before showing an error.
- 502/503/504 load failures now show a friendly temporary-connection message instead of raw Supabase gateway text.
- Added `BET_UPLOAD_TOKEN` to `.env.example`; real secrets remain in `.env.local`, which is git-ignored.
