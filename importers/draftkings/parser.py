import re
from datetime import datetime
from .sport_detector import infer_sport

MONEY_RE = r"\$\s*([\d,]+(?:\.\d{1,2})?)"
DATE_RE = r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)"

NFL_TEAM_PREFIXES = [
    'ARI Cardinals','ATL Falcons','BAL Ravens','BUF Bills','CAR Panthers','CHI Bears',
    'CIN Bengals','CLE Browns','DAL Cowboys','DEN Broncos','DET Lions','GB Packers',
    'HOU Texans','IND Colts','JAX Jaguars','KC Chiefs','LV Raiders','LAC Chargers',
    'LA Chargers','LAR Rams','LA Rams','MIA Dolphins','MIN Vikings','NE Patriots',
    'NO Saints','NYG Giants','NYJ Jets','PHI Eagles','PIT Steelers','SEA Seahawks',
    'SF 49ers','TB Buccaneers','TEN Titans','WAS Commanders'
]

def _normalize_team_selection(selection):
    """Strip OCR icon garbage before a recognized NFL team name."""
    if not selection:
        return selection
    raw=re.sub(r'\s+',' ',selection).strip()
    upper=raw.upper()
    best=None
    for team in NFL_TEAM_PREFIXES:
        pos=upper.find(team.upper())
        if pos >= 0 and (best is None or pos < best[0]):
            best=(pos, team)
    if best is not None:
        pos, team=best
        tail=raw[pos+len(team):].strip()
        return (team + (' ' + tail if tail else '')).strip()
    # Conservative generic cleanup for stray leading icon tokens.
    raw=re.sub(r'^(?:(?:O|0|Q|@|&|©|\||\*)\s*){1,4}(?=[A-Za-z])','',raw,flags=re.I).strip()
    return raw

def _money(label, text):
    m = re.search(rf"{label}\s*:?\s*{MONEY_RE}", text, re.I)
    return float(m.group(1).replace(',','')) if m else None

def _date_to_iso(s):
    if not s: return None
    try: return datetime.strptime(s, '%b %d, %Y, %I:%M:%S %p').isoformat()
    except ValueError: return s

def _clean(line):
    line = re.sub(r'^[^A-Za-z0-9+\-]+', '', line).strip()
    return re.sub(r'\s+', ' ', line)

def _status(text):
    # Status is shown as a short badge near the top. Avoid false positives like 'THE OPEN CHAMPIONSHIP'.
    top='\n'.join(text.splitlines()[:8]).upper()
    for status in ('WON','LOST','OPEN','VOID','PUSH'):
        if re.search(rf'\b{status}\b', top):
            if status == 'OPEN' and 'OPEN CHAMPIONSHIP' in top and not re.search(r'\bOPEN\s*$', top, re.M):
                continue
            return status
    return 'UNKNOWN'

def _bet_id(text):
    # OCR commonly turns D into 0 in DK bet IDs.
    m=re.search(r'\b(?:DK|0K|OK)(\d{12,})\b',text,re.I)
    return 'DK'+m.group(1) if m else None

def _placed_at(text):
    bid=_bet_id(text)
    if bid:
        # nearest timestamp before DK id
        pos=max(text.upper().rfind('DK'), text.upper().rfind('0K'), text.upper().rfind('OK'))
        prior=text[:pos]
        matches=re.findall(DATE_RE, prior)
        if matches: return _date_to_iso(matches[-1])
    matches=re.findall(DATE_RE,text)
    return _date_to_iso(matches[-1]) if matches else None

def _odds_from_header(lines):
    for line in lines[:6]:
        vals=re.findall(r'(?<!\d)([+-]\d{2,6})(?!\d)',line)
        if vals:
            return [int(v) for v in vals]
    return []

def _headline_subtitle(lines):
    # The bet headline is usually the first early line containing American odds.
    candidates=[]
    for l in lines[:10]:
        c=_clean(l)
        u=c.upper()
        if not c or 'DRAFTKINGS' in u or 'SPORTSBOOK' in u:
            continue
        if re.search(r'(?<!\d)[+-]\d{2,6}(?!\d)', c):
            candidates.append(c)
    headline_line=candidates[0] if candidates else None
    if headline_line:
        # Strip status, active odds and leading OCR symbols. Keep things like OVER 74.5.
        headline=re.sub(r'\b(WON|LOST|OPEN)\b','',headline_line,flags=re.I)
        headline=re.sub(r'\s+[+-]\d{2,6}(?:\s+[+-]\d{2,6})?\s*$','',headline).strip(' ~-')
        headline=re.sub(r'^[^A-Za-z0-9]+','',headline).strip()
        idx=lines.index(headline_line) if headline_line in lines else -1
        subtitle=None
        if idx>=0:
            for nxt in lines[idx+1:idx+6]:
                c=_clean(nxt)
                if not c:
                    continue
                if c.upper() in ('OPEN','WON','LOST','PUSH','VOID'):
                    continue
                if re.search(r'Wager:|Paid:|To Pay:|Cash Out',c,re.I):
                    continue
                subtitle=c; break
        return headline or None, subtitle
    useful=[]
    for l in lines:
        c=_clean(l)
        if not c or 'DRAFTKINGS' in c.upper() or 'SPORTSBOOK' in c.upper(): continue
        useful.append(c)
    return (useful[0] if useful else None, useful[1] if len(useful)>1 else None)

