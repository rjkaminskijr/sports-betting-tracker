from datetime import datetime

import pandas as pd
import streamlit as st

from database.db import (
    init_db,
    list_bets,
    list_legs,
    update_bet_espn_scope,
    update_leg_future_settings,
    update_leg_future_live,
    update_leg_line_direction,
    update_leg_manual_status,
    future_legs,
)
from services.espn_season import canonical_market, future_progress
from services.supabase_api import (
    refresh_all_active_bets,
    recheck_leg,
    recalculate_parent_from_manual_leg,
    list_round_robin_combinations,
)

st.set_page_config(page_title='Sports Bet Tracker', page_icon='🎟️', layout='wide')
init_db()

st.title('Sports Bet Tracker')
st.caption('Version 15.0 • Round Robin details + manual VOID settlement')

def _money(v): return '' if v is None else f'${float(v):,.2f}'
def _odds(v): return '' if v is None else f'{int(v):+d}'

ACTIVE_STATUSES={'PENDING','OPEN','LIVE','IN_PROGRESS'}
SETTLED_STATUSES={'WON','LOST','PUSH','VOID','VOIDED','CANCELLED','CANCELED','CASHED_OUT'}
def _is_active_status(v): return str(v or '').upper() in ACTIVE_STATUSES


def _safe_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _display_sport(bet, legs):
    sport = str(bet.get('sport') or '').strip()
    if sport:
        upper = sport.upper()
        aliases = {
            'NCAAF': 'CFB',
            'CFB': 'CFB',
            'COLLEGE FOOTBALL': 'CFB',
        }
        return aliases.get(upper, upper)

    # match-players only assigns athlete IDs to NFL props, so this is
    # a safe fallback for imported NFL player slips whose parent sport
    # was not populated.
    if any(lg.get('espn_athlete_id') for lg in legs):
        return 'NFL'

    return 'Football'


def _clean_market_name(value):
    value = str(value or '').strip()
    if not value:
        return 'Bet'

    replacements = {
        'First TD Scorer': 'First TD Scorer',
        'Anytime TD Scorer': 'Anytime TD Scorer',
        'Last TD Scorer': 'Last TD Scorer',
        'Moneyline': 'Moneyline',
        'Spread': 'Spread',
        'Total': 'Total',
    }
    return replacements.get(value, value)


def _bet_description(bet, legs):
    sport = _display_sport(bet, legs)

    markets = [
        _clean_market_name(lg.get('market'))
        for lg in legs
        if str(lg.get('market') or '').strip()
    ]
    unique_markets = list(dict.fromkeys(markets))

    rr_size = bet.get('round_robin_size')
    rr_combos = bet.get('round_robin_combinations')
    bet_type = str(bet.get('bet_type') or '').strip().upper()

    if 'TEASER' in bet_type:
        # DraftKings teaser receipts may store the teaser point value in
        # headline/subtitle. Prefer that when available.
        teaser_text = 'Teaser'
        source_text = ' '.join(
            str(x or '')
            for x in [
                bet.get('headline'),
                bet.get('subtitle'),
            ]
        )
        import re as _re
        match = _re.search(r'(\d+(?:\.\d+)?)\s*[- ]?Point\s+Teaser', source_text, _re.I)
        if match:
            teaser_text = f"{match.group(1)}-Point Teaser"

        return f"{sport} {teaser_text}"

    if 'SGPX' in bet_type:
        return f"{sport} SGPx {len(legs)}-Pick Parlay"

    if rr_size or rr_combos or 'ROUND ROBIN' in bet_type:
        if len(unique_markets) == 1:
            return f"{sport} {unique_markets[0]} Round Robin"
        return f"{sport} Round Robin"

    if len(legs) == 1:
        market = unique_markets[0] if unique_markets else 'Straight Bet'
        return f"{sport} {market}"

    if len(unique_markets) == 1:
        return f"{sport} {unique_markets[0]} Parlay"

    if 'SGP' in bet_type or 'SAME GAME' in bet_type:
        return f"{sport} Same Game Parlay"

    if len(legs) > 1:
        return f"{sport} {len(legs)}-Leg Parlay"

    headline = str(bet.get('headline') or '').strip()
    if headline:
        return headline

    return f"{sport} Bet"


