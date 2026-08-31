import io
from datetime import datetime
from PIL import Image
import pandas as pd
import streamlit as st
st.write("Secret keys loaded:", list(st.secrets.keys()))
st.write(
    "Has SUPABASE_URL:",
    "SUPABASE_URL" in st.secrets
)
st.write(
    "Has SUPABASE_SERVICE_ROLE_KEY:",
    "SUPABASE_SERVICE_ROLE_KEY" in st.secrets
)
st.write(
    "Has BET_UPLOAD_TOKEN:",
    "BET_UPLOAD_TOKEN" in st.secrets
)
from ocr.extractor import extract_text
from importers.draftkings.parser import parse_draftkings
from services.duplicate_detector import sha256_bytes
from database.db import init_db, save_bet, replace_bet, is_duplicate, list_bets, list_legs, update_leg_live, update_bet_espn_scope, update_leg_future_settings, update_leg_future_live, update_leg_line_direction, update_leg_manual_status, future_legs
from services.espn_nfl import find_event, event_snapshot, game_summary, snapshot_display, team_for_player
from services.progress import evaluate_leg, parlay_state, progress_text
from services.gmail_import import scan_label
from services.espn_season import canonical_market, future_progress
from importers.fanatics.parser import parse_fanatics_share_url
from services.supabase_api import refresh_all_active_bets, recheck_leg

st.set_page_config(page_title='Sports Bet Tracker', page_icon='🎟️', layout='wide')
init_db()

st.title('Sports Bet Tracker')
st.caption('Version 8.0 • Supabase-backed bet tracking + on-demand live refresh')

def _money(v): return '' if v is None else f'${float(v):,.2f}'
def _odds(v): return '' if v is None else f'{int(v):+d}'

ACTIVE_STATUSES={'PENDING','OPEN','LIVE','IN_PROGRESS'}
SETTLED_STATUSES={'WON','LOST','PUSH','VOID','VOIDED','CANCELLED','CANCELED','CASHED_OUT'}
def _is_active_status(v): return str(v or '').upper() in ACTIVE_STATUSES

def refresh_bet_live(bet):
    results=[]
    summary_cache={}
    season_year=bet.get('espn_season_year')
    season_type=bet.get('espn_season_type')
    week=bet.get('espn_week')
    for leg in list_legs(bet['id']):
        if (bet.get('sport') or '').upper()!='NFL':
            results.append((leg,{'state':'NFL ONLY'})); continue
        team_a=leg.get('event_team_a'); team_b=leg.get('event_team_b')
        # If parser did not capture matchup, the selection itself is enough when date is known.
        event=find_event(team_a, team_b, leg.get('event_time'), selection=leg.get('selection'), season_year=season_year, season_type=season_type, week=week)
        # Player-only props (especially mobile Anytime TD slips) often contain no
        # printed matchup. Resolve the player's current ESPN roster team, then
        # find that team's scheduled/live game.
        if not event and leg.get('selection'):
            team=team_for_player(leg.get('selection'))
            if team:
                event=find_event(selection=team, start_time=leg.get('event_time'), season_year=season_year, season_type=season_type, week=week)
        if not event:
            results.append((leg,{'state':'NO ESPN MATCH'})); continue
        snap=event_snapshot(event)
        summary=None
        # Team-yardage and player-prop markets require ESPN box-score data.
        # Cache by event so multi-leg SGPs do not download the same game summary
        # repeatedly during one refresh.
        market=(leg.get('market') or '').upper()
        needs_summary = (
            'TEAM TOTAL YARDS' in market or
            'TEAM TOTAL RUSHING YARDS' in market or
            'TEAM TOTAL RECEIVING YARDS' in market or
            ('MONEYLINE' not in market and 'SPREAD' not in market and 'TOTAL' not in market)
        )
        if needs_summary:
            event_id=str(event.get('id'))
            if event_id not in summary_cache:
                try: summary_cache[event_id]=game_summary(event_id)
                except Exception: summary_cache[event_id]=None
            summary=summary_cache.get(event_id)
        prog=evaluate_leg(leg,snap,summary)
        display=progress_text(prog)
        if not display: display=snapshot_display(snap)
        update_leg_live(leg['id'],event.get('id'),prog.get('state'),display,datetime.now().isoformat(timespec='seconds'))
        leg.update({'espn_event_id':event.get('id'),'live_state':prog.get('state'),'live_value':display})
        results.append((leg,prog))
    return results

