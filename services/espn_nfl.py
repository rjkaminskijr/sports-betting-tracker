import json
import re
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode

BASE = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl'
UA = 'Mozilla/5.0 BetTracker/1.0'

TEAM_ALIASES = {
    'ARI':'ARI','ARIZONA':'ARI','CARDINALS':'ARI','ARIZONA CARDINALS':'ARI','ARI CARDINALS':'ARI',
    'ATL':'ATL','FALCONS':'ATL','ATLANTA':'ATL','ATLANTA FALCONS':'ATL',
    'BAL':'BAL','RAVENS':'BAL','BALTIMORE':'BAL','BALTIMORE RAVENS':'BAL',
    'BUF':'BUF','BILLS':'BUF','BUFFALO':'BUF','BUFFALO BILLS':'BUF','BUF BILLS':'BUF',
    'CAR':'CAR','PANTHERS':'CAR','CAROLINA':'CAR','CAROLINA PANTHERS':'CAR','CAR PANTHERS':'CAR',
    'CHI':'CHI','BEARS':'CHI','CHICAGO':'CHI','CHICAGO BEARS':'CHI',
    'CIN':'CIN','BENGALS':'CIN','CINCINNATI':'CIN','CINCINNATI BENGALS':'CIN',
    'CLE':'CLE','BROWNS':'CLE','CLEVELAND':'CLE','CLEVELAND BROWNS':'CLE','CLE BROWNS':'CLE',
    'DAL':'DAL','COWBOYS':'DAL','DALLAS':'DAL','DALLAS COWBOYS':'DAL',
    'DEN':'DEN','BRONCOS':'DEN','DENVER':'DEN','DENVER BRONCOS':'DEN',
    'DET':'DET','LIONS':'DET','DETROIT':'DET','DETROIT LIONS':'DET',
    'GB':'GB','PACKERS':'GB','GREEN BAY':'GB','GREEN BAY PACKERS':'GB',
    'HOU':'HOU','TEXANS':'HOU','HOUSTON':'HOU','HOUSTON TEXANS':'HOU',
    'IND':'IND','COLTS':'IND','INDIANAPOLIS':'IND','INDIANAPOLIS COLTS':'IND',
    'JAX':'JAX','JAC':'JAX','JAGUARS':'JAX','JACKSONVILLE':'JAX','JACKSONVILLE JAGUARS':'JAX',
    'KC':'KC','CHIEFS':'KC','KANSAS CITY':'KC','KANSAS CITY CHIEFS':'KC',
    'LV':'LV','RAIDERS':'LV','LAS VEGAS':'LV','LAS VEGAS RAIDERS':'LV',
    'LAC':'LAC','CHARGERS':'LAC','LA CHARGERS':'LAC','LOS ANGELES CHARGERS':'LAC',
    'LAR':'LAR','RAMS':'LAR','LA RAMS':'LAR','LOS ANGELES RAMS':'LAR',
    'MIA':'MIA','DOLPHINS':'MIA','MIAMI':'MIA','MIAMI DOLPHINS':'MIA',
    'MIN':'MIN','VIKINGS':'MIN','MINNESOTA':'MIN','MINNESOTA VIKINGS':'MIN',
    'NE':'NE','PATRIOTS':'NE','NEW ENGLAND':'NE','NEW ENGLAND PATRIOTS':'NE','NE PATRIOTS':'NE',
    'NO':'NO','SAINTS':'NO','NEW ORLEANS':'NO','NEW ORLEANS SAINTS':'NO',
    'NYG':'NYG','GIANTS':'NYG','NEW YORK GIANTS':'NYG',
    'NYJ':'NYJ','JETS':'NYJ','NEW YORK JETS':'NYJ',
    'PHI':'PHI','EAGLES':'PHI','PHILADELPHIA':'PHI','PHILADELPHIA EAGLES':'PHI',
    'PIT':'PIT','STEELERS':'PIT','PITTSBURGH':'PIT','PITTSBURGH STEELERS':'PIT','PIT STEELERS':'PIT',
    'SEA':'SEA','SEAHAWKS':'SEA','SEATTLE':'SEA','SEATTLE SEAHAWKS':'SEA',
    'SF':'SF','49ERS':'SF','SAN FRANCISCO':'SF','SAN FRANCISCO 49ERS':'SF',
    'TB':'TB','BUCCANEERS':'TB','TAMPA BAY':'TB','TAMPA BAY BUCCANEERS':'TB','BUCS':'TB',
    'TEN':'TEN','TITANS':'TEN','TENNESSEE':'TEN','TENNESSEE TITANS':'TEN',
    'WSH':'WSH','WAS':'WSH','COMMANDERS':'WSH','WASHINGTON':'WSH','WASHINGTON COMMANDERS':'WSH'
}