def _profit_loss(bet):
    stake = _safe_float(bet.get('stake'))
    paid = _safe_float(bet.get('paid'))
    status = str(bet.get('status') or '').upper()

    if stake is None:
        return None

    if status == 'LOST':
        return -stake

    if status in {'PUSH', 'VOID', 'VOIDED', 'CANCELLED', 'CANCELED'}:
        return 0.0

    if paid is not None and status in SETTLED_STATUSES:
        return paid - stake

    return None


def _format_datetime(value):
    if not value:
        return ''

    value = str(value)
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %I:%M %p')
    except Exception:
        return value


def _build_bet_table_rows(bets):
    table_rows = []
    leg_map = {}

    for bet in bets:
        legs = list_legs(bet['id'])
        leg_map[bet['id']] = legs

        rr_size = bet.get('round_robin_size')
        rr_combos = bet.get('round_robin_combinations')
        bet_type = str(bet.get('bet_type') or '').strip()

        if rr_size:
            type_text = f"Round Robin {rr_size}s"
            if rr_combos:
                type_text += f" ({rr_combos})"
        elif bet_type:
            type_text = bet_type
        elif len(legs) > 1:
            type_text = 'Parlay'
        else:
            type_text = 'Straight'

        table_rows.append({
            'ID': bet.get('id'),
            'Description': _bet_description(bet, legs),
            'Sportsbook': bet.get('sportsbook') or '',
            'Type': type_text,
            'Legs': len(legs),
            'Status': bet.get('status') or 'PENDING',
            'Wager': _safe_float(bet.get('stake')),
            'Odds': bet.get('current_odds') if bet.get('current_odds') is not None else bet.get('original_odds'),
            'To Pay': _safe_float(bet.get('to_pay')),
            'Paid': _safe_float(bet.get('paid')),
            'P/L': _profit_loss(bet),
            'Placed': _format_datetime(bet.get('placed_at')),
        })

    return table_rows, leg_map


def _leg_game_detail(leg):
    team_a = str(leg.get('event_team_a') or '').strip()
    team_b = str(leg.get('event_team_b') or '').strip()

    if team_a and team_b:
        return f"{team_a} @ {team_b}"

    if team_a:
        return team_a

    if team_b:
        return team_b

    event_name = str(leg.get('event_name') or '').strip()
    if event_name:
        return event_name

    return ''


def _render_leg_table(legs, bet=None):
    display = []

    show_leg_odds = not (
        len(legs) == 1 or
        str((bet or {}).get('bet_type') or '').upper() == 'STRAIGHT'
    )

    for leg in legs:
        row = {
            '#': leg.get('leg_index'),
            'Selection': leg.get('selection'),
            'Game': _leg_game_detail(leg),
            'Market': leg.get('market'),
            'Line': leg.get('line_value'),
            'Live': leg.get('live_value'),
            'Game State': leg.get('live_state') or leg.get('future_state'),
            'Status': leg.get('status') or 'PENDING',
        }

        if show_leg_odds:
            row['Odds'] = leg.get('odds')

        display.append(row)

    if display:
        st.dataframe(
            pd.DataFrame(display),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption('No legs stored for this bet.')



def _round_robin_status(value):
    value = str(value or 'PENDING').strip().upper()
    return value or 'PENDING'


def _render_round_robin_combinations(bet, legs):
    bet_type = str(bet.get('bet_type') or '').strip().upper()
    is_round_robin = (
        bet_type == 'ROUND_ROBIN'
        or bet.get('round_robin_size') is not None
        or bet.get('round_robin_combinations') is not None
    )

    if not is_round_robin:
        return

    try:
        combinations = list_round_robin_combinations(bet['id'])
    except Exception as exc:
        st.warning(f'Could not load Round Robin combinations: {exc}')
        return

    st.markdown('#### Round Robin Combinations')

    if not combinations:
        st.caption('No stored Round Robin combinations were found for this bet.')
        return

    legs_by_id = {
        int(leg['id']): leg
        for leg in legs
        if leg.get('id') is not None
    }

    rows = []

    for combo in combinations:
        combo_leg_names = []

        for link in combo.get('combination_legs') or []:
            leg_id = link.get('bet_leg_id')
            leg = legs_by_id.get(int(leg_id)) if leg_id is not None else None

            if leg:
                selection = str(leg.get('selection') or '').strip()
                market = str(leg.get('market') or '').strip()

                if selection and market:
                    combo_leg_names.append(f'{selection} — {market}')
                elif selection:
                    combo_leg_names.append(selection)
                elif market:
                    combo_leg_names.append(market)
                else:
                    combo_leg_names.append(f'Leg {leg_id}')
            else:
                combo_leg_names.append(f'Leg {leg_id}')

        rows.append({
            '#': combo.get('combination_index'),
            'Legs': ' + '.join(combo_leg_names),
            'Wager': _safe_float(combo.get('stake')),
            'Odds': combo.get('odds'),
            'Potential Payout': _safe_float(combo.get('potential_payout')),
            'Paid': _safe_float(combo.get('actual_payout')),
            'Status': _round_robin_status(combo.get('status')),
        })

    combo_df = pd.DataFrame(rows)

    st.dataframe(
        combo_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            '#': st.column_config.NumberColumn('#', width='small'),
            'Legs': st.column_config.TextColumn('Legs', width='large'),
            'Wager': st.column_config.NumberColumn('Wager', format='$%.2f'),
            'Odds': st.column_config.NumberColumn('Odds', format='%+d'),
            'Potential Payout': st.column_config.NumberColumn(
                'Potential Payout',
                format='$%.2f',
            ),
            'Paid': st.column_config.NumberColumn('Paid', format='$%.2f'),
            'Status': st.column_config.TextColumn('Status', width='small'),
        },
    )

    total_wager = sum(
        _safe_float(combo.get('stake')) or 0.0
        for combo in combinations
    )
    total_potential = sum(
        _safe_float(combo.get('potential_payout')) or 0.0
        for combo in combinations
    )
    total_paid = sum(
        _safe_float(combo.get('actual_payout')) or 0.0
        for combo in combinations
    )

    c1, c2, c3 = st.columns(3)
    c1.metric('Combination Wager Total', _money(total_wager))
    c2.metric('Combination Potential Total', _money(total_potential))
    c3.metric('Combination Paid Total', _money(total_paid))


