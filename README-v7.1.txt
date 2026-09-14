Sports Bet Tracker Next.js v7.1 — Sweat FINAL cleanup

Changes:
- Sweat view excludes any game ESPN reports as FINAL/postgame, even if child legs remain stale PENDING/LIVE.
- Active Legs now fetches ESPN state for all displayed event IDs, not only legs already marked LIVE in the database.
- Games Live and Still Sweating summary counts ignore ESPN-final games.
- FINAL ESPN state overrides a stale LIVE badge in the game header.
- Keeps last known ESPN state through transient status-request failures.
- Game-status endpoint accepts up to 50 event IDs for larger slates.