def _get_json(url, timeout=12):
    req = Request(url, headers={'User-Agent': UA, 'Accept':'application/json'})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def scoreboard_for_date(date_value):
    if isinstance(date_value, datetime):
        ds = date_value.strftime('%Y%m%d')
    else:
        ds = str(date_value).replace('-','')[:8]
    return _get_json(f'{BASE}/scoreboard?{urlencode({"dates": ds, "limit": 100})}')

def scoreboard_for_week(season_year, season_type, week):
    """Return the ESPN NFL scoreboard for one explicit season/week.

    season_type: 1 preseason, 2 regular season, 3 postseason.
    This prevents a Week 1 regular-season bet from accidentally matching the
    current preseason game when both involve the same team.
    """
    params={
        'seasontype': int(season_type),
        'week': int(week),
        'year': int(season_year),
        'limit': 100,
    }
    return _get_json(f'{BASE}/scoreboard?{urlencode(params)}')


def game_summary(event_id):
    return _get_json(f'{BASE}/summary?{urlencode({"event": str(event_id)})}')


def normalize_team(value):
    if not value:
        return None
    x = re.sub(r'[^A-Z0-9 ]+', ' ', str(value).upper())
    x = re.sub(r'\s+', ' ', x).strip()
    if x in TEAM_ALIASES:
        return TEAM_ALIASES[x]
    # Prefer the longest alias so "NEW ENGLAND PATRIOTS" wins over PATRIOTS.
    for key in sorted(TEAM_ALIASES, key=len, reverse=True):
        if len(key) > 2 and re.search(rf'\b{re.escape(key)}\b', x):
            return TEAM_ALIASES[key]
    for token in x.split():
        if token in TEAM_ALIASES:
            return TEAM_ALIASES[token]
    return None


def _event_teams(event):
    comp = (event.get('competitions') or [{}])[0]
    out = []
    for c in comp.get('competitors') or []:
        team = c.get('team') or {}
        try:
            score = float(c.get('score') or 0)
        except Exception:
            score = 0.0
        out.append({
            'id': team.get('id'),
            'abbr': team.get('abbreviation'),
            'name': team.get('displayName') or team.get('shortDisplayName'),
            'homeAway': c.get('homeAway'),
            'score': score,
            'winner': c.get('winner')
        })
    return out


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return None


def _score_event(event, wanted, target_dt=None):
    teams = _event_teams(event)
    abbrs = {t.get('abbr') for t in teams if t.get('abbr')}
    wanted = [x for x in wanted if x]
    matches = sum(1 for x in wanted if x in abbrs)
    if wanted and matches == 0:
        return -999
    score = matches * 100
    if len(wanted) >= 2 and matches == len(set(wanted)):
        score += 100
    if target_dt:
        event_dt = _parse_dt(event.get('date'))
        if event_dt:
            delta_hours = abs((event_dt - target_dt).total_seconds()) / 3600
            score += max(0, 48 - delta_hours)
    return score


_PLAYER_TEAM_CACHE = {}
_ROSTER_CACHE = {}

def team_roster(team_abbr):
    team=str(team_abbr or '').upper()
    if not team:
        return None
    if team in _ROSTER_CACHE:
        return _ROSTER_CACHE[team]
    try:
        data=_get_json(f'{BASE}/teams/{team.lower()}/roster')
    except Exception:
        data=None
    _ROSTER_CACHE[team]=data
    return data

def _roster_names(obj):
    out=[]
    def walk(x):
        if isinstance(x, dict):
            name=x.get('displayName') or x.get('fullName')
            if name:
                out.append(str(name))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    return out

def team_for_player(player_name):
    """Resolve a player to an NFL team using ESPN team rosters.

    This is used for mobile DraftKings props where the slip shows only the
    player's name/helmet and no matchup text. Results are cached for the app
    session so later refreshes are fast.
    """
    key=_norm_name(player_name)
    if not key:
        return None
    if key in _PLAYER_TEAM_CACHE:
        return _PLAYER_TEAM_CACHE[key]
    teams=sorted(set(TEAM_ALIASES.values()))
    for team in teams:
        roster=team_roster(team)
        if not roster:
            continue
        for name in _roster_names(roster):
            if _norm_name(name)==key:
                _PLAYER_TEAM_CACHE[key]=team
                return team
    _PLAYER_TEAM_CACHE[key]=None
    return None

