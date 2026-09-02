import json
from datetime import datetime, timezone
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DEFAULT_SUPABASE_URL = "https://mdbruqgyxeyxzpasfltv.supabase.co"


def _secret(name, default=None):
    value = os.getenv(name)
    if value:
        return value

    try:
        import streamlit as st

        # Support flat Streamlit secrets.
        if name in st.secrets:
            value = st.secrets[name]
            if value:
                return str(value)

        # Support the existing nested [supabase] section.
        supabase = st.secrets.get("supabase", {})

        nested_map = {
            "SUPABASE_URL": "url",
            "SUPABASE_SERVICE_ROLE_KEY": "service_role_key",
            "BET_UPLOAD_TOKEN": "bet_upload_token",
        }

        nested_key = nested_map.get(name)

        if nested_key and nested_key in supabase:
            value = supabase[nested_key]
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


def invoke_edge_function(function_name, body=None, timeout=120):
    url = f"{supabase_url()}/functions/v1/{function_name}"
    return _json_request(
        url,
        method="POST",
        body=body or {},
        headers={
            "x-upload-token": bet_upload_token(),
        },
        timeout=timeout,
    )


def invoke_update_live_bets(body=None, timeout=120):
    return invoke_edge_function(
        "update-live-bets",
        body=body,
        timeout=timeout,
    )


def invoke_match_events(body=None, timeout=120):
    return invoke_edge_function(
        "match-events",
        body=body,
        timeout=timeout,
    )


def invoke_match_players(body=None, timeout=180):
    return invoke_edge_function(
        "match-players",
        body=body,
        timeout=timeout,
    )


def _run_match_events_all(batch_size=8, max_batches=100):
    after_leg_id = 0
    batches = []
    totals = {
        "processed": 0,
        "matched": 0,
        "unmatched": 0,
        "skipped": 0,
        "inherited": 0,
    }

    for _ in range(max_batches):
        result = invoke_match_events(
            {
                "after_leg_id": after_leg_id,
                "batch_size": min(10, max(1, int(batch_size))),
            }
        ) or {}

        if not result.get("ok", True):
            raise RuntimeError(
                f"match-events failed: {result}"
            )

        batches.append(result)

        for key in totals:
            try:
                totals[key] += int(result.get(key) or 0)
            except Exception:
                pass

        has_more = bool(result.get("has_more"))
        last_leg_id = result.get("last_leg_id")

        if not has_more:
            return {
                "ok": True,
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
                "match-events returned has_more=true without last_leg_id."
            )

        new_after = int(last_leg_id)
        if new_after <= after_leg_id:
            raise RuntimeError(
                "match-events pagination did not advance."
            )

        after_leg_id = new_after

    raise RuntimeError(
        f"match-events exceeded safety limit of {max_batches} batches."
    )


def refresh_all_active_bets(batch_size=50, max_batches=100):
    """
    One app refresh does the entire live-data pipeline:

      1. Match currently-unmatched team/game legs.
         match-events uses parent placed_at first and source_captured_at
         as its fallback timestamp.
      2. Match currently-unmatched NFL player props.
         match-players uses the same timestamp fallback when explicit
         season/week context is absent.
      3. Run update-live-bets across all active legs.
      4. Return one combined summary to Streamlit.

    Settled legs are skipped by the normal batch paths. Direct leg_id
    calls remain available for manual rechecks.
    """

    matching_events = _run_match_events_all(
        batch_size=8,
        max_batches=max_batches,
    )

    matching_players = invoke_match_players({}) or {}
    if not matching_players.get("ok", True):
        raise RuntimeError(
            f"match-players failed: {matching_players}"
        )

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

        if not result.get("ok", True):
            raise RuntimeError(
                f"update-live-bets failed: {result}"
            )

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
                "match_events": matching_events,
                "match_players": matching_players,
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


def recalculate_parent_from_manual_leg(leg_id):
    """
    Recalculate parent/Round Robin settlement from the leg status already
    stored in Supabase WITHOUT asking ESPN to grade the leg again.
    """
    return invoke_update_live_bets({
        "settlement_only_leg_id": int(leg_id),
    })


ACTIVE_BET_STATUSES = {"PENDING", "OPEN", "LIVE", "IN_PROGRESS"}


