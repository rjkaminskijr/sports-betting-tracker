# Sports Bet Tracker v8.1 Cloud

Version 8.1 converts the tracker from PC-dependent storage to a cloud-first architecture.

## What changed

- Supabase becomes the persistent database when Streamlit Secrets are configured.
- SQLite remains available automatically as a local fallback.
- Uploaded bet screenshots are persisted to a Supabase Storage bucket named `bet-screenshots`.
- DraftKings, FanDuel, and Fanatics screenshot parsing remains intact.
- Existing ESPN NFL live tracking and season-future tracking now work through the same storage adapter.
- `packages.txt` installs Tesseract OCR on Streamlit Community Cloud.
- The app displays whether it is currently using `Supabase Cloud` or `Local SQLite`.

## Start here

Follow `CLOUD_SETUP.md`.

After the cloud deployment is confirmed from the iPhone, the next phase is a direct iOS Shortcut upload endpoint so screenshots can flow straight from the phone into the tracker without iCloud Drive or a Windows PC.