def _bet_type(text):
    u=text.upper()
    m=re.search(r'(\d+)\s*PICKS?\s*PARLAY',u)
    if m:return 'PARLAY',int(m.group(1))
    m=re.search(r'\bSGP\b[^\n]*?(\d+)\s*PICKS?',u)
    if m:return 'SGP',int(m.group(1))
    # OCR may drop SGP
    m=re.search(r'\b(\d+)\s*PICKS?\b',u)
    if m and int(m.group(1))>1:return 'PARLAY',int(m.group(1))
    return 'SINGLE',1

def _extract_promo(text):
    for p in ['ODDS BOOST','INSURANCE','UP 1 EARLY WIN']:
        if p in text.upper(): return p
    return None

def _event_name(text):
    patterns=[r'(THE OPEN CHAMPIONSHIP 2026)',r'(HOME RUN DERBY 2026)']
    for p in patterns:
        m=re.search(p,text,re.I)
        if m:return m.group(1).title() if 'OPEN' not in m.group(1).upper() else 'The Open Championship 2026'
    return None

def _player_name_from_line(line):
    # DraftKings mobile slips often put a helmet/icon before the player name. OCR
    # turns those icons into short junk tokens. Extract the longest title-case name.
    x=re.sub(r'(?<!\d)[+-]\d{2,6}(?!\d)',' ',line)
    x=re.sub(r'^[^A-Za-z]+','',x).strip()
    # Drop common one-character/icon OCR prefixes.
    x=re.sub(r'^(?:[Oo0@©]\s+)+','',x).strip()
    candidates=re.findall(r"[A-Z][A-Za-z.'’\-]+(?:\s+[A-Z][A-Za-z.'’\-]+){1,3}",x)
    if not candidates:
        return None
    name=max(candidates,key=len).strip()
    # Tesseract commonly reads the suffix III as Ill.
    name=re.sub(r'\s+Ill$', ' III', name)
    return name

def _nearby_odds(lines, idx, radius=2):
    # Same row is best; sparse OCR often emits odds on an adjacent line.
    order=[idx]
    for d in range(1, radius+1):
        order.extend([idx-d, idx+d])
    for j in order:
        if 0 <= j < len(lines):
            vals=re.findall(r'(?<!\d)([+-]\d{2,6})(?!\d)', lines[j])
            if vals:
                return int(vals[-1])
    return None