def list_bets(status=None):
    """
    Supabase-backed replacement for the legacy database.db.list_bets().

    The app historically requests list_bets("OPEN"). Supabase uses
    PENDING as its normal open state, so OPEN is treated as all active
    statuses for backward-compatible UI behavior.
    """
    query = {
        "select": "*",
        "order": "placed_at.desc.nullslast,id.desc",
    }

    if status:
        normalized = str(status).strip().upper()

        if normalized == "OPEN":
            query["status"] = "in.(PENDING,OPEN,LIVE,IN_PROGRESS)"
        else:
            query["status"] = f"eq.{normalized}"

    return rest_request(
        "bets",
        query=query,
    ) or []


def list_legs(bet_row_id):
    """
    Supabase-backed replacement for database.db.list_legs().
    """
    return rest_request(
        "bet_legs",
        query={
            "select": "*",
            "bet_row_id": f"eq.{int(bet_row_id)}",
            "order": "leg_index.asc.nullslast,id.asc",
        },
    ) or []


def update_bet_espn_scope(
    bet_id,
    season_year=None,
    season_type=None,
    week=None,
):
    """
    Persist an explicit ESPN schedule scope directly to Supabase.

    ESPN season type:
      1 = preseason
      2 = regular season
      3 = postseason

    Null values restore automatic/date-based matching.
    """
    return rest_request(
        "bets",
        method="PATCH",
        query={
            "id": f"eq.{int(bet_id)}",
        },
        body={
            "espn_season_year": season_year,
            "espn_season_type": season_type,
            "espn_week": week,
        },
        prefer="return=representation",
    ) or []


def update_leg_manual_status(leg_id, status):
    """
    Update one leg status directly in Supabase.

    OPEN is normalized to PENDING because PENDING is the canonical
    open status used by the current Edge Functions.
    """
    normalized = str(status or "PENDING").strip().upper()

    if normalized == "OPEN":
        normalized = "PENDING"

    return rest_request(
        "bet_legs",
        method="PATCH",
        query={
            "id": f"eq.{int(leg_id)}",
        },
        body={
            "status": normalized,
        },
        prefer="return=representation",
    ) or []




def list_all_table_rows(
    table,
    order="id.asc",
    page_size=1000,
):
    # Read a whole Supabase table using PostgREST paging.
    rows = []
    offset = 0
    page_size = max(1, min(int(page_size), 1000))

    while True:
        query = {
            "select": "*",
            "limit": str(page_size),
            "offset": str(offset),
        }

        if order:
            query["order"] = order

        page = rest_request(
            table,
            query=query,
            timeout=120,
        ) or []

        rows.extend(page)

        if len(page) < page_size:
            break

        offset += page_size

    return rows


def export_backup_tables():
    # Database-record backup. Screenshot image bytes remain in Storage.
    table_orders = {
        "bets": "id.asc",
        "bet_legs": "id.asc",
        "bet_combinations": "id.asc",
        "bet_combination_legs": "id.asc",
        "incoming_bet_screenshots": "id.asc",
    }

    return {
        table: list_all_table_rows(
            table,
            order=order,
        )
        for table, order in table_orders.items()
    }




def get_notification_settings():
    rows = rest_request(
        "notification_settings",
        query={
            "select": "*",
            "id": "eq.1",
            "limit": "1",
        },
    ) or []

    if rows:
        return rows[0]

    return {
        "id": 1,
        "wins_enabled": True,
        "losses_enabled": False,
        "big_wins_enabled": True,
        "big_win_profit_threshold": 100.0,
        "import_failures_enabled": True,
        "needs_review_enabled": False,
        "tracking_errors_enabled": False,
    }


def update_notification_settings(settings):
    allowed = {
        "wins_enabled",
        "losses_enabled",
        "big_wins_enabled",
        "big_win_profit_threshold",
        "import_failures_enabled",
        "needs_review_enabled",
        "tracking_errors_enabled",
    }

    body = {
        key: value
        for key, value in (settings or {}).items()
        if key in allowed
    }

    if "big_win_profit_threshold" in body:
        body["big_win_profit_threshold"] = max(
            0.0,
            float(body["big_win_profit_threshold"]),
        )

    body["updated_at"] = datetime.now(timezone.utc).isoformat()

    rows = rest_request(
        "notification_settings",
        method="PATCH",
        query={
            "id": "eq.1",
        },
        body=body,
        prefer="return=representation",
    ) or []

    if rows:
        return rows[0]

    # This fallback is mainly useful if the migration created the table
    # but the singleton row was removed accidentally.
    insert_body = {
        "id": 1,
        "wins_enabled": True,
        "losses_enabled": False,
        "big_wins_enabled": True,
        "big_win_profit_threshold": 100.0,
        "import_failures_enabled": True,
        "needs_review_enabled": False,
        "tracking_errors_enabled": False,
        **body,
    }

    rows = rest_request(
        "notification_settings",
        method="POST",
        body=insert_body,
        prefer="return=representation",
    ) or []

    return rows[0] if rows else insert_body