tab_dash, tab_email, tab_import, tab_fanatics, tab_active, tab_futures, tab_history = st.tabs(['Dashboard','Email Import','Import Bets','Fanatics Import','Active Bets','Season Futures','History'])

with tab_dash:
    all_bets=list_bets(); open_bets=[b for b in all_bets if _is_active_status(b.get('status'))]
    c1,c2,c3,c4=st.columns(4)
    c1.metric('Active Bets',len(open_bets)); c2.metric('Total Bets',len(all_bets))
    total_stake=sum(float(b.get('stake') or 0) for b in all_bets); c3.metric('Total Wagered',_money(total_stake))
    pnl=sum((float(b.get('paid') or 0)-float(b.get('stake') or 0)) if b.get('status')=='WON' else (-float(b.get('stake') or 0) if b.get('status')=='LOST' else 0) for b in all_bets)
    c4.metric('Settled P/L',_money(pnl))
    st.info('Live tracking is enabled for NFL bets first. Other sports remain stored normally and will be added later.')


with tab_email:
    st.subheader('Import from Gmail')
    st.caption('Reads the Gmail label “Sports Bet Tracker” using Gmail IMAP. The app only reads that label and does not modify or delete email.')
    st.info('Gmail requires a Google App Password for this local importer. Your normal Gmail password will not work. The password is used only for this scan and is not written to bet_tracker.db.')
    ec1,ec2=st.columns([1.2,1])
    gmail_address=ec1.text_input('Gmail address', placeholder='you@gmail.com')
    gmail_app_password=ec2.text_input('Google App Password', type='password', help='16-character App Password generated by Google Account security.')
    limit=st.number_input('Messages to scan', min_value=1, max_value=500, value=100, step=25)
    if st.button('Scan Sports Bet Tracker label', type='primary'):
        if not gmail_address or not gmail_app_password:
            st.error('Enter your Gmail address and Google App Password first.')
        else:
            try:
                messages=scan_label(gmail_address, gmail_app_password, 'Sports Bet Tracker', int(limit))
                st.session_state['gmail_scan']=messages
                st.success(f'Found {len(messages)} labeled email(s) with a DraftKings link and/or image attachment.')
            except Exception as e:
                st.error(f'Gmail scan failed: {e}')
    messages=st.session_state.get('gmail_scan', [])
    if messages:
        new_count=0; imported_count=0
        for mi,msg in enumerate(messages):
            imgs=msg.get('images') or []
            link=(msg.get('draftkings_links') or [None])[0]
            with st.expander(f"{msg.get('subject') or 'DraftKings bet'} — {msg.get('date') or ''}", expanded=(mi==0)):
                if link:
                    st.write(f'**DraftKings share link:** {link}')
                if not imgs:
                    st.warning('No image attachment found in this email. The share link was saved for reference, but there is no screenshot to OCR.')
                    continue
                for ii,item in enumerate(imgs):
                    raw=item['bytes']; img=item['image']; h=sha256_bytes(raw)
                    
                    try:
                        text = extract_text(img, sparse=True)
                    except TypeError:
                        # Backward compatibility with older extractor.py copies.
                        text = extract_text(img)
                    parsed=parse_draftkings(text)
                    parsed['screenshot_hash']=h
                    parsed['source_filename']=item.get('filename') or 'email-bet.jpg'
                    parsed['draftkings_share_url']=link
                    parsed['source_email_id']=msg.get('message_id') or msg.get('imap_id')
                    parsed['source_email_subject']=msg.get('subject')
                    dup=is_duplicate(h,parsed.get('sportsbook_bet_id'))
                    c1,c2=st.columns([1,1.25])
                    with c1: st.image(img,use_container_width=True)
                    with c2:
                        st.write(f"**{parsed.get('headline') or 'Detected bet'}**")
                        st.write(f"Status: `{parsed.get('status')}`  •  Wager: `{_money(parsed.get('money',{}).get('stake'))}`  •  Odds: `{_odds(parsed.get('odds',{}).get('current'))}`")
                        st.write(f"Detected legs: **{len(parsed.get('legs') or [])}**")
                        if parsed.get('legs'):
                            st.dataframe(pd.DataFrame([{'#':x.get('index'),'Selection':x.get('selection'),'Market':x.get('market'),'Line':x.get('line'),'Odds':x.get('odds')} for x in parsed['legs']]),use_container_width=True,hide_index=True)
                        if dup:
                            st.caption('Already imported — skipped by duplicate detection.')
                        else:
                            new_count += 1
                            if st.button('Import this bet',key=f'emailimport_{mi}_{ii}'):
                                save_bet(parsed); imported_count += 1; st.success('Imported from Gmail.')
        st.caption('Tip: once the parser is reliable on your bet formats, we can change this page to one-click “Import All New Bets.”')