def _parse_legs(text, headline, subtitle, bet_type, leg_count, status):
    lines=[_clean(x) for x in text.splitlines() if _clean(x)]
    legs=[]
    if bet_type=='SINGLE':
        odds=None
        if headline:
            m=re.search(r'([+-]\d{2,6})', next((x for x in lines if headline.upper() in x.upper()), ''))
            odds=int(m.group(1)) if m else None
        selection=headline
        market=subtitle
        # Normalize over/under headers.
        if headline and re.match(r'^(OVER|UNDER)\s+\d',headline,re.I):
            m=re.match(r'^(OVER|UNDER)\s+([\d.]+)',headline,re.I)
            line=m.group(2) if m else None
            direction=m.group(1).upper() if m else None
        else: line=direction=None
        legs.append({'index':1,'selection':selection,'participant_type':None,'market':market,'line':line,'direction':direction,
                     'odds':odds,'status':status if status in ('WON','LOST','PUSH','VOID') else 'PENDING','event':{'start_time':None},'raw_leg_text':None})
        return legs

    # Prefer explicit golf/results leg blocks.
    market_terms=('Moneyline','Top 5 (Including Ties)','Top 10 (Including Ties)','Points - 1st Quarter','Anytime TD Scorer')
    for i,line in enumerate(lines):
        market=None
        if line.lower()=='moneyline': market='Moneyline'
        elif 'Top 5 (Including Ties)' in line: market='Top 5 (Including Ties)'
        elif 'Top 10 (Including Ties)' in line: market='Top 10 (Including Ties)'
        elif 'Points - 1st Quarter' in line:
            market=line
        elif 'ANYTIME TD SCORER' in line.upper():
            market='Anytime TD Scorer'
        if market and i>0:
            if market == 'Anytime TD Scorer':
                # Find the closest preceding row that contains a real player name.
                sel=None; source_idx=None
                for j in range(i-1, max(-1,i-5), -1):
                    cand=_player_name_from_line(lines[j])
                    if cand and cand.upper() not in ('ANYTIME TD SCORER','DRAFTKINGS SPORTSBOOK'):
                        sel=cand; source_idx=j; break
                if not sel:
                    continue
                odds=_nearby_odds(lines, source_idx if source_idx is not None else i)
                lineval='1+'
            elif 'Points - 1st Quarter' in market:
                # DK writes e.g. 'Jalen Brunson Points - 1st Quarter'; player is the prefix.
                sel=re.sub(r'\s+Points\s*-\s*1st Quarter.*$','',market,flags=re.I).strip()
                lineval=None
                if i>0 and re.fullmatch(r'\d+\+?', lines[i-1].strip()): lineval=lines[i-1].strip()
                market='Points - 1st Quarter'
                odds=None
            else:
                sel=lines[i-1]
                om=re.search(r'([+-]\d{2,6})$',sel)
                odds=int(om.group(1)) if om else _nearby_odds(lines, i-1, radius=2)
                sel=re.sub(r'\s+[+-]?\d{2,6}$','',sel).strip()
                lineval=None
            # Look ahead for a matchup card and event timestamp. Player-only TD slips
            # do not show matchup text, so do not mistake neighboring players for teams.
            event_time=None; team_a=None; team_b=None
            for look in lines[i+1:i+8]:
                dm=re.search(DATE_RE, look)
                if dm and not event_time: event_time=_date_to_iso(dm.group(1))
            if market != 'Anytime TD Scorer':
                likely=[]
                for look in lines[i+1:i+7]:
                    if re.search(DATE_RE,look): break
                    if re.search(r'WAGER|CASH OUT|HIDE PICKS|DK\d+',look,re.I): continue
                    if len(look) >= 3 and not re.search(r'^[+-]\d+$',look): likely.append(look)
                if len(likely)>=2: team_a,team_b=likely[-2],likely[-1]
            legs.append({'index':len(legs)+1,'selection':sel,'participant_type':None,'market':market,
                         'line':lineval,'direction':None,'odds':odds,'status':'PENDING',
                         'event':{'start_time':event_time,'away_team':team_a,'home_team':team_b},'raw_leg_text':sel+' | '+market})

    # Team stat-total legs on mobile SGPx / nested SGP slips. DraftKings can show:
    #   275+  PIT Steelers Total Yards
    #   120+  BUF Bills Total Rushing Yards
    #   170+  CLE Browns Total Receiving Yards
    # Email OCR is deliberately multi-pass, so the same selection may appear twice.
    # Merge by normalized selection and keep the richest threshold/odds we find.
    def _team_stat_market(selection):
        u=selection.upper()
        if 'TOTAL RUSHING YARDS' in u: return 'Team Total Rushing Yards'
        if 'TOTAL RECEIVING YARDS' in u: return 'Team Total Receiving Yards'
        if 'TOTAL YARDS' in u: return 'Team Total Yards'
        return None

    def _threshold_from_nearby(idx):
        # Search only within the current leg. Do not borrow the previous leg's
        # threshold when OCR omitted this leg's number in one pass.
        for j in range(idx-1, max(-1,idx-7), -1):
            x=lines[j].strip()
            xu=x.upper()
            if j != idx-1 and (_team_stat_market(x) or 'PICK SGP' in xu or x.lower().startswith('market will be settled')):
                break
            m=re.search(r'(?<!\d)(\d{2,3}(?:\.\d+)?)\s*\+(?!\d)', x)
            if m: return m.group(1)+'+'
            if re.fullmatch(r'\d{4}', x):
                n=int(x)
                if x.endswith('5') and 500 <= n <= 9999:
                    candidate=x[:-1]
                    if 50 <= int(candidate) <= 500:
                        return candidate+'+'
        return None

    def _team_odds_before(idx):
        # Individual prices are printed before/on the same visual row. Never scan
        # forward because that can steal the next nested SGP combo price.
        for j in range(idx-1, max(-1,idx-6), -1):
            x=lines[j].strip()
            xu=x.upper()
            if (_team_stat_market(x) or 'PICK SGP' in xu or 'PICK PARLAY' in xu or
                'WAGER' in xu or x.lower().startswith('market will be settled')):
                break
            vals=re.findall(r'(?<!\d)([+-]\d{2,6})(?!\d)',x)
            if vals:
                return int(vals[-1])
        return None

    def _is_nested_sgp(idx):
        # Find the most recent section header. A '2 Pick SGP' price is the combo
        # price for the child legs and must not be assigned as a leg price.
        for j in range(idx-1, max(-1,idx-10), -1):
            x=lines[j].upper().strip()
            if 'PICK SGP' in x:
                return True
            if re.search(r'\bWAGER\b|\bPICK PARLAY\b', x):
                break
        return False

    leg_by_key={(str(l.get('selection') or '').upper(), str(l.get('market') or '').upper()):l for l in legs}
    for i,line in enumerate(lines):
        market=_team_stat_market(line)
        if not market:
            continue
        selection=_normalize_team_selection(re.sub(r'^[^A-Za-z0-9]+','',line).strip())
        if selection.lower().startswith('market will be settled'):
            continue
        threshold=_threshold_from_nearby(i)
        key=(selection.upper(),market.upper())
        existing=leg_by_key.get(key)
        candidate_odds=None if _is_nested_sgp(i) else _team_odds_before(i)
        if existing:
            if not existing.get('line') and threshold:
                existing['line']=threshold
            if existing.get('odds') is None and candidate_odds is not None:
                existing['odds']=candidate_odds
            if existing.get('direction') is None:
                existing['direction']='OVER'
            continue
        odds=candidate_odds
        leg={
            'index':len(legs)+1,
            'selection':selection,
            'participant_type':'TEAM',
            'market':market,
            'line':threshold,
            'direction':'OVER',
            'odds':odds,
            'status':'PENDING',
            'event':{'start_time':None},
            'raw_leg_text':selection+' | '+(threshold or '')+' '+market
        }
        legs.append(leg)
        leg_by_key[key]=leg

    # DraftKings' compact shared image includes a clean comma-separated summary
    # directly under the parlay header. Use those names as the canonical leg
    # selections when the count matches. This removes OCR artifacts from icons
    # such as 'O BUF Bills EN'.
    if bet_type != 'SINGLE' and subtitle and ',' in subtitle:
        summary_names=[_clean(x) for x in subtitle.split(',') if _clean(x)]
        summary_names=[x for x in summary_names if not re.search(r'\bPICK\s+SGP\b|^\d+(?:\.\d+)?\+$',x,re.I)]
        if len(summary_names) >= leg_count and legs:
            for idx,leg in enumerate(legs[:leg_count]):
                if idx < len(summary_names):
                    leg['selection']=summary_names[idx]
                    leg['raw_leg_text']=(summary_names[idx] + ' | ' + (leg.get('market') or '')).strip(' |')

    # Fallback for player summary names if OCR did not make blocks cleanly.
    if len(legs)<leg_count and subtitle and ',' in subtitle:
        existing={l['selection'].upper() for l in legs}
        fallback_names=[x.strip() for x in subtitle.split(',')]
        fallback_names=[x for x in fallback_names if x and not re.search(r'\bPICK\s+SGP\b|^\d+(?:\.\d+)?\+$',x,re.I)]
        for name in fallback_names:
            if name and name.upper() not in existing and len(legs)<leg_count:
                legs.append({'index':len(legs)+1,'selection':name,'participant_type':None,'market':None,'line':None,'direction':None,'odds':None,
                             'status':'PENDING','event':{'start_time':None},'raw_leg_text':name})
    return legs[:leg_count]

