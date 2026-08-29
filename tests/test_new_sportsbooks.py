from pathlib import Path
from PIL import Image
from ocr.extractor import extract_text
from importers.screenshot import parse_screenshot

ROOT = Path(__file__).resolve().parent / 'screenshots'

def test_fanatics_sample():
    p = parse_screenshot(extract_text(Image.open(ROOT/'fanatics_2_leg_parlay.jpeg'), sparse=True))
    assert p['sportsbook'] == 'Fanatics'
    assert p['sportsbook_bet_id'] == '26344302000059703'
    assert p['bet_type'] == 'PARLAY'
    assert p['leg_count'] == 2
    assert p['odds']['current'] == 148
    assert p['money']['stake'] == 1.00
    assert p['money']['to_pay'] == 2.49
    assert [x['selection'] for x in p['legs']] == ['Baltimore Ravens','Atlanta Falcons']

def test_fanduel_sample():
    p = parse_screenshot(extract_text(Image.open(ROOT/'fanduel_moneyline.jpeg'), sparse=True))
    assert p['sportsbook'] == 'FanDuel'
    assert p['bet_type'] == 'SINGLE'
    assert p['odds']['current'] == 122
    assert p['money']['stake'] == 1.00
    assert p['money']['to_pay'] == 2.22
    assert p['legs'][0]['selection'] == 'Chicago Bears'
    assert p['legs'][0]['market'] == 'Moneyline'