with tab_import:
    files=st.file_uploader('Upload DraftKings screenshots',type=['png','jpg','jpeg'],accept_multiple_files=True)
    if files:
        for idx,f in enumerate(files):
            raw=f.getvalue(); h=sha256_bytes(raw); img=Image.open(io.BytesIO(raw))
            with st.expander(f'{idx+1}. {f.name}',expanded=(idx==0)):
                c1,c2=st.columns([1,1.15])
                with c1: st.image(img,use_container_width=True)
                text=extract_text(img); parsed=parse_draftkings(text); parsed['screenshot_hash']=h; parsed['source_filename']=f.name
                dup=is_duplicate(h,parsed.get('sportsbook_bet_id'))
                with c2:
                    if dup: st.warning('Possible duplicate: screenshot hash or DraftKings bet ID already saved.')
                    statuses=['OPEN','WON','LOST','PUSH','VOID','CASHED_OUT','UNKNOWN']; types=['SINGLE','PARLAY','SGP']
                    status=st.selectbox('Status',statuses,index=statuses.index(parsed['status']) if parsed['status'] in statuses else -1,key=f's{idx}')
                    bt=st.selectbox('Bet type',types,index=types.index(parsed['bet_type']) if parsed['bet_type'] in types else 0,key=f'b{idx}')
                    a,b,c=st.columns(3)
                    stake=a.number_input('Wager',min_value=0.0,value=float(parsed['money']['stake'] or 0),step=1.0,key=f'w{idx}')
                    odds=b.number_input('Odds',value=int(parsed['odds']['current'] or 0),step=1,key=f'o{idx}')
                    payout=c.number_input('To Pay / Paid',min_value=0.0,value=float(parsed['money']['to_pay'] or parsed['money']['paid'] or 0),step=1.0,key=f'p{idx}')
                    betid=st.text_input('DraftKings bet ID',parsed.get('sportsbook_bet_id') or '',key=f'i{idx}')
                    headline=st.text_input('Headline',parsed.get('headline') or '',key=f'h{idx}'); subtitle=st.text_input('Subtitle / market',parsed.get('subtitle') or '',key=f'u{idx}')
                    parsed.update({'status':status,'bet_type':bt,'sportsbook_bet_id':betid or None,'headline':headline or None,'subtitle':subtitle or None}); parsed['odds']['current']=odds; parsed['money']['stake']=stake
                    if status=='OPEN': parsed['money']['to_pay']=payout; parsed['money']['paid']=None
                    elif status=='WON': parsed['money']['paid']=payout; parsed['money']['to_pay']=None
                    st.write('**Detected legs**')
                    if parsed['legs']:
                        st.dataframe(pd.DataFrame([{'#':x.get('index'),'Selection':x.get('selection'),'Market':x.get('market'),'Line':x.get('line'),'Odds':x.get('odds'),'Event':(x.get('event') or {}).get('start_time')} for x in parsed['legs']]),use_container_width=True,hide_index=True)
                    else: st.info('No legs detected. Save only after reviewing the OCR output.')
                    if dup:
                        st.caption('This screenshot/bet already exists. Use Replace Existing after parser upgrades to rebuild its legs.')
                        if st.button('Replace Existing Bet',key=f'replace{idx}',type='primary'):
                            replace_bet(parsed); st.success('Existing bet replaced with the newly parsed version.')
                    else:
                        if st.button('Save Bet',key=f'save{idx}'):
                            save_bet(parsed); st.success('Saved to bet_tracker.db')
                with st.expander('OCR / normalized JSON'): st.text(text); st.json(parsed)


