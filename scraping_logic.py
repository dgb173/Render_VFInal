
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import datetime
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
from app_utils import normalize_handicap_to_half_bucket_str

URL_NOWGOAL = "https://live20.nowgoal25.com/"
REQUEST_TIMEOUT_SECONDS = 12
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": URL_NOWGOAL,
}

_requests_session = None
_requests_session_lock = threading.Lock()
_requests_fetch_lock = threading.Lock()

def _build_nowgoal_url(path: str | None = None) -> str:
    if not path:
        return URL_NOWGOAL
    base = URL_NOWGOAL.rstrip('/')
    suffix = path.lstrip('/')
    return f"{base}/{suffix}"

def _get_shared_requests_session():
    global _requests_session
    with _requests_session_lock:
        if _requests_session is None:
            session = requests.Session()
            retries = Retry(total=3, backoff_factor=0.4, status_forcelist=[500, 502, 503, 504])
            adapter = HTTPAdapter(max_retries=retries)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers.update(_REQUEST_HEADERS)
            _requests_session = session
        return _requests_session

def _fetch_nowgoal_html_sync(url: str) -> str | None:
    session = _get_shared_requests_session()
    try:
        with _requests_fetch_lock:
            response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        print(f"Error al obtener {url} con requests: {exc}")
        return None

async def _fetch_nowgoal_html(path: str | None = None, filter_state: int | None = None, requests_first: bool = True) -> str | None:
    target_url = _build_nowgoal_url(path)
    html_content = None

    if requests_first:
        try:
            html_content = await asyncio.to_thread(_fetch_nowgoal_html_sync, target_url)
        except Exception as exc:
            print(f"Error asincronico al lanzar la carga con requests ({target_url}): {exc}")
            html_content = None

    if html_content:
        return html_content

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(4000)
                if filter_state is not None:
                    try:
                        await page.evaluate("(state) => { if (typeof HideByState === 'function') { HideByState(state); } }", filter_state)
                        await page.wait_for_timeout(1500)
                    except Exception as eval_err:
                        print(f"Advertencia al aplicar HideByState({filter_state}) en {target_url}: {eval_err}")
                return await page.content()
            finally:
                await browser.close()
    except Exception as browser_exc:
        print(f"Error al obtener la pagina con Playwright ({target_url}): {browser_exc}")
    return None

def parse_main_page_matches(html_content, limit=20, offset=0, handicap_filter=None):
    soup = BeautifulSoup(html_content, 'html.parser')
    match_rows = soup.find_all('tr', id=lambda x: x and x.startswith('tr1_'))
    upcoming_matches = []
    now_utc = datetime.datetime.utcnow()

    for row in match_rows:
        match_id = row.get('id', '').replace('tr1_', '')
        if not match_id: continue

        time_cell = row.find('td', {'name': 'timeData'})
        if not time_cell or not time_cell.has_attr('data-t'): continue
        
        try:
            match_time = datetime.datetime.strptime(time_cell['data-t'], '%Y-%m-%d %H:%M:%S')
        except (ValueError, IndexError):
            continue

        if match_time < now_utc: continue

        home_team_tag = row.find('a', {'id': f'team1_{match_id}'})
        away_team_tag = row.find('a', {'id': f'team2_{match_id}'})
        odds_data = row.get('odds', '').split(',')
        handicap = odds_data[2] if len(odds_data) > 2 else "N/A"
        goal_line = odds_data[10] if len(odds_data) > 10 else "N/A"

        if handicap == "N/A":
            continue

        upcoming_matches.append({
            "id": match_id,
            "time_obj": match_time,
            "home_team": home_team_tag.text.strip() if home_team_tag else "N/A",
            "away_team": away_team_tag.text.strip() if away_team_tag else "N/A",
            "handicap": handicap,
            "goal_line": goal_line
        })

    if handicap_filter:
        try:
            target = normalize_handicap_to_half_bucket_str(handicap_filter)
            if target is not None:
                filtered = []
                for m in upcoming_matches:
                    hv = normalize_handicap_to_half_bucket_str(m.get('handicap', ''))
                    if hv == target:
                        filtered.append(m)
                upcoming_matches = filtered
        except Exception:
            pass

    upcoming_matches.sort(key=lambda x: x['time_obj'])
    
    paginated_matches = upcoming_matches[offset:offset+limit]

    for match in paginated_matches:
        match['time'] = (match['time_obj'] + datetime.timedelta(hours=2)).strftime('%H:%M')
        # Keep time_obj for sorting but convert to string for JSON compatibility
        match['time_obj'] = match['time_obj'].isoformat()

    return paginated_matches