def send_test_pushover(
    title="Sports Bet Tracker",
    message="✅ Pushover notifications are connected.",
):
    return invoke_edge_function(
        "test-pushover",
        body={
            "title": str(title),
            "message": str(message),
        },
        timeout=60,
    )



def set_big_win_hidden(bet_id, hidden=True):
    """
    Persist whether a qualifying big win should be hidden from the
    Big Wins tab. This does not remove the bet or affect analytics.
    """
    return rest_request(
        "bets",
        method="PATCH",
        query={
            "id": f"eq.{int(bet_id)}",
        },
        body={
            "big_win_hidden": bool(hidden),
        },
        prefer="return=representation",
    ) or []


def list_round_robin_combinations(bet_id):
    """
    Return Round Robin combinations plus their linked bet-leg IDs.

    This is read-only and uses the existing server-side Supabase service
    role configuration. It does not modify settlement data.
    """
    bet_id = int(bet_id)

    combos = rest_request(
        "bet_combinations",
        query={
            "select": (
                "id,bet_row_id,combination_index,stake,odds,"
                "potential_payout,actual_payout,status,created_at"
            ),
            "bet_row_id": f"eq.{bet_id}",
            "order": "combination_index.asc",
        },
    ) or []

    if not combos:
        return []

    combo_ids = [
        int(row["id"])
        for row in combos
        if row.get("id") is not None
    ]

    links = []
    if combo_ids:
        id_filter = ",".join(str(x) for x in combo_ids)
        links = rest_request(
            "bet_combination_legs",
            query={
                "select": "combination_id,bet_leg_id,leg_position",
                "combination_id": f"in.({id_filter})",
                "order": "combination_id.asc,leg_position.asc",
            },
        ) or []

    links_by_combo = {}
    for row in links:
        cid = row.get("combination_id")
        links_by_combo.setdefault(cid, []).append(row)

    for combo in combos:
        combo["combination_legs"] = links_by_combo.get(
            combo.get("id"),
            [],
        )

    return combos

# ----------------------------------------------------------------------
# Season Futures — Supabase-backed helpers
# ----------------------------------------------------------------------

SUPPORTED_SEASON_MARKETS = {
    "passing yards",
    "passing tds",
    "passing touchdowns",
    "interceptions",
    "rushing yards",
    "rushing tds",
    "rushing touchdowns",
    "receiving yards",
    "receptions",
    "receiving tds",
    "receiving touchdowns",
    "regular season passing yards",
    "regular season passing tds",
    "regular season passing touchdowns",
    "regular season interceptions",
    "regular season rushing yards",
    "regular season rushing tds",
    "regular season rushing touchdowns",
    "regular season receiving yards",
    "regular season receptions",
    "regular season receiving tds",
    "regular season receiving touchdowns",
}


def is_supported_season_market(market):
    value = str(market or "").strip().lower()
    return value in SUPPORTED_SEASON_MARKETS


def list_supabase_bets(sport=None):
    query = {
        "select": (
            "id,sportsbook,sportsbook_bet_id,status,bet_type,leg_count,"
            "current_odds,stake,to_pay,paid,placed_at,source_captured_at,"
            "sport,headline,espn_season_year,espn_season_type,espn_week"
        ),
        "order": "id.desc",
    }

    if sport:
        query["sport"] = f"eq.{str(sport).upper()}"

    return rest_request(
        "bets",
        query=query,
    ) or []


def list_supabase_legs(bet_id):
    return rest_request(
        "bet_legs",
        query={
            "select": (
                "id,bet_row_id,leg_index,selection,market,line_value,"
                "direction,odds,status,event_team_a,event_team_b,"
                "espn_event_id,espn_athlete_id,tracking_scope,"
                "future_season_year,future_season_type,future_state,"
                "future_current,future_games_played,future_pace,"
                "future_updated_at,espn_season_year,espn_season_type,"
                "espn_week"
            ),
            "bet_row_id": f"eq.{int(bet_id)}",
            "order": "leg_index.asc,id.asc",
        },
    ) or []