with tab_fanatics:
    st.subheader('Fanatics Share Link Import')
    st.caption('Paste the expanded Fanatics share URL from your browser/iPhone. The app decodes the bet ID and every event/market/selection ID locally.')
    f_url=st.text_area('Fanatics share URL', height=110, placeholder='https://betfanatics.com/sportsbook?...deep_link_sub1=...')
    if st.button('Decode Fanatics bet', type='primary'):
        try:
            fbet=parse_fanatics_share_url(f_url)
            st.session_state['fanatics_decoded']=fbet
        except Exception as e:
            st.error(str(e))
    fbet=st.session_state.get('fanatics_decoded')
    if fbet:
        if fbet.get('needs_expanded_link'):
            st.warning('This is the short fanatics.onelink.me URL. Open it in a browser and paste the final betfanatics.com URL so the embedded bet payload is available.')
        else:
            st.success(f"Decoded Fanatics bet ID {fbet.get('sportsbook_bet_id')} with {fbet.get('leg_count')} leg(s).")
            rows=[]
            for x in fbet.get('legs') or []:
                rows.append({'#':x.get('index'),'Event ID':x.get('fanatics_event_id'),'Market ID':x.get('fanatics_market_id'),'Selection ID':x.get('fanatics_selection_id')})
            if rows: st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.info('The shared URL gives us stable Fanatics IDs, but not the human-readable team/market/odds yet. The next resolver step will map these IDs to bet text without OCR.')
            if is_duplicate(None,fbet.get('sportsbook_bet_id')):
                st.caption('Already imported — duplicate bet ID detected.')
            elif st.button('Import Fanatics skeleton bet'):
                save_bet(fbet); st.success('Fanatics bet IDs saved.'); st.rerun()

with tab_active:
    st.subheader('Active Bets')
    rows=list_bets('OPEN')

    top1,top2=st.columns([1.2,4.8])
    do_refresh=top1.button('↻ Refresh Bets',type='primary',disabled=(len(rows)==0))
    top2.caption('Refreshes only active/open bets through Supabase. Settled bets are skipped automatically.')

    if do_refresh:
        try:
            with st.spinner('Updating active bets...'):
                refresh_result=refresh_all_active_bets(batch_size=50)
            st.session_state['last_live_refresh']=refresh_result
            st.session_state['last_live_refresh_at']=datetime.now().isoformat(timespec='seconds')
            st.rerun()
        except Exception as e:
            st.error(f'Live refresh failed: {e}')

    last_refresh=st.session_state.get('last_live_refresh')
    last_refresh_at=st.session_state.get('last_live_refresh_at')
    if last_refresh:
        st.success(
            f"Updated {last_refresh.get('updated',0)} leg(s) "
            f"across {last_refresh.get('batches',0)} batch(es). "
            f"Skipped {last_refresh.get('skipped',0)} • Failed {last_refresh.get('failed',0)}"
        )
        if last_refresh_at:
            st.caption(f'Last app refresh: {last_refresh_at}')

    # Reload after a possible Edge Function refresh.
    rows=list_bets('OPEN')

    if not rows:
        st.info('No active bets. Settled bets remain in History and are not refreshed automatically.')
    else:
        for bet in rows:
            with st.container(border=True):
                h1,h2,h3,h4=st.columns([4,1,1,1])
                h1.subheader(bet.get('headline') or bet.get('sportsbook_bet_id') or 'Bet')
                h2.metric('Wager',_money(bet.get('stake')))
                h3.metric('Odds',_odds(bet.get('current_odds')))
                h4.metric('To Pay',_money(bet.get('to_pay')))

                st.caption(
                    f"{bet.get('sportsbook_bet_id') or ''} • "
                    f"{bet.get('sport') or 'Unknown sport'} • "
                    f"{bet.get('placed_at') or ''}"
                )

                if bet.get('draftkings_share_url'):
                    st.markdown(f"[Open DraftKings shared slip]({bet.get('draftkings_share_url')})")

                # Existing per-bet schedule overrides are still useful for any
                # imported ticket that needs explicit season/week scoping.
                if (bet.get('sport') or '').upper() == 'NFL':
                    scope_cols=st.columns([1.7,1,1,1,1.1])
                    current_type=bet.get('espn_season_type')
                    scope_label={1:'Preseason',2:'Regular Season',3:'Postseason'}.get(current_type,'Auto')
                    season_label=scope_cols[0].selectbox(
                        'ESPN schedule',
                        ['Auto','Preseason','Regular Season','Postseason'],
                        index=['Auto','Preseason','Regular Season','Postseason'].index(scope_label),
                        key=f"scope_{bet['id']}"
                    )
                    default_year=int(
                        bet.get('espn_season_year')
                        or (
                            str(bet.get('placed_at') or '')[:4]
                            if str(bet.get('placed_at') or '')[:4].isdigit()
                            else datetime.now().year
                        )
                    )
                    season_year=scope_cols[1].number_input(
                        'Season',
                        min_value=2020,
                        max_value=2100,
                        value=default_year,
                        step=1,
                        key=f"year_{bet['id']}"
                    )
                    week_value=int(bet.get('espn_week') or 1)
                    week_num=scope_cols[2].number_input(
                        'Week',
                        min_value=1,
                        max_value=25,
                        value=week_value,
                        step=1,
                        key=f"week_{bet['id']}"
                    )
                    if scope_cols[3].button('Save', key=f"save_scope_{bet['id']}"):
                        type_num={'Preseason':1,'Regular Season':2,'Postseason':3}.get(season_label)
                        if season_label=='Auto':
                            update_bet_espn_scope(bet['id'], None, None, None)
                        else:
                            update_bet_espn_scope(
                                bet['id'],
                                int(season_year),
                                type_num,
                                int(week_num)
                            )
                        st.success('ESPN schedule setting saved.')
                        st.rerun()
                    saved_scope='Auto' if not current_type else f"{scope_label} W{bet.get('espn_week') or '?'} {bet.get('espn_season_year') or ''}"
                    scope_cols[4].caption(f"Saved: {saved_scope}")

                legs=list_legs(bet['id'])
                display=[]
                for leg in legs:
                    display.append({
                        '#':leg.get('leg_index'),
                        'Selection':leg.get('selection'),
                        'Market':leg.get('market'),
                        'Line':leg.get('line_value'),
                        'Odds':leg.get('odds'),
                        'Live':leg.get('live_value'),
                        'Game State':leg.get('live_state') or leg.get('future_state'),
                        'Status':leg.get('status') or 'PENDING',
                    })

                if display:
                    st.dataframe(
                        pd.DataFrame(display),
                        use_container_width=True,
                        hide_index=True
                    )

                st.write(f"**Bet status:** {bet.get('status') or 'PENDING'}")


