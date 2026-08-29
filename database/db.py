import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / 'bet_tracker.db'


def _secrets_value(section, key, default=''):
    try:
        import streamlit as st
        cfg = st.secrets.get(section, {})
        return str(cfg.get(key, default)).strip()
    except Exception:
        return str(os.getenv(f'{section.upper()}_{key.upper()}', default)).strip()


def get_supabase():
    url = _secrets_value('supabase', 'url')
    key = _secrets_value('supabase', 'secret_key') or _secrets_value('supabase', 'key')
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


def cloud_enabled():
    return get_supabase() is not None


def storage_backend_name():
    return 'Supabase Cloud' if cloud_enabled() else 'Local SQLite'


def connect():
    """SQLite connection retained for local/fallback mode only."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def _ensure_column(con, table, col, decl):
    cols = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
    if col not in cols:
        con.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')


def init_db():
    # Supabase tables are created once with supabase_schema.sql. Do not attempt
    # DDL from the Streamlit app because hosted PostgREST does not expose it.
    if cloud_enabled():
        return

    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sportsbook TEXT NOT NULL, sportsbook_bet_id TEXT,
            status TEXT, bet_type TEXT, leg_count INTEGER, current_odds INTEGER, original_odds INTEGER,
            boosted_odds INTEGER, stake REAL, to_pay REAL, paid REAL, cash_out REAL, promo TEXT,
            placed_at TEXT, sport TEXT, headline TEXT, subtitle TEXT, event_name TEXT, raw_ocr_text TEXT,
            screenshot_hash TEXT UNIQUE, source_filename TEXT, normalized_json TEXT NOT NULL,
            screenshot_path TEXT, screenshot_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS bet_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bet_row_id INTEGER NOT NULL, leg_index INTEGER,
            selection TEXT, market TEXT, line_value TEXT, direction TEXT, odds INTEGER, status TEXT,
            event_time TEXT, raw_leg_text TEXT, event_team_a TEXT, event_team_b TEXT, espn_event_id TEXT,
            live_state TEXT, live_value TEXT, live_updated_at TEXT,
            FOREIGN KEY(bet_row_id) REFERENCES bets(id) ON DELETE CASCADE);
        ''')
        for c, d in [
            ('event_team_a','TEXT'),('event_team_b','TEXT'),('espn_event_id','TEXT'),
            ('live_state','TEXT'),('live_value','TEXT'),('live_updated_at','TEXT'),
            ('tracking_scope','TEXT'),('future_season_year','INTEGER'),('future_season_type','INTEGER'),
            ('espn_athlete_id','TEXT'),('future_state','TEXT'),('future_current','REAL'),
            ('future_games_played','INTEGER'),('future_pace','REAL'),('future_updated_at','TEXT'),
            ('fanatics_event_id','TEXT'),('fanatics_market_id','TEXT'),('fanatics_selection_id','TEXT')
        ]:
            _ensure_column(con, 'bet_legs', c, d)
        for c, d in [
            ('draftkings_share_url','TEXT'),('fanatics_share_url','TEXT'),('fanatics_shortlink','TEXT'),
            ('source_email_id','TEXT'),('source_email_subject','TEXT'),('espn_season_year','INTEGER'),
            ('espn_season_type','INTEGER'),('espn_week','INTEGER'),('screenshot_path','TEXT'),('screenshot_url','TEXT')
        ]:
            _ensure_column(con, 'bets', c, d)
        con.execute("""
            UPDATE bets SET status='OPEN'
            WHERE UPPER(TRIM(COALESCE(status,'')))='UNKNOWN'
              AND to_pay IS NOT NULL AND paid IS NULL
        """)


def _bet_row(b):
    return {
        'sportsbook': b.get('sportsbook') or 'Unknown',
        'sportsbook_bet_id': b.get('sportsbook_bet_id'),
        'status': b.get('status'), 'bet_type': b.get('bet_type'), 'leg_count': b.get('leg_count'),
        'current_odds': b.get('odds',{}).get('current'), 'original_odds': b.get('odds',{}).get('original'),
        'boosted_odds': b.get('odds',{}).get('boosted'), 'stake': b.get('money',{}).get('stake'),
        'to_pay': b.get('money',{}).get('to_pay'), 'paid': b.get('money',{}).get('paid'),
        'cash_out': b.get('money',{}).get('cash_out'), 'promo': b.get('promo'), 'placed_at': b.get('placed_at'),
        'sport': b.get('sport'), 'headline': b.get('headline'), 'subtitle': b.get('subtitle'),
        'event_name': b.get('event',{}).get('name'), 'raw_ocr_text': b.get('raw_ocr_text'),
        'screenshot_hash': b.get('screenshot_hash'), 'source_filename': b.get('source_filename'),
        'normalized_json': json.dumps(b, ensure_ascii=False),
        'draftkings_share_url': b.get('draftkings_share_url'), 'fanatics_share_url': b.get('fanatics_share_url'),
        'fanatics_shortlink': b.get('fanatics_shortlink'), 'source_email_id': b.get('source_email_id'),
        'source_email_subject': b.get('source_email_subject'), 'espn_season_year': b.get('espn_season_year'),
        'espn_season_type': b.get('espn_season_type'), 'espn_week': b.get('espn_week'),
        'screenshot_path': b.get('screenshot_path'), 'screenshot_url': b.get('screenshot_url'),
    }


def _leg_row(bid, leg):
    ev = leg.get('event') or {}
    return {
        'bet_row_id': bid, 'leg_index': leg.get('index'), 'selection': leg.get('selection'),
        'market': leg.get('market'), 'line_value': leg.get('line') or leg.get('line_value'),
        'direction': leg.get('direction'), 'odds': leg.get('odds'), 'status': leg.get('status'),
        'event_time': ev.get('start_time'), 'raw_leg_text': leg.get('raw_leg_text'),
        'event_team_a': ev.get('away_team') or ev.get('team_1'),
        'event_team_b': ev.get('home_team') or ev.get('team_2'),
        'fanatics_event_id': leg.get('fanatics_event_id'), 'fanatics_market_id': leg.get('fanatics_market_id'),
        'fanatics_selection_id': leg.get('fanatics_selection_id'),
    }


def is_duplicate(screenshot_hash=None, sportsbook_bet_id=None):
    sb = get_supabase()
    if sb:
        if screenshot_hash:
            r = sb.table('bets').select('id').eq('screenshot_hash', screenshot_hash).limit(1).execute()
            if r.data: return True
        if sportsbook_bet_id:
            r = sb.table('bets').select('id').eq('sportsbook_bet_id', sportsbook_bet_id).limit(1).execute()
            if r.data: return True
        return False
    with connect() as con:
        if screenshot_hash and con.execute('SELECT 1 FROM bets WHERE screenshot_hash=?',(screenshot_hash,)).fetchone(): return True
        if sportsbook_bet_id and con.execute('SELECT 1 FROM bets WHERE sportsbook_bet_id=?',(sportsbook_bet_id,)).fetchone(): return True
    return False


def save_bet(b):
    sb = get_supabase()
    if sb:
        result = sb.table('bets').insert(_bet_row(b)).execute()
        if not result.data:
            raise RuntimeError('Supabase did not return the inserted bet row.')
        bid = result.data[0]['id']
        legs = [_leg_row(bid, leg) for leg in (b.get('legs') or [])]
        if legs:
            sb.table('bet_legs').insert(legs).execute()
        return bid

    row = _bet_row(b)
    cols = list(row.keys())
    vals = [row[c] for c in cols]
    with connect() as con:
        cur = con.execute(f"INSERT INTO bets({','.join(cols)}) VALUES({','.join(['?']*len(cols))})", vals)
        bid = cur.lastrowid
        for leg in b.get('legs',[]):
            lr = _leg_row(bid, leg); lcols=list(lr.keys()); lvals=[lr[c] for c in lcols]
            con.execute(f"INSERT INTO bet_legs({','.join(lcols)}) VALUES({','.join(['?']*len(lcols))})", lvals)
        return bid


def list_bets(status=None):
    sb = get_supabase()
    if sb:
        q = sb.table('bets').select('*')
        if status: q = q.eq('status', status)
        r = q.order('placed_at', desc=True).order('id', desc=True).execute()
        return r.data or []
    with connect() as con:
        q='SELECT * FROM bets'; p=[]
        if status: q+=' WHERE status=?'; p=[status]
        q+=' ORDER BY placed_at DESC,id DESC'
        return [dict(r) for r in con.execute(q,p)]


def list_legs(bet_row_id):
    sb = get_supabase()
    if sb:
        r = sb.table('bet_legs').select('*').eq('bet_row_id', bet_row_id).order('leg_index').order('id').execute()
        return r.data or []
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM bet_legs WHERE bet_row_id=? ORDER BY leg_index,id',(bet_row_id,))]


def update_leg_live(leg_id,event_id,state,value,updated_at):
    data={'espn_event_id':event_id,'live_state':state,'live_value':str(value) if value is not None else None,'live_updated_at':updated_at}
    sb=get_supabase()
    if sb: sb.table('bet_legs').update(data).eq('id',leg_id).execute(); return
    with connect() as con: con.execute('UPDATE bet_legs SET espn_event_id=?,live_state=?,live_value=?,live_updated_at=? WHERE id=?',(event_id,state,data['live_value'],updated_at,leg_id))


def replace_bet(b):
    h=b.get('screenshot_hash'); sbid=b.get('sportsbook_bet_id'); sb=get_supabase()
    if sb:
        ids=[]
        if h:
            ids += [r['id'] for r in (sb.table('bets').select('id').eq('screenshot_hash',h).execute().data or [])]
        if sbid:
            ids += [r['id'] for r in (sb.table('bets').select('id').eq('sportsbook_bet_id',sbid).execute().data or [])]
        for bid in sorted(set(ids)):
            sb.table('bet_legs').delete().eq('bet_row_id',bid).execute()
            sb.table('bets').delete().eq('id',bid).execute()
        return save_bet(b)
    with connect() as con:
        ids=[]
        if h: ids += [r[0] for r in con.execute('SELECT id FROM bets WHERE screenshot_hash=?',(h,)).fetchall()]
        if sbid: ids += [r[0] for r in con.execute('SELECT id FROM bets WHERE sportsbook_bet_id=?',(sbid,)).fetchall()]
        for bid in sorted(set(ids)): con.execute('DELETE FROM bets WHERE id=?',(bid,))
    return save_bet(b)


def update_bet_espn_scope(bet_id, season_year=None, season_type=None, week=None):
    data={'espn_season_year':season_year,'espn_season_type':season_type,'espn_week':week}; sb=get_supabase()
    if sb: sb.table('bets').update(data).eq('id',bet_id).execute(); return
    with connect() as con: con.execute('UPDATE bets SET espn_season_year=?, espn_season_type=?, espn_week=? WHERE id=?',(season_year,season_type,week,bet_id))


def update_leg_future_settings(leg_id, tracking_scope='SEASON', season_year=2026, season_type=2):
    data={'tracking_scope':tracking_scope,'future_season_year':season_year,'future_season_type':season_type}; sb=get_supabase()
    if sb: sb.table('bet_legs').update(data).eq('id',leg_id).execute(); return
    with connect() as con: con.execute('UPDATE bet_legs SET tracking_scope=?, future_season_year=?, future_season_type=? WHERE id=?',(tracking_scope,season_year,season_type,leg_id))


def update_leg_future_line_direction(leg_id, line_value, direction):
    data={'line_value':str(line_value),'direction':direction}; sb=get_supabase()
    if sb: sb.table('bet_legs').update(data).eq('id',leg_id).execute(); return
    with connect() as con: con.execute('UPDATE bet_legs SET line_value=?,direction=? WHERE id=?',(str(line_value),direction,leg_id))


def update_leg_future_live(leg_id, athlete_id, state, current, games_played, pace, updated_at):
    data={'espn_athlete_id':athlete_id,'future_state':state,'future_current':current,'future_games_played':games_played,'future_pace':pace,'future_updated_at':updated_at}; sb=get_supabase()
    if sb: sb.table('bet_legs').update(data).eq('id',leg_id).execute(); return
    with connect() as con: con.execute('UPDATE bet_legs SET espn_athlete_id=?,future_state=?,future_current=?,future_games_played=?,future_pace=?,future_updated_at=? WHERE id=?',(athlete_id,state,current,games_played,pace,updated_at,leg_id))


def future_legs():
    sb=get_supabase()
    if sb:
        legs = sb.table('bet_legs').select('*').eq('tracking_scope','SEASON').order('bet_row_id',desc=True).order('leg_index').execute().data or []
        if not legs: return []
        bet_ids=sorted({x['bet_row_id'] for x in legs})
        bets = sb.table('bets').select('id,headline,sport,status,current_odds,stake,to_pay').in_('id',bet_ids).execute().data or []
        bm={b['id']:b for b in bets}
        out=[]
        for l in legs:
            b=bm.get(l['bet_row_id'],{}); x=dict(l)
            x.update({'bet_headline':b.get('headline'),'bet_sport':b.get('sport'),'bet_status':b.get('status'),'bet_odds':b.get('current_odds'),'bet_stake':b.get('stake'),'bet_to_pay':b.get('to_pay')})
            out.append(x)
        return out
    with connect() as con:
        rows=con.execute("""SELECT l.*, b.headline AS bet_headline, b.sport AS bet_sport, b.status AS bet_status, b.current_odds AS bet_odds, b.stake AS bet_stake, b.to_pay AS bet_to_pay
                            FROM bet_legs l JOIN bets b ON b.id=l.bet_row_id
                            WHERE COALESCE(l.tracking_scope,'')='SEASON'
                            ORDER BY b.id DESC,l.leg_index""").fetchall()
        return [dict(r) for r in rows]