def parse_main_page_finished_matches(html_content, limit=20, offset=0, handicap_filter=None):
    soup = BeautifulSoup(html_content, 'html.parser')
    match_rows = soup.find_all('tr', id=lambda x: x and x.startswith('tr1_'))
    finished_matches = []
    for row in match_rows:
        match_id = row.get('id', '').replace('tr1_', '')
        if not match_id: continue

        state = row.get('state')
        if state is not None and state != "-1":
            continue

        cells = row.find_all('td')
        if len(cells) < 8: continue

        home_team_tag = row.find('a', {'id': f'team1_{match_id}'})
        away_team_tag = row.find('a', {'id': f'team2_{match_id}'})
        
        score_cell = cells[6]
        score_text = "N/A"
        if score_cell:
            b_tag = score_cell.find('b')
            if b_tag:
                score_text = b_tag.text.strip()
            else:
                score_text = score_cell.get_text(strip=True)

        if not re.match(r'^\d+\s*-\s*\d+$', score_text):
            continue

        odds_data = row.get('odds', '').split(',')
        handicap = odds_data[2] if len(odds_data) > 2 else "N/A"
        goal_line = odds_data[10] if len(odds_data) > 10 else "N/A"

        if handicap == "N/A":
            continue

        time_cell = row.find('td', {'name': 'timeData'})
        match_time = datetime.datetime.now()
        if time_cell and time_cell.has_attr('data-t'):
            try:
                match_time = datetime.datetime.strptime(time_cell['data-t'], '%Y-%m-%d %H:%M:%S')
            except (ValueError, IndexError):
                continue
        
        finished_matches.append({
            "id": match_id,
            "time_obj": match_time,
            "home_team": home_team_tag.text.strip() if home_team_tag else "N/A",
            "away_team": away_team_tag.text.strip() if away_team_tag else "N/A",
            "score": score_text,
            "handicap": handicap,
            "goal_line": goal_line
        })

    if handicap_filter:
        try:
            target = normalize_handicap_to_half_bucket_str(handicap_filter)
            if target is not None:
                filtered = []
                for m in finished_matches:
                    hv = normalize_handicap_to_half_bucket_str(m.get('handicap', ''))
                    if hv == target:
                        filtered.append(m)
                finished_matches = filtered
        except Exception:
            pass

    finished_matches.sort(key=lambda x: x['time_obj'], reverse=True)
    
    paginated_matches = finished_matches[offset:offset+limit]

    for match in paginated_matches:
        match['time'] = (match['time_obj'] + datetime.timedelta(hours=2)).strftime('%d/%m %H:%M')
        match['time_obj'] = match['time_obj'].isoformat()

    return paginated_matches

async def get_main_page_matches_async(limit=20, offset=0, handicap_filter=None):
    html_content = await _fetch_nowgoal_html(filter_state=3)
    if not html_content:
        html_content = await _fetch_nowgoal_html(filter_state=3, requests_first=False)
        if not html_content:
            return []
    matches = parse_main_page_matches(html_content, limit, offset, handicap_filter)
    if not matches:
        html_content = await _fetch_nowgoal_html(filter_state=3, requests_first=False)
        if not html_content:
            return []
        matches = parse_main_page_matches(html_content, limit, offset, handicap_filter)
    return matches

async def get_main_page_finished_matches_async(limit=20, offset=0, handicap_filter=None):
    html_content = await _fetch_nowgoal_html(path='football/results')
    if not html_content:
        html_content = await _fetch_nowgoal_html(path='football/results', requests_first=False)
        if not html_content:
            return []
    matches = parse_main_page_finished_matches(html_content, limit, offset, handicap_filter)
    if not matches:
        html_content = await _fetch_nowgoal_html(path='football/results', requests_first=False)
        if not html_content:
            return []
        matches = parse_main_page_finished_matches(html_content, limit, offset, handicap_filter)
    return matches
