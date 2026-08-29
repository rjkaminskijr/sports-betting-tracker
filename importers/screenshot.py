import re
from importers.draftkings.parser import parse_draftkings
from importers.fanduel.parser import parse_fanduel
from importers.fanatics.parser import parse_fanatics_screenshot

def detect_sportsbook(text: str) -> str:
    t = (text or '').upper()
    if 'FANATICS SPORTSBOOK' in t or ('FANATICS' in t and 'BET ID' in t):
        return 'Fanatics'
    # FanDuel screenshots may omit the word FanDuel, but commonly include these labels.
    if 'TOTAL WAGER' in t and 'TOTAL PAYOUT' in t and ('FOLLOW BET ON LOCK SCREEN' in t or 'MY BETS' in t):
        return 'FanDuel'
    if 'FANDUEL' in t:
        return 'FanDuel'
    if 'DRAFTKINGS' in t or re.search(r'\bDK\d{6,}\b', t):
        return 'DraftKings'
    # Existing tracker was DraftKings-first, so preserve fallback behavior.
    return 'DraftKings'

def parse_screenshot(text: str) -> dict:
    sportsbook = detect_sportsbook(text)
    if sportsbook == 'Fanatics':
        return parse_fanatics_screenshot(text)
    if sportsbook == 'FanDuel':
        return parse_fanduel(text)
    return parse_draftkings(text)