def list_future_candidates():
    rows = []

    for bet in list_supabase_bets("NFL"):
        for leg in list_supabase_legs(bet["id"]):
            if not is_supported_season_market(leg.get("market")):
                continue

            # Already-tracked futures belong only in the tracked table below.
            # Do not offer them again in the Add / configure dropdown.
            if str(leg.get("tracking_scope") or "").strip().upper() == "SEASON":
                continue

            row = dict(leg)
            row["bet_headline"] = bet.get("headline")
            row["bet_sport"] = bet.get("sport")
            row["bet_status"] = bet.get("status")
            row["bet_odds"] = bet.get("current_odds")
            row["bet_stake"] = bet.get("stake")
            row["bet_to_pay"] = bet.get("to_pay")
            rows.append(row)

    return rows


def list_future_legs():
    legs = rest_request(
        "bet_legs",
        query={
            "select": (
                "id,bet_row_id,leg_index,selection,market,line_value,"
                "direction,odds,status,event_team_a,event_team_b,"
                "espn_event_id,espn_athlete_id,tracking_scope,"
                "future_season_year,future_season_type,future_state,"
                "future_current,future_games_played,future_pace,"
                "future_updated_at,espn_season_year,espn_season_type,"
                "espn_week"
            ),
            "tracking_scope": "eq.SEASON",
            "order": "bet_row_id.desc,leg_index.asc,id.asc",
        },
    ) or []

    if not legs:
        return []

    bet_ids = sorted({
        int(leg["bet_row_id"])
        for leg in legs
        if leg.get("bet_row_id") is not None
    })

    bet_map = {}
    for bet_id in bet_ids:
        bets = rest_request(
            "bets",
            query={
                "select": (
                    "id,headline,sport,status,current_odds,stake,to_pay,"
                    "paid,placed_at,source_captured_at"
                ),
                "id": f"eq.{bet_id}",
                "limit": "1",
            },
        ) or []

        if bets:
            bet_map[bet_id] = bets[0]

    rows = []
    for leg in legs:
        row = dict(leg)
        bet = bet_map.get(int(leg["bet_row_id"]), {})
        row["bet_headline"] = bet.get("headline")
        row["bet_sport"] = bet.get("sport")
        row["bet_status"] = bet.get("status")
        row["bet_odds"] = bet.get("current_odds")
        row["bet_stake"] = bet.get("stake")
        row["bet_to_pay"] = bet.get("to_pay")
        rows.append(row)

    return rows


def configure_future_leg(
    leg_id,
    line_value,
    direction,
    season_year,
    season_type=2,
):
    direction = str(direction or "OVER").strip().upper()

    if direction not in {"OVER", "UNDER"}:
        raise ValueError("direction must be OVER or UNDER")

    result = rest_request(
        "bet_legs",
        method="PATCH",
        query={
            "id": f"eq.{int(leg_id)}",
        },
        body={
            "line_value": float(line_value),
            "direction": direction,
            "tracking_scope": "SEASON",
            "future_season_year": int(season_year),
            "future_season_type": int(season_type),
            "status": "PENDING",
        },
        prefer="return=representation",
    )

    return result or []


def refresh_future_leg(leg_id):
    """
    Refresh a season future entirely through Supabase Edge Functions.

    1) match-players ensures the ESPN athlete ID exists.
    2) update-live-bets handles the SEASON stat lookup and persists
       future_current / games / pace / state.
    """
    leg_id = int(leg_id)

    match_result = invoke_match_players({
        "leg_id": leg_id,
    }) or {}

    update_result = invoke_update_live_bets({
        "leg_id": leg_id,
    }) or {}

    return {
        "ok": bool(update_result.get("ok", False)),
        "match_players": match_result,
        "update_live_bets": update_result,
    }


def refresh_all_future_legs():
    rows = list_future_legs()
    results = []

    for leg in rows:
        leg_id = int(leg["id"])

        try:
            result = refresh_future_leg(leg_id)
            results.append({
                "leg_id": leg_id,
                "selection": leg.get("selection"),
                "ok": bool(result.get("ok")),
                "result": result,
            })
        except Exception as exc:
            results.append({
                "leg_id": leg_id,
                "selection": leg.get("selection"),
                "ok": False,
                "error": str(exc),
            })

    return {
        "ok": all(row.get("ok") for row in results) if results else True,
        "processed": len(results),
        "successful": sum(1 for row in results if row.get("ok")),
        "failed": sum(1 for row in results if not row.get("ok")),
        "results": results,
    }

