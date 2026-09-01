from datetime import datetime
import hmac
import math

import pandas as pd
import streamlit as st

from services.supabase_api import (
    list_bets,
    list_legs,
    update_bet_espn_scope,
    update_leg_manual_status,
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
st.caption('Version 25.0 • Import quality review + fully Supabase-backed tracking')

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

    return (
        f"{description}  •  "
        f"{status}  •  "
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

        with st.expander(
            label,
            expanded=False,
        ):
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
    active_bets = [
        bet
        for bet in all_bets
        if _is_active_status(bet.get('status'))
    ]

    player_rows = []
    team_rows = []
    active_leg_count = 0
    winning_legs = 0
    losing_legs = 0
    pending_legs = 0

    for bet in active_bets:
        stake = _safe_float(bet.get('stake')) or 0.0
        potential = _safe_float(bet.get('to_pay')) or 0.0
        legs = list_legs(bet['id'])

        for leg in legs:
            active_leg_count += 1
            leg_status = str(leg.get('status') or 'PENDING').upper()

            if leg_status == 'WON':
                winning_legs += 1
            elif leg_status == 'LOST':
                losing_legs += 1
            else:
                pending_legs += 1

            player = str(leg.get('selection') or '').strip()
            market = str(leg.get('market') or '').strip()

            is_player = bool(
                leg.get('espn_athlete_id')
                or any(
                    token in market.lower()
                    for token in [
                        'td scorer',
                        'receiving',
                        'rushing',
                        'passing',
                        'receptions',
                    ]
                )
            )

            if player and is_player:
                player_rows.append({
                    'Player': player,
                    'Bet ID': bet.get('id'),
                    'Wager Exposure': stake,
                    'Potential Return': potential,
                })

            teams = []
            for value in [
                leg.get('event_team_a'),
                leg.get('event_team_b'),
            ]:
                value = str(value or '').strip()
                if value and value not in teams:
                    teams.append(value)

            for team in teams:
                team_rows.append({
                    'Team': team,
                    'Bet ID': bet.get('id'),
                    'Wager Exposure': stake,
                    'Potential Return': potential,
                })

    def summarize(rows, label):
        if not rows:
            return pd.DataFrame()

        frame = pd.DataFrame(rows)

        return (
            frame.groupby(label)
            .agg(
                Bets=('Bet ID', 'nunique'),
                Wager_Exposure=('Wager Exposure', 'sum'),
                Potential_Return=('Potential Return', 'sum'),
            )
            .reset_index()
            .rename(columns={
                'Wager_Exposure': 'Wager Exposure',
                'Potential_Return': 'Potential Return',
            })
            .sort_values(
                ['Wager Exposure', 'Potential Return'],
                ascending=False,
            )
        )

    return {
        'active_leg_count': active_leg_count,
        'winning_legs': winning_legs,
        'losing_legs': losing_legs,
        'pending_legs': pending_legs,
        'players': summarize(player_rows, 'Player'),
        'teams': summarize(team_rows, 'Team'),
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

    st.markdown('#### Recent Performance')

    dated = settled_df.dropna(subset=['Placed']).copy()

    if dated.empty:
        st.caption('No dated settled bets are available for a P/L trend yet.')
    else:
        dated['Day'] = dated['Placed'].dt.tz_convert(None).dt.date
        daily = (
            dated.groupby('Day')
            .agg(
                Wagered=('Wagered', 'sum'),
                Daily_P_L=('P/L', 'sum'),
            )
            .reset_index()
            .rename(columns={'Daily_P_L': 'Daily P/L'})
            .sort_values('Day')
        )
        daily['Cumulative P/L'] = daily['Daily P/L'].cumsum()

        chart_df = daily.set_index('Day')[['Daily P/L', 'Cumulative P/L']]
        st.line_chart(chart_df, use_container_width=True)

        st.dataframe(
            daily.tail(14),
            use_container_width=True,
            hide_index=True,
            column_config={
                'Day': st.column_config.DateColumn('Day'),
                'Wagered': st.column_config.NumberColumn(
                    'Wagered',
                    format='$%.2f',
                ),
                'Daily P/L': st.column_config.NumberColumn(
                    'Daily P/L',
                    format='$%.2f',
                ),
                'Cumulative P/L': st.column_config.NumberColumn(
                    'Cumulative P/L',
                    format='$%.2f',
                ),
            },
        )

    st.markdown('#### Exposure Concentration')
    x1, x2 = st.columns(2)

    with x1:
        st.markdown('**Top Players**')
        players = exposure['players'].head(10)
        if players.empty:
            st.caption('No active player exposure.')
        else:
            st.dataframe(
                players,
                use_container_width=True,
                hide_index=True,
                column_config={
                    'Bets': st.column_config.NumberColumn('Bets', format='%d'),
                    'Wager Exposure': st.column_config.NumberColumn(
                        'Wager Exposure',
                        format='$%.2f',
                    ),
                    'Potential Return': st.column_config.NumberColumn(
                        'Potential Return',
                        format='$%.2f',
                    ),
                },
            )

    with x2:
        st.markdown('**Top Teams**')
        teams = exposure['teams'].head(10)
        if teams.empty:
            st.caption('No active team exposure.')
        else:
            st.dataframe(
                teams,
                use_container_width=True,
                hide_index=True,
                column_config={
                    'Bets': st.column_config.NumberColumn('Bets', format='%d'),
                    'Wager Exposure': st.column_config.NumberColumn(
                        'Wager Exposure',
                        format='$%.2f',
                    ),
                    'Potential Return': st.column_config.NumberColumn(
                        'Potential Return',
                        format='$%.2f',
                    ),
                },
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


tab_dash, tab_active, tab_legs, tab_review, tab_futures, tab_history = st.tabs(['Dashboard','Active Bets','Active Legs','Import Review','Season Futures','History'])

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

        _render_bet_expanders(
            rows,
            'active',
            show_schedule_override=True,
        )


with tab_legs:
    _render_active_legs_tab()


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

with tab_history:
    st.subheader('Bet History')
    rows = list_bets()

    if rows:
        st.caption(
            'Click the arrow beside a bet description to show its legs.'
        )

        _render_bet_expanders(
            rows,
            'history',
            show_schedule_override=False,
        )

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

