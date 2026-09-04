from datetime import datetime, timedelta
import csv
import hmac
import io
import json
import math
import zipfile
from xml.sax.saxutils import escape as xml_escape

import pandas as pd
import streamlit as st

from services.supabase_api import (
    list_bets,
    list_legs,
    update_bet_espn_scope,
    update_leg_manual_status,
    set_big_win_hidden,
    export_backup_tables,
    get_notification_settings,
    update_notification_settings,
    send_test_pushover,
    refresh_all_active_bets,
    recheck_leg,
    recalculate_parent_from_manual_leg,
    list_round_robin_combinations,
    list_future_candidates,
    list_future_legs,
    configure_future_leg,
    refresh_all_future_legs,
)

st.set_page_config(page_title='Sports Bet Tracker', page_icon='🎟️', layout='wide')

st.markdown(
    """
    <style>
    /* History settlement colors: full expandable bar, not just text. */
    details:has(.history-win-marker) > summary {
        background: #138A3D !important;
        border: 1px solid #28D764 !important;
        border-radius: 0.5rem !important;
    }

    details:has(.history-loss-marker) > summary {
        background: #B4232F !important;
        border: 1px solid #FF5263 !important;
        border-radius: 0.5rem !important;
    }

    details:has(.history-win-marker) > summary *,
    details:has(.history-loss-marker) > summary * {
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }

    details:has(.history-win-marker) > summary:hover {
        background: #17A84B !important;
    }

    details:has(.history-loss-marker) > summary:hover {
        background: #D12D3B !important;
    }

    .history-win-marker,
    .history-loss-marker {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _auth_secret(auth, *names):
    for name in names:
        try:
            value = auth.get(name)
        except Exception:
            value = None

        if value is not None and str(value) != '':
            return str(value)

    return None


def _require_login():
    """
    Protect the Streamlit UI using the existing nested [auth] section
    in .streamlit/secrets.toml / Streamlit Cloud secrets.

    Supported existing layouts:

        [auth]
        username = "..."
        password = "..."

    Password-only [auth] also works if no username is configured.
    """
    try:
        auth = st.secrets.get('auth', {})
    except Exception:
        auth = {}

    expected_username = _auth_secret(
        auth,
        'username',
        'user',
        'email',
    )

    expected_password = _auth_secret(
        auth,
        'password',
        'pass',
    )

    if not expected_password:
        st.error(
            'App login is enabled, but no password was found in the '
            'existing [auth] section of Streamlit secrets.'
        )
        st.code(
            '[auth]\n'
            'username = "your_username"\n'
            'password = "your_password"',
            language='toml',
        )
        st.stop()

    if st.session_state.get('authenticated') is True:
        return

    st.title('Sports Bet Tracker')
    st.subheader('Sign in')

    with st.form('login_form', clear_on_submit=False):
        username = None

        if expected_username:
            username = st.text_input(
                'Username',
                autocomplete='username',
            )

        password = st.text_input(
            'Password',
            type='password',
            autocomplete='current-password',
        )

        submitted = st.form_submit_button(
            'Sign in',
            type='primary',
        )

    if submitted:
        username_ok = (
            True
            if expected_username is None
            else hmac.compare_digest(
                str(username or ''),
                expected_username,
            )
        )

        password_ok = hmac.compare_digest(
            str(password or ''),
            expected_password,
        )

        if username_ok and password_ok:
            st.session_state['authenticated'] = True
            st.rerun()

        st.error('Incorrect username or password.')

    st.stop()


_require_login()

logout_col, _ = st.columns([1, 8])
with logout_col:
    if st.button('Log out'):
        st.session_state.pop('authenticated', None)
        st.rerun()

st.title('Sports Bet Tracker')
st.caption('Version 37.0 • Parlay leg progress + notification controls + export/backup + Big Wins')

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



def _expander_bet_label(bet, legs):
    description = _bet_description(bet, legs)

    status = str(
        bet.get('status') or 'PENDING'
    ).strip().upper()

    wager = _money(
        bet.get('stake')
    )

    odds = _odds(
        bet.get('current_odds')
        if bet.get('current_odds') is not None
        else bet.get('original_odds')
    )

    progress_text = ''

    if len(legs) > 1:
        progress = _parlay_leg_progress(
            legs
        )
        progress_text = (
            f"  •  {progress['summary']}"
        )

    return (
        f"{description}  •  "
        f"{status}"
        f"{progress_text}  •  "
        f"{wager}  •  {odds}"
    )


def _render_bet_expanders(bets, key_prefix, show_schedule_override=False):
    """
    Render one native Streamlit expander per bet.

    The expander arrow is the primary navigation: click the bet
    description to reveal its legs and details directly underneath.
    """
    if not bets:
        return

    for bet in bets:
        legs = list_legs(
            bet['id']
        )

        label = _expander_bet_label(
            bet,
            legs,
        )

        quick_issues = _bet_quality_issues(
            bet,
            legs,
        )

        if quick_issues:
            label = f"⚠️ {label}"

        bet_status = str(
            bet.get('status') or 'PENDING'
        ).strip().upper()

        # Keep the normal expander/arrow behavior. For History, a hidden
        # marker inside the expanded content lets the CSS above color the
        # entire summary bar via the :has() selector.
        bet_container = st.expander(
            label,
            expanded=False,
        )

        with bet_container:
            if key_prefix == 'history' and bet_status == 'WON':
                st.markdown(
                    '<span class="history-win-marker"></span>',
                    unsafe_allow_html=True,
                )
            elif key_prefix == 'history' and bet_status == 'LOST':
                st.markdown(
                    '<span class="history-loss-marker"></span>',
                    unsafe_allow_html=True,
                )
            m1, m2, m3, m4, m5 = st.columns(5)

            m1.metric(
                'Wager',
                _money(
                    bet.get('stake')
                ),
            )

            m2.metric(
                'Odds',
                _odds(
                    bet.get('current_odds')
                    if bet.get('current_odds') is not None
                    else bet.get('original_odds')
                ),
            )

            m3.metric(
                'To Pay',
                _money(
                    bet.get('to_pay')
                ),
            )

            m4.metric(
                'Paid',
                _money(
                    bet.get('paid')
                ),
            )

            m5.metric(
                'P/L',
                _money(
                    _profit_loss(
                        bet
                    )
                ),
            )

            st.caption(
                f"{bet.get('sportsbook') or ''}"
                f" • {_display_sport(bet, legs)}"
                f" • {len(legs)} leg(s)"
                f" • {_format_datetime(bet.get('placed_at')) or 'Placed time unavailable'}"
            )

            _render_round_robin_combinations(
                bet,
                legs,
            )

            _render_leg_table(
                legs,
                bet,
            )

            # Less-frequently needed receipt/database details stay one
            # level deeper so the main bet list remains compact.
            with st.expander(
                'Bet details',
                expanded=False,
            ):
                detail_rows = {
                    'Sportsbook': bet.get('sportsbook') or '',
                    'Sportsbook Bet ID': bet.get('sportsbook_bet_id') or '',
                    'Bet Type': bet.get('bet_type') or '',
                    'Status': bet.get('status') or 'PENDING',
                    'Sport': _display_sport(bet, legs),
                    'Leg Count': bet.get('leg_count') or len(legs),
                    'Placed At': _format_datetime(bet.get('placed_at')),
                    'Screenshot Captured': _format_datetime(
                        bet.get('source_captured_at')
                    ),
                }

                if bet.get('round_robin_size'):
                    detail_rows['Round Robin Size'] = bet.get(
                        'round_robin_size'
                    )

                if bet.get('round_robin_combinations'):
                    detail_rows['RR Combinations'] = bet.get(
                        'round_robin_combinations'
                    )

                if bet.get('round_robin_wager_each') is not None:
                    detail_rows['Wager / Combination'] = _money(
                        bet.get('round_robin_wager_each')
                    )

                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                'Field': field,
                                'Value': value,
                            }
                            for field, value in detail_rows.items()
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

                if bet.get('draftkings_share_url'):
                    st.markdown(
                        f"[Open DraftKings shared slip]"
                        f"({bet.get('draftkings_share_url')})"
                    )

                if bet.get('fanatics_share_url'):
                    st.markdown(
                        f"[Open Fanatics shared slip]"
                        f"({bet.get('fanatics_share_url')})"
                    )

            if (
                show_schedule_override
                and _display_sport(bet, legs) == 'NFL'
            ):
                with st.expander(
                    'ESPN Schedule Override',
                    expanded=False,
                ):
                    scope_cols = st.columns(
                        [1.7, 1, 1, 1, 1.1]
                    )

                    current_type = bet.get(
                        'espn_season_type'
                    )

                    scope_label = {
                        1: 'Preseason',
                        2: 'Regular Season',
                        3: 'Postseason',
                    }.get(
                        current_type,
                        'Auto',
                    )

                    season_label = scope_cols[0].selectbox(
                        'ESPN schedule',
                        [
                            'Auto',
                            'Preseason',
                            'Regular Season',
                            'Postseason',
                        ],
                        index=[
                            'Auto',
                            'Preseason',
                            'Regular Season',
                            'Postseason',
                        ].index(
                            scope_label
                        ),
                        key=f"{key_prefix}_scope_{bet['id']}",
                    )

                    default_year = int(
                        bet.get('espn_season_year')
                        or (
                            str(
                                bet.get('placed_at') or ''
                            )[:4]
                            if str(
                                bet.get('placed_at') or ''
                            )[:4].isdigit()
                            else datetime.now().year
                        )
                    )

                    season_year = scope_cols[1].number_input(
                        'Season',
                        min_value=2020,
                        max_value=2100,
                        value=default_year,
                        step=1,
                        key=f"{key_prefix}_year_{bet['id']}",
                    )

                    week_num = scope_cols[2].number_input(
                        'Week',
                        min_value=1,
                        max_value=25,
                        value=int(
                            bet.get('espn_week') or 1
                        ),
                        step=1,
                        key=f"{key_prefix}_week_{bet['id']}",
                    )

                    if scope_cols[3].button(
                        'Save',
                        key=f"{key_prefix}_save_scope_{bet['id']}",
                    ):
                        type_num = {
                            'Preseason': 1,
                            'Regular Season': 2,
                            'Postseason': 3,
                        }.get(
                            season_label
                        )

                        if season_label == 'Auto':
                            update_bet_espn_scope(
                                bet['id'],
                                None,
                                None,
                                None,
                            )
                        else:
                            update_bet_espn_scope(
                                bet['id'],
                                int(season_year),
                                type_num,
                                int(week_num),
                            )

                        st.success(
                            'ESPN schedule setting saved.'
                        )
                        st.rerun()

                    saved_scope = (
                        'Auto'
                        if not current_type
                        else (
                            f"{scope_label} "
                            f"W{bet.get('espn_week') or '?'} "
                            f"{bet.get('espn_season_year') or ''}"
                        )
                    )

                    scope_cols[4].caption(
                        f"Saved: {saved_scope}"
                    )


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



def _dashboard_status(bet):
    return str(bet.get('status') or 'PENDING').strip().upper()


def _dashboard_returned(bet):
    status = _dashboard_status(bet)
    stake = _safe_float(bet.get('stake')) or 0.0
    paid = _safe_float(bet.get('paid'))

    if status in SETTLED_STATUSES:
        if paid is not None:
            return paid
        if status in {'PUSH', 'VOID', 'VOIDED', 'CANCELLED', 'CANCELED'}:
            return stake
        return 0.0

    return 0.0


def _dashboard_bet_type(bet):
    bet_type = str(bet.get('bet_type') or '').strip().upper()

    if (
        bet.get('round_robin_size') is not None
        or bet.get('round_robin_combinations') is not None
        or bet_type == 'ROUND_ROBIN'
    ):
        return 'Round Robin'

    aliases = {
        'STRAIGHT': 'Straight',
        'SINGLE': 'Straight',
        'PARLAY': 'Parlay',
        'SGP': 'SGP',
        'SGPX': 'SGPx',
        'TEASER': 'Teaser',
    }

    return aliases.get(
        bet_type,
        bet_type.title() if bet_type else 'Other',
    )


def _dashboard_sport(bet):
    value = str(bet.get('sport') or '').strip().upper()

    aliases = {
        'NCAAF': 'CFB',
        'COLLEGE FOOTBALL': 'CFB',
    }

    if value:
        return aliases.get(value, value)

    # Use the same leg-aware fallback that the bet detail view uses.
    # This fixes historical bets whose parent bets.sport is NULL but
    # whose legs are already matched to NFL players/events.
    try:
        legs = list_legs(bet['id'])
        inferred = _display_sport(bet, legs)

        if inferred and str(inferred).strip().upper() not in {'', 'UNKNOWN'}:
            return aliases.get(
                str(inferred).strip().upper(),
                str(inferred).strip().upper(),
            )
    except Exception:
        pass

    return 'Unknown'


def _dashboard_date(bet):
    value = bet.get('placed_at') or bet.get('source_captured_at')
    if not value:
        return None

    try:
        return pd.to_datetime(value, utc=True, errors='coerce')
    except Exception:
        return None


def _dashboard_bet_rows(all_bets):
    rows = []

    for bet in all_bets:
        status = _dashboard_status(bet)
        stake = _safe_float(bet.get('stake')) or 0.0
        returned = _dashboard_returned(bet)
        pnl = _profit_loss(bet)
        to_pay = _safe_float(bet.get('to_pay')) or 0.0

        parent_odds = _safe_float(
            bet.get('current_odds')
            if bet.get('current_odds') is not None
            else (
                bet.get('boosted_odds')
                if bet.get('boosted_odds') is not None
                else bet.get('original_odds')
            )
        )

        leg_count = bet.get('leg_count')
        try:
            leg_count = int(leg_count) if leg_count is not None else None
        except (TypeError, ValueError):
            leg_count = None

        rows.append({
            'Bet ID': bet.get('id'),
            'Sportsbook': bet.get('sportsbook') or 'Unknown',
            'Bet Type': _dashboard_bet_type(bet),
            'Sport': _dashboard_sport(bet),
            'Status': status,
            'Wagered': stake,
            'Returned': returned,
            'P/L': pnl if pnl is not None else 0.0,
            'Potential Return': to_pay,
            'Odds': parent_odds,
            'Leg Count': leg_count,
            'Placed': _dashboard_date(bet),
            'Is Active': _is_active_status(status),
            'Is Settled': status in SETTLED_STATUSES,
        })

    return pd.DataFrame(rows)


def _summary_breakdown(df, group_col):
    if df.empty:
        return pd.DataFrame()

    grouped = (
        df.groupby(group_col, dropna=False)
        .agg(
            Bets=('Bet ID', 'count'),
            Wagered=('Wagered', 'sum'),
            Returned=('Returned', 'sum'),
            P_L=('P/L', 'sum'),
            Wins=('Status', lambda s: int((s == 'WON').sum())),
            Losses=('Status', lambda s: int((s == 'LOST').sum())),
            Active=('Is Active', 'sum'),
        )
        .reset_index()
    )

    grouped['ROI %'] = grouped.apply(
        lambda r: (
            (r['P_L'] / r['Wagered']) * 100.0
            if r['Wagered']
            else 0.0
        ),
        axis=1,
    )

    grouped = grouped.rename(columns={'P_L': 'P/L'})
    return grouped.sort_values('P/L', ascending=False)


def _render_summary_dataframe(df, group_col):
    if df.empty:
        st.caption('No data yet.')
        return

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            group_col: st.column_config.TextColumn(group_col),
            'Bets': st.column_config.NumberColumn('Bets', format='%d'),
            'Wagered': st.column_config.NumberColumn('Wagered', format='$%.2f'),
            'Returned': st.column_config.NumberColumn('Returned', format='$%.2f'),
            'P/L': st.column_config.NumberColumn('P/L', format='$%.2f'),
            'ROI %': st.column_config.NumberColumn('ROI %', format='%.1f%%'),
            'Wins': st.column_config.NumberColumn('Wins', format='%d'),
            'Losses': st.column_config.NumberColumn('Losses', format='%d'),
            'Active': st.column_config.NumberColumn('Active', format='%d'),
        },
    )


def _dashboard_leg_exposure(all_bets):
    """
    Active game-day exposure.

    Season-future bets are excluded. Player/team wager exposure is
    counted once per parent bet, even if the same player/team appears
    multiple times within that bet.
    """
    future_bet_ids = set()

    try:
        for future_leg in list_future_legs():
            if future_leg.get('bet_row_id') is not None:
                future_bet_ids.add(
                    int(future_leg['bet_row_id'])
                )
    except Exception:
        future_bet_ids = set()

    active_bets = [
        bet
        for bet in all_bets
        if (
            _is_active_status(bet.get('status'))
            and int(bet.get('id')) not in future_bet_ids
        )
    ]

    player_rows = []
    team_rows = []
    active_leg_count = 0
    winning_legs = 0
    losing_legs = 0
    pending_legs = 0

    for bet in active_bets:
        bet_id = int(
            bet.get('id')
        )
        stake = _safe_float(
            bet.get('stake')
        ) or 0.0
        potential = _safe_float(
            bet.get('to_pay')
        ) or 0.0
        legs = list_legs(
            bet_id
        )

        seen_players = set()
        seen_teams = set()

        for leg in legs:
            if (
                str(
                    leg.get('tracking_scope')
                    or ''
                )
                .strip()
                .upper()
                == 'SEASON'
            ):
                continue

            active_leg_count += 1
            leg_status = str(
                leg.get('status')
                or 'PENDING'
            ).upper()

            if leg_status == 'WON':
                winning_legs += 1
            elif leg_status == 'LOST':
                losing_legs += 1
            else:
                pending_legs += 1

            player = str(
                leg.get('selection')
                or ''
            ).strip()
            market = str(
                leg.get('market')
                or ''
            ).strip()

            is_player = bool(
                leg.get('espn_athlete_id')
                or any(
                    token in market.lower()
                    for token in [
                        'td scorer',
                        'touchdown scorer',
                        'receiving',
                        'rushing',
                        'passing',
                        'receptions',
                    ]
                )
            )

            player_key = player.casefold()

            if (
                player
                and is_player
                and player_key not in seen_players
            ):
                seen_players.add(
                    player_key
                )
                player_rows.append({
                    'Player': player,
                    'Bet ID': bet_id,
                    'Wager Exposure': stake,
                    'Potential Return': potential,
                })

            for value in [
                leg.get('event_team_a'),
                leg.get('event_team_b'),
            ]:
                team = str(
                    value or ''
                ).strip()

                if not team:
                    continue

                team_key = team.casefold()

                if team_key in seen_teams:
                    continue

                seen_teams.add(
                    team_key
                )
                team_rows.append({
                    'Team': team,
                    'Bet ID': bet_id,
                    'Wager Exposure': stake,
                    'Potential Return': potential,
                })

    def summarize(rows, label):
        if not rows:
            return pd.DataFrame()

        frame = pd.DataFrame(
            rows
        )

        summary = (
            frame.groupby(
                label,
                as_index=False,
            )
            .agg(
                Bets=('Bet ID', 'nunique'),
                Wager_Exposure=('Wager Exposure', 'sum'),
                Potential_Return=('Potential Return', 'sum'),
            )
            .rename(columns={
                'Wager_Exposure': 'Wager Exposure',
                'Potential_Return': 'Potential Return',
            })
        )

        summary['Concentration'] = summary['Bets'].apply(
            lambda x: (
                'HIGH'
                if int(x) >= 4
                else (
                    'MEDIUM'
                    if int(x) >= 2
                    else ''
                )
            )
        )

        return summary.sort_values(
            [
                'Wager Exposure',
                'Potential Return',
                'Bets',
            ],
            ascending=False,
        )

    return {
        'active_bet_count': len(active_bets),
        'active_leg_count': active_leg_count,
        'winning_legs': winning_legs,
        'losing_legs': losing_legs,
        'pending_legs': pending_legs,
        'players': summarize(
            player_rows,
            'Player',
        ),
        'teams': summarize(
            team_rows,
            'Team',
        ),
    }


def _round_robin_dashboard_summary(all_bets):
    rr_bets = [
        bet
        for bet in all_bets
        if _dashboard_bet_type(bet) == 'Round Robin'
    ]

    if not rr_bets:
        return {
            'bets': 0,
            'wagered': 0.0,
            'returned': 0.0,
            'pnl': 0.0,
            'won': 0,
            'lost': 0,
            'pending': 0,
        }

    wagered = sum(_safe_float(bet.get('stake')) or 0.0 for bet in rr_bets)
    returned = sum(_dashboard_returned(bet) for bet in rr_bets)
    pnl = sum((_profit_loss(bet) or 0.0) for bet in rr_bets)

    won = 0
    lost = 0
    pending = 0

    for bet in rr_bets:
        try:
            combos = list_round_robin_combinations(bet['id'])
        except Exception:
            combos = []

        for combo in combos:
            status = str(combo.get('status') or 'PENDING').upper()
            if status == 'WON':
                won += 1
            elif status == 'LOST':
                lost += 1
            else:
                pending += 1

    return {
        'bets': len(rr_bets),
        'wagered': wagered,
        'returned': returned,
        'pnl': pnl,
        'won': won,
        'lost': lost,
        'pending': pending,
    }



def _settled_performance_summary(df, group_col):
    if df.empty:
        return pd.DataFrame()

    grouped = (
        df.groupby(
            group_col,
            dropna=False,
        )
        .agg(
            Bets=('Bet ID', 'count'),
            Wagered=('Wagered', 'sum'),
            Returned=('Returned', 'sum'),
            P_L=('P/L', 'sum'),
            Wins=('Status', lambda s: int((s == 'WON').sum())),
            Losses=('Status', lambda s: int((s == 'LOST').sum())),
        )
        .reset_index()
    )

    grouped['Win Rate %'] = grouped.apply(
        lambda row: (
            (
                row['Wins']
                / (row['Wins'] + row['Losses'])
            ) * 100.0
            if (row['Wins'] + row['Losses'])
            else 0.0
        ),
        axis=1,
    )

    grouped['ROI %'] = grouped.apply(
        lambda row: (
            (row['P_L'] / row['Wagered']) * 100.0
            if row['Wagered']
            else 0.0
        ),
        axis=1,
    )

    grouped['Avg Wager'] = grouped.apply(
        lambda row: (
            row['Wagered'] / row['Bets']
            if row['Bets']
            else 0.0
        ),
        axis=1,
    )

    grouped['Avg P/L'] = grouped.apply(
        lambda row: (
            row['P_L'] / row['Bets']
            if row['Bets']
            else 0.0
        ),
        axis=1,
    )

    grouped = grouped.rename(
        columns={'P_L': 'P/L'}
    )

    return grouped.sort_values(
        ['P/L', 'ROI %'],
        ascending=False,
    )


def _render_settled_analytics_table(
    df,
    group_col,
):
    if df.empty:
        st.caption('No settled data available yet.')
        return

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            group_col: st.column_config.TextColumn(
                group_col
            ),
            'Bets': st.column_config.NumberColumn(
                'Bets',
                format='%d',
            ),
            'Wagered': st.column_config.NumberColumn(
                'Wagered',
                format='$%.2f',
            ),
            'Returned': st.column_config.NumberColumn(
                'Returned',
                format='$%.2f',
            ),
            'P/L': st.column_config.NumberColumn(
                'P/L',
                format='$%.2f',
            ),
            'Wins': st.column_config.NumberColumn(
                'Wins',
                format='%d',
            ),
            'Losses': st.column_config.NumberColumn(
                'Losses',
                format='%d',
            ),
            'Win Rate %': st.column_config.NumberColumn(
                'Win Rate',
                format='%.1f%%',
            ),
            'ROI %': st.column_config.NumberColumn(
                'ROI',
                format='%.1f%%',
            ),
            'Avg Wager': st.column_config.NumberColumn(
                'Avg Wager',
                format='$%.2f',
            ),
            'Avg P/L': st.column_config.NumberColumn(
                'Avg P/L',
                format='$%.2f',
            ),
        },
    )


def _odds_range(value):
    value = _safe_float(
        value
    )

    if value is None:
        return 'Unknown'

    if value <= -200:
        return '≤ -200'
    if value <= -101:
        return '-199 to -101'
    if value < 100:
        return '-100 to +99'
    if value <= 299:
        return '+100 to +299'
    if value <= 999:
        return '+300 to +999'

    return '+1000+'


def _leg_count_bucket(value):
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 'Unknown'

    if count <= 1:
        return '1 leg'
    return f'{count} legs'


def _render_historical_analytics(settled_df):
    st.markdown('#### Historical Analytics')

    if settled_df.empty:
        st.caption(
            'Historical analytics will appear after bets settle.'
        )
        return

    graded = settled_df[
        settled_df['Status'].isin(
            ['WON', 'LOST']
        )
    ]

    avg_wager = float(
        settled_df['Wagered'].mean()
    ) if len(settled_df) else 0.0

    avg_return = float(
        settled_df['Returned'].mean()
    ) if len(settled_df) else 0.0

    avg_pnl = float(
        settled_df['P/L'].mean()
    ) if len(settled_df) else 0.0

    win_rate = (
        float(
            (graded['Status'] == 'WON').mean()
        ) * 100.0
        if not graded.empty
        else 0.0
    )

    a1, a2, a3, a4 = st.columns(4)
    a1.metric(
        'Average Wager',
        _money(avg_wager),
    )
    a2.metric(
        'Average Return',
        _money(avg_return),
    )
    a3.metric(
        'Average P/L / Bet',
        _money(avg_pnl),
    )
    a4.metric(
        'Win Rate',
        f'{win_rate:.1f}%',
    )

    analytics = settled_df.copy()

    analytics['Odds Range'] = analytics['Odds'].apply(
        _odds_range
    )

    analytics['Parlay Size'] = analytics['Leg Count'].apply(
        _leg_count_bucket
    )

    c1, c2 = st.columns(2)

    with c1:
        st.markdown('**By Odds Range**')

        odds_summary = _settled_performance_summary(
            analytics,
            'Odds Range',
        )

        odds_order = {
            '≤ -200': 0,
            '-199 to -101': 1,
            '-100 to +99': 2,
            '+100 to +299': 3,
            '+300 to +999': 4,
            '+1000+': 5,
            'Unknown': 6,
        }

        if not odds_summary.empty:
            odds_summary['_sort_order'] = (
                odds_summary['Odds Range']
                .map(odds_order)
                .fillna(999)
            )

            odds_summary = (
                odds_summary
                .sort_values(
                    '_sort_order',
                    ascending=True,
                )
                .drop(
                    columns=['_sort_order']
                )
            )

        _render_settled_analytics_table(
            odds_summary,
            'Odds Range',
        )

    with c2:
        st.markdown('**By Leg Count**')
        _render_settled_analytics_table(
            _settled_performance_summary(
                analytics,
                'Parlay Size',
            ),
            'Parlay Size',
        )

    dated = analytics.dropna(
        subset=['Placed']
    ).copy()

    if dated.empty:
        st.caption(
            'No dated settled bets are available for weekly/monthly summaries.'
        )
        return

    local_dates = dated[
        'Placed'
    ].dt.tz_convert(None)

    dated['Week'] = (
        local_dates.dt.to_period('W-SUN')
        .apply(
            lambda period:
                f"{period.start_time:%b %d} – {period.end_time:%b %d, %Y}"
        )
    )

    dated['Month'] = local_dates.dt.strftime(
        '%b %Y'
    )

    t1, t2 = st.columns(2)

    with t1:
        st.markdown('**By Week**')
        weekly = _settled_performance_summary(
            dated,
            'Week',
        )
        _render_settled_analytics_table(
            weekly,
            'Week',
        )

    with t2:
        st.markdown('**By Month**')
        monthly = _settled_performance_summary(
            dated,
            'Month',
        )
        _render_settled_analytics_table(
            monthly,
            'Month',
        )



def _parlay_leg_progress(legs):
    """
    Compact child-leg progress for multi-leg bets.

    WON = correct
    LOST = incorrect
    PUSH/VOID/etc. = neutral
    LIVE = unsettled leg whose live_state is LIVE
    PENDING = all other unsettled legs
    """
    correct = 0
    lost = 0
    neutral = 0
    live = 0
    pending = 0

    neutral_statuses = {
        'PUSH',
        'VOID',
        'VOIDED',
        'CANCELLED',
        'CANCELED',
    }

    settled_leg_statuses = {
        'WON',
        'LOST',
        *neutral_statuses,
    }

    for leg in legs:
        status = str(
            leg.get('status') or 'PENDING'
        ).strip().upper()

        live_state = str(
            leg.get('live_state') or ''
        ).strip().upper()

        if status == 'WON':
            correct += 1
        elif status == 'LOST':
            lost += 1
        elif status in neutral_statuses:
            neutral += 1
        elif status not in settled_leg_statuses and live_state == 'LIVE':
            live += 1
        else:
            pending += 1

    total = len(legs)

    parts = [
        f"✓ {correct}/{total}",
    ]

    if lost:
        parts.append(
            f"✕ {lost}"
        )

    if live:
        parts.append(
            f"● {live} Live"
        )

    if pending:
        parts.append(
            f"○ {pending} Pending"
        )

    if neutral:
        parts.append(
            f"↔ {neutral} Push/Void"
        )

    return {
        'correct': correct,
        'lost': lost,
        'live': live,
        'pending': pending,
        'neutral': neutral,
        'total': total,
        'summary': '   '.join(parts),
        'unsettled': live + pending,
    }


def _dashboard_parlay_progress_rows(all_bets):
    rows = []

    for bet in all_bets:
        try:
            bet_id = int(
                bet.get('id')
            )
        except (TypeError, ValueError):
            continue

        legs = list_legs(
            bet_id
        )

        if len(legs) <= 1:
            continue

        progress = _parlay_leg_progress(
            legs
        )

        parent_status = str(
            bet.get('status') or 'PENDING'
        ).strip().upper()

        # Dashboard is for parlays that are still relevant right now.
        # A LOST parent stays visible while child legs are still tracking,
        # matching update-live-bets v16.4 behavior.
        if (
            not _is_active_status(parent_status)
            and not (
                parent_status == 'LOST'
                and progress['unsettled'] > 0
            )
        ):
            continue

        rows.append({
            'Bet': _bet_description(
                bet,
                legs,
            ),
            'Sportsbook': bet.get('sportsbook') or '',
            'Leg Progress': progress['summary'],
            'Correct': progress['correct'],
            'Lost': progress['lost'],
            'Live': progress['live'],
            'Pending': progress['pending'],
            'Push/Void': progress['neutral'],
            'Status': parent_status,
            'To Pay': _safe_float(
                bet.get('to_pay')
            ),
            'Bet ID': bet_id,
        })

    return pd.DataFrame(
        rows
    )


def _render_dashboard_parlay_progress(all_bets):
    parlay_df = _dashboard_parlay_progress_rows(
        all_bets
    )

    st.markdown('#### Parlay Progress')

    if parlay_df.empty:
        st.caption(
            'No active multi-leg bets to track.'
        )
        return

    st.caption(
        '✓ = settled winner • ✕ = settled loser • '
        '● = currently live • ○ = not started/pending. '
        'A lost parlay remains here while unfinished legs continue tracking.'
    )

    st.dataframe(
        parlay_df,
        use_container_width=True,
        hide_index=True,
        column_order=[
            'Bet',
            'Leg Progress',
            'Status',
            'To Pay',
            'Sportsbook',
            'Bet ID',
        ],
        column_config={
            'Bet': st.column_config.TextColumn(
                'Parlay',
            ),
            'Leg Progress': st.column_config.TextColumn(
                'Legs',
                help=(
                    'WON legs count as correct. '
                    'Push/Void legs are neutral.'
                ),
            ),
            'Status': st.column_config.TextColumn(
                'Bet Status',
            ),
            'To Pay': st.column_config.NumberColumn(
                'To Pay',
                format='$%.2f',
            ),
            'Sportsbook': st.column_config.TextColumn(
                'Book',
            ),
            'Bet ID': st.column_config.NumberColumn(
                'ID',
                format='%d',
            ),
        },
    )


def _render_dashboard(all_bets):
    bet_df = _dashboard_bet_rows(all_bets)

    if bet_df.empty:
        st.info('No bets have been imported yet.')
        return

    settled_df = bet_df[bet_df['Is Settled']]
    active_df = bet_df[bet_df['Is Active']]

    total_wagered = float(bet_df['Wagered'].sum())
    total_returned = float(settled_df['Returned'].sum())
    settled_wagered = float(settled_df['Wagered'].sum())
    net_pnl = float(settled_df['P/L'].sum())
    roi = (
        (net_pnl / settled_wagered) * 100.0
        if settled_wagered
        else 0.0
    )
    open_exposure = float(active_df['Wagered'].sum())
    active_potential = float(active_df['Potential Return'].sum())

    wins = int((settled_df['Status'] == 'WON').sum())
    losses = int((settled_df['Status'] == 'LOST').sum())
    pushes = int(
        settled_df['Status'].isin(
            ['PUSH', 'VOID', 'VOIDED', 'CANCELLED', 'CANCELED']
        ).sum()
    )

    st.subheader('Performance Overview')

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric('Total Wagered', _money(total_wagered))
    c2.metric('Total Returned', _money(total_returned))
    c3.metric('Net P/L', _money(net_pnl))
    c4.metric('ROI', f'{roi:.1f}%')
    c5.metric('Open Exposure', _money(open_exposure))
    c6.metric('Active Bets', len(active_df))

    st.caption(
        f"Settled record: {wins}-{losses}"
        + (f"-{pushes} push/void" if pushes else "")
        + f" • Active potential return: {_money(active_potential)}"
    )

    exposure = _dashboard_leg_exposure(all_bets)

    st.markdown('#### Active Exposure')
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric('At Risk', _money(open_exposure))
    e2.metric('Potential Return', _money(active_potential))
    e3.metric('Active Legs', exposure['active_leg_count'])
    e4.metric(
        'Won / Lost Legs',
        f"{exposure['winning_legs']} / {exposure['losing_legs']}",
    )
    e5.metric('Pending Legs', exposure['pending_legs'])

    st.markdown('#### Performance Breakdowns')
    b1, b2, b3 = st.columns(3)

    with b1:
        st.markdown('**By Sportsbook**')
        _render_summary_dataframe(
            _summary_breakdown(bet_df, 'Sportsbook'),
            'Sportsbook',
        )

    with b2:
        st.markdown('**By Bet Type**')
        _render_summary_dataframe(
            _summary_breakdown(bet_df, 'Bet Type'),
            'Bet Type',
        )

    with b3:
        st.markdown('**By Sport**')
        _render_summary_dataframe(
            _summary_breakdown(bet_df, 'Sport'),
            'Sport',
        )

    _render_historical_analytics(
        settled_df
    )

    st.markdown('#### Highlights')

    settled_with_pnl = settled_df.copy()
    h1, h2, h3 = st.columns(3)

    with h1:
        if settled_with_pnl.empty:
            st.metric('Biggest Win', '—')
        else:
            win_rows = settled_with_pnl[settled_with_pnl['P/L'] > 0]
            if win_rows.empty:
                st.metric('Biggest Win', '—')
            else:
                row = win_rows.loc[win_rows['P/L'].idxmax()]
                st.metric(
                    'Biggest Win',
                    _money(row['P/L']),
                    help=f"Bet {int(row['Bet ID'])} • {row['Sportsbook']}",
                )

    with h2:
        if settled_with_pnl.empty:
            st.metric('Biggest Loss', '—')
        else:
            loss_rows = settled_with_pnl[settled_with_pnl['P/L'] < 0]
            if loss_rows.empty:
                st.metric('Biggest Loss', '—')
            else:
                row = loss_rows.loc[loss_rows['P/L'].idxmin()]
                st.metric(
                    'Biggest Loss',
                    _money(row['P/L']),
                    help=f"Bet {int(row['Bet ID'])} • {row['Sportsbook']}",
                )

    with h3:
        if active_df.empty:
            st.metric('Largest Open Return', '—')
        else:
            row = active_df.loc[active_df['Potential Return'].idxmax()]
            st.metric(
                'Largest Open Return',
                _money(row['Potential Return']),
                help=f"Bet {int(row['Bet ID'])} • {row['Sportsbook']}",
            )

    rr = _round_robin_dashboard_summary(all_bets)

    if rr['bets']:
        st.markdown('#### Round Robin Summary')
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric('RR Bets', rr['bets'])
        r2.metric('RR Wagered', _money(rr['wagered']))
        r3.metric('RR Returned', _money(rr['returned']))
        r4.metric('RR P/L', _money(rr['pnl']))
        r5.metric(
            'Combos W/L/P',
            f"{rr['won']}/{rr['lost']}/{rr['pending']}",
        )



def _render_exposure_tab(all_bets):
    st.subheader('Active Exposure')
    st.caption(
        'Game-day player and team exposure across active bets. '
        'Season Futures are excluded. Each parent bet counts only once '
        'per player/team, even if that name appears multiple times in the bet.'
    )

    exposure = _dashboard_leg_exposure(
        all_bets
    )

    players = exposure['players'].copy()
    teams = exposure['teams'].copy()

    m1, m2, m3 = st.columns(3)
    m1.metric(
        'Active Game Bets',
        exposure['active_bet_count'],
    )
    m2.metric(
        'Unique Players',
        0 if players.empty else len(players),
    )
    m3.metric(
        'Unique Teams',
        0 if teams.empty else len(teams),
    )

    search = st.text_input(
        'Search exposure',
        placeholder='Player or team...',
        key='exposure_search',
    ).strip().casefold()

    if search:
        if not players.empty:
            players = players[
                players['Player']
                .astype(str)
                .str.casefold()
                .str.contains(
                    search,
                    regex=False,
                    na=False,
                )
            ]

        if not teams.empty:
            teams = teams[
                teams['Team']
                .astype(str)
                .str.casefold()
                .str.contains(
                    search,
                    regex=False,
                    na=False,
                )
            ]

    pcol, tcol = st.columns(2)

    with pcol:
        st.markdown('#### Player Exposure')

        if players.empty:
            st.info('No active player exposure.')
        else:
            st.dataframe(
                players,
                use_container_width=True,
                hide_index=True,
                column_config={
                    'Bets': st.column_config.NumberColumn(
                        'Active Bets',
                        format='%d',
                    ),
                    'Wager Exposure': st.column_config.NumberColumn(
                        'Wager Exposure',
                        format='$%.2f',
                    ),
                    'Potential Return': st.column_config.NumberColumn(
                        'Potential Return',
                        format='$%.2f',
                    ),
                    'Concentration': st.column_config.TextColumn(
                        'Exposure Flag',
                    ),
                },
            )

    with tcol:
        st.markdown('#### Team Exposure')

        if teams.empty:
            st.info('No active team exposure.')
        else:
            st.dataframe(
                teams,
                use_container_width=True,
                hide_index=True,
                column_config={
                    'Bets': st.column_config.NumberColumn(
                        'Active Bets',
                        format='%d',
                    ),
                    'Wager Exposure': st.column_config.NumberColumn(
                        'Wager Exposure',
                        format='$%.2f',
                    ),
                    'Potential Return': st.column_config.NumberColumn(
                        'Potential Return',
                        format='$%.2f',
                    ),
                    'Concentration': st.column_config.TextColumn(
                        'Exposure Flag',
                    ),
                },
            )

    concentrated_players = (
        players[
            players['Bets'] >= 2
        ]
        if not players.empty
        else pd.DataFrame()
    )

    concentrated_teams = (
        teams[
            teams['Bets'] >= 2
        ]
        if not teams.empty
        else pd.DataFrame()
    )

    if (
        not concentrated_players.empty
        or not concentrated_teams.empty
    ):
        st.markdown('#### Concentrated Exposure')

        messages = []

        if not concentrated_players.empty:
            for _, row in concentrated_players.head(10).iterrows():
                messages.append(
                    f"Player: {row['Player']} is in "
                    f"{int(row['Bets'])} active bets "
                    f"({_money(row['Wager Exposure'])} wager exposure)."
                )

        if not concentrated_teams.empty:
            for _, row in concentrated_teams.head(10).iterrows():
                messages.append(
                    f"Team: {row['Team']} is in "
                    f"{int(row['Bets'])} active bets "
                    f"({_money(row['Wager Exposure'])} wager exposure)."
                )

        for message in messages:
            st.warning(
                message
            )


def _active_leg_rows_excluding_futures():
    """
    Return active game-bet legs only.

    Excludes any leg explicitly tracked as a season future and also
    excludes every leg belonging to a bet that has at least one
    tracking_scope=SEASON leg, so bets shown on the Season Futures tab
    never appear on Active Legs.
    """
    all_bets = list_bets()

    future_bet_ids = set()

    try:
        for future_leg in list_future_legs():
            if future_leg.get('bet_row_id') is not None:
                future_bet_ids.add(
                    int(future_leg['bet_row_id'])
                )
    except Exception:
        future_bet_ids = set()

    rows = []

    for bet in all_bets:
        if not _is_active_status(
            bet.get('status')
        ):
            continue

        bet_id = int(
            bet['id']
        )

        if bet_id in future_bet_ids:
            continue

        legs = list_legs(
            bet_id
        )

        for leg in legs:
            if (
                str(
                    leg.get('tracking_scope')
                    or ''
                )
                .strip()
                .upper()
                == 'SEASON'
            ):
                continue

            leg_status = str(
                leg.get('status')
                or 'PENDING'
            ).strip().upper()

            if leg_status in SETTLED_STATUSES:
                continue

            game = _leg_game_detail(
                leg
            )

            rows.append({
                'Bet ID': bet_id,
                'Sportsbook': bet.get('sportsbook') or '',
                'Bet': _bet_description(bet, legs),
                'Sport': _display_sport(bet, legs),
                'Leg #': leg.get('leg_index'),
                'Selection': leg.get('selection') or '',
                'Market': leg.get('market') or '',
                'Line': leg.get('line_value'),
                'Direction': leg.get('direction') or '',
                'Game': game or '',
                'Status': leg_status,
                'Live': leg.get('live_value') or '',
                'Event ID': leg.get('espn_event_id') or '',
            })

    return rows


def _render_active_legs_tab():
    st.subheader('Active Legs')
    st.caption(
        'Individual active game-bet legs. Bets tracked on the '
        'Season Futures tab are excluded completely.'
    )

    rows = _active_leg_rows_excluding_futures()

    if not rows:
        st.info('No active non-futures legs.')
        return

    rows = _filter_active_legs_rows_ui(
        rows,
        'active_legs',
    )

    if not rows:
        st.info('No active legs match the selected filters.')
        return

    df = pd.DataFrame(rows)

    # Combine duplicate legs that represent the same wager.
    # We deliberately keep different lines/directions/games separate.
    group_cols = [
        'Sport',
        'Selection',
        'Market',
        'Line',
        'Direction',
        'Game',
        'Status',
        'Live',
    ]

    combined_rows = []

    for _, group in df.groupby(
        group_cols,
        dropna=False,
        sort=False,
    ):
        bet_ids = sorted(
            {
                int(x)
                for x in group['Bet ID'].tolist()
            }
        )

        sportsbooks = sorted(
            {
                str(x)
                for x in group['Sportsbook'].tolist()
                if str(x).strip()
            }
        )

        descriptions = []
        for value in group['Bet'].tolist():
            value = str(value or '').strip()
            if value and value not in descriptions:
                descriptions.append(value)

        first = group.iloc[0].to_dict()

        first['Count'] = len(group)
        first['Bet IDs'] = ', '.join(
            str(x)
            for x in bet_ids
        )
        first['Sportsbook'] = ', '.join(
            sportsbooks
        )
        first['Bet'] = ' | '.join(
            descriptions
        )

        combined_rows.append(
            first
        )

    combined_df = pd.DataFrame(
        combined_rows
    )

    # Keep the most useful columns visible first.
    visible = [
        'Count',
        'Sportsbook',
        'Sport',
        'Selection',
        'Market',
        'Line',
        'Direction',
        'Game',
        'Status',
        'Live',
        'Bet IDs',
        'Bet',
    ]

    st.dataframe(
        combined_df[visible],
        use_container_width=True,
        hide_index=True,
        column_config={
            'Count': st.column_config.NumberColumn(
                'Count',
                format='%d',
                width='small',
                help='Number of active bets containing this same leg.',
            ),
            'Sportsbook': st.column_config.TextColumn(
                'Sportsbook',
                width='small',
            ),
            'Sport': st.column_config.TextColumn(
                'Sport',
                width='small',
            ),
            'Selection': st.column_config.TextColumn(
                'Selection',
                width='medium',
            ),
            'Market': st.column_config.TextColumn(
                'Market',
                width='medium',
            ),
            'Line': st.column_config.NumberColumn(
                'Line',
                format='%.1f',
                width='small',
            ),
            'Direction': st.column_config.TextColumn(
                'Dir',
                width='small',
            ),
            'Game': st.column_config.TextColumn(
                'Game',
                width='large',
            ),
            'Status': st.column_config.TextColumn(
                'Status',
                width='small',
            ),
            'Live': st.column_config.TextColumn(
                'Live',
                width='medium',
            ),
            'Bet IDs': st.column_config.TextColumn(
                'Bets',
                width='small',
            ),
            'Bet': st.column_config.TextColumn(
                'Bet Description',
                width='large',
            ),
        },
    )

    st.caption(
        f"{len(rows)} active leg occurrence(s) combined into "
        f"{len(combined_df)} unique leg(s) across "
        f"{df['Bet ID'].nunique()} bet(s)."
    )



def _is_round_robin_bet(bet):
    bet_type = str(bet.get('bet_type') or '').upper()
    return (
        'ROUND ROBIN' in bet_type
        or bet.get('round_robin_size') is not None
        or bet.get('round_robin_combinations') is not None
    )


def _looks_like_player_market(market):
    value = str(market or '').upper()

    player_terms = (
        'ANYTIME TD',
        'FIRST TD',
        'LAST TD',
        'TO SCORE',
        'PASSING YARD',
        'PASSING TD',
        'PASSING TOUCHDOWN',
        'INTERCEPTION',
        'RUSHING YARD',
        'RUSHING TD',
        'RUSHING TOUCHDOWN',
        'RECEIVING YARD',
        'RECEPTION',
        'RECEIVING TD',
        'RECEIVING TOUCHDOWN',
    )

    return any(term in value for term in player_terms)


def _quality_issue(level, message):
    return {
        'level': level,
        'message': message,
    }


def _bet_quality_issues(
    bet,
    legs,
    sportsbook_id_counts=None,
    screenshot_hash_counts=None,
):
    """
    Conservative import validation.

    These checks do not modify anything. They only flag records that are
    worth reviewing. Matching-related warnings are primarily applied to
    active bets so older settled bets are not unnecessarily flagged.
    """
    issues = []

    sportsbook_id_counts = sportsbook_id_counts or {}
    screenshot_hash_counts = screenshot_hash_counts or {}

    bet_id = bet.get('id')
    status = str(bet.get('status') or 'PENDING').strip().upper()
    is_active = status in ACTIVE_STATUSES
    is_rr = _is_round_robin_bet(bet)

    # ----------------------------------------------------------
    # Parent bet checks
    # ----------------------------------------------------------
    if not str(bet.get('sportsbook') or '').strip():
        issues.append(
            _quality_issue(
                'ERROR',
                'Sportsbook is missing.',
            )
        )

    displayed_sport = _display_sport(bet, legs)
    if displayed_sport == 'Football':
        issues.append(
            _quality_issue(
                'WARNING',
                'Sport is missing and could not be confidently inferred.',
            )
        )

    if bet.get('stake') is None:
        issues.append(
            _quality_issue(
                'ERROR',
                'Wager/stake is missing.',
            )
        )

    if (
        not is_rr
        and bet.get('current_odds') is None
        and bet.get('original_odds') is None
        and bet.get('boosted_odds') is None
    ):
        issues.append(
            _quality_issue(
                'WARNING',
                'Parent odds are missing.',
            )
        )

    if (
        bet.get('to_pay') is None
        and bet.get('cash_out') is None
    ):
        issues.append(
            _quality_issue(
                'WARNING',
                'Potential/total payout is missing.',
            )
        )

    declared_leg_count = bet.get('leg_count')
    if declared_leg_count is not None:
        try:
            declared_leg_count = int(declared_leg_count)
        except (TypeError, ValueError):
            declared_leg_count = None

    if (
        declared_leg_count is not None
        and declared_leg_count != len(legs)
    ):
        issues.append(
            _quality_issue(
                'ERROR',
                f"Leg-count mismatch: bet says {declared_leg_count}, "
                f"but {len(legs)} leg(s) are stored.",
            )
        )

    if not legs:
        issues.append(
            _quality_issue(
                'ERROR',
                'No bet legs are stored.',
            )
        )

    sportsbook_bet_id = str(
        bet.get('sportsbook_bet_id') or ''
    ).strip()

    if (
        sportsbook_bet_id
        and sportsbook_id_counts.get(sportsbook_bet_id, 0) > 1
    ):
        issues.append(
            _quality_issue(
                'ERROR',
                'Sportsbook bet ID appears more than once.',
            )
        )

    screenshot_hash = str(
        bet.get('screenshot_hash') or ''
    ).strip()

    if (
        screenshot_hash
        and screenshot_hash_counts.get(screenshot_hash, 0) > 1
    ):
        issues.append(
            _quality_issue(
                'ERROR',
                'Screenshot hash appears more than once.',
            )
        )

    # ----------------------------------------------------------
    # Leg checks
    # ----------------------------------------------------------
    for leg in legs:
        leg_num = leg.get('leg_index')
        prefix = f"Leg {leg_num}: " if leg_num is not None else 'Leg: '

        selection = str(
            leg.get('selection') or ''
        ).strip()

        market = str(
            leg.get('market') or ''
        ).strip()

        if not selection:
            issues.append(
                _quality_issue(
                    'ERROR',
                    prefix + 'selection is missing.',
                )
            )

        if not market:
            issues.append(
                _quality_issue(
                    'ERROR',
                    prefix + 'market is missing.',
                )
            )

        team_a = str(
            leg.get('event_team_a') or ''
        ).strip()

        team_b = str(
            leg.get('event_team_b') or ''
        ).strip()

        if (
            team_a
            and team_b
            and team_a.casefold() == team_b.casefold()
        ):
            issues.append(
                _quality_issue(
                    'ERROR',
                    prefix + f"suspicious self-matchup ({team_a} @ {team_b}).",
                )
            )

        scope = str(
            leg.get('tracking_scope') or ''
        ).strip().upper()

        player_market = _looks_like_player_market(
            market
        )

        # Season futures require a player identity and season scope.
        if scope == 'SEASON':
            if not leg.get('espn_athlete_id'):
                issues.append(
                    _quality_issue(
                        'WARNING',
                        prefix + 'season future has no ESPN athlete match.',
                    )
                )

            if not (
                leg.get('future_season_year')
                or leg.get('espn_season_year')
            ):
                issues.append(
                    _quality_issue(
                        'WARNING',
                        prefix + 'season future has no season year.',
                    )
                )

            continue

        # Matching warnings are meaningful primarily while a bet is open.
        if is_active:
            if player_market:
                # NFL player markets should be athlete-matched. CFB player
                # props are intentionally not part of live tracking.
                if (
                    displayed_sport == 'NFL'
                    and not leg.get('espn_athlete_id')
                ):
                    issues.append(
                        _quality_issue(
                            'WARNING',
                            prefix + 'NFL player prop has no ESPN athlete match.',
                        )
                    )
            elif not leg.get('espn_event_id'):
                issues.append(
                    _quality_issue(
                        'WARNING',
                        prefix + 'game/event has not been matched to ESPN.',
                    )
                )

    # ----------------------------------------------------------
    # Round Robin checks
    # ----------------------------------------------------------
    if is_rr:
        rr_size = bet.get('round_robin_size')
        rr_declared = bet.get('round_robin_combinations')

        try:
            rr_size_int = int(rr_size) if rr_size is not None else None
        except (TypeError, ValueError):
            rr_size_int = None

        try:
            rr_declared_int = (
                int(rr_declared)
                if rr_declared is not None
                else None
            )
        except (TypeError, ValueError):
            rr_declared_int = None

        if rr_size_int is None:
            issues.append(
                _quality_issue(
                    'WARNING',
                    'Round Robin size is missing.',
                )
            )
        elif rr_size_int <= 0 or rr_size_int > len(legs):
            issues.append(
                _quality_issue(
                    'ERROR',
                    f"Round Robin size {rr_size_int} is invalid for "
                    f"{len(legs)} leg(s).",
                )
            )
        else:
            expected = math.comb(
                len(legs),
                rr_size_int,
            )

            if (
                rr_declared_int is not None
                and rr_declared_int != expected
            ):
                issues.append(
                    _quality_issue(
                        'ERROR',
                        f"Round Robin combination mismatch: expected "
                        f"{expected}, stored metadata says {rr_declared_int}.",
                    )
                )

            try:
                stored_combos = list_round_robin_combinations(
                    bet_id
                )
            except Exception:
                stored_combos = []

            if stored_combos and len(stored_combos) != expected:
                issues.append(
                    _quality_issue(
                        'ERROR',
                        f"Round Robin has {len(stored_combos)} stored "
                        f"combination(s); expected {expected}.",
                    )
                )

            if not stored_combos:
                issues.append(
                    _quality_issue(
                        'WARNING',
                        'Round Robin has no stored combination rows.',
                    )
                )

    return issues


def _quality_level(issues):
    if any(x['level'] == 'ERROR' for x in issues):
        return 'ERROR'
    if issues:
        return 'WARNING'
    return 'OK'


def _quality_icon(level):
    return {
        'ERROR': '❌',
        'WARNING': '⚠️',
        'OK': '✅',
    }.get(level, '⚠️')


def _render_import_review_tab():
    st.subheader('Import Review')
    st.caption(
        'Automatically checks imported bets for missing data, parsing '
        'inconsistencies, matching problems, duplicates, and Round Robin '
        'issues. This screen is read-only; it does not change bet data.'
    )

    all_bets = list_bets()

    if not all_bets:
        st.info('No imported bets to review.')
        return

    # Duplicate checks need context from the full data set.
    sportsbook_id_counts = {}
    screenshot_hash_counts = {}

    for bet in all_bets:
        sportsbook_id = str(
            bet.get('sportsbook_bet_id') or ''
        ).strip()

        if sportsbook_id:
            sportsbook_id_counts[sportsbook_id] = (
                sportsbook_id_counts.get(sportsbook_id, 0) + 1
            )

        screenshot_hash = str(
            bet.get('screenshot_hash') or ''
        ).strip()

        if screenshot_hash:
            screenshot_hash_counts[screenshot_hash] = (
                screenshot_hash_counts.get(screenshot_hash, 0) + 1
            )

    # list_bets() is newest-first. Reviewing the latest 50 keeps this
    # screen fast even after the tracker contains hundreds of bets.
    recent_bets = all_bets[:50]

    review_rows = []
    details = {}

    for bet in recent_bets:
        legs = list_legs(
            bet['id']
        )

        issues = _bet_quality_issues(
            bet,
            legs,
            sportsbook_id_counts=sportsbook_id_counts,
            screenshot_hash_counts=screenshot_hash_counts,
        )

        level = _quality_level(
            issues
        )

        details[bet['id']] = {
            'bet': bet,
            'legs': legs,
            'issues': issues,
            'level': level,
        }

        review_rows.append({
            'Result': f"{_quality_icon(level)} {level.title()}",
            'Bet ID': bet.get('id'),
            'Sportsbook': bet.get('sportsbook') or '',
            'Sport': _display_sport(bet, legs),
            'Bet': _bet_description(bet, legs),
            'Status': str(
                bet.get('status') or 'PENDING'
            ).upper(),
            'Wager': _safe_float(
                bet.get('stake')
            ),
            'Issues': len(issues),
            'Issue Summary': ' • '.join(
                x['message']
                for x in issues
            ),
        })

    ok_count = sum(
        1
        for row in review_rows
        if row['Result'].startswith('✅')
    )
    warning_count = sum(
        1
        for row in review_rows
        if row['Result'].startswith('⚠️')
    )
    error_count = sum(
        1
        for row in review_rows
        if row['Result'].startswith('❌')
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric('Reviewed', len(review_rows))
    m2.metric('Looks Good', ok_count)
    m3.metric('Needs Review', warning_count)
    m4.metric('Import Problems', error_count)

    show_all = st.checkbox(
        'Show bets that passed all checks',
        value=False,
        key='import_review_show_all',
    )

    table_rows = (
        review_rows
        if show_all
        else [
            row
            for row in review_rows
            if not row['Result'].startswith('✅')
        ]
    )

    if not table_rows:
        st.success(
            'The 50 most recent imports passed all current quality checks.'
        )
        return

    review_df = pd.DataFrame(
        table_rows
    )

    st.dataframe(
        review_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            'Result': st.column_config.TextColumn(
                'Check',
                width='small',
            ),
            'Bet ID': st.column_config.NumberColumn(
                'Bet',
                format='%d',
                width='small',
            ),
            'Sportsbook': st.column_config.TextColumn(
                'Sportsbook',
                width='small',
            ),
            'Sport': st.column_config.TextColumn(
                'Sport',
                width='small',
            ),
            'Bet': st.column_config.TextColumn(
                'Bet Description',
                width='large',
            ),
            'Status': st.column_config.TextColumn(
                'Status',
                width='small',
            ),
            'Wager': st.column_config.NumberColumn(
                'Wager',
                format='$%.2f',
                width='small',
            ),
            'Issues': st.column_config.NumberColumn(
                'Issues',
                format='%d',
                width='small',
            ),
            'Issue Summary': st.column_config.TextColumn(
                'Why',
                width='large',
            ),
        },
    )

    flagged_ids = [
        int(row['Bet ID'])
        for row in table_rows
        if int(row['Issues']) > 0
    ]

    if flagged_ids:
        st.markdown('#### Review details')

    for bet_id in flagged_ids:
        item = details[bet_id]
        bet = item['bet']
        legs = item['legs']
        issues = item['issues']
        level = item['level']

        label = (
            f"{_quality_icon(level)} Bet {bet_id} • "
            f"{_bet_description(bet, legs)}"
        )

        with st.expander(
            label,
            expanded=False,
        ):
            for issue in issues:
                if issue['level'] == 'ERROR':
                    st.error(
                        issue['message']
                    )
                else:
                    st.warning(
                        issue['message']
                    )

            st.caption(
                'These are review flags only. A warning does not '
                'automatically mean the imported bet is wrong.'
            )



def _normalized_text(value):
    return str(value or '').strip().casefold()


def _bet_search_blob(bet, legs):
    parts = [
        bet.get('sportsbook'),
        bet.get('sportsbook_bet_id'),
        bet.get('bet_type'),
        bet.get('sport'),
        bet.get('headline'),
        bet.get('subtitle'),
        bet.get('event_name'),
        bet.get('status'),
    ]

    for leg in legs:
        parts.extend([
            leg.get('selection'),
            leg.get('market'),
            leg.get('event_team_a'),
            leg.get('event_team_b'),
            leg.get('raw_leg_text'),
        ])

    return ' '.join(
        str(x)
        for x in parts
        if x is not None
    ).casefold()



def _filter_parse_datetime(value):
    if value in (None, ''):
        return None

    if isinstance(value, datetime):
        return value

    raw = str(value).strip()

    if not raw:
        return None

    # ISO 8601 first, including trailing Z.
    try:
        return datetime.fromisoformat(
            raw.replace('Z', '+00:00')
        )
    except Exception:
        pass

    # Common sportsbook / legacy formats.
    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%m/%d/%Y %I:%M %p',
        '%m/%d/%Y %H:%M',
        '%Y-%m-%d',
    ):
        try:
            return datetime.strptime(
                raw,
                fmt,
            )
        except Exception:
            continue

    return None


def _filter_bets_ui(
    bets,
    key_prefix,
    include_status=True,
    include_date=True,
):
    if not bets:
        return bets

    bet_leg_map = {
        int(bet['id']): list_legs(bet['id'])
        for bet in bets
    }

    sportsbooks = sorted({
        str(bet.get('sportsbook') or '').strip()
        for bet in bets
        if str(bet.get('sportsbook') or '').strip()
    })

    sports = sorted({
        _display_sport(
            bet,
            bet_leg_map[int(bet['id'])],
        )
        for bet in bets
    })

    bet_types = sorted({
        str(bet.get('bet_type') or '').strip()
        for bet in bets
        if str(bet.get('bet_type') or '').strip()
    })

    statuses = sorted({
        str(bet.get('status') or 'PENDING').strip().upper()
        for bet in bets
    })

    st.markdown('#### Filters')

    row1 = st.columns([2.2, 1.3, 1.1, 1.5])

    search_text = row1[0].text_input(
        'Search',
        placeholder='Player, team, market, bet ID...',
        key=f'{key_prefix}_search',
    )

    sportsbook_filter = row1[1].multiselect(
        'Sportsbook',
        sportsbooks,
        key=f'{key_prefix}_sportsbook',
    )

    sport_filter = row1[2].multiselect(
        'Sport',
        sports,
        key=f'{key_prefix}_sport',
    )

    bet_type_filter = row1[3].multiselect(
        'Bet Type',
        bet_types,
        key=f'{key_prefix}_bet_type',
    )

    status_filter = []
    date_range = None

    if include_status or include_date:
        row2 = st.columns([1.3, 1.7, 3])

        if include_status:
            status_filter = row2[0].multiselect(
                'Status',
                statuses,
                key=f'{key_prefix}_status',
            )

        if include_date:
            dated_values = []

            for bet in bets:
                raw = (
                    bet.get('placed_at')
                    or bet.get('source_captured_at')
                    or bet.get('created_at')
                )

                dt = _filter_parse_datetime(
                    raw
                )

                if dt:
                    dated_values.append(
                        dt.date()
                    )

            if dated_values:
                min_date = min(dated_values)
                max_date = max(dated_values)

                date_range = row2[1].date_input(
                    'Date Range',
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date,
                    key=f'{key_prefix}_date',
                )

    filtered = []

    search_value = _normalized_text(
        search_text
    )

    for bet in bets:
        legs = bet_leg_map[
            int(bet['id'])
        ]

        if sportsbook_filter:
            if str(
                bet.get('sportsbook') or ''
            ).strip() not in sportsbook_filter:
                continue

        displayed_sport = _display_sport(
            bet,
            legs,
        )

        if (
            sport_filter
            and displayed_sport not in sport_filter
        ):
            continue

        if bet_type_filter:
            if str(
                bet.get('bet_type') or ''
            ).strip() not in bet_type_filter:
                continue

        if status_filter:
            status = str(
                bet.get('status') or 'PENDING'
            ).strip().upper()

            if status not in status_filter:
                continue

        if search_value:
            if search_value not in _bet_search_blob(
                bet,
                legs,
            ):
                continue

        if include_date and date_range:
            raw = (
                bet.get('placed_at')
                or bet.get('source_captured_at')
                or bet.get('created_at')
            )

            dt = _filter_parse_datetime(
                raw
            )

            if dt:
                if isinstance(
                    date_range,
                    (tuple, list),
                ):
                    if len(date_range) == 2:
                        start_date, end_date = date_range
                    else:
                        start_date = end_date = date_range[0]
                else:
                    start_date = end_date = date_range

                if (
                    dt.date() < start_date
                    or dt.date() > end_date
                ):
                    continue

        filtered.append(
            bet
        )

    st.caption(
        f"Showing {len(filtered)} of {len(bets)} bet(s)."
    )

    return filtered


def _filter_active_legs_rows_ui(
    rows,
    key_prefix='active_legs',
):
    if not rows:
        return rows

    df = pd.DataFrame(rows)

    st.markdown('#### Filters')

    c1, c2, c3, c4 = st.columns(
        [2.3, 1.2, 1.0, 1.4]
    )

    search_text = c1.text_input(
        'Search',
        placeholder='Player, team, market, bet...',
        key=f'{key_prefix}_search',
    )

    sportsbooks = sorted(
        x
        for x in df['Sportsbook'].dropna().unique().tolist()
        if str(x).strip()
    )

    sports = sorted(
        x
        for x in df['Sport'].dropna().unique().tolist()
        if str(x).strip()
    )

    markets = sorted(
        x
        for x in df['Market'].dropna().unique().tolist()
        if str(x).strip()
    )

    sportsbook_filter = c2.multiselect(
        'Sportsbook',
        sportsbooks,
        key=f'{key_prefix}_sportsbook',
    )

    sport_filter = c3.multiselect(
        'Sport',
        sports,
        key=f'{key_prefix}_sport',
    )

    market_filter = c4.multiselect(
        'Market',
        markets,
        key=f'{key_prefix}_market',
    )

    filtered = df.copy()

    if sportsbook_filter:
        filtered = filtered[
            filtered['Sportsbook'].isin(
                sportsbook_filter
            )
        ]

    if sport_filter:
        filtered = filtered[
            filtered['Sport'].isin(
                sport_filter
            )
        ]

    if market_filter:
        filtered = filtered[
            filtered['Market'].isin(
                market_filter
            )
        ]

    search_value = _normalized_text(
        search_text
    )

    if search_value:
        mask = filtered.apply(
            lambda row: search_value in ' '.join(
                str(row.get(col) or '')
                for col in [
                    'Selection',
                    'Market',
                    'Game',
                    'Bet',
                    'Sportsbook',
                    'Sport',
                ]
            ).casefold(),
            axis=1,
        )

        filtered = filtered[
            mask
        ]

    st.caption(
        f"Showing {len(filtered)} of {len(df)} active leg occurrence(s)."
    )

    return filtered.to_dict(
        orient='records'
    )



HISTORY_DEFAULT_DAYS = 14
BIG_WIN_DEFAULT_PROFIT = 100.0


def _bet_reference_datetime(bet):
    for value in (
        bet.get('placed_at'),
        bet.get('source_captured_at'),
        bet.get('created_at'),
    ):
        dt = _filter_parse_datetime(
            value
        )
        if dt:
            return dt
    return None


def _history_recent_bets(
    bets,
    days=HISTORY_DEFAULT_DAYS,
):
    cutoff = (
        datetime.now()
        - timedelta(
            days=int(days)
        )
    ).date()

    recent = []

    for bet in bets:
        dt = _bet_reference_datetime(
            bet
        )

        # Keep undated bets visible rather than silently archiving them.
        if dt is None or dt.date() >= cutoff:
            recent.append(
                bet
            )

    return recent


def _render_big_wins_tab(all_bets):
    st.subheader('Big Wins')
    st.caption(
        'Settled wins remain here even after they age out of the normal '
        '14-day History view. Hiding a win only removes it from this tab; '
        'it remains in Supabase and continues to count in all statistics.'
    )

    threshold = st.number_input(
        'Minimum profit',
        min_value=0.0,
        value=float(BIG_WIN_DEFAULT_PROFIT),
        step=25.0,
        format='%.2f',
        key='big_win_threshold',
    )

    show_hidden = st.checkbox(
        'Show hidden big wins',
        value=False,
        key='show_hidden_big_wins',
    )

    qualifying = []

    for bet in all_bets:
        status = str(
            bet.get('status') or ''
        ).strip().upper()

        if status != 'WON':
            continue

        pnl = _profit_loss(
            bet
        )

        if pnl is None:
            continue

        pnl = float(
            pnl
        )

        if pnl < float(threshold):
            continue

        hidden = bool(
            bet.get('big_win_hidden')
        )

        if hidden and not show_hidden:
            continue

        legs = list_legs(
            bet['id']
        )

        qualifying.append(
            (
                pnl,
                bet,
                legs,
                hidden,
            )
        )

    qualifying.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    if not qualifying:
        st.info(
            f'No wins with at least {_money(threshold)} profit.'
        )
        return

    st.caption(
        f"{len(qualifying)} qualifying big win(s)."
    )

    for pnl, bet, legs, hidden in qualifying:
        label = (
            f"{'🙈 ' if hidden else ''}"
            f"{_bet_description(bet, legs)}"
            f" • PROFIT {_money(pnl)}"
            f" • WAGER {_money(bet.get('stake'))}"
        )

        with st.expander(
            label,
            expanded=False,
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(
                'Profit',
                _money(pnl),
            )
            c2.metric(
                'Wager',
                _money(bet.get('stake')),
            )
            c3.metric(
                'Paid',
                _money(bet.get('paid')),
            )
            c4.metric(
                'Odds',
                _odds(
                    bet.get('current_odds')
                    if bet.get('current_odds') is not None
                    else bet.get('original_odds')
                ),
            )

            _render_leg_table(
                legs,
                bet,
            )

            if hidden:
                if st.button(
                    'Restore to Big Wins',
                    key=f"restore_big_win_{bet['id']}",
                ):
                    try:
                        set_big_win_hidden(
                            bet['id'],
                            False,
                        )
                        st.success(
                            'Big win restored.'
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(
                            f'Could not restore big win: {exc}'
                        )
            else:
                if st.button(
                    'Hide from Big Wins',
                    key=f"hide_big_win_{bet['id']}",
                ):
                    try:
                        set_big_win_hidden(
                            bet['id'],
                            True,
                        )
                        st.success(
                            'Big win hidden. The bet was not deleted.'
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(
                            f'Could not hide big win: {exc}'
                        )


BACKUP_SCHEMA_VERSION = 1


def _export_json_value(value):
    if value is None:
        return ''

    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
        )

    return value


def _records_to_csv_bytes(records):
    output = io.StringIO(newline='')

    if not records:
        return b''

    fields = []
    seen = set()

    for record in records:
        for field in record.keys():
            if field not in seen:
                seen.add(field)
                fields.append(field)

    writer = csv.DictWriter(
        output,
        fieldnames=fields,
        extrasaction='ignore',
    )
    writer.writeheader()

    for record in records:
        writer.writerow({
            field: _export_json_value(record.get(field))
            for field in fields
        })

    return output.getvalue().encode('utf-8-sig')


def _xlsx_col_name(index):
    index = int(index)
    result = ''

    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result

    return result


def _xlsx_cell_xml(row_num, col_num, value, style_id=0):
    ref = f"{_xlsx_col_name(col_num)}{row_num}"
    style_attr = f' s="{style_id}"' if style_id else ''

    if value is None:
        return (
            f'<c r="{ref}"{style_attr} t="inlineStr">'
            f'<is><t></t></is></c>'
        )

    if isinstance(value, bool):
        return (
            f'<c r="{ref}"{style_attr} t="b">'
            f'<v>{1 if value else 0}</v></c>'
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (
            f'<c r="{ref}"{style_attr}>'
            f'<v>{value}</v></c>'
        )

    if isinstance(value, (dict, list)):
        value = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
        )

    value = str(value)

    if len(value) > 32767:
        value = value[:32740] + '… [truncated]'

    return (
        f'<c r="{ref}"{style_attr} t="inlineStr">'
        f'<is><t xml:space="preserve">'
        f'{xml_escape(value)}'
        f'</t></is></c>'
    )


def _xlsx_sheet_xml(records):
    fields = []
    seen = set()

    for record in records:
        for field in record.keys():
            if field not in seen:
                seen.add(field)
                fields.append(field)

    if not fields:
        fields = ['No Data']
        records = [{'No Data': 'No records'}]

    rows_xml = []

    header_cells = ''.join(
        _xlsx_cell_xml(
            1,
            index,
            field,
            style_id=1,
        )
        for index, field in enumerate(fields, start=1)
    )
    rows_xml.append(f'<row r="1">{header_cells}</row>')

    for row_num, record in enumerate(records, start=2):
        cells = ''.join(
            _xlsx_cell_xml(
                row_num,
                col_num,
                _export_json_value(record.get(field)),
            )
            for col_num, field in enumerate(fields, start=1)
        )
        rows_xml.append(f'<row r="{row_num}">{cells}</row>')

    last_col = _xlsx_col_name(len(fields))
    widths = []

    for col_num, field in enumerate(fields, start=1):
        sample = [
            str(_export_json_value(row.get(field)) or '')
            for row in records[:100]
        ]

        width = max(
            [len(str(field))]
            + [min(len(value), 40) for value in sample]
        )
        width = min(max(width + 2, 10), 42)

        widths.append(
            f'<col min="{col_num}" max="{col_num}" '
            f'width="{width}" customWidth="1"/>'
        )

    auto_filter = (
        f'<autoFilter ref="A1:{last_col}{len(records) + 1}"/>'
    )

    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
    </sheetView>
  </sheetViews>
  <cols>{''.join(widths)}</cols>
  <sheetData>{''.join(rows_xml)}</sheetData>
  {auto_filter}
</worksheet>'''


def _make_xlsx_bytes(sheet_records):
    output = io.BytesIO()
    sheet_names = list(sheet_records.keys())

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]

    for index in range(1, len(sheet_names) + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )

    content_types.append('</Types>')

    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="xl/workbook.xml"/>
</Relationships>'''

    workbook_sheets = []
    workbook_rels = []

    for index, name in enumerate(sheet_names, start=1):
        safe_name = str(name)[:31]

        workbook_sheets.append(
            f'<sheet name="{xml_escape(safe_name)}" '
            f'sheetId="{index}" r:id="rId{index}"/>'
        )

        workbook_rels.append(
            f'<Relationship Id="rId{index}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )

    styles_rid = len(sheet_names) + 1
    workbook_rels.append(
        f'<Relationship Id="rId{styles_rid}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        f'Target="styles.xml"/>'
    )

    workbook_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{''.join(workbook_sheets)}</sheets>
</workbook>'''

    workbook_rels_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {''.join(workbook_rels)}
</Relationships>'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="1">
    <border><left/><right/><top/><bottom/><diagonal/></border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>'''

    with zipfile.ZipFile(
        output,
        'w',
        compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        zf.writestr(
            '[Content_Types].xml',
            ''.join(content_types),
        )
        zf.writestr('_rels/.rels', root_rels)
        zf.writestr('xl/workbook.xml', workbook_xml)
        zf.writestr(
            'xl/_rels/workbook.xml.rels',
            workbook_rels_xml,
        )
        zf.writestr('xl/styles.xml', styles_xml)

        for index, name in enumerate(sheet_names, start=1):
            zf.writestr(
                f'xl/worksheets/sheet{index}.xml',
                _xlsx_sheet_xml(sheet_records[name]),
            )

    return output.getvalue()


def _backup_payload(tables):
    return {
        'backup_schema_version': BACKUP_SCHEMA_VERSION,
        'exported_at': datetime.now().astimezone().isoformat(
            timespec='seconds'
        ),
        'notes': (
            'Database record backup. Supabase Storage image bytes are '
            'not embedded; incoming screenshot storage paths are preserved.'
        ),
        'counts': {
            name: len(rows)
            for name, rows in tables.items()
        },
        'tables': tables,
    }


def _season_future_export_rows(bet_legs):
    return [
        row
        for row in bet_legs
        if str(
            row.get('tracking_scope')
            or ''
        ).strip().upper() == 'SEASON'
    ]


def _make_backup_zip_bytes(tables):
    payload = _backup_payload(tables)
    season_futures = _season_future_export_rows(
        tables.get('bet_legs', [])
    )

    output = io.BytesIO()

    with zipfile.ZipFile(
        output,
        'w',
        compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        zf.writestr(
            'sports_bet_tracker_backup.json',
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )

        for table, rows in tables.items():
            zf.writestr(
                f'csv/{table}.csv',
                _records_to_csv_bytes(rows),
            )

        zf.writestr(
            'csv/season_futures.csv',
            _records_to_csv_bytes(season_futures),
        )

        readme = (
            'SPORTS BET TRACKER BACKUP\\n'
            '=========================\\n\\n'
            f"Exported: {payload['exported_at']}\\n"
            f"Backup schema version: {BACKUP_SCHEMA_VERSION}\\n\\n"
            'Contents:\\n'
            '- sports_bet_tracker_backup.json: complete record snapshot\\n'
            '- csv/: one CSV per exported table\\n'
            '- csv/season_futures.csv: convenience view of tracked futures\\n\\n'
            'Important:\\n'
            '- This backup contains database records and IDs.\\n'
            '- Supabase Storage image bytes are NOT embedded.\\n'
            '- Screenshot storage paths and metadata remain in '
            'incoming_bet_screenshots.\\n'
            '- Hiding/archive settings are included because they live '
            'on the bets rows.\\n'
        )

        zf.writestr('README.txt', readme)

    return output.getvalue()


def _render_export_tab():
    st.subheader('Export & Backup')
    st.caption(
        'Create portable copies of the tracker without changing Supabase. '
        'Exports include all historical bets, including bets hidden from '
        'the normal 14-day History view.'
    )

    if st.button(
        'Prepare Export Files',
        type='primary',
        key='prepare_export_files',
    ):
        try:
            with st.spinner(
                'Reading tracker data from Supabase...'
            ):
                tables = export_backup_tables()

                season_futures = _season_future_export_rows(
                    tables.get('bet_legs', [])
                )

                workbook_sheets = {
                    'Bets': tables.get('bets', []),
                    'Bet Legs': tables.get('bet_legs', []),
                    'RR Combinations': tables.get('bet_combinations', []),
                    'RR Combo Legs': tables.get('bet_combination_legs', []),
                    'Season Futures': season_futures,
                    'Imports': tables.get('incoming_bet_screenshots', []),
                }

                st.session_state[
                    'export_xlsx_bytes'
                ] = _make_xlsx_bytes(workbook_sheets)

                st.session_state[
                    'export_backup_zip_bytes'
                ] = _make_backup_zip_bytes(tables)

                st.session_state[
                    'export_counts'
                ] = {
                    key: len(value)
                    for key, value in tables.items()
                }

                st.session_state[
                    'export_prepared_at'
                ] = datetime.now().astimezone().isoformat(
                    timespec='seconds'
                )

            st.success('Export files are ready.')
        except Exception as exc:
            st.error(
                f'Export preparation failed: {exc}'
            )

    prepared_at = st.session_state.get(
        'export_prepared_at'
    )
    counts = st.session_state.get(
        'export_counts'
    )

    if prepared_at and counts:
        st.caption(f'Prepared {prepared_at}')

        c1, c2, c3, c4, c5 = st.columns(5)

        c1.metric('Bets', counts.get('bets', 0))
        c2.metric('Legs', counts.get('bet_legs', 0))
        c3.metric(
            'RR Combos',
            counts.get('bet_combinations', 0),
        )
        c4.metric(
            'RR Links',
            counts.get('bet_combination_legs', 0),
        )
        c5.metric(
            'Imports',
            counts.get('incoming_bet_screenshots', 0),
        )

    xlsx_bytes = st.session_state.get(
        'export_xlsx_bytes'
    )
    zip_bytes = st.session_state.get(
        'export_backup_zip_bytes'
    )

    if xlsx_bytes and zip_bytes:
        today = datetime.now().strftime('%Y-%m-%d')
        d1, d2 = st.columns(2)

        with d1:
            st.markdown('#### Excel Workbook')
            st.caption(
                'Separate sheets for Bets, Bet Legs, Round Robin data, '
                'Season Futures, and import metadata.'
            )

            st.download_button(
                'Download Excel Export',
                data=xlsx_bytes,
                file_name=f'sports_bet_tracker_{today}.xlsx',
                mime=(
                    'application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.sheet'
                ),
                key='download_full_excel',
                use_container_width=True,
            )

        with d2:
            st.markdown('#### Full Backup')
            st.caption(
                'Complete JSON record snapshot plus individual CSV files. '
                'Best option for safekeeping/recovery.'
            )

            st.download_button(
                'Download Full Backup ZIP',
                data=zip_bytes,
                file_name=(
                    f'sports_bet_tracker_backup_{today}.zip'
                ),
                mime='application/zip',
                key='download_full_backup_zip',
                use_container_width=True,
            )

        st.info(
            'The backup preserves database rows, IDs, parser data, '
            'Round Robin links, futures fields, and screenshot metadata. '
            'It does not download the actual screenshot image files from '
            'Supabase Storage.'
        )




def _render_notification_settings_tab():
    st.subheader('Notifications')
    st.caption(
        'Choose which Pushover alerts the tracker is allowed to send. '
        'These settings are stored in Supabase and will be used by the '
        'automatic notification pipeline.'
    )

    try:
        settings = get_notification_settings()
    except Exception as exc:
        st.error(
            'Could not load notification settings. '
            'Make sure the v36 Supabase migration has been run.'
        )
        st.exception(exc)
        return

    with st.form(
        'notification_settings_form',
        clear_on_submit=False,
    ):
        st.markdown('#### Bet Results')

        c1, c2 = st.columns(2)

        with c1:
            wins_enabled = st.toggle(
                'Winning bets',
                value=bool(
                    settings.get(
                        'wins_enabled',
                        True,
                    )
                ),
                help=(
                    'Send a notification when a bet settles as WON.'
                ),
            )

        with c2:
            losses_enabled = st.toggle(
                'Losing bets',
                value=bool(
                    settings.get(
                        'losses_enabled',
                        False,
                    )
                ),
                help=(
                    'Send a notification when a bet settles as LOST.'
                ),
            )

        st.markdown('#### Big Wins')

        b1, b2 = st.columns([1, 1])

        with b1:
            big_wins_enabled = st.toggle(
                'Big win alert',
                value=bool(
                    settings.get(
                        'big_wins_enabled',
                        True,
                    )
                ),
                help=(
                    'Send a special Big Win notification when profit '
                    'meets the threshold.'
                ),
            )

        with b2:
            big_win_threshold = st.number_input(
                'Minimum profit for Big Win',
                min_value=0.0,
                value=float(
                    settings.get(
                        'big_win_profit_threshold',
                        100.0,
                    )
                    or 100.0
                ),
                step=25.0,
                format='%.2f',
                disabled=not big_wins_enabled,
            )

        st.markdown('#### Import & Tracking')

        i1, i2, i3 = st.columns(3)

        with i1:
            import_failures_enabled = st.toggle(
                'Import failures',
                value=bool(
                    settings.get(
                        'import_failures_enabled',
                        True,
                    )
                ),
                help=(
                    'Alert when a screenshot import fails.'
                ),
            )

        with i2:
            needs_review_enabled = st.toggle(
                'Needs review',
                value=bool(
                    settings.get(
                        'needs_review_enabled',
                        False,
                    )
                ),
                help=(
                    'Alert when an imported bet is flagged for review.'
                ),
            )

        with i3:
            tracking_errors_enabled = st.toggle(
                'Tracking / matching errors',
                value=bool(
                    settings.get(
                        'tracking_errors_enabled',
                        False,
                    )
                ),
                help=(
                    'Alert on live tracking or ESPN matching problems.'
                ),
            )

        save_settings = st.form_submit_button(
            'Save Notification Settings',
            type='primary',
            use_container_width=True,
        )

    if save_settings:
        try:
            saved = update_notification_settings({
                'wins_enabled': wins_enabled,
                'losses_enabled': losses_enabled,
                'big_wins_enabled': big_wins_enabled,
                'big_win_profit_threshold': float(
                    big_win_threshold
                ),
                'import_failures_enabled': import_failures_enabled,
                'needs_review_enabled': needs_review_enabled,
                'tracking_errors_enabled': tracking_errors_enabled,
            })

            st.success(
                'Notification settings saved.'
            )

            st.session_state[
                'notification_settings_last_saved'
            ] = saved

        except Exception as exc:
            st.error(
                f'Could not save notification settings: {exc}'
            )

    st.divider()
    st.markdown('#### Pushover Connection Test')
    st.caption(
        'This only sends a test message. It does not change any '
        'notification preferences.'
    )

    if st.button(
        'Send Test Notification',
        key='send_pushover_test_from_settings',
    ):
        try:
            with st.spinner(
                'Sending test notification...'
            ):
                result = send_test_pushover(
                    title='Sports Bet Tracker',
                    message=(
                        '✅ Notification settings are connected.'
                    ),
                )

            if result and result.get('ok'):
                st.success(
                    'Test notification sent to Pushover.'
                )
            else:
                st.error(
                    'Pushover test did not report success.'
                )
                if result is not None:
                    st.json(result)

        except Exception as exc:
            st.error(
                f'Pushover test failed: {exc}'
            )

    st.info(
        'Saving these controls does not send automatic settlement alerts '
        'yet. The next step is wiring update-live-bets to these settings '
        'with duplicate protection.'
    )



tab_dash, tab_active, tab_legs, tab_exposure, tab_review, tab_futures, tab_big_wins, tab_history, tab_notifications, tab_export = st.tabs(['Dashboard','Active Bets','Active Legs','Exposure','Import Review','Season Futures','Big Wins','History','Notifications','Export'])

with tab_dash:
    _render_dashboard(list_bets())



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
            'Click the arrow beside a bet to show its legs and details.'
        )

        rows = _filter_bets_ui(
            rows,
            'active',
            include_status=True,
            include_date=True,
        )

        active_results = st.empty()

        with active_results.container():
            if not rows:
                st.info('No active bets match the selected filters.')
            else:
                _render_bet_expanders(
                    rows,
                    'active',
                    show_schedule_override=True,
                )


with tab_legs:
    _render_active_legs_tab()


with tab_exposure:
    _render_exposure_tab(list_bets())


with tab_review:
    _render_import_review_tab()


with tab_futures:
    st.subheader('NFL Season Futures')
    st.caption(
        'Season-future tracking is fully Supabase-backed. '
        'Player matching and season-stat refreshes run through the same '
        'Supabase Edge Functions used by the rest of the tracker.'
    )
    st.info(
        'Supported markets: Passing Yards, Passing TDs, Interceptions, '
        'Rushing Yards, Rushing TDs, Receiving Yards, Receptions, and '
        'Receiving TDs. Regular-season tracking uses ESPN season type 2.'
    )

    # ----------------------------------------------------------
    # Configure an imported NFL player prop as a season future.
    # ----------------------------------------------------------
    eligible = list_future_candidates()

    if eligible:
        st.markdown('#### Add / configure season futures')

        options = {
            (
                f"Bet {lg.get('bet_row_id')} • "
                f"{lg.get('selection')} • "
                f"{lg.get('market')} • "
                f"line {lg.get('line_value') if lg.get('line_value') is not None else '—'}"
            ): lg
            for lg in eligible
        }

        chosen = st.selectbox(
            'Choose an imported player leg',
            list(options.keys()),
        )

        lg = options[chosen]

        c1, c2, c3 = st.columns(3)

        yr = c1.number_input(
            'Season',
            min_value=2020,
            max_value=2100,
            value=int(
                lg.get('future_season_year')
                or lg.get('espn_season_year')
                or 2026
            ),
            step=1,
            key='futureyear',
        )

        direction = c2.selectbox(
            'Direction',
            ['OVER', 'UNDER'],
            index=(
                1
                if str(lg.get('direction') or 'OVER').upper() == 'UNDER'
                else 0
            ),
            key='futuredir',
        )

        line_default = _safe_float(lg.get('line_value'))
        if line_default is None:
            line_default = 0.0

        line = c3.number_input(
            'Line',
            min_value=0.0,
            value=float(line_default),
            step=0.5,
            key='futureline',
        )

        if st.button('Track this leg as season future'):
            try:
                configure_future_leg(
                    lg['id'],
                    line,
                    direction,
                    int(yr),
                    2,
                )
                st.success(
                    'Season-future tracking enabled in Supabase.'
                )
                st.rerun()

            except Exception as exc:
                st.error(
                    f'Could not configure season future: {exc}'
                )

    else:
        st.caption(
            'No imported NFL player markets are eligible yet. '
            'Import a supported season-long player prop first.'
        )

    # ----------------------------------------------------------
    # Read and refresh tracked futures only from Supabase.
    # ----------------------------------------------------------
    tracked = list_future_legs()

    if tracked:
        st.markdown('#### Tracked season futures')

        if st.button(
            'Refresh Season Stats',
            type='primary',
        ):
            try:
                with st.spinner(
                    'Refreshing season futures through Supabase...'
                ):
                    future_result = refresh_all_future_legs()

                st.session_state['future_refresh'] = future_result

                if future_result.get('failed', 0):
                    st.warning(
                        f"Season refresh finished with "
                        f"{future_result.get('successful', 0)} successful "
                        f"and {future_result.get('failed', 0)} failed."
                    )
                else:
                    st.success(
                        f"Refreshed "
                        f"{future_result.get('successful', 0)} "
                        f"season-future leg(s)."
                    )

                st.rerun()

            except Exception as exc:
                st.error(
                    f'Season refresh failed: {exc}'
                )

        tracked = list_future_legs()

        rows = []

        for lg in tracked:
            cur = _safe_float(
                lg.get('future_current')
            )

            line = _safe_float(
                lg.get('line_value')
            )

            gp = lg.get(
                'future_games_played'
            )

            pace = _safe_float(
                lg.get('future_pace')
            )

            rows.append({
                'Bet': lg.get('bet_row_id'),
                'Player': lg.get('selection'),
                'Market': lg.get('market'),
                'Direction': lg.get('direction'),
                'Line': line,
                'Current': cur,
                'Games': gp,
                'Pace': (
                    round(pace, 1)
                    if pace is not None
                    else None
                ),
                'State': (
                    lg.get('future_state')
                    or 'NOT REFRESHED'
                ),
                'Season': (
                    lg.get('future_season_year')
                    or lg.get('espn_season_year')
                    or 2026
                ),
                'ESPN Athlete ID': lg.get('espn_athlete_id'),
                'Last Updated': lg.get('future_updated_at'),
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            'Pace = current stat ÷ games played × 17. '
            'Official sportsbook settlement rules still control voids, '
            'injuries, pushes, and special minimum-game conditions.'
        )

    else:
        st.caption(
            'No season futures are currently being tracked.'
        )

with tab_big_wins:
    _render_big_wins_tab(list_bets())


with tab_history:
    st.subheader('Bet History')
    all_history_rows = list_bets()

    show_older_history = st.checkbox(
        'Show bets older than 14 days',
        value=False,
        key='show_older_history',
    )

    rows = (
        all_history_rows
        if show_older_history
        else _history_recent_bets(
            all_history_rows,
            HISTORY_DEFAULT_DAYS,
        )
    )

    if rows:
        if show_older_history:
            st.caption(
                'Showing full history. Click the arrow beside a bet '
                'description to show its legs.'
            )
        else:
            st.caption(
                'Showing the last 14 days only. Older bets remain in '
                'Supabase and continue to count in Dashboard statistics.'
            )

        rows = _filter_bets_ui(
            rows,
            'history',
            include_status=True,
            include_date=True,
        )

        history_results = st.empty()

        with history_results.container():
            if rows:
                _render_bet_expanders(
                    rows,
                    'history',
                    show_schedule_override=False,
                )
            else:
                st.info('No history bets match the selected filters.')

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



with tab_notifications:
    _render_notification_settings_tab()


with tab_export:
    _render_export_tab()
