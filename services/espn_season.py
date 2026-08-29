import json, re
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode

UA='Mozilla/5.0 BetTracker/1.0'
SEARCH='https://site.api.espn.com/apis/search/v2'
WEB='https://site.web.api.espn.com/apis/common/v3/sports/football/nfl'
CORE='https://sports.core.api.espn.com/v2/sports/football/leagues/nfl'


def _get_json(url, timeout=12):
    req=Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
    with urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _norm(s):
    s=re.sub(r'\b(JR|SR|II|III|IV)\.?\b','',str(s or '').upper())
    return re.sub(r'[^A-Z0-9]','',s)


def find_athlete(player_name):
    """Resolve an NFL player name to an ESPN athlete id.

    Prefer ESPN's search endpoint, then fall back to the active-athletes collection.
    """
    q=quote(str(player_name or '').strip())
    if not q:
        return None
    try:
        data=_get_json(f'{SEARCH}?query={q}&limit=20')
        target=_norm(player_name)
        candidates=[]
        def walk(obj):
            if isinstance(obj,dict):
                # Search responses vary; capture any dict that looks athlete-like.
                name=obj.get('displayName') or obj.get('name') or obj.get('fullName')
                oid=obj.get('id')
                typ=str(obj.get('type') or obj.get('subtype') or '').lower()
                sport=str(obj.get('sport') or obj.get('league') or '').lower()
                if name and oid and (_norm(name)==target or target in _norm(name) or _norm(name) in target):
                    candidates.append((0 if _norm(name)==target else 1, str(oid), name, obj))
                for v in obj.values(): walk(v)
            elif isinstance(obj,list):
                for v in obj: walk(v)
        walk(data)
        if candidates:
            candidates.sort(key=lambda x:x[0])
            _,aid,name,obj=candidates[0]
            return {'id':aid,'name':name}
    except Exception:
        pass

    # Conservative fallback for cases where search response shape changes.
    try:
        target=_norm(player_name)
        for page in range(1,8):
            data=_get_json(f'{CORE}/athletes?active=true&limit=100&page={page}')
            items=data.get('items') or []
            if not items: break
            for item in items:
                ref=item.get('$ref') if isinstance(item,dict) else None
                if ref:
                    try: a=_get_json(ref.replace('http://','https://'))
                    except Exception: continue
                else: a=item
                name=a.get('displayName') or a.get('fullName')
                if _norm(name)==target:
                    return {'id':str(a.get('id')),'name':name}
    except Exception:
        pass
    return None


def season_stats(athlete_id, season_year, season_type=2):
    """Fetch cumulative NFL season stats for one ESPN athlete.

    ESPN's web v3 endpoint is preferred because it carries labels/categories.
    The core v2 season/type endpoint is a fallback.
    """
    urls=[
        f'{WEB}/athletes/{athlete_id}/stats?{urlencode({"season":season_year,"seasontype":season_type})}',
        f'{CORE}/seasons/{season_year}/types/{season_type}/athletes/{athlete_id}/statistics/0',
        f'{CORE}/seasons/{season_year}/athletes/{athlete_id}/statistics',
    ]
    last=None
    for url in urls:
        try:
            return _get_json(url),url
        except Exception as e:
            last=e
    raise last or RuntimeError('Unable to retrieve ESPN season statistics')


def _to_num(v):
    if isinstance(v,(int,float)): return float(v)
    s=str(v or '').replace(',','').strip()
    m=re.search(r'-?\d+(?:\.\d+)?',s)
    return float(m.group()) if m else None


def flatten_stats(data):
    """Flatten ESPN's varying stats schemas to (name,label,category,value) records."""
    out=[]
    def process_category(cat):
        if not isinstance(cat,dict): return
        category=cat.get('name') or cat.get('displayName') or cat.get('abbreviation') or ''
        names=cat.get('names') or []
        labels=cat.get('labels') or []
        stats=cat.get('stats')
        if isinstance(stats,list):
            # Schema A: stat objects.
            for i,st in enumerate(stats):
                if isinstance(st,dict):
                    name=st.get('name') or st.get('abbreviation') or (names[i] if i<len(names) else '')
                    label=st.get('displayName') or st.get('label') or (labels[i] if i<len(labels) else name)
                    val=st.get('value')
                    if val is None: val=st.get('displayValue')
                    n=_to_num(val)
                    if n is not None: out.append({'name':name,'label':label,'category':category,'value':n})
                else:
                    n=_to_num(st)
                    if n is not None:
                        name=names[i] if i<len(names) else (labels[i] if i<len(labels) else str(i))
                        label=labels[i] if i<len(labels) else name
                        out.append({'name':name,'label':label,'category':category,'value':n})
        # Some endpoints put statistics beneath nested splits/categories.
        for key in ('categories','statistics','splits'):
            child=cat.get(key)
            if isinstance(child,list):
                for x in child: process_category(x)
            elif isinstance(child,dict): process_category(child)
    process_category(data)
    # Generic recursive pass for standalone stat objects missed above.
    def walk(obj, category=''):
        if isinstance(obj,dict):
            name=obj.get('name') or obj.get('abbreviation')
            val=obj.get('value')
            if val is None: val=obj.get('displayValue')
            if name and val is not None:
                n=_to_num(val)
                if n is not None: out.append({'name':name,'label':obj.get('displayName') or obj.get('label') or name,'category':category,'value':n})
            newcat=obj.get('displayName') or obj.get('name') or category
            for v in obj.values(): walk(v,newcat)
        elif isinstance(obj,list):
            for v in obj: walk(v,category)
    walk(data)
    # Dedupe exact triplets, preserve first.
    seen=set(); ded=[]
    for r in out:
        k=(str(r['name']).lower(),str(r['category']).lower(),r['value'])
        if k not in seen: seen.add(k); ded.append(r)
    return ded


