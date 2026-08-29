# Sports Bet Tracker v8.1 — Cloud Setup

This version is designed so your Windows PC does **not** have to stay on.

Final architecture:

**iPhone -> Streamlit Community Cloud -> Supabase**

The iPhone Shortcut direct-upload step comes after the cloud app is online and tested.

## 1. Create a Supabase project

1. Go to Supabase and create a free project.
2. Open **SQL Editor**.
3. Open `supabase_schema.sql` from this folder, copy all of it, and run it once.
4. Open **Storage** and create a bucket named exactly:

   `bet-screenshots`

The bucket may remain private.

## 2. Get the two Supabase values Streamlit needs

In your Supabase project settings, copy:

- Project URL
- Secret/service-role key suitable for server-side use

**Never put the secret key in GitHub, screenshots, or your iPhone Shortcut.** It belongs only in Streamlit's encrypted Secrets settings.

## 3. Put the tracker in GitHub

Create a private GitHub repository and upload the contents of this folder.

Important files at the repository root:

- `app.py`
- `requirements.txt`
- `packages.txt`
- `database/`
- `importers/`
- `ocr/`
- `services/`

Do **not** commit `.streamlit/secrets.toml` or a Supabase secret key.

## 4. Deploy on Streamlit Community Cloud

1. Sign in to Streamlit Community Cloud with GitHub.
2. Create a new app from the repository.
3. Main file path: `app.py`
4. Open the app's **Settings -> Secrets**.
5. Paste this, replacing the values:

```toml
[supabase]
url = "https://YOUR_PROJECT.supabase.co"
secret_key = "YOUR_SUPABASE_SECRET_OR_SERVICE_ROLE_KEY"
```

6. Save Secrets and reboot/redeploy the app.

The top of the tracker should now say:

`Data storage: Supabase Cloud`

## 5. Test from the iPhone

Open the Streamlit URL in Safari and import one FanDuel or Fanatics screenshot.

Confirm:

- the bet appears in Dashboard/History;
- refreshing the page does not remove it;
- opening the same Streamlit URL on another device shows the same bet;
- Supabase `bets` and `bet_legs` tables contain the imported bet;
- Supabase Storage contains the screenshot in `bet-screenshots`.

## 6. Put it on the iPhone Home Screen

In Safari:

1. Open the Streamlit tracker.
2. Tap **Share**.
3. Tap **Add to Home Screen**.
4. Name it `Bet Tracker`.

You can then launch the tracker from its Home Screen icon with no Windows computer running.

## 7. Next phase: direct Shortcut upload

After the cloud app passes the test above, the next version will add a small authenticated upload endpoint. Your iOS Shortcut will send a detected bet screenshot directly to that endpoint, which will parse and save it to Supabase. That removes the need for the iCloud `Bets` folder entirely.