def _render_bet_metadata(bet, legs):
    description = _bet_description(bet, legs)
    st.markdown(f"### {description}")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric('Wager', _money(bet.get('stake')))
    m2.metric('Odds', _odds(
        bet.get('current_odds')
        if bet.get('current_odds') is not None
        else bet.get('original_odds')
    ))
    m3.metric('To Pay', _money(bet.get('to_pay')))
    m4.metric('Paid', _money(bet.get('paid')))
    m5.metric('P/L', _money(_profit_loss(bet)))

    detail_rows = {
        'Sportsbook': bet.get('sportsbook') or '',
        'Sportsbook Bet ID': bet.get('sportsbook_bet_id') or '',
        'Bet Type': bet.get('bet_type') or '',
        'Status': bet.get('status') or 'PENDING',
        'Sport': _display_sport(bet, legs),
        'Leg Count': bet.get('leg_count') or len(legs),
        'Placed At': _format_datetime(bet.get('placed_at')),
        'Screenshot Captured': _format_datetime(bet.get('source_captured_at')),
    }

    if bet.get('round_robin_size'):
        detail_rows['Round Robin Size'] = bet.get('round_robin_size')
    if bet.get('round_robin_combinations'):
        detail_rows['RR Combinations'] = bet.get('round_robin_combinations')
    if bet.get('round_robin_wager_each') is not None:
        detail_rows['Wager / Combination'] = _money(bet.get('round_robin_wager_each'))

    meta_df = pd.DataFrame(
        [{'Field': key, 'Value': value} for key, value in detail_rows.items()]
    )
    st.dataframe(meta_df, use_container_width=True, hide_index=True)

    if bet.get('draftkings_share_url'):
        st.markdown(f"[Open DraftKings shared slip]({bet.get('draftkings_share_url')})")

    if bet.get('fanatics_share_url'):
        st.markdown(f"[Open Fanatics shared slip]({bet.get('fanatics_share_url')})")

    _render_round_robin_combinations(bet, legs)

    st.markdown('#### Legs')
    _render_leg_table(legs, bet)