def find_event(team_a=None, team_b=None, start_time=None, selection=None, season_year=None, season_type=None, week=None):
    """Find an NFL event robustly using matchup teams, selection team and date.

    DK screenshots sometimes lose one matchup team during OCR, so we score every
    candidate rather than requiring an exact two-team match.
    """
    wanted = []
    for raw in (team_a, team_b, selection):
        n = normalize_team(raw)
        if n and n not in wanted:
            wanted.append(n)

    target_dt = _parse_dt(start_time)
    if not wanted:
        return None

    best = None
    best_score = -999

    # Explicit schedule scope wins over date/current-week matching. This is
    # critical in late August when preseason and regular-season Week 1 games
    # are both close enough to be plausible matches for the same team.
    if season_year and season_type and week:
        try:
            data = scoreboard_for_week(season_year, season_type, week)
        except Exception:
            data = None
        for ev in (data or {}).get('events') or []:
            score = _score_event(ev, wanted, target_dt=None)
            if score > best_score:
                best, best_score = ev, score
    else:
        dates = []
        if target_dt:
            dates = [target_dt + timedelta(days=d) for d in (-2, -1, 0, 1, 2)]
        else:
            now = datetime.now()
            dates = [now + timedelta(days=d) for d in range(-1, 8)]

        seen_dates = set()
        for d in dates:
            key = d.strftime('%Y%m%d')
            if key in seen_dates:
                continue
            seen_dates.add(key)
            try:
                data = scoreboard_for_date(d)
            except Exception:
                continue
            for ev in data.get('events') or []:
                score = _score_event(ev, wanted, target_dt)
                if score > best_score:
                    best, best_score = ev, score

    # Require at least one team match whenever we know a team.
    if wanted and best_score < 100:
        return None
    return best


def event_snapshot(event):
    comp = (event.get('competitions') or [{}])[0]
    status = event.get('status') or comp.get('status') or {}
    typ = status.get('type') or {}
    teams = _event_teams(event)
    home = next((x for x in teams if x.get('homeAway') == 'home'), None)
    away = next((x for x in teams if x.get('homeAway') == 'away'), None)
    state = str(typ.get('state') or '').lower()
    status_name = typ.get('name')
    completed = bool(typ.get('completed')) or state == 'post'
    pregame = state == 'pre' or status_name in ('STATUS_SCHEDULED', 'STATUS_DELAYED')
    live = state == 'in' or (not completed and not pregame and (status.get('period') or 0) > 0)
    return {
        'event_id': event.get('id'), 'name': event.get('name'), 'date': event.get('date'),
        'status': status_name, 'status_detail': typ.get('shortDetail') or typ.get('detail'),
        'completed': completed, 'pregame': pregame, 'live': live,
        'period': status.get('period'), 'clock': status.get('displayClock'),
        'home': home, 'away': away
    }


def snapshot_display(snapshot):
    away = snapshot.get('away') or {}
    home = snapshot.get('home') or {}
    detail = snapshot.get('status_detail') or ''
    if snapshot.get('pregame'):
        return f"{away.get('abbr','')} @ {home.get('abbr','')} — {detail}".strip(' —')
    if away and home:
        score = f"{away.get('abbr')} {int(away.get('score',0))} - {home.get('abbr')} {int(home.get('score',0))}"
        return f"{score} — {detail}" if detail else score
    return detail or None


def _norm_name(s):
    s = re.sub(r'\b(JR|SR|II|III|IV)\.?\b', '', str(s or '').upper())
    return re.sub(r'[^A-Z0-9]', '', s)


def player_stats(summary):
    results = []
    players = ((summary.get('boxscore') or {}).get('players') or [])
    for group in players:
        team = (group.get('team') or {}).get('abbreviation')
        for stat_group in group.get('statistics') or []:
            labels = stat_group.get('labels') or []
            names = stat_group.get('names') or []
            category = stat_group.get('name') or stat_group.get('type') or ''
            for athlete_row in stat_group.get('athletes') or []:
                athlete = athlete_row.get('athlete') or {}
                vals = athlete_row.get('stats') or []
                d = {}
                for i, v in enumerate(vals):
                    k = names[i] if i < len(names) else (labels[i] if i < len(labels) else str(i))
                    d[k] = v
                results.append({
                    'name': athlete.get('displayName') or athlete.get('shortName'),
                    'id': athlete.get('id'), 'team': team, 'category': category, 'stats': d
                })
    return results


def find_player_rows(summary, player_name):
    target = _norm_name(player_name)
    rows = [r for r in player_stats(summary) if _norm_name(r.get('name')) == target]
    if rows:
        return rows
    # Conservative fallback: last name + first initial, useful for OCR punctuation/suffix issues.
    target_words = re.findall(r'[A-Z0-9]+', str(player_name or '').upper())
    if not target_words:
        return []
    last = target_words[-1]
    first_initial = target_words[0][:1]
    out = []
    for r in player_stats(summary):
        words = re.findall(r'[A-Z0-9]+', str(r.get('name') or '').upper())
        if words and words[-1] == last and (not first_initial or words[0].startswith(first_initial)):
            out.append(r)
    return out


