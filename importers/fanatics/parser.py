import json
from urllib.parse import urlparse, parse_qs, unquote


def parse_fanatics_share_url(url: str) -> dict:
    """Decode Fanatics shared bet deeplink payload without calling Fanatics.

    Returns a normalized skeleton bet. Human-readable selection names/odds are
    not always present in the deeplink, so legs are retained by stable IDs.
    """
    url=(url or '').strip()
    if not url:
        raise ValueError('Paste a Fanatics share link first.')
    p=urlparse(url)
    if 'betfanatics.com' not in p.netloc.lower() and 'fanatics.onelink.me' not in p.netloc.lower():
        raise ValueError('That does not look like a Fanatics share link.')
    qs=parse_qs(p.query)
    raw=(qs.get('deep_link_sub1') or [None])[0]
    if not raw:
        # Short OneLinks must first be expanded by the browser/iPhone. The app
        # cannot infer the deeplink payload from the short code alone.
        short=(p.path.rstrip('/').split('/')[-1] if p.path else '')
        return {
            'sportsbook':'Fanatics',
            'status':'UNKNOWN',
            'bet_type':'UNKNOWN',
            'leg_count':0,
            'odds':{'current':None,'original':None,'boosted':None},
            'money':{'stake':None,'to_pay':None,'paid':None,'cash_out':None},
            'sport':None,
            'headline':'Fanatics Shared Bet',
            'subtitle':None,
            'placed_at':None,
            'event':{'name':None,'start_time':None},
            'legs':[],
            'fanatics_share_url':url,
            'fanatics_shortlink':short or None,
            'fanatics_payload':None,
            'needs_expanded_link':True,
        }
    try:
        payload=json.loads(unquote(raw))
    except Exception as e:
        raise ValueError(f'Could not decode Fanatics bet payload: {e}')
    legs=[]
    for i,item in enumerate(payload.get('legs') or [],1):
        legs.append({
            'index':i,
            'selection':f"Selection {item.get('selectionId')}",
            'market':'Fanatics selection',
            'line':None,
            'direction':None,
            'odds':None,
            'status':'PENDING',
            'event':{'start_time':None,'away_team':None,'home_team':None},
            'fanatics_event_id':str(item.get('eventId')) if item.get('eventId') is not None else None,
            'fanatics_market_id':str(item.get('marketId')) if item.get('marketId') is not None else None,
            'fanatics_selection_id':str(item.get('selectionId')) if item.get('selectionId') is not None else None,
        })
    return {
        'sportsbook':'Fanatics',
        'sportsbook_bet_id':str(payload.get('betId')) if payload.get('betId') is not None else None,
        'status':'OPEN',
        'bet_type':'PARLAY' if len(legs)>1 else 'SINGLE',
        'leg_count':len(legs),
        'odds':{'current':None,'original':None,'boosted':None},
        'money':{'stake':None,'to_pay':None,'paid':None,'cash_out':None},
        'promo':None,
        'placed_at':None,
        'sport':None,
        'headline':f"{len(legs)} Pick Fanatics Bet" if legs else 'Fanatics Shared Bet',
        'subtitle':None,
        'event':{'name':None,'start_time':None},
        'legs':legs,
        'fanatics_share_url':url,
        'fanatics_shortlink':(qs.get('shortlink') or [None])[0],
        'fanatics_payload':payload,
        'needs_expanded_link':False,
    }

# ---------------------------------------------------------------------------
# Screenshot parser (separate from share-link decoder above)
# ---------------------------------------------------------------------------
def parse_fanatics_screenshot(text: str) -> dict:
    import re
    from importers.draftkings.sport_detector import infer_sport

    primary = text.split('__OCR_SECOND_PASS__', 1)[0]
    lines = [re.sub(r'\s+', ' ', x).strip() for x in primary.splitlines() if x.strip()]

    def money(label):
        # Values are usually on the next visual line; OCR may flatten columns.
        m = re.search(rf"{label}[^\n]*\n?[^\n]*?\$\s*([\d,]+(?:\.\d{{1,2}})?)", primary, re.I)
        if m:
            return float(m.group(1).replace(',', ''))
        return None

    header = next((x for x in lines if re.search(r'\b\d+\s+Leg\s+Parlay\b', x, re.I)), None)
    leg_count = 1
    bet_type = 'SINGLE'
    if header:
        m = re.search(r'(\d+)\s+Leg\s+Parlay', header, re.I)
        if m:
            leg_count = int(m.group(1)); bet_type = 'PARLAY'
    odds = None
    scan = ' '.join(lines[:8])
    om = re.search(r'(?<!\d)([+-]\d{2,5})(?!\d)', scan)
    if om:
        odds = int(om.group(1))
    bid = None
    bm = re.search(r'Bet\s*ID\s*:\s*([A-Za-z0-9:_-]+)', primary, re.I)
    if bm:
        bid = bm.group(1)

    # Prefer explicit labels and their adjacent dollar values.
    dollars = [float(x.replace(',', '')) for x in re.findall(r'\$\s*([\d,]+(?:\.\d{1,2})?)', primary)]
    stake = dollars[0] if dollars else None
    payout = dollars[1] if len(dollars) > 1 else None

    legs = []
    market_names = {'MONEY LINE':'Moneyline', 'MONEYLINE':'Moneyline', 'SPREAD':'Spread', 'TOTAL':'Total'}
    for i, line in enumerate(lines):
        u = line.upper()
        if u not in market_names:
            continue
        market = market_names[u]
        selection = lines[i-1] if i > 0 else None
        if not selection or selection.upper().startswith(('WAGER','PAYOUT')) or selection.startswith('$'):
            continue
        away = home = event_name = None
        for look in lines[i+1:i+4]:
            mm = re.match(r'(.+?)\s+(?:at|@)\s+(.+)$', look, re.I)
            if mm:
                away, home = mm.group(1).strip(), mm.group(2).strip()
                event_name = f'{away} at {home}'
                break
        legs.append({
            'index': len(legs)+1, 'selection': selection, 'participant_type': 'TEAM', 'market': market,
            'line': None, 'direction': None, 'odds': None, 'status': 'PENDING',
            'event': {'start_time': None, 'away_team': away, 'home_team': home},
            'raw_leg_text': ' | '.join(x for x in [selection, market, event_name] if x)
        })
    if legs:
        leg_count = len(legs) if bet_type != 'PARLAY' else max(leg_count, len(legs))
        if len(legs) > 1:
            bet_type = 'PARLAY'
    headline = header or (legs[0]['selection'] if legs else 'Fanatics Bet')
    subtitle = ', '.join(x['selection'] for x in legs) if len(legs) > 1 else (legs[0]['market'] if legs else None)
    event_name = legs[0]['raw_leg_text'].split(' | ',2)[-1] if len(legs)==1 and legs[0].get('raw_leg_text') else None

    return {
        'sportsbook':'Fanatics','sportsbook_bet_id':bid,'status':'OPEN','bet_type':bet_type,'leg_count':leg_count,
        'odds':{'current':odds,'original':None,'boosted':None},
        'money':{'stake':stake,'to_pay':payout,'paid':None,'cash_out':None},
        'promo':None,'placed_at':None,'sport':infer_sport(primary),'headline':headline,'subtitle':subtitle,
        'event':{'name':event_name,'start_time':None},'legs':legs,
        'import':{'method':'SCREENSHOT','confidence':0.90,'needs_review':True},'raw_ocr_text':text
    }
