import re
from datetime import datetime
from importers.draftkings.sport_detector import infer_sport

MONEY_RE = r"\$\s*([\d,]+(?:\.\d{1,2})?)"

def _money_after(label: str, text: str):
    lines = [re.sub(r'\s+', ' ', x).strip() for x in text.splitlines() if x.strip()]
    target = label.upper()
    for i, line in enumerate(lines):
        if target in line.upper():
            # FanDuel commonly OCRs '$1.00  $2.22' on one row followed by
            # 'TOTAL WAGER  TOTAL PAYOUT'. Map values by column order.
            if i > 0:
                vals = re.findall(MONEY_RE, lines[i-1])
                if vals:
                    if target == 'TOTAL WAGER':
                        return float(vals[0].replace(',', ''))
                    if target == 'TOTAL PAYOUT':
                        return float(vals[-1].replace(',', ''))
            vals = re.findall(MONEY_RE, line)
            if vals:
                return float((vals[0] if target == 'TOTAL WAGER' else vals[-1]).replace(',', ''))
    return None

def _bet_id(text: str):
    m = re.search(r"BET\s*ID\s*:\s*([^\s]+)", text, re.I)
    return m.group(1).strip() if m else None

def _placed_at(text: str):
    m = re.search(r"PLACED\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*([AP]M)\s*(?:ET|CT|MT|PT)?", text, re.I)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3).upper()}", "%m/%d/%Y %I:%M %p").isoformat(timespec='minutes')
    except Exception:
        return f"{m.group(1)} {m.group(2)} {m.group(3).upper()}"

def _odds(text: str):
    # Prefer odds near the first market heading, before wager/payout values.
    first = text.split('__OCR_SECOND_PASS__', 1)[0]
    for line in [x.strip() for x in first.splitlines() if x.strip()][:12]:
        vals = re.findall(r"(?<!\d)([+-]\d{2,5})(?!\d)", line)
        if vals:
            return int(vals[-1])
    return None

def _matchup(text: str):
    # @ is FanDuel's common event separator.
    m = re.search(r"^\s*([A-Za-z][A-Za-z .'-]+?)\s*@\s*([A-Za-z][A-Za-z .'-]+?)(?:\s{2,}|\s+(?:MON|TUE|WED|THU|FRI|SAT|SUN)\b|$)", text, re.I | re.M)
    if m:
        return m.group(1).strip(), m.group(2).strip(), f"{m.group(1).strip()} @ {m.group(2).strip()}"
    return None, None, None

def parse_fanduel(text: str) -> dict:
    primary = text.split('__OCR_SECOND_PASS__', 1)[0]
    lines = [re.sub(r'\s+', ' ', x).strip() for x in primary.splitlines() if x.strip()]
    market = None
    market_idx = None
    market_map = {
        'MONEYLINE': 'Moneyline',
        'SPREAD': 'Spread',
        'TOTAL': 'Total',
    }
    for i, line in enumerate(lines):
        u = line.upper()
        if u in market_map:
            market = market_map[u]
            market_idx = i
            break
        if 'ANYTIME TOUCHDOWN' in u or 'ANYTIME TD' in u:
            market = 'Anytime TD Scorer'; market_idx = i; break
    selection = None
    if market_idx is not None and market_idx > 0:
        prev = lines[market_idx - 1]
        prev = re.sub(r"\s+[+-]\d{2,5}\s*$", "", prev).strip()
        if prev and not prev.startswith('$'):
            selection = prev
    away, home, event_name = _matchup(primary)
    if not selection and away:
        selection = away
    odds = _odds(primary)
    stake = _money_after('TOTAL WAGER', primary)
    payout = _money_after('TOTAL PAYOUT', primary)
    bet_id = _bet_id(primary)
    placed = _placed_at(primary)

    # Count explicit market blocks; current implementation handles common singles
    # and preserves safe review behavior for more complex FanDuel slips.
    market_hits = sum(1 for l in lines if l.upper() in market_map or 'ANYTIME TD' in l.upper())
    leg_count = max(1, market_hits)
    bet_type = 'SINGLE' if leg_count == 1 else 'PARLAY'
    legs = []
    if selection or market:
        legs.append({
            'index': 1, 'selection': selection, 'participant_type': 'TEAM' if market in ('Moneyline','Spread','Total') else None,
            'market': market, 'line': None, 'direction': None, 'odds': odds, 'status': 'PENDING',
            'event': {'start_time': None, 'away_team': away, 'home_team': home},
            'raw_leg_text': ' | '.join(x for x in [selection, market] if x)
        })
    return {
        'sportsbook': 'FanDuel', 'sportsbook_bet_id': bet_id, 'status': 'OPEN', 'bet_type': bet_type,
        'leg_count': leg_count, 'odds': {'current': odds, 'original': None, 'boosted': None},
        'money': {'stake': stake, 'to_pay': payout, 'paid': None, 'cash_out': None},
        'promo': None, 'placed_at': placed, 'sport': infer_sport(primary),
        'headline': selection or ('FanDuel Bet' if bet_type == 'SINGLE' else f'{leg_count} Leg Parlay'),
        'subtitle': market, 'event': {'name': event_name, 'start_time': None}, 'legs': legs,
        'import': {'method': 'SCREENSHOT', 'confidence': 0.88, 'needs_review': True}, 'raw_ocr_text': text
    }
