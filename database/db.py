import json

from services.supabase_api import rest_request


ACTIVE_STATUSES = {"PENDING", "OPEN", "LIVE", "IN_PROGRESS"}


def _normalize_status_for_write(value):
    status = str(value or "PENDING").strip().upper()
    if status == "OPEN":
        return "PENDING"
    return status


def init_db():
    """
    Compatibility hook retained for app.py.

    The authoritative database is now Supabase, so there is no local SQLite
    schema to initialize.
    """
    return None


def _get_rows(table, query=None):
    data = rest_request(table, "GET", query=query or {})
    return data if isinstance(data, list) else []


def _patch(table, filters, values):
    query = dict(filters or {})
    return rest_request(
        table,
        "PATCH",
        query=query,
        body=values,
        prefer="return=representation",
    )


def _delete(table, filters):
    return rest_request(
        table,
        "DELETE",
        query=dict(filters or {}),
        prefer="return=representation",
    )


def is_duplicate(screenshot_hash=None, sportsbook_bet_id=None):
    if screenshot_hash:
        rows = _get_rows(
            "bets",
            {
                "select": "id",
                "screenshot_hash": f"eq.{screenshot_hash}",
                "limit": "1",
            },
        )
        if rows:
            return True

    if sportsbook_bet_id:
        rows = _get_rows(
            "bets",
            {
                "select": "id",
                "sportsbook_bet_id": f"eq.{sportsbook_bet_id}",
                "limit": "1",
            },
        )
        if rows:
            return True

    return False


def save_bet(b):
    money = b.get("money") or {}
    odds = b.get("odds") or {}
    event = b.get("event") or {}

    bet_row = {
        "sportsbook": b.get("sportsbook"),
        "sportsbook_bet_id": b.get("sportsbook_bet_id"),
        "status": _normalize_status_for_write(b.get("status")),
        "bet_type": b.get("bet_type"),
        "leg_count": b.get("leg_count") if b.get("leg_count") is not None else len(b.get("legs") or []),
        "current_odds": odds.get("current"),
        "original_odds": odds.get("original"),
        "boosted_odds": odds.get("boosted"),
        "stake": money.get("stake"),
        "to_pay": money.get("to_pay"),
        "paid": money.get("paid"),
        "cash_out": money.get("cash_out"),
        "promo": b.get("promo"),
        "placed_at": b.get("placed_at"),
        "sport": b.get("sport"),
        "headline": b.get("headline"),
        "subtitle": b.get("subtitle"),
        "event_name": event.get("name"),
        "raw_ocr_text": b.get("raw_ocr_text"),
        "screenshot_hash": b.get("screenshot_hash"),
        "source_filename": b.get("source_filename"),
        "normalized_json": b,
        "draftkings_share_url": b.get("draftkings_share_url"),
        "fanatics_share_url": b.get("fanatics_share_url"),
        "fanatics_shortlink": b.get("fanatics_shortlink"),
        "source_email_id": b.get("source_email_id"),
        "source_email_subject": b.get("source_email_subject"),
        "espn_season_year": b.get("espn_season_year"),
        "espn_season_type": b.get("espn_season_type"),
        "espn_week": b.get("espn_week"),
    }

    created = rest_request(
        "bets",
        "POST",
        body=bet_row,
        prefer="return=representation",
    )
    if not created:
        raise RuntimeError("Supabase did not return the inserted bet.")

    bid = int(created[0]["id"])

    legs = []
    for leg in b.get("legs") or []:
        ev = leg.get("event") or {}
        legs.append(
            {
                "bet_row_id": bid,
                "leg_index": leg.get("index"),
                "selection": leg.get("selection"),
                "market": leg.get("market"),
                "line_value": leg.get("line") if leg.get("line") is not None else leg.get("line_value"),
                "direction": leg.get("direction"),
                "odds": leg.get("odds"),
                "status": _normalize_status_for_write(leg.get("status")),
                "event_time": ev.get("start_time"),
                "raw_leg_text": leg.get("raw_leg_text"),
                "event_team_a": ev.get("away_team") or ev.get("team_1"),
                "event_team_b": ev.get("home_team") or ev.get("team_2"),
                "tracking_scope": leg.get("tracking_scope"),
                "future_season_year": leg.get("future_season_year"),
                "future_season_type": leg.get("future_season_type"),
                "espn_athlete_id": leg.get("espn_athlete_id"),
                "fanatics_event_id": leg.get("fanatics_event_id"),
                "fanatics_market_id": leg.get("fanatics_market_id"),
                "fanatics_selection_id": leg.get("fanatics_selection_id"),
                "espn_season_year": leg.get("espn_season_year"),
                "espn_season_type": leg.get("espn_season_type"),
                "espn_week": leg.get("espn_week"),
            }
        )

    if legs:
        rest_request(
            "bet_legs",
            "POST",
            body=legs,
            prefer="return=representation",
        )

    return bid