MARKET_KEYS={
    'PASSING YARDS':['passingyards','passyards','yds','yards'],
    'PASSING TDS':['passingtouchdowns','passingtouchdown','passtd','td'],
    'INTERCEPTIONS':['interceptions','int'],
    'RUSHING YARDS':['rushingyards','rushyards','yds','yards'],
    'RUSHING TDS':['rushingtouchdowns','rushingtouchdown','rushtd','td'],
    'RECEIVING YARDS':['receivingyards','recyards','yds','yards'],
    'RECEPTIONS':['receptions','rec'],
    'RECEIVING TDS':['receivingtouchdowns','receivingtouchdown','rectd','td'],
}

def canonical_market(market):
    u=str(market or '').upper()
    if 'PASS' in u and ('YARD' in u or 'YDS' in u): return 'PASSING YARDS'
    if 'PASS' in u and ('TD' in u or 'TOUCHDOWN' in u): return 'PASSING TDS'
    if 'INTERCEPTION' in u: return 'INTERCEPTIONS'
    if 'RUSH' in u and ('YARD' in u or 'YDS' in u): return 'RUSHING YARDS'
    if 'RUSH' in u and ('TD' in u or 'TOUCHDOWN' in u): return 'RUSHING TDS'
    if ('RECEIV' in u or 'REC ' in u) and ('YARD' in u or 'YDS' in u): return 'RECEIVING YARDS'
    if 'RECEPTION' in u or re.search(r'\bRECS?\b',u): return 'RECEPTIONS'
    if ('RECEIV' in u or 'REC ' in u) and ('TD' in u or 'TOUCHDOWN' in u): return 'RECEIVING TDS'
    return None


def stat_for_market(data, market):
    canon=canonical_market(market)
    if not canon: return None
    rows=flatten_stats(data)
    # Score rows by category + name specificity so generic YDS/TD doesn't cross categories.
    wantcat=''
    if canon.startswith('PASSING'): wantcat='PASS'
    elif canon.startswith('RUSHING'): wantcat='RUSH'
    elif canon.startswith('RECEIVING') or canon=='RECEPTIONS': wantcat='RECEIV'
    elif canon=='INTERCEPTIONS': wantcat='PASS'
    keys=MARKET_KEYS[canon]
    best=[]
    for r in rows:
        name=re.sub(r'[^a-z0-9]','',str(r['name']).lower())
        label=re.sub(r'[^a-z0-9]','',str(r['label']).lower())
        cat=str(r['category']).upper()
        score=0
        if wantcat and wantcat in cat: score+=6
        for k in keys:
            kk=re.sub(r'[^a-z0-9]','',k.lower())
            if name==kk: score+=10
            elif kk and kk in name: score+=7
            if label==kk: score+=8
            elif kk and kk in label: score+=4
        # TD guardrails: only trust generic TD inside correct category.
        if canon.endswith('TDS') and wantcat and wantcat not in cat and name in ('td','touchdowns'): score-=10
        if canon=='INTERCEPTIONS' and 'int' not in name and 'interception' not in label: score-=8
        if score>0: best.append((score,r))
    if not best: return None
    best.sort(key=lambda x:x[0],reverse=True)
    return best[0][1]['value']


def games_played(data):
    rows=flatten_stats(data)
    candidates=[]
    for r in rows:
        key=re.sub(r'[^a-z0-9]','',str(r['name']).lower())
        label=re.sub(r'[^a-z0-9]','',str(r['label']).lower())
        if key in ('gamesplayed','gp','games') or label in ('gamesplayed','gp'):
            candidates.append(r['value'])
    return int(max(candidates)) if candidates else None


def future_progress(player_name, market, line, direction='OVER', season_year=2026, season_type=2, athlete_id=None):
    athlete={'id':athlete_id,'name':player_name} if athlete_id else find_athlete(player_name)
    if not athlete:
        return {'state':'NO ESPN PLAYER','athlete_id':None}
    data,source=season_stats(athlete['id'],season_year,season_type)
    value=stat_for_market(data,market)
    gp=games_played(data)
    if value is None:
        return {'state':'STAT NOT FOUND','athlete_id':athlete['id'],'player':athlete.get('name'),'games_played':gp,'source':source}
    try: line=float(line)
    except Exception: line=None
    d=str(direction or 'OVER').upper()
    if line is None:
        return {'state':'MISSING LINE','current':value,'athlete_id':athlete['id'],'games_played':gp,'source':source}
    remaining=max(0,17-(gp or 0)) if gp is not None else None
    pace=(value/(gp or 1)*17) if gp and gp>0 else (0.0 if value==0 else None)
    if d=='UNDER':
        pacing = pace is not None and pace < line
        needed=None
        state='PACING UNDER' if pacing else 'PACING OVER LINE'
    else:
        pacing = pace is not None and pace > line
        needed=max(0,line-value)
        state='PACING OVER' if pacing else 'PACING UNDER'
    # Once the target is mathematically hit, Over is won regardless of remaining games.
    if d=='OVER' and value>line: state='WON'
    # Under only settles after 17 games (injury/official grading caveats remain sportsbook-specific).
    if gp is not None and gp>=17:
        if d=='OVER': state='WON' if value>line else ('PUSH' if value==line else 'LOST')
        else: state='WON' if value<line else ('PUSH' if value==line else 'LOST')
    return {'state':state,'current':value,'line':line,'direction':d,'needed':needed,'games_played':gp,
            'remaining_games':remaining,'pace':pace,'athlete_id':athlete['id'],'player':athlete.get('name'),'source':source}
