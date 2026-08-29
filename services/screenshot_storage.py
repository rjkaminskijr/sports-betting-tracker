import hashlib
import mimetypes
from datetime import datetime, timezone
from database.db import get_supabase

BUCKET = 'bet-screenshots'


def save_screenshot(raw: bytes, filename: str, sportsbook: str = 'Unknown'):
    """Persist a screenshot in Supabase Storage when cloud mode is configured.

    Returns (path, public_url). In local mode both values are None because the
    uploaded Streamlit file itself is temporary and SQLite remains the fallback.
    """
    client = get_supabase()
    if client is None:
        return None, None
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else 'jpg').lower()
    stamp = datetime.now(timezone.utc).strftime('%Y/%m/%d')
    digest = hashlib.sha256(raw).hexdigest()[:18]
    safe_book = ''.join(ch for ch in (sportsbook or 'unknown').lower() if ch.isalnum() or ch in '-_') or 'unknown'
    path = f'{safe_book}/{stamp}/{digest}.{ext}'
    content_type = mimetypes.guess_type(filename)[0] or 'image/jpeg'
    bucket = client.storage.from_(BUCKET)
    try:
        bucket.upload(path, raw, {'content-type': content_type, 'upsert': 'true'})
    except Exception as exc:
        # If the object already exists, keep using its deterministic path.
        if 'duplicate' not in str(exc).lower() and 'already exists' not in str(exc).lower():
            raise
    try:
        public = bucket.get_public_url(path)
        if isinstance(public, dict):
            public = public.get('publicUrl') or public.get('public_url')
    except Exception:
        public = None
    return path, public