with tab_futures:
    st.subheader('NFL Season Futures')
    st.caption('Track cumulative regular-season player props separately from single-game bets. ESPN season type 2 = Regular Season.')
    st.info('Season-future tracking supports Passing Yards, Passing TDs, Interceptions, Rushing Yards, Rushing TDs, Receiving Yards, Receptions, and Receiving TDs.')

    # Let any imported NFL player leg be promoted to a season future without re-importing the bet.
    eligible=[]
    for b in list_bets():
        if (b.get('sport') or '').upper()!='NFL':
            continue
        for lg in list_legs(b['id']):
            if canonical_market(lg.get('market')):
                eligible.append((b,lg))
    if eligible:
        st.markdown('#### Add / configure season futures')
        options={f"Bet {b['id']} • {lg.get('selection')} • {lg.get('market')} • line {lg.get('line_value') or '—'}":(b,lg) for b,lg in eligible}
        chosen=st.selectbox('Choose an imported player leg', list(options.keys()))
        b,lg=options[chosen]
        c1,c2,c3=st.columns(3)
        yr=c1.number_input('Season',min_value=2020,max_value=2100,value=int(lg.get('future_season_year') or 2026),step=1,key='futureyear')
        direction=c2.selectbox('Direction',['OVER','UNDER'],index=0 if str(lg.get('direction') or 'OVER').upper()!='UNDER' else 1,key='futuredir')
        line_default=0.0
        try:
            import re as _re
            _m=_re.search(r'\d+(?:\.\d+)?',str(lg.get('line_value') or ''))
            if _m: line_default=float(_m.group())
        except Exception: pass
        line=c3.number_input('Line',min_value=0.0,value=line_default,step=0.5,key='futureline')
        if st.button('Track this leg as season future'):
            # Preserve the user's normalized line/direction directly on the Supabase leg.
            update_leg_line_direction(lg['id'], line, direction)
            update_leg_future_settings(lg['id'],'SEASON',int(yr),2)
            st.success('Season-future tracking enabled for this leg.')
            st.rerun()
    else:
        st.caption('No imported NFL player markets are eligible yet. Import a season-long player prop first.')

    tracked=future_legs()
    if tracked:
        st.markdown('#### Tracked season futures')
        if st.button('Refresh Season Stats',type='primary'):
            refreshed=[]
            for lg in tracked:
                try:
                    import re as _re
                    m=_re.search(r'\d+(?:\.\d+)?',str(lg.get('line_value') or ''))
                    line=float(m.group()) if m else None
                    prog=future_progress(lg.get('selection'),lg.get('market'),line,lg.get('direction') or 'OVER',int(lg.get('future_season_year') or 2026),int(lg.get('future_season_type') or 2),lg.get('espn_athlete_id'))
                    update_leg_future_live(lg['id'],prog.get('athlete_id'),prog.get('state'),prog.get('current'),prog.get('games_played'),prog.get('pace'),datetime.now().isoformat(timespec='seconds'))
                    refreshed.append((lg,prog))
                except Exception as e:
                    refreshed.append((lg,{'state':f'ERROR: {e}'}))
            st.session_state['future_refresh']=refreshed
            st.rerun()
        # Reload after possible refresh so persisted values display consistently.
        tracked=future_legs()
        rows=[]
        for lg in tracked:
            cur=lg.get('future_current'); line=lg.get('line_value'); gp=lg.get('future_games_played'); pace=lg.get('future_pace')
            rows.append({'Player':lg.get('selection'),'Market':lg.get('market'),'Line':line,'Current':cur,'Games':gp,'Pace':round(float(pace),1) if pace is not None else None,'State':lg.get('future_state') or 'NOT REFRESHED','Season':lg.get('future_season_year') or 2026})
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        st.caption('Pace = current stat ÷ games played × 17. Official sportsbook settlement rules still control voids, injuries, pushes, and special minimum-game conditions.')