def _render_selectable_bet_table(bets, key):
    table_rows, leg_map = _build_bet_table_rows(bets)

    if not table_rows:
        return None, leg_map

    df = pd.DataFrame(table_rows)

    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select='rerun',
        selection_mode='single-row',
        key=key,
        column_config={
            'ID': st.column_config.NumberColumn('ID', width='small'),
            'Description': st.column_config.TextColumn('Description', width='large'),
            'Sportsbook': st.column_config.TextColumn('Sportsbook', width='medium'),
            'Type': st.column_config.TextColumn('Type', width='medium'),
            'Legs': st.column_config.NumberColumn('Legs', width='small'),
            'Status': st.column_config.TextColumn('Status', width='small'),
            'Wager': st.column_config.NumberColumn('Wager', format='$%.2f'),
            'Odds': st.column_config.NumberColumn('Odds', format='%+d'),
            'To Pay': st.column_config.NumberColumn('To Pay', format='$%.2f'),
            'Paid': st.column_config.NumberColumn('Paid', format='$%.2f'),
            'P/L': st.column_config.NumberColumn('P/L', format='$%.2f'),
            'Placed': st.column_config.TextColumn('Placed', width='medium'),
        },
    )

    selected_rows = list(event.selection.rows) if event and event.selection else []

    if not selected_rows:
        return None, leg_map

    selected_index = selected_rows[0]
    selected_id = int(df.iloc[selected_index]['ID'])

    selected_bet = next(
        (bet for bet in bets if int(bet['id']) == selected_id),
        None,
    )

    return selected_bet, leg_map


tab_dash, tab_active, tab_futures, tab_history = st.tabs(['Dashboard','Active Bets','Season Futures','History'])

with tab_dash:
    all_bets=list_bets(); open_bets=[b for b in all_bets if _is_active_status(b.get('status'))]
    c1,c2,c3,c4=st.columns(4)
    c1.metric('Active Bets',len(open_bets)); c2.metric('Total Bets',len(all_bets))
    total_stake=sum(float(b.get('stake') or 0) for b in all_bets); c3.metric('Total Wagered',_money(total_stake))
    pnl=sum((float(b.get('paid') or 0)-float(b.get('stake') or 0)) if b.get('status')=='WON' else (-float(b.get('stake') or 0) if b.get('status')=='LOST' else 0) for b in all_bets)
    c4.metric('Settled P/L',_money(pnl))
    st.info('Bets are imported through the iPhone/iPad Shortcut into Supabase. This app is now the tracking, refresh, futures, and history interface.')