def parse_draftkings(text: str) -> dict:
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    status=_status(text)
    bet_type,leg_count=_bet_type(text)
    headline,subtitle=_headline_subtitle(lines)
    odds_vals=_odds_from_header(lines)
    current=odds_vals[-1] if odds_vals else None
    original=odds_vals[0] if len(odds_vals)>1 else None
    boosted=current if len(odds_vals)>1 else None
    legs=_parse_legs(text,headline,subtitle,bet_type,leg_count,status)
    if bet_type!='SINGLE' and legs:
        leg_count=max(leg_count,len(legs))
    return {
      'sportsbook':'DraftKings','sportsbook_bet_id':_bet_id(text),'status':status,'bet_type':bet_type,'leg_count':leg_count,
      'odds':{'current':current,'original':original,'boosted':boosted},
      'money':{'stake':_money('Wager',text),'to_pay':_money('To Pay',text),'paid':_money('Paid',text),'cash_out':_money('Cash Out',text)},
      'promo':_extract_promo(text),'placed_at':_placed_at(text),'sport':infer_sport(text),'headline':headline,'subtitle':subtitle,
      'event':{'name':_event_name(text),'start_time':None},'legs':legs,
      'import':{'method':'SCREENSHOT','confidence':0.90,'needs_review':True},'raw_ocr_text':text
    }
