import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DEFAULT_SUPABASE_URL = "https://mdbruqgyxeyxzpasfltv.supabase.co"


def _secret(name, default=None):
    value = os.getenv(name)
    if value:
        return value

    # Streamlit Cloud / local .streamlit/secrets.toml support.
    try:
        import streamlit as st
        if name in st.secrets:
            value = st.secrets[name]
            if value:
                return str(value)
    except Exception:
        pass

    return default


def supabase_url():
    return str(_secret("SUPABASE_URL", DEFAULT_SUPABASE_URL)).rstrip("/")


def service_role_key():
    key = _secret("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError(
            "Missing SUPABASE_SERVICE_ROLE_KEY. Add it to Streamlit secrets "
            "or the server environment. Never place the service-role key in "
            "browser/client JavaScript."
        )
    return str(key)


def bet_upload_token():
    token = _secret("BET_UPLOAD_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing BET_UPLOAD_TOKEN. Add the same token used by the "
            "Supabase Edge Functions to Streamlit secrets or the server environment."
        )
    return str(token)


def _json_request(url, method="GET", body=None, headers=None, timeout=60):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)

    req = Request(
        url,
        data=payload,
        headers=req_headers,
        method=method,
    )

    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return None
            return json.loads(raw)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} calling {url}: {raw or exc.reason}"
        ) from exc


def rest_request(table, method="GET", query=None, body=None, prefer=None, timeout=60):
    key = service_role_key()
    url = f"{supabase_url()}/rest/v1/{table}"
    if query:
        url += "?" + urlencode(query, doseq=True, safe="(),.*:!")

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    if prefer:
        headers["Prefer"] = prefer

    return _json_request(
        url,
        method=method,
        body=body,
        headers=headers,
        timeout=timeout,
    )


def invoke_update_live_bets(body=None, timeout=120):
    url = f"{supabase_url()}/functions/v1/update-live-bets"
    return _json_request(
        url,
        method="POST",
        body=body or {},
        headers={
            "x-upload-token": bet_upload_token(),
        },
        timeout=timeout,
    )


def refresh_all_active_bets(batch_size=50, max_batches=100):
    """
    Refresh every currently active/open leg.

    update-live-bets v8 filters out settled parent bets and settled legs.
    Pagination continues with last_leg_id until has_more is false.
    """
    batch_size = max(1, min(50, int(batch_size)))
    after_leg_id = 0
    batches = []
    totals = {
        "processed": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "summaries_loaded": 0,
        "season_stat_requests": 0,
    }

    for _ in range(max_batches):
        result = invoke_update_live_bets(
            {
                "after_leg_id": after_leg_id,
                "batch_size": batch_size,
            }
        ) or {}

        batches.append(result)

        for key in totals:
            try:
                totals[key] += int(result.get(key) or 0)
            except Exception:
                pass

        last_leg_id = result.get("last_leg_id")
        has_more = bool(result.get("has_more"))

        if not has_more:
            return {
                "ok": all(bool(x.get("ok", True)) for x in batches),
                "batches": len(batches),
                **totals,
                "last_leg_id": last_leg_id,
                "has_more": False,
                "results": [
                    item
                    for batch in batches
                    for item in (batch.get("results") or [])
                ],
            }

        if last_leg_id is None:
            raise RuntimeError(
                "update-live-bets returned has_more=true without last_leg_id."
            )

        new_after = int(last_leg_id)
        if new_after <= after_leg_id:
            raise RuntimeError(
                "update-live-bets pagination did not advance."
            )

        after_leg_id = new_after

    raise RuntimeError(
        f"Refresh exceeded safety limit of {max_batches} batches."
    )


def recheck_leg(leg_id):
    """Force one leg through update-live-bets, even if its parent is settled."""
    return invoke_update_live_bets({"leg_id": int(leg_id)})