with tab_active:
    st.subheader('Active Bets')
    rows = list_bets('OPEN')

    top1, top2 = st.columns([1.2, 4.8])
    do_refresh = top1.button(
        '↻ Refresh Bets',
        type='primary',
        disabled=(len(rows) == 0),
    )
    top2.caption(
        'Refreshes only active/open bets through Supabase. '
        'Settled bets are skipped automatically.'
    )

    if do_refresh:
        try:
            with st.spinner('Updating active bets...'):
                refresh_result = refresh_all_active_bets(batch_size=50)

            st.session_state['last_live_refresh'] = refresh_result
            st.session_state['last_live_refresh_at'] = datetime.now().isoformat(
                timespec='seconds'
            )
            st.rerun()
        except Exception as e:
            st.error(f'Live refresh failed: {e}')

    last_refresh = st.session_state.get('last_live_refresh')
    last_refresh_at = st.session_state.get('last_live_refresh_at')

    if last_refresh:
        match_events = last_refresh.get('match_events') or {}
        match_players = last_refresh.get('match_players') or {}

        st.success(
            f"Matched events: {match_events.get('matched', 0)} "
            f"across {match_events.get('batches', 0)} batch(es) • "
            f"Matched players: {match_players.get('matched', 0)} • "
            f"Updated live: {last_refresh.get('updated', 0)} leg(s) "
            f"across {last_refresh.get('batches', 0)} batch(es) • "
            f"Skipped {last_refresh.get('skipped', 0)} • "
            f"Failed {last_refresh.get('failed', 0)}"
        )

        if last_refresh.get('failed', 0):
            with st.expander('Show refresh errors'):
                failed_rows = [
                    r
                    for r in (last_refresh.get('results') or [])
                    if not r.get('ok', False)
                ]
                st.json(failed_rows[:10])

        if last_refresh_at:
            st.caption(f'Last app refresh: {last_refresh_at}')

    rows = list_bets('OPEN')

    if not rows:
        st.info(
            'No active bets. Settled bets remain in History and are '
            'not refreshed automatically.'
        )
    else:
        st.caption(
            'Click a bet row to expand its legs and additional details below.'
        )

        selected_bet, leg_map = _render_selectable_bet_table(
            rows,
            'active_bet_table',
        )

        if selected_bet:
            legs = leg_map.get(selected_bet['id']) or list_legs(selected_bet['id'])

            with st.container(border=True):
                _render_bet_metadata(selected_bet, legs)

                # Keep the manual ESPN schedule override available, but only
                # show it for the selected bet instead of every active bet.
                if _display_sport(selected_bet, legs) == 'NFL':
                    st.markdown('#### ESPN Schedule Override')

                    scope_cols = st.columns([1.7, 1, 1, 1, 1.1])
                    current_type = selected_bet.get('espn_season_type')
                    scope_label = {
                        1: 'Preseason',
                        2: 'Regular Season',
                        3: 'Postseason',
                    }.get(current_type, 'Auto')

                    season_label = scope_cols[0].selectbox(
                        'ESPN schedule',
                        ['Auto', 'Preseason', 'Regular Season', 'Postseason'],
                        index=[
                            'Auto',
                            'Preseason',
                            'Regular Season',
                            'Postseason',
                        ].index(scope_label),
                        key=f"scope_{selected_bet['id']}",
                    )

                    default_year = int(
                        selected_bet.get('espn_season_year')
                        or (
                            str(selected_bet.get('placed_at') or '')[:4]
                            if str(selected_bet.get('placed_at') or '')[:4].isdigit()
                            else datetime.now().year
                        )
                    )

                    season_year = scope_cols[1].number_input(
                        'Season',
                        min_value=2020,
                        max_value=2100,
                        value=default_year,
                        step=1,
                        key=f"year_{selected_bet['id']}",
                    )

                    week_value = int(selected_bet.get('espn_week') or 1)
                    week_num = scope_cols[2].number_input(
                        'Week',
                        min_value=1,
                        max_value=25,
                        value=week_value,
                        step=1,
                        key=f"week_{selected_bet['id']}",
                    )

                    if scope_cols[3].button(
                        'Save',
                        key=f"save_scope_{selected_bet['id']}",
                    ):
                        type_num = {
                            'Preseason': 1,
                            'Regular Season': 2,
                            'Postseason': 3,
                        }.get(season_label)

                        if season_label == 'Auto':
                            update_bet_espn_scope(
                                selected_bet['id'],
                                None,
                                None,
                                None,
                            )
                        else:
                            update_bet_espn_scope(
                                selected_bet['id'],
                                int(season_year),
                                type_num,
                                int(week_num),
                            )

                        st.success('ESPN schedule setting saved.')
                        st.rerun()

                    saved_scope = (
                        'Auto'
                        if not current_type
                        else (
                            f"{scope_label} "
                            f"W{selected_bet.get('espn_week') or '?'} "
                            f"{selected_bet.get('espn_season_year') or ''}"
                        )
                    )
                    scope_cols[4].caption(f"Saved: {saved_scope}")


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
    st.subheader('Bet History')
    rows = list_bets()

    if rows:
        st.caption(
            'All bets are shown in one table. Click a row to expand the '
            'full bet details and legs below.'
        )

        selected_bet, leg_map = _render_selectable_bet_table(
            rows,
            'history_bet_table',
        )

        if selected_bet:
            legs = leg_map.get(selected_bet['id']) or list_legs(selected_bet['id'])

            with st.container(border=True):
                _render_bet_metadata(selected_bet, legs)

        export_rows, _ = _build_bet_table_rows(rows)
        export_df = pd.DataFrame(export_rows)

        st.download_button(
            'Export CSV',
            export_df.to_csv(index=False).encode(),
            'bet_history.csv',
            'text/csv',
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
        st.caption('Use this only when the sportsbook explicitly voids a leg. The leg and parent are recalculated immediately without asking ESPN to regrade the manual VOID.')
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
                    with st.spinner('Applying VOID and recalculating parent...'):
                        update_leg_manual_status(void_leg['id'], 'VOID')
                        settlement = recalculate_parent_from_manual_leg(
                            void_leg['id']
                        )

                    if not settlement or not settlement.get('ok'):
                        raise RuntimeError(
                            (settlement or {}).get('error')
                            or 'Settlement recalculation did not succeed.'
                        )

                    st.success(
                        'Leg marked VOID and parent settlement recalculated '
                        'without ESPN regrading the leg.'
                    )
                    st.rerun()

                except Exception as e:
                    st.error(f'VOID update failed: {e}')
    else:
        st.info('No bets saved yet.')