with tab_history:
    rows=list_bets()
    if rows:
        df=pd.DataFrame(rows)
        show=['status','sportsbook_bet_id','headline','stake','current_odds','paid','to_pay','sport','placed_at']
        existing=[c for c in show if c in df.columns]
        st.dataframe(df[existing],use_container_width=True,hide_index=True)
        st.download_button(
            'Export CSV',
            df[existing].to_csv(index=False).encode(),
            'bet_history.csv',
            'text/csv'
        )

        settled=[b for b in rows if str(b.get('status') or '').upper() in SETTLED_STATUSES]
        if settled:
            st.markdown('#### Manual settled-bet recheck')
            st.caption('Settled bets are skipped during normal Refresh Bets. Use this only for a stat correction or sportsbook adjustment.')
            options={
                f"Bet {b['id']} • {b.get('sportsbook') or ''} • {b.get('headline') or b.get('sportsbook_bet_id') or ''} • {b.get('status')}":b
                for b in settled
            }
            chosen=st.selectbox('Settled bet',list(options.keys()),key='settled_recheck_bet')
            chosen_bet=options[chosen]
            if st.button('Recheck selected settled bet'):
                try:
                    legs=list_legs(chosen_bet['id'])
                    if not legs:
                        st.warning('This bet has no legs to recheck.')
                    else:
                        results=[]
                        with st.spinner('Rechecking settled bet...'):
                            for leg in legs:
                                results.append(recheck_leg(leg['id']))
                        st.success(f"Rechecked {len(results)} leg(s).")
                        st.rerun()
                except Exception as e:
                    st.error(f'Recheck failed: {e}')

        st.markdown('#### Manual VOID override')
        st.caption('Use this when the sportsbook explicitly voids a leg. Parent/parlay settlement will be recalculated on the next Refresh Bets or manual recheck.')
        bet_options={
            f"Bet {b['id']} • {b.get('sportsbook') or ''} • {b.get('headline') or b.get('sportsbook_bet_id') or ''}":b
            for b in rows
        }
        void_bet_label=st.selectbox('Bet for VOID override',list(bet_options.keys()),key='void_bet')
        void_bet=bet_options[void_bet_label]
        void_legs=list_legs(void_bet['id'])
        if void_legs:
            leg_options={
                f"Leg {lg['id']} • {lg.get('selection') or ''} • {lg.get('market') or ''} • {lg.get('status') or 'PENDING'}":lg
                for lg in void_legs
            }
            void_leg_label=st.selectbox('Leg to VOID',list(leg_options.keys()),key='void_leg')
            void_leg=leg_options[void_leg_label]
            if st.button('Mark selected leg VOID'):
                try:
                    update_leg_manual_status(void_leg['id'],'VOID')
                    # Direct recheck would overwrite a sportsbook-specific VOID
                    # from ESPN, so we intentionally leave the manual override as-is.
                    st.success('Leg marked VOID. Parent settlement will honor the manual status.')
                    st.rerun()
                except Exception as e:
                    st.error(f'VOID update failed: {e}')
    else:
        st.info('No bets saved yet.')

