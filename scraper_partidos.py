# scraper_partidos.py
# Scraper preparado para Render.com: usa requests con fallback a Playwright (Chromium).

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from modules.utils import normalize_handicap_to_half_bucket_str

logger = logging.getLogger(__name__)

TARGET_URL = "https://live20.nowgoal25.com/"
RESULTS_URL = f"{TARGET_URL.rstrip('/')}/football/results"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": TARGET_URL,
}

PLAYWRIGHT_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]
PLAYWRIGHT_TIMEOUT_MS = 20_000
REQUEST_TIMEOUT = 20
UTC = ZoneInfo("UTC")
MADRID_TZ = ZoneInfo("Europe/Madrid")
VIEWPORT = {"width": 1280, "height": 720}

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _get_requests_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            session = requests.Session()
            retries = Retry(total=3, backoff_factor=0.4, status_forcelist=[500, 502, 503, 504])
            adapter = HTTPAdapter(max_retries=retries)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers.update(REQUEST_HEADERS)
            _session = session
        return _session


def fetch_with_requests(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[str]:
    """
    Intenta cargar la página objetivo con requests (más barato que lanzar Chromium).
    """
    try:
        response = _get_requests_session().get(url, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        logger.warning("requests falló para %s: %s", url, exc)
        return None


def html_has_rows(html: Optional[str]) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.select("tr[id^='tr1_'], tr[id^='tr2_'], tr[id^='tr3_']"))


def fetch_html_via_playwright_sync(
    url: str,
    timeout_ms: int = PLAYWRIGHT_TIMEOUT_MS,
    filter_state: Optional[int] = None,
) -> Optional[str]:
    """
    Lanza Chromium con Playwright para cargar la página y devolver el HTML renderizado.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=PLAYWRIGHT_ARGS)
            context = None
            try:
                context = browser.new_context(
                    user_agent=UA,
                    locale="es-ES",
                    timezone_id="UTC",
                    viewport=VIEWPORT,
                )
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_selector(
                        "tr[id^='tr1_'], tr[id^='tr2_'], tr[id^='tr3_']",
                        timeout=5000,
                    )
                except PlaywrightTimeoutError:
                    logger.debug("Selector principal no disponible aún en %s", url)
                if filter_state is not None:
                    try:
                        page.evaluate(
                            "state => { if (typeof HideByState === 'function') { HideByState(state); } }",
                            filter_state,
                        )
                        page.wait_for_timeout(1200)
                    except Exception as eval_exc:
                        logger.debug("HideByState(%s) falló: %s", filter_state, eval_exc)
                page.wait_for_timeout(600)
                return page.content()
            finally:
                if context:
                    try:
                        context.close()
                    except Exception:
                        pass
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Playwright falló para %s: %s", url, exc)
        return None


def _get_html_with_fallback(url: str, *, filter_state: Optional[int] = None) -> Optional[str]:
    html = fetch_with_requests(url)
    if html and html_has_rows(html):
        return html
    return fetch_html_via_playwright_sync(url, filter_state=filter_state)


def _parse_match_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        naive_dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return naive_dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _extract_team_name(row: BeautifulSoup, element_id: str) -> str:
    tag = row.find("a", {"id": element_id})
    return tag.get_text(strip=True) if tag else "N/A"


def _extract_league_name(row: BeautifulSoup) -> str:
    league_cell = row.find("td", {"name": "leagueData"})
    return league_cell.get_text(strip=True) if league_cell else "N/A"


def get_upcoming_matches(
    limit: int = 20,
    offset: int = 0,
    handicap_filter: Optional[str] = None,
) -> List[Dict]:
    html = _get_html_with_fallback(TARGET_URL, filter_state=3)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr", id=lambda value: value and value.startswith("tr1_"))
    now_utc = datetime.now(tz=UTC)
    matches: List[Dict] = []

    for row in rows:
        match_id = (row.get("id") or "").replace("tr1_", "")
        if not match_id:
            continue

        time_cell = row.find("td", {"name": "timeData"})
        kickoff = _parse_match_time(time_cell.get("data-t") if time_cell else None)
        if not kickoff or kickoff < now_utc:
            continue

        odds_data = (row.get("odds") or "").split(",")
        handicap = odds_data[2] if len(odds_data) > 2 else "N/A"
        goal_line = odds_data[10] if len(odds_data) > 10 else "N/A"
        if handicap in ("", "N/A") or goal_line in ("", "N/A"):
            continue

        kickoff_local = kickoff.astimezone(MADRID_TZ)
        matches.append(
            {
                "id": match_id,
                "_kickoff": kickoff,
                "kickoff_utc": kickoff.isoformat(),
                "kickoff_local": kickoff_local.isoformat(timespec="minutes"),
                "time": kickoff_local.strftime("%H:%M"),
                "home_team": _extract_team_name(row, f"team1_{match_id}"),
                "away_team": _extract_team_name(row, f"team2_{match_id}"),
                "handicap": handicap,
                "handicap_bucket": normalize_handicap_to_half_bucket_str(handicap),
                "goal_line": goal_line,
                "league": _extract_league_name(row),
            }
        )

    if handicap_filter:
        target = normalize_handicap_to_half_bucket_str(handicap_filter)
        if target is not None:
            matches = [m for m in matches if m.get("handicap_bucket") == target]

    matches.sort(key=lambda m: m["_kickoff"])
    slice_matches = matches[offset : offset + limit]
    for match in slice_matches:
        match.pop("_kickoff", None)
    return slice_matches


def get_finished_matches(
    limit: int = 20,
    offset: int = 0,
    handicap_filter: Optional[str] = None,
) -> List[Dict]:
    html = _get_html_with_fallback(RESULTS_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr", id=lambda value: value and value.startswith("tr1_"))
    finished: List[Dict] = []

    for row in rows:
        if row.get("state") not in {None, "-1"}:
            continue

        match_id = (row.get("id") or "").replace("tr1_", "")
        if not match_id:
            continue

        cells = row.find_all("td")
        score_cell = cells[6] if len(cells) > 6 else None
        score_text = "N/A"
        if score_cell:
            b_tag = score_cell.find("b")
            score_text = (b_tag.text if b_tag else score_cell.get_text()).strip()
        if not score_text or not score_text or not getattr(score_text, "strip", None):
            continue
        score_text = score_text.strip()
        if not score_text or "-" not in score_text:
            continue

        odds_data = (row.get("odds") or "").split(",")
        handicap = odds_data[2] if len(odds_data) > 2 else "N/A"
        goal_line = odds_data[10] if len(odds_data) > 10 else "N/A"
        if handicap in ("", "N/A") or goal_line in ("", "N/A"):
            continue

        time_cell = row.find("td", {"name": "timeData"})
        kickoff = _parse_match_time(time_cell.get("data-t") if time_cell else None)
        if not kickoff:
            continue
        kickoff_local = kickoff.astimezone(MADRID_TZ)

        finished.append(
            {
                "id": match_id,
                "_kickoff": kickoff,
                "kickoff_utc": kickoff.isoformat(),
                "kickoff_local": kickoff_local.isoformat(timespec="minutes"),
                "time": kickoff_local.strftime("%d/%m %H:%M"),
                "home_team": _extract_team_name(row, f"team1_{match_id}"),
                "away_team": _extract_team_name(row, f"team2_{match_id}"),
                "score": score_text,
                "handicap": handicap,
                "handicap_bucket": normalize_handicap_to_half_bucket_str(handicap),
                "goal_line": goal_line,
                "league": _extract_league_name(row),
            }
        )

    if handicap_filter:
        target = normalize_handicap_to_half_bucket_str(handicap_filter)
        if target is not None:
            finished = [m for m in finished if m.get("handicap_bucket") == target]

    finished.sort(key=lambda m: m["_kickoff"], reverse=True)
    slice_matches = finished[offset : offset + limit]
    for match in slice_matches:
        match.pop("_kickoff", None)
    return slice_matches