def _num(v):
    if v is None:
        return None
    s = str(v).replace(',', '').strip()
    m = re.search(r'-?\d+(?:\.\d+)?', s)
    return float(m.group()) if m else None


def _stat_from_rows(rows, category_hint, keys):
    vals = []
    for row in rows:
        category = str(row.get('category') or '').upper()
        if category_hint and category_hint not in category:
            continue
        for k, v in (row.get('stats') or {}).items():
            ku = str(k).upper()
            if any(ku == key.upper() or key.upper() in ku for key in keys):
                n = _num(v)
                if n is not None:
                    vals.append(n)
    return max(vals) if vals else None


def stat_value(summary, player_name, market):
    rows = find_player_rows(summary, player_name)
    if not rows:
        return None
    m = (market or '').upper()

    if 'ANYTIME' in m and ('TD' in m or 'TOUCHDOWN' in m):
        rush = _stat_from_rows(rows, 'RUSH', ['rushingTouchdowns', 'TD']) or 0
        rec = _stat_from_rows(rows, 'RECEIV', ['receivingTouchdowns', 'TD']) or 0
        return rush + rec
    if 'PASS' in m and 'YARD' in m:
        return _stat_from_rows(rows, 'PASS', ['passingYards', 'YDS'])
    if 'PASS' in m and ('TD' in m or 'TOUCHDOWN' in m):
        return _stat_from_rows(rows, 'PASS', ['passingTouchdowns', 'TD'])
    if 'INTERCEPTION' in m or re.search(r'\bINT', m):
        return _stat_from_rows(rows, 'PASS', ['interceptions', 'INT'])
    if 'RUSH' in m and 'YARD' in m:
        return _stat_from_rows(rows, 'RUSH', ['rushingYards', 'YDS'])
    if 'RUSH' in m and ('TD' in m or 'TOUCHDOWN' in m):
        return _stat_from_rows(rows, 'RUSH', ['rushingTouchdowns', 'TD'])
    if 'RECEIV' in m and 'YARD' in m:
        return _stat_from_rows(rows, 'RECEIV', ['receivingYards', 'YDS'])
    if 'RECEPTION' in m or 'CATCH' in m:
        return _stat_from_rows(rows, 'RECEIV', ['receptions', 'REC'])
    if 'RECEIV' in m and ('TD' in m or 'TOUCHDOWN' in m):
        return _stat_from_rows(rows, 'RECEIV', ['receivingTouchdowns', 'TD'])
    return None


def _sum_team_category(summary, team_abbr, category_hint, keys):
    """Sum a box-score stat across all players on one team.

    This is preferred for DraftKings team rushing/receiving yard markets because
    the wager is settled from team production, not from the game score.
    """
    team = normalize_team(team_abbr) or str(team_abbr or '').upper()
    total = 0.0
    found = False
    for row in player_stats(summary):
        if str(row.get('team') or '').upper() != team:
            continue
        category = str(row.get('category') or '').upper()
        if category_hint and category_hint not in category:
            continue
        for k, v in (row.get('stats') or {}).items():
            ku = str(k).upper()
            if any(ku == key.upper() or key.upper() in ku for key in keys):
                n = _num(v)
                if n is not None:
                    total += n
                    found = True
                    break
    return total if found else None


def team_stat_value(summary, team_name, market):
    """Return the live team-yardage value for supported DraftKings markets.

    Team Total Rushing Yards   -> sum of ESPN player rushing yards
    Team Total Receiving Yards -> sum of ESPN player receiving yards
    Team Total Yards           -> rushing + receiving yards
    """
    if not summary:
        return None
    team = normalize_team(team_name)
    if not team:
        return None
    m = str(market or '').upper()
    rush = None
    rec = None
    if 'RUSH' in m or ('TOTAL YARDS' in m and 'RECEIV' not in m):
        rush = _sum_team_category(summary, team, 'RUSH', ['rushingYards', 'YDS'])
    if 'RECEIV' in m or ('TOTAL YARDS' in m and 'RUSH' not in m):
        rec = _sum_team_category(summary, team, 'RECEIV', ['receivingYards', 'YDS'])
    if 'RUSH' in m:
        return rush
    if 'RECEIV' in m:
        return rec
    if 'TOTAL YARDS' in m:
        if rush is None and rec is None:
            return None
        return (rush or 0) + (rec or 0)
    return None