def list_bets(status=None):
    query = {
        "select": "*",
        "order": "placed_at.desc.nullslast,id.desc",
    }

    # Legacy app code asks for OPEN. Supabase's canonical active state is PENDING.
    if status:
        normalized = str(status).strip().upper()
        if normalized == "OPEN":
            query["status"] = "in.(PENDING,OPEN,LIVE,IN_PROGRESS)"
        else:
            query["status"] = f"eq.{normalized}"

    return _get_rows("bets", query)


def list_legs(bet_row_id):
    return _get_rows(
        "bet_legs",
        {
            "select": "*",
            "bet_row_id": f"eq.{int(bet_row_id)}",
            "order": "leg_index.asc.nullslast,id.asc",
        },
    )


def update_leg_live(leg_id, event_id, state, value, updated_at):
    return _patch(
        "bet_legs",
        {"id": f"eq.{int(leg_id)}"},
        {
            "espn_event_id": event_id,
            "live_state": state,
            "live_value": str(value) if value is not None else None,
            "live_updated_at": updated_at,
        },
    )


def replace_bet(b):
    ids = set()
    h = b.get("screenshot_hash")
    sbid = b.get("sportsbook_bet_id")

    if h:
        for row in _get_rows(
            "bets",
            {"select": "id", "screenshot_hash": f"eq.{h}"},
        ):
            ids.add(int(row["id"]))

    if sbid:
        for row in _get_rows(
            "bets",
            {"select": "id", "sportsbook_bet_id": f"eq.{sbid}"},
        ):
            ids.add(int(row["id"]))

    for bid in sorted(ids):
        _delete("bets", {"id": f"eq.{bid}"})

    return save_bet(b)


def update_bet_espn_scope(bet_id, season_year=None, season_type=None, week=None):
    return _patch(
        "bets",
        {"id": f"eq.{int(bet_id)}"},
        {
            "espn_season_year": season_year,
            "espn_season_type": season_type,
            "espn_week": week,
        },
    )


def update_leg_future_settings(leg_id, tracking_scope="SEASON", season_year=2026, season_type=2):
    return _patch(
        "bet_legs",
        {"id": f"eq.{int(leg_id)}"},
        {
            "tracking_scope": tracking_scope,
            "future_season_year": season_year,
            "future_season_type": season_type,
        },
    )


def update_leg_future_live(leg_id, athlete_id, state, current, games_played, pace, updated_at):
    return _patch(
        "bet_legs",
        {"id": f"eq.{int(leg_id)}"},
        {
            "espn_athlete_id": athlete_id,
            "future_state": state,
            "future_current": current,
            "future_games_played": games_played,
            "future_pace": pace,
            "future_updated_at": updated_at,
        },
    )


def update_leg_line_direction(leg_id, line_value, direction):
    return _patch(
        "bet_legs",
        {"id": f"eq.{int(leg_id)}"},
        {
            "line_value": str(line_value),
            "direction": direction,
        },
    )


def update_leg_manual_status(leg_id, status):
    return _patch(
        "bet_legs",
        {"id": f"eq.{int(leg_id)}"},
        {
            "status": _normalize_status_for_write(status),
        },
    )


def future_legs():
    legs = _get_rows(
        "bet_legs",
        {
            "select": "*",
            "tracking_scope": "eq.SEASON",
            "order": "bet_row_id.desc,leg_index.asc.nullslast",
        },
    )

    if not legs:
        return []

    bet_ids = sorted({int(x["bet_row_id"]) for x in legs if x.get("bet_row_id") is not None})
    if not bet_ids:
        return legs

    id_list = ",".join(str(x) for x in bet_ids)
    bets = _get_rows(
        "bets",
        {
            "select": "id,headline,sport,status,current_odds,stake,to_pay",
            "id": f"in.({id_list})",
        },
    )
    bet_map = {int(b["id"]): b for b in bets}

    out = []
    for leg in legs:
        row = dict(leg)
        bet = bet_map.get(int(leg["bet_row_id"]), {})
        row["bet_headline"] = bet.get("headline")
        row["bet_sport"] = bet.get("sport")
        row["bet_status"] = bet.get("status")
        row["bet_odds"] = bet.get("current_odds")
        row["bet_stake"] = bet.get("stake")
        row["bet_to_pay"] = bet.get("to_pay")
        out.append(row)

    return out
