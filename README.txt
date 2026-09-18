SETTLE BET UPDATE — Vercel / Next.js

Copy BOTH files to the matching paths in your GitHub repository:
  app/bets/page.js
  app/api/active-bets/settle/route.js (create the settle folder)

The page includes the previously supplied manual Mark VOID button.
The new endpoint records WON/LOST/VOID, actual total returned (paid), and settled_at.
History already queries these statuses and uses settled_at. No changes to the existing Cash Out API.

IMPORTANT: We have NOT inspected the Supabase update-live-bets function, database triggers, or schema constraints. A scheduled updater might overwrite manually settled parent status. Verify protection before relying on this in production. Check the existing status/paid/settled_at columns and that the manual override is honored. No live deployment or end-to-end test was performed.

Test on a disposable/test bet before using a real settled bet.
