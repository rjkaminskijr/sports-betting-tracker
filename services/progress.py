import re
from .espn_nfl import normalize_team, stat_value, team_stat_value, snapshot_display


def _line_num(leg):
    v = leg.get('line_value') or leg.get('line')
    if v is not None:
        m = re.search(r'\d+(?:\.\d+)?', str(v))
        if m:
            return float(m.group())
    text = ' '.join(str(leg.get(k) or '') for k in ('selection','market'))
    m = re.search(r'\b(?:OVER|UNDER|O|U)\s*(\d+(?:\.\d+)?)', text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r'\b(\d+(?:\.\d+)?)\+', text)
    return float(m.group(1)) if m else None


def _signed_line(leg):
    v = leg.get('line_value') or leg.get('line')
    text = str(v) if v is not None else ' '.join(str(leg.get(k) or '') for k in ('selection','market'))
    m = re.search(r'(?<!\d)([+-]\d+(?:\.\d+)?)', text)
    return float(m.group(1)) if m else None


def _direction(leg):
    if leg.get('direction'):
        return str(leg['direction']).upper()
    text = ' '.join(str(leg.get(k) or '') for k in ('selection','market')).upper()
    if re.search(r'\bOVER\b|\bO\s*\d', text):
        return 'OVER'
    if re.search(r'\bUNDER\b|\bU\s*\d', text):
        return 'UNDER'
    return None


def grade_numeric(value, line, direction, completed=False):
    if value is None or line is None:
        return {'state':'UNTRACKED'}
    if direction == 'OVER':
        winning = value > line
        final = 'WON' if winning else ('PUSH' if value == line else 'LOST')
        need = max(0, line - value)
    elif direction == 'UNDER':
        winning = value < line
        final = 'WON' if winning else ('PUSH' if value == line else 'LOST')
        need = None
    else:
        winning = value >= line
        final = 'WON' if winning else 'LOST'
        need = max(0, line - value)
    return {
        'state': final if completed else ('WINNING' if winning else 'LOSING'),
        'current': value, 'line': line, 'direction': direction, 'needed': need
    }


def evaluate_leg(leg, snapshot, summary=None):
    selection = leg.get('selection') or ''
    market = leg.get('market') or ''
    completed = bool(snapshot.get('completed'))
    mkt = market.upper()

    if snapshot.get('pregame'):
        return {'state':'PREGAME', 'current':snapshot_display(snapshot)}

    # Moneyline
    if 'MONEYLINE' in mkt:
        team = normalize_team(selection)
        home = snapshot.get('home') or {}
        away = snapshot.get('away') or {}
        picked = home if home.get('abbr') == team else away if away.get('abbr') == team else None
        other = away if picked is home else home if picked else None
        if not picked or not other:
            return {'state':'UNMATCHED', 'current':snapshot_display(snapshot)}
        pscore, oscore = picked.get('score',0), other.get('score',0)
        winning = pscore > oscore
        if completed:
            state = 'WON' if winning else ('PUSH' if pscore == oscore else 'LOST')
        else:
            state = 'WINNING' if winning else ('TIED' if pscore == oscore else 'LOSING')
        return {'state':state, 'current':snapshot_display(snapshot)}

    # Spread
    if 'SPREAD' in mkt or _signed_line(leg) is not None:
        team = normalize_team(selection)
        home = snapshot.get('home') or {}
        away = snapshot.get('away') or {}
        picked = home if home.get('abbr') == team else away if away.get('abbr') == team else None
        other = away if picked is home else home if picked else None
        line = _signed_line(leg)
        if picked and other and line is not None:
            margin = picked.get('score',0) + line - other.get('score',0)
            if completed:
                state = 'WON' if margin > 0 else ('PUSH' if margin == 0 else 'LOST')
            else:
                state = 'WINNING' if margin > 0 else ('TIED' if margin == 0 else 'LOSING')
            return {'state':state, 'current':snapshot_display(snapshot), 'line':line}

    # DraftKings team-yardage props. These must use ESPN box-score yards,
    # not the combined game score.
    if 'TEAM TOTAL YARDS' in mkt or 'TEAM TOTAL RUSHING YARDS' in mkt or 'TEAM TOTAL RECEIVING YARDS' in mkt:
        value = team_stat_value(summary, selection, market) if summary else None
        line = _line_num(leg)
        if value is not None and line is not None:
            result = grade_numeric(value, line, None, completed)
            result['unit'] = 'yds'
            result['game'] = snapshot_display(snapshot)
            return result
        return {'state':'UNTRACKED', 'current':snapshot_display(snapshot), 'line':line}

    # Game total (points). Keep this after team-yardage handling so a market
    # containing the word TOTAL is not accidentally graded from points.
    if 'TOTAL' in mkt and snapshot.get('home') and snapshot.get('away'):
        total = (snapshot['home'].get('score') or 0) + (snapshot['away'].get('score') or 0)
        result = grade_numeric(total, _line_num(leg), _direction(leg), completed)
        result['current'] = f"{int(total)} pts — {snapshot_display(snapshot)}"
        return result

    # Player props
    if summary:
        value = stat_value(summary, selection, market)
        # Anytime TD is a binary 1+ market even though DraftKings does not print
        # a numeric line next to the market text.
        if value is not None and 'ANYTIME' in mkt and ('TD' in mkt or 'TOUCHDOWN' in mkt):
            hit = value >= 1
            state = ('WON' if hit else 'LOST') if completed else ('WINNING' if hit else 'LOSING')
            return {'state':state,'current':value,'line':1.0,'direction':'OVER','needed':max(0,1-value)}
        line = _line_num(leg)
        direction = _direction(leg)
        if value is not None and line is not None:
            return grade_numeric(value, line, direction, completed)

    return {'state':'UNTRACKED', 'current':snapshot_display(snapshot)}


def progress_text(prog):
    state = prog.get('state') or 'PENDING'
    current = prog.get('current')
    line = prog.get('line')
    needed = prog.get('needed')
    direction = prog.get('direction')
    if isinstance(current, (int, float)) and line is not None:
        unit = ' yds' if prog.get('unit') == 'yds' else ''
        game = prog.get('game')
        if direction == 'OVER' and needed is not None and needed > 0:
            base = f"{current:g} / {line:g}{unit} — needs {needed:g}"
        elif direction == 'UNDER':
            base = f"{current:g} / under {line:g}{unit}"
        else:
            base = f"{current:g} / {line:g}{unit}"
        return f"{base} — {game}" if game else base
    return str(current) if current is not None else state


def parlay_state(states):
    vals = [s for s in states if s]
    if any(s == 'LOST' for s in vals):
        return 'LOST'
    if vals and all(s in ('WON','PUSH') for s in vals):
        return 'WON'
    if any(s == 'LOSING' for s in vals):
        return 'LOSING'
    if any(s in ('WINNING','TIED') for s in vals):
        return 'LIVE'
    if any(s == 'PREGAME' for s in vals):
        return 'PREGAME'
    return 'PENDING'
