# modules/estudio_scraper.py
from modules.analisis_avanzado import generar_analisis_comparativas_indirectas
from modules.analisis_reciente import analizar_rendimiento_reciente_con_handicap, comparar_lineas_handicap_recientes
from modules.analisis_rivales import analizar_rivales_comunes, analizar_contra_rival_del_rival
from modules.funciones_resumen import generar_resumen_rendimiento_reciente
from modules.funciones_auxiliares import (
    _calcular_estadisticas_contra_rival,
    _analizar_over_under,
    _analizar_ah_cubierto,
    _analizar_desempeno_casa_fuera,
)
import logging
import math
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from modules.utils import (
    check_goal_line_cover,
    check_handicap_cover,
    extract_final_score_of,
    format_ah_as_decimal_string_of,
    get_match_details_from_row_of,
    parse_ah_to_number_of,
)

logger = logging.getLogger(__name__)

BASE_URL_OF = "https://live18.nowgoal25.com"
PLAYWRIGHT_TIMEOUT_MS = 20_000
PLAYWRIGHT_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]
PLAYWRIGHT_VIEWPORT = {"width": 1280, "height": 720}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": BASE_URL_OF,
}
PLACEHOLDER_NODATA = "*(No disponible)*"

_REQUESTS_SESSION_LOCK = threading.Lock()
_REQUESTS_SESSION: Optional[requests.Session] = None


def _get_requests_session() -> requests.Session:
    global _REQUESTS_SESSION
    with _REQUESTS_SESSION_LOCK:
        if _REQUESTS_SESSION is None:
            session = requests.Session()
            retries = Retry(total=3, backoff_factor=0.4, status_forcelist=[500, 502, 503, 504])
            adapter = HTTPAdapter(max_retries=retries)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers.update(REQUEST_HEADERS)
            _REQUESTS_SESSION = session
        return _REQUESTS_SESSION


def _fetch_with_requests(url: str, timeout: int = 20) -> Optional[str]:
    try:
        response = _get_requests_session().get(url, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        logger.debug("requests falló para %s: %s", url, exc)
        return None


def _fetch_with_playwright(
    url: str,
    *,
    wait_selector: Optional[str] = None,
    selects: Optional[tuple[str, ...]] = None,
    timeout_ms: int = PLAYWRIGHT_TIMEOUT_MS,
) -> Optional[str]:
    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=PLAYWRIGHT_ARGS)
            context = browser.new_context(
                user_agent=UA,
                locale="es-ES",
                timezone_id="UTC",
                viewport=PLAYWRIGHT_VIEWPORT,
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    logger.debug("Selector %s no disponible aún en %s", wait_selector, url)
            if selects:
                for select_id in selects:
                    selector = f"#{select_id}"
                    try:
                        page.wait_for_selector(selector, timeout=3000)
                        page.select_option(selector, value="8")
                    except PlaywrightTimeoutError:
                        logger.debug("Select %s no disponible en %s", select_id, url)
                    except Exception as exc:
                        logger.debug("Error seleccionando %s en %s: %s", select_id, url, exc)
                page.wait_for_timeout(800)
            page.wait_for_timeout(400)
            return page.content()
    except Exception as exc:
        logger.warning("Playwright falló para %s: %s", url, exc)
        return None
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass


def _load_page_soup(
    url: str,
    *,
    wait_selector: Optional[str] = None,
    selects: Optional[tuple[str, ...]] = None,
    timeout_ms: int = PLAYWRIGHT_TIMEOUT_MS,
) -> Optional[BeautifulSoup]:
    html = _fetch_with_playwright(
        url,
        wait_selector=wait_selector,
        selects=selects,
        timeout_ms=timeout_ms,
    )
    if not html:
        html = _fetch_with_requests(url)
    if not html:
        return None
    return BeautifulSoup(html, "lxml")


def _load_main_match_soup(match_id: str) -> Optional[BeautifulSoup]:
    return _load_page_soup(
        url,
        wait_selector="#table_v1",
        selects=("hSelect_1", "hSelect_2", "hSelect_3"),
    )


def _load_h2h_column_soup(key_match_id: str) -> Optional[BeautifulSoup]:
    url = f"{BASE_URL_OF}/match/h2h-{key_match_id}"
    return _load_page_soup(
        url,
        wait_selector="#table_v2",
        selects=("hSelect_2",),
    )


def _analizar_precedente_handicap(precedente_data, ah_actual_num, favorito_actual_name, main_home_team_name):
    """
    Función helper para generar la síntesis de Hándicap de UN solo precedente.
    VERSIÓN FINAL CORREGIDA Y MEJORADA: Unifica la lógica para todos los cambios de favoritismo,
    incluyendo desde/hacia una línea de 0, y siempre muestra el movimiento de la línea.
    """
    res_raw = precedente_data.get('res_raw')
    ah_raw = precedente_data.get('ah_raw')
    home_team_precedente = precedente_data.get('home')
    away_team_precedente = precedente_data.get('away')

    if not all([res_raw, res_raw != '?-?', ah_raw, ah_raw != '-']):
        return "<li><span class='ah-value'>Hándicap:</span> No hay datos suficientes en este precedente.</li>"

    ah_historico_num = parse_ah_to_number_of(ah_raw)
    comparativa_texto = ""

    if ah_historico_num is not None and ah_actual_num is not None:
        formatted_ah_historico = format_ah_as_decimal_string_of(ah_raw)
        formatted_ah_actual = format_ah_as_decimal_string_of(str(ah_actual_num))
        line_movement_str = f"{formatted_ah_historico} → {formatted_ah_actual}"
        
        # 1. Identificar al favorito del partido histórico.
        favorito_historico_name = None
        if ah_historico_num > 0:
            favorito_historico_name = home_team_precedente
        elif ah_historico_num < 0:
            favorito_historico_name = away_team_precedente
        
        # 2. Lógica de comparación unificada
        if favorito_actual_name.lower() == (favorito_historico_name or "").lower():
            # El favorito es el mismo equipo (o ambos son 'Ninguno'), ahora comparamos la magnitud.
            if abs(ah_actual_num) > abs(ah_historico_num):
                comparativa_texto = f"El mercado considera a este equipo <strong>más favorito</strong> que en el precedente (movimiento: <strong style='color: green; font-size:1.2em;'>{line_movement_str}</strong>). "
            elif abs(ah_actual_num) < abs(ah_historico_num):
                comparativa_texto = f"El mercado considera a este equipo <strong>menos favorito</strong> que en el precedente (movimiento: <strong style='color: orange; font-size:1.2em;'>{line_movement_str}</strong>). "
            else:
                comparativa_texto = f"El mercado mantiene una línea de <strong>magnitud idéntica</strong> a la del precedente (<strong>{formatted_ah_historico}</strong>). "
        else:
            # Los favoritos han cambiado (A->B, Ninguno->A, o A->Ninguno).
            if favorito_historico_name and favorito_actual_name != "Ninguno (línea en 0)":
                # Caso 1: Cambio total de favorito de un equipo a otro.
                comparativa_texto = f"Ha habido un <strong>cambio total de favoritismo</strong>. En el precedente el favorito era '{favorito_historico_name}' (movimiento: <strong style='color: red; font-size:1.2em;'>{line_movement_str}</strong>). "
            elif not favorito_historico_name:
                # Caso 2: Se establece un favorito donde antes no lo había (línea 0).
                comparativa_texto = f"El mercado establece un favorito claro, considerándolo <strong>mucho más favorito</strong> que en el precedente (movimiento: <strong style='color: green; font-size:1.2em;'>{line_movement_str}</strong>). "
            else: # favorito_actual_name es "Ninguno (línea en 0)"
                # Caso 3: Se elimina un favorito que antes existía.
                comparativa_texto = f"El mercado <strong>ha eliminado al favorito</strong> ('{favorito_historico_name}') que existía en el precedente (movimiento: <strong style='color: orange; font-size:1.2em;'>{line_movement_str}</strong>). "
    else:
        comparativa_texto = f"No se pudo realizar una comparación detallada (línea histórica: <strong>{format_ah_as_decimal_string_of(ah_raw)}</strong>). "

    # 3. Simular el resultado del hándicap
    resultado_cover, cubierto = check_handicap_cover(res_raw, ah_actual_num, favorito_actual_name, home_team_precedente, away_team_precedente, main_home_team_name)
    
    if cubierto is True:
        cover_html = f"<span style='color: green; font-weight: bold;'>CUBIERTO ✅</span>"
    elif cubierto is False:
        cover_html = f"<span style='color: red; font-weight: bold;'>NO CUBIERTO ❌</span>"
    else: # PUSH o indeterminado
        cover_html = f"<span style='color: #6c757d; font-weight: bold;'>{resultado_cover.upper()} 🤔</span>"

    return f"<li><span class='ah-value'>Hándicap:</span> {comparativa_texto}Con el resultado ({res_raw.replace('-' , ':')}), la línea actual se habría considerado {cover_html}.</li>"

def _analizar_precedente_goles(precedente_data, goles_actual_num):
    res_raw = precedente_data.get('res_raw')
    if not res_raw or res_raw == '?-?':
        return "<li><span class='score-value'>Goles:</span> No hay datos suficientes en este precedente.</li>"
    try:
        total_goles = sum(map(int, res_raw.split('-')))
        resultado_cover, _ = check_goal_line_cover(res_raw, goles_actual_num)
        # Simplificar el mensaje para que sea más claro
        if 'SUPERADA' in resultado_cover:
            cover_html = "<span style='color: green; font-weight: bold;'>OVER</span>"
        elif 'NO SUPERADA' in resultado_cover:
            cover_html = "<span style='color: red; font-weight: bold;'>UNDER</span>"
        else: # PUSH or indeterminado
            cover_html = f"<span style='color: #6c757d; font-weight: bold;'>{resultado_cover}</span>"
        return f"<li><span class='score-value'>Goles:</span> El partido tuvo <strong>{total_goles} goles</strong>, por lo que la línea actual habría resultado {cover_html}.</li>"
    except (ValueError, TypeError):
        return "<li><span class='score-value'>Goles:</span> No se pudo procesar el resultado del precedente.</li>"

def generar_analisis_completo_mercado(main_odds, h2h_data, home_name, away_name):
    ah_actual_str = format_ah_as_decimal_string_of(main_odds.get('ah_linea_raw', '-'))
    ah_actual_num = parse_ah_to_number_of(ah_actual_str)
    goles_actual_num = parse_ah_to_number_of(main_odds.get('goals_linea_raw', '-'))
    if ah_actual_num is None or goles_actual_num is None: return ""
    favorito_name, favorito_html = "Ninguno (línea en 0)", "Ninguno (línea en 0)"
    if ah_actual_num < 0:
        favorito_name, favorito_html = away_name, f"<span class='away-color'>{away_name}</span>"
    elif ah_actual_num > 0:
        favorito_name, favorito_html = home_name, f"<span class='home-color'>{home_name}</span>"
    titulo_html = f"<p style='margin-bottom: 12px;'><strong>📊 Análisis de Mercado vs. Histórico H2H</strong><br><span style='font-style: italic; font-size: 0.9em;'>Líneas actuales: AH {ah_actual_str} / Goles {goles_actual_num} | Favorito: {favorito_html}</span></p>"
    precedente_estadio = {
        'res_raw': h2h_data.get('res1_raw'), 'ah_raw': h2h_data.get('ah1'),
        'home': home_name, 'away': away_name, 'match_id': h2h_data.get('match1_id')
    }
    sintesis_ah_estadio = _analizar_precedente_handicap(precedente_estadio, ah_actual_num, favorito_name, home_name)
    sintesis_goles_estadio = _analizar_precedente_goles(precedente_estadio, goles_actual_num)
    analisis_estadio_html = (
        f"<div style='margin-bottom: 10px;'>"
        f"  <strong style='font-size: 1.05em;'>🏟️ Análisis del Precedente en Este Estadio</strong>"
        f"  <ul style='margin: 5px 0 0 20px; padding-left: 0;'>{sintesis_ah_estadio}{sintesis_goles_estadio}</ul>"
        f"</div>"
    )
    precedente_general_id = h2h_data.get('match6_id')
    if precedente_estadio['match_id'] and precedente_general_id and precedente_estadio['match_id'] == precedente_general_id:
        analisis_general_html = (
            "<div style='margin-top: 10px;'>"
            "  <strong>✈️ Análisis del H2H General Más Reciente</strong>"
            "  <p style='margin: 5px 0 0 20px; font-style: italic; font-size: 0.9em;'>"
            "    El precedente es el mismo partido analizado arriba."
            "  </p>"
            "</div>"
        )
    else:
        precedente_general = {
            'res_raw': h2h_data.get('res6_raw'),
            'ah_raw': h2h_data.get('ah6'),
            'home': h2h_data.get('h2h_gen_home'),
            'away': h2h_data.get('h2h_gen_away'),
            'match_id': precedente_general_id
        }
        sintesis_ah_general = _analizar_precedente_handicap(precedente_general, ah_actual_num, favorito_name, home_name)
        sintesis_goles_general = _analizar_precedente_goles(precedente_general, goles_actual_num)
        analisis_general_html = (
            f"<div>"
            f"  <strong style='font-size: 1.05em;'>✈️ Análisis del H2H General Más Reciente</strong>"
            f"  <ul style='margin: 5px 0 0 20px; padding-left: 0;'>{sintesis_ah_general}{sintesis_goles_general}</ul>"
            f"</div>"
        )
    return f'''
    <div style="border-left: 4px solid #1E90FF; padding: 12px 15px; margin-top: 15px; background-color: #f0f2f6; border-radius: 5px; font-size: 0.95em;">
        {titulo_html}
        {analisis_estadio_html}
        {analisis_general_html}
    </div>
    '''

def _analizar_precedente_mercado_simplificado(precedente_data, ah_actual_num, favorito_actual_name, main_home_team_name):
    res_raw = precedente_data.get('res_raw')
    if not res_raw or res_raw == '?-?':
        return None

    ah_raw = precedente_data.get('ah_raw')
    home_team_precedente = precedente_data.get('home')
    away_team_precedente = precedente_data.get('away')

    line_movement_str = 'N/A'
    if ah_actual_num is not None:
        formatted_actual = format_ah_as_decimal_string_of(str(ah_actual_num))
        if ah_raw and ah_raw != '-':
            formatted_historico = format_ah_as_decimal_string_of(ah_raw)
            line_movement_str = f"{formatted_historico} -> {formatted_actual}"
        else:
            line_movement_str = f"Sin dato -> {formatted_actual}"
    elif ah_raw and ah_raw != '-':
        line_movement_str = format_ah_as_decimal_string_of(ah_raw)

    cover_status = 'NEUTRO'
    if ah_actual_num is not None:
        cover_status, _ = check_handicap_cover(
            res_raw,
            ah_actual_num,
            favorito_actual_name,
            home_team_precedente,
            away_team_precedente,
            main_home_team_name
        )

    cover_styles = {
        'CUBIERTO': "<span style='color: green; font-weight: bold;'>CUBIERTO</span>",
        'NO CUBIERTO': "<span style='color: red; font-weight: bold;'>NO CUBIERTO</span>",
        'NULO': "<span style='color: #000000; font-weight: bold;'>NULO</span>",
        'NEUTRO': "<span style='color: #6c757d; font-weight: bold;'>NEUTRO</span>"
    }
    cover_html = cover_styles.get(
        cover_status,
        f"<span style='color: #6c757d; font-weight: bold;'>{cover_status.upper()}</span>"
    )

    return {
        'resultado': res_raw.replace('-', ':'),
        'movimiento_linea': line_movement_str,
        'cobertura': cover_html
    }

def generar_analisis_mercado_simplificado(main_odds, h2h_data, home_name, away_name):
    ah_actual_str = format_ah_as_decimal_string_of(main_odds.get('ah_linea_raw', '-'))
    ah_actual_num = parse_ah_to_number_of(ah_actual_str)
    if ah_actual_num is None:
        return ""

    favorito_name = "Ninguno (línea en 0)"
    if ah_actual_num < 0:
        favorito_name = away_name
    elif ah_actual_num > 0:
        favorito_name = home_name

    # Analizar precedente en el mismo estadio
    precedente_estadio_data = {
        'res_raw': h2h_data.get('res1_raw'), 'ah_raw': h2h_data.get('ah1'),
        'home': home_name, 'away': away_name, 'match_id': h2h_data.get('match1_id')
    }
    analisis_estadio = _analizar_precedente_mercado_simplificado(precedente_estadio_data, ah_actual_num, favorito_name, home_name)

    # Analizar precedente general más reciente
    precedente_general_data = {
        'res_raw': h2h_data.get('res6_raw'),
        'ah_raw': h2h_data.get('ah6'),
        'home': h2h_data.get('h2h_gen_home'),
        'away': h2h_data.get('h2h_gen_away'),
        'match_id': h2h_data.get('match6_id')
    }
    analisis_general = _analizar_precedente_mercado_simplificado(precedente_general_data, ah_actual_num, favorito_name, home_name)

    # Construir HTML
    html = '<div class="card"><div class="card-body">'
    html += '<h6 class="card-title">📊 Análisis Mercado vs. H2H</h6>'

    if analisis_estadio:
        html += f"""
            <div class="mb-2">
                <strong>Precedente (este estadio):</strong>
                <div>Resultado: <span class="score-value">{analisis_estadio['resultado']}</span></div>
                <div>Mov. Línea: <span class="ah-value">{analisis_estadio['movimiento_linea']}</span></div>
                <div>Cobertura AH: {analisis_estadio['cobertura']}</div>
            </div>
        """
    else:
        html += "<p>No hay precedente H2H en este estadio.</p>"

    # Evitar duplicados si el precedente general es el mismo
    match1_id = h2h_data.get('match1_id')
    match6_id = h2h_data.get('match6_id')

    if match6_id:
        if analisis_general:
            if match1_id and match1_id != match6_id:
                html += "<hr>"
            html += f"""
                <div class="mt-2">
                    <strong>Precedente (H2H mǭs reciente):</strong>
                    <div>Resultado: <span class="score-value">{analisis_general['resultado']}</span></div>
                    <div>Mov. L��nea: <span class="ah-value">{analisis_general['movimiento_linea']}</span></div>
                    <div>Cobertura AH: {analisis_general['cobertura']}</div>
                </div>
            """
        elif match1_id and match1_id != match6_id:
            html += "<hr><p>No hay precedente H2H general.</p>"

    html += '</div></div>'
    return html

def get_match_details_from_row_of(row_element, score_class_selector='score', source_table_type='h2h'):
    try:
        cells = row_element.find_all('td')
        home_idx, score_idx, away_idx, ah_idx = 2, 3, 4, 11
        if len(cells) <= ah_idx: return None
        date_span = cells[1].find('span', attrs={'name': 'timeData'})
        date_txt = date_span.get_text(strip=True) if date_span else ''
        def get_cell_txt(idx):
            a = cells[idx].find('a')
            return a.get_text(strip=True) if a else cells[idx].get_text(strip=True)
        home, away = get_cell_txt(home_idx), get_cell_txt(away_idx)
        if not home or not away: return None
        score_cell = cells[score_idx]
        score_span = score_cell.find('span', class_=lambda c: isinstance(c, str) and score_class_selector in c)
        score_raw_text = (score_span.get_text(strip=True) if score_span else score_cell.get_text(strip=True)) or ''
        m = re.search(r'(\d+)\s*-\s*(\d+)', score_raw_text)
        score_raw, score_fmt = (f"{m.group(1)}-{m.group(2)}", f"{m.group(1)}:{m.group(2)}") if m else ('?-?', '?:?')
        ah_cell = cells[ah_idx]
        ah_line_raw = (ah_cell.get('data-o') or ah_cell.text).strip()
        ah_line_fmt = format_ah_as_decimal_string_of(ah_line_raw) if ah_line_raw not in ['', '-'] else '-'
        return {
            'date': date_txt, 'home': home, 'away': away, 'score': score_fmt,
            'score_raw': score_raw, 'ahLine': ah_line_fmt, 'ahLine_raw': ah_line_raw or '-',
            'matchIndex': row_element.get('index'), 'vs': row_element.get('vs'),
            'league_id_hist': row_element.get('name')
        }
    except Exception:
        return None

def _colorear_stats(val1_str, val2_str):
    """Compara dos valores de estadísticas y devuelve strings con formato HTML para colorearlos."""
    try:
        val1 = int(val1_str)
        val2 = int(val2_str)
        if val1 > val2:
            return f'<span style="color: green; font-weight: bold;">{val1}</span>', f'<span style="color: red;">{val2}</span>'
        elif val2 > val1:
            return f'<span style="color: red;">{val1}</span>', f'<span style="color: green; font-weight: bold;">{val2}</span>'
        else:
            # Si son iguales, no se aplica color
            return val1_str, val2_str
    except (ValueError, TypeError):
        # Si no se pueden convertir a números (ej. texto), devolver los originales
        return val1_str, val2_str

def get_match_progression_stats_data(match_id: str) -> pd.DataFrame | None:
    if not match_id or not match_id.isdigit(): return None
    url = f"{BASE_URL_OF}/match/live-{match_id}"
    try:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/116.0.0.0 Safari/537.36"})
        response = session.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Definir el orden específico de las estadísticas (sin Yellow Cards)
        stat_order = ["Corners", "Shots", "Shots on Goal", "Attacks", "Dangerous Attacks", "Red Cards"]
        stat_titles = {stat: "-" for stat in stat_order}
        
        team_tech_div = soup.find('div', id='teamTechDiv_detail')
        if team_tech_div and (stat_list := team_tech_div.find('ul', class_='stat')):
            for li in stat_list.find_all('li'):
                if (title_span := li.find('span', class_='stat-title')) and (stat_title := title_span.get_text(strip=True)) in stat_titles:
                    values = [v.get_text(strip=True) for v in li.find_all('span', class_='stat-c')]
                    if len(values) == 2:
                        home_val, away_val = _colorear_stats(values[0], values[1])
                        stat_titles[stat_title] = {"Home": home_val, "Away": away_val}
        
        # Si no encontramos las tarjetas rojas en la sección principal, las buscamos en la sección de eventos
        if stat_titles["Red Cards"] == "-":
            red_cards = {"Home": 0, "Away": 0}
            events_table = soup.find('table', id='eventsTable')
            if events_table:
                # Buscar imágenes de tarjetas rojas
                red_card_images = events_table.find_all('img', alt='Red Card')
                for img in red_card_images:
                    # Determinar si es para el equipo local o visitante basado en la estructura de la tabla
                    parent_td = img.find_parent('td')
                    if parent_td:
                        # Si el td tiene style="text-align: right;", es para el equipo local
                        if "text-align: right;" in parent_td.get('style', ''):
                            red_cards["Home"] += 1
                        # Si el td tiene style="text-align: left;", es para el equipo visitante
                        elif "text-align: left;" in parent_td.get('style', ''):
                            red_cards["Away"] += 1
            stat_titles["Red Cards"] = red_cards
            
        # Eliminamos la extracción de tarjetas amarillas según solicitud
        # Pasamos directamente a procesar Red Cards
            
        # Crear las filas respetando el orden definido
        table_rows = []
        for stat_name in stat_order:
            vals = stat_titles[stat_name]
            if isinstance(vals, dict):
                table_rows.append({
                    "Estadistica_EN": stat_name,
                    "Casa": vals.get('Home', '-'),
                    "Fuera": vals.get('Away', '-')
                })
        
        df = pd.DataFrame(table_rows)
        return df.set_index("Estadistica_EN") if not df.empty else df
    except requests.RequestException:
        return None

def get_rival_a_for_original_h2h_of(soup, league_id=None):
    if not soup or not (table := soup.find("table", id="table_v1")): return None, None, None
    for row in table.find_all("tr", id=re.compile(r"tr1_\d+")):
        if league_id and row.get("name") != str(league_id):
            continue
        if row.get("vs") == "1" and (key_id := row.get("index")):
            onclicks = row.find_all("a", onclick=True)
            if len(onclicks) > 1 and (rival_tag := onclicks[1]) and (rival_id_match := re.search(r"team\((\d+)\)", rival_tag.get("onclick", ""))):
                return key_id, rival_id_match.group(1), rival_tag.text.strip()
    return None, None, None

def get_rival_b_for_original_h2h_of(soup, league_id=None):
    if not soup or not (table := soup.find("table", id="table_v2")): return None, None, None
    for row in table.find_all("tr", id=re.compile(r"tr2_\d+")):
        if league_id and row.get("name") != str(league_id):
            continue
        if row.get("vs") == "1" and (key_id := row.get("index")):
            onclicks = row.find_all("a", onclick=True)
            if len(onclicks) > 0 and (rival_tag := onclicks[0]) and (rival_id_match := re.search(r"team\((\d+)\)", rival_tag.get("onclick", ""))):
                return key_id, rival_id_match.group(1), rival_tag.text.strip()
    return None, None, None

def get_h2h_details_for_original_logic_of(
    key_match_id,
    rival_a_id,
    rival_b_id,
    rival_a_name="Rival A",
    rival_b_name="Rival B",
):
    if not all([key_match_id, rival_a_id, rival_b_id]):
        return {"status": "error", "resultado": "N/A (Datos incompletos para H2H)"}

    soup = _load_h2h_column_soup(str(key_match_id))
    if soup is None:
        return {"status": "error", "resultado": "N/A (No se pudo cargar H2H col3)"}

    table = soup.find("table", id="table_v2")
    if not table:
        return {"status": "error", "resultado": "N/A (Tabla H2H Col3 no encontrada)"}

    for row in table.find_all("tr", id=re.compile(r"tr2_\d+")):
        links = row.find_all("a", onclick=True)
        if len(links) < 2: continue
        h_id_m = re.search(r"team\((\d+)\)", links[0].get("onclick", "")); a_id_m = re.search(r"team\((\d+)\)", links[1].get("onclick", ""))
        if not (h_id_m and a_id_m): continue
        h_id, a_id = h_id_m.group(1), a_id_m.group(1)
        if {h_id, a_id} == {str(rival_a_id), str(rival_b_id)}:
            if not (score_span := row.find("span", class_="fscore_2")) or "-" not in score_span.text: continue
            score = score_span.text.strip().split("(")[0].strip()
            g_h, g_a = score.split("-", 1)
            tds = row.find_all("td")
            handicap_raw = "N/A"
            if len(tds) > 11:
                cell = tds[11]
                handicap_raw = (cell.get("data-o") or cell.text).strip() or "N/A"
            # Intentar extraer la fecha de la segunda columna (si existe)
            date_txt = None
            try:
                if len(tds) > 1:
                    date_span = tds[1].find('span', attrs={'name': 'timeData'})
                    date_txt = date_span.get_text(strip=True) if date_span else None
            except Exception:
                date_txt = None
            return {
                "status": "found", "goles_home": g_h.strip(), "goles_away": g_a.strip(),
                "handicap_line_raw": handicap_raw, "match_id": row.get('index'),
                "h2h_home_team_name": links[0].text.strip(), "h2h_away_team_name": links[1].text.strip(),
                "date": date_txt
            }
    return {"status": "not_found", "resultado": f"H2H directo no encontrado para {rival_a_name} vs {rival_b_name}."}

def get_team_league_info_from_script_of(soup):
    script_tag = soup.find("script", string=re.compile(r"var _matchInfo = "))
    if not (script_tag and script_tag.string): return (None,) * 3 + ("N/A",) * 3
    content = script_tag.string
    def find_val(pattern):
        match = re.search(pattern, content)
        return match.group(1).replace("'", "") if match else None
    home_id = find_val(r"hId:\s*parseInt\('(\d+)'\)")
    away_id = find_val(r"gId:\s*parseInt\('(\d+)'\)")
    league_id = find_val(r"sclassId:\s*parseInt\('(\d+)'\)")
    home_name = find_val(r"hName:\s*'([^']*)'") or "N/A"
    away_name = find_val(r"gName:\s*'([^']*)'") or "N/A"
    league_name = find_val(r"lName:\s*'([^']*)'") or "N/A"
    return home_id, away_id, league_id, home_name, away_name, league_name

def get_match_datetime_from_script_of(soup):
    """
    Extrae fecha/hora del partido desde el script _matchInfo si está disponible.
    Devuelve dict con 'match_date', 'match_time' y 'match_datetime'.
    """
    result = {"match_date": None, "match_time": None, "match_datetime": None}
    try:
        script_tag = soup.find("script", string=re.compile(r"var _matchInfo = "))
        if not (script_tag and script_tag.string):
            return result
        content = script_tag.string

        def find_val(pattern):
            m = re.search(pattern, content)
            return m.group(1) if m else None

        # Posibles fuentes en el script
        match_time_txt = find_val(r"matchTime:\s*'([^']+)'")  # ej: 9/9/2025 5:00:00 PM
        start_date = find_val(r"startDate:\s*'([^']+)'")      # ej: 2025-09-09
        door_time = find_val(r"doorTime:\s*'([^']+)'")        # ej: 09:00:00.000+08:00

        normalized_date = None
        normalized_time = None

        # 1) Intentar usar matchTime completo si viene (m/d/Y h:m:s AM/PM)
        if match_time_txt:
            try:
                # Aceptar tanto m/d como mm/dd
                from datetime import datetime
                dt = None
                for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
                    try:
                        dt = datetime.strptime(match_time_txt, fmt)
                        break
                    except Exception:
                        continue
                if dt:
                    normalized_date = dt.strftime("%Y-%m-%d")
                    normalized_time = dt.strftime("%H:%M")
            except Exception:
                pass

        # 2) Si no hay matchTime, combinar startDate + doorTime
        if not normalized_date and start_date:
            normalized_date = start_date
            if door_time:
                # Tomar HH:MM de door_time y omitir zona
                m = re.match(r"(\d{2}):(\d{2})", door_time)
                if m:
                    normalized_time = f"{m.group(1)}:{m.group(2)}"

        if normalized_date:
            result["match_date"] = normalized_date
            result["match_time"] = normalized_time
            result["match_datetime"] = f"{normalized_date} {normalized_time}".strip()
    except Exception:
        pass
    return result

def _parse_date_ddmmyyyy(d: str) -> tuple:
    m = re.search(r'(\d{2})-(\d{2})-(\d{4})', d or '')
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (1900, 1, 1)

def extract_last_match_in_league_of(soup, table_id, team_name, league_id, is_home_game):
    if not soup or not (table := soup.find("table", id=table_id)): return None
    candidate_matches = []
    score_selector = 'fscore_1' if is_home_game else 'fscore_2'
    for row in table.find_all("tr", id=re.compile(rf"tr{table_id[-1]}_\d+")):
        if not (details := get_match_details_from_row_of(row, score_class_selector=score_selector, source_table_type='hist')):
            continue
        if league_id and details.get("league_id_hist") != str(league_id):
            continue
        is_team_home = team_name.lower() in details.get('home', '').lower()
        is_team_away = team_name.lower() in details.get('away', '').lower()
        if (is_home_game and is_team_home) or (not is_home_game and is_team_away):
            candidate_matches.append(details)
    if not candidate_matches: return None
    candidate_matches.sort(key=lambda x: _parse_date_ddmmyyyy(x.get('date', '')), reverse=True)
    last_match = candidate_matches[0]
    return {
        "date": last_match.get('date', 'N/A'), "home_team": last_match.get('home'),
        "away_team": last_match.get('away'), "score": last_match.get('score_raw', 'N/A').replace('-', ':'),
        "handicap_line_raw": last_match.get('ahLine_raw', 'N/A'), "match_id": last_match.get('matchIndex')
    }

def extract_bet365_initial_odds_of(soup):
    odds_info = {
        "ah_home_cuota": "N/A", "ah_linea_raw": "N/A", "ah_away_cuota": "N/A",
        "goals_over_cuota": "N/A", "goals_linea_raw": "N/A", "goals_under_cuota": "N/A"
    }
    if not soup: return odds_info
    bet365_row = soup.select_one("tr#tr_o_1_8[name='earlyOdds'], tr#tr_o_1_31[name='earlyOdds']")
    if not bet365_row: return odds_info
    tds = bet365_row.find_all("td")
    if len(tds) >= 11:
        odds_info["ah_home_cuota"] = tds[2].get("data-o", tds[2].text).strip()
        odds_info["ah_linea_raw"] = tds[3].get("data-o", tds[3].text).strip()
        odds_info["ah_away_cuota"] = tds[4].get("data-o", tds[4].text).strip()
        odds_info["goals_over_cuota"] = tds[8].get("data-o", tds[8].text).strip()
        odds_info["goals_linea_raw"] = tds[9].get("data-o", tds[9].text).strip()
        odds_info["goals_under_cuota"] = tds[10].get("data-o", tds[10].text).strip()
    return odds_info

def extract_standings_data_from_h2h_page_of(soup, team_name):
    data = {"name": team_name, "ranking": "N/A", "total_pj": "N/A", "total_v": "N/A",
            "total_e": "N/A", "total_d": "N/A", "total_gf": "N/A", "total_gc": "N/A",
            "specific_pj": "N/A", "specific_v": "N/A", "specific_e": "N/A",
            "specific_d": "N/A", "specific_gf": "N/A", "specific_gc": "N/A",
            "specific_type": "N/A"}
    if not soup or not team_name:
        return data
    standings_section = soup.find("div", id="porletP4")
    if not standings_section:
        return data
    team_table_soup = None
    is_home_table = False
    home_div = standings_section.find("div", class_="home-div")
    if home_div and team_name.lower() in home_div.get_text(strip=True).lower():
        team_table_soup = home_div.find("table", class_="team-table-home")
        is_home_table = True
        data["specific_type"] = "Est. como Local (en Liga)"
    else:
        guest_div = standings_section.find("div", class_="guest-div")
        if guest_div and team_name.lower() in guest_div.get_text(strip=True).lower():
            team_table_soup = guest_div.find("table", class_="team-table-guest")
            is_home_table = False
            data["specific_type"] = "Est. como Visitante (en Liga)"
    if not team_table_soup:
        return data
    header_link = team_table_soup.find("a")
    if header_link:
        full_text = header_link.get_text(separator=" ", strip=True)
        rank_match = re.search(r'\[.*?-(\d+)\]', full_text)
        if rank_match:
            data["ranking"] = rank_match.group(1)
    all_rows = team_table_soup.find_all("tr", align="center")
    is_ft_section = False
    for row in all_rows:
        header_cell = row.find("th")
        if header_cell:
            header_text = header_cell.get_text(strip=True)
            if "FT" in header_text:
                is_ft_section = True
            elif "HT" in header_text:
                is_ft_section = False
            continue
        if is_ft_section and len(cells := row.find_all("td")) >= 7:
            row_type_element = cells[0].find("span") or cells[0]
            row_type = row_type_element.get_text(strip=True)
            stats = [cell.get_text(strip=True) for cell in cells[1:7]]
            pj, v, e, d, gf, gc = stats
            if row_type == "Total":
                data.update({"total_pj": pj, "total_v": v, "total_e": e,
                            "total_d": d, "total_gf": gf, "total_gc": gc})
            specific_row_needed = "Home" if is_home_table else "Away"
            if row_type == specific_row_needed:
                data.update({"specific_pj": pj, "specific_v": v, "specific_e": e,
                            "specific_d": d, "specific_gf": gf, "specific_gc": gc})
    return data

def extract_over_under_stats_from_div_of(soup, team_type: str):
    default_stats = {"over_pct": 0, "under_pct": 0, "push_pct": 0, "total": 0}
    if not soup:
        return default_stats
    table_id = "table_v1" if team_type == 'home' else "table_v2"
    table = soup.find("table", id=table_id)
    if not table:
        return default_stats
    y_bar = table.find("ul", class_="y-bar")
    if not y_bar:
        return default_stats
    ou_group = None
    for group in y_bar.find_all("li", class_="group"):
        if "Over/Under Odds" in group.get_text():
            ou_group = group
            break
    if not ou_group:
        return default_stats
    try:
        total_text = ou_group.find("div", class_="tit").find("span").get_text(strip=True)
        total_match = re.search(r'\((\d+)\s*games\)', total_text)
        total = int(total_match.group(1)) if total_match else 0
        values = ou_group.find_all("span", class_="value")
        if len(values) == 3:
            over_pct_text = values[0].get_text(strip=True).replace('%', '')
            push_pct_text = values[1].get_text(strip=True).replace('%', '')
            under_pct_text = values[2].get_text(strip=True).replace('%', '')
            return {"over_pct": float(over_pct_text), "under_pct": float(under_pct_text), "push_pct": float(push_pct_text), "total": total}
    except (ValueError, TypeError, AttributeError):
        return default_stats
    return default_stats

def extract_h2h_data_of(soup, home_name, away_name, league_id=None):
    results = {'ah1': '-', 'res1': '?:?', 'res1_raw': '?-?', 'match1_id': None, 'ah6': '-', 'res6': '?:?', 'res6_raw': '?-?', 'match6_id': None, 'h2h_gen_home': "Local (H2H Gen)", 'h2h_gen_away': "Visitante (H2H Gen)"}
    if not soup or not home_name or not away_name or not (h2h_table := soup.find("table", id="table_v3")): return results
    all_matches = []
    for r in h2h_table.find_all("tr", id=re.compile(r"tr3_\d+")):
        if (d := get_match_details_from_row_of(r, score_class_selector='fscore_3', source_table_type='h2h')):
            if not league_id or (d.get('league_id_hist') and d.get('league_id_hist') == str(league_id)):
                all_matches.append(d)
    if not all_matches: return results
    all_matches.sort(key=lambda x: _parse_date_ddmmyyyy(x.get('date', '')), reverse=True)
    most_recent = all_matches[0]
    results.update({'ah6': most_recent.get('ahLine', '-'), 'res6': most_recent.get('score', '?:?'), 'res6_raw': most_recent.get('score_raw', '?-?'), 'match6_id': most_recent.get('matchIndex'), 'h2h_gen_home': most_recent.get('home'), 'h2h_gen_away': most_recent.get('away')})
    for d in all_matches:
        if d['home'].lower() == home_name.lower() and d['away'].lower() == away_name.lower():
            results.update({'ah1': d.get('ahLine', '-'), 'res1': d.get('score', '?:?'), 'res1_raw': d.get('score_raw', '?-?'), 'match1_id': d.get('matchIndex')})
            break
    return results

def extract_comparative_match_of(soup, table_id, main_team, opponent, league_id, is_home_table):
    if not opponent or opponent == "N/A" or not main_team or not (table := soup.find("table", id=table_id)): return None
    score_selector = 'fscore_1' if is_home_table else 'fscore_2'
    for row in table.find_all("tr", id=re.compile(rf"tr{table_id[-1]}_\d+")):
        if not (details := get_match_details_from_row_of(row, score_class_selector=score_selector, source_table_type='hist')): continue
        if league_id and details.get('league_id_hist') and details.get('league_id_hist') != str(league_id): continue
        h, a = details.get('home','').lower(), details.get('away','').lower()
        main, opp = main_team.lower(), opponent.lower()
        if (main == h and opp == a) or (main == a and opp == h):
            return {"score": details.get('score', '?:?'), "ah_line": details.get('ahLine', '-'), "localia": 'H' if main == h else 'A', "home_team": details.get('home'), "away_team": details.get('away'), "match_id": details.get('matchIndex')}
    return None

def extract_indirect_comparison_data(soup):
    """
    Extrae los datos de los dos paneles de Comparativas Indirectas.
    """
    data = {"comp1": None, "comp2": None}
    comparativas_divs = soup.select("div.football-history-list > div.content") # Asumiendo una estructura de selectores; ajustar si es necesario.

    if len(comparativas_divs) < 2:
        return data

    def parse_comparison_box(box_soup):
        try:
            # Título: "Yangon United FC U21 vs. Últ. Rival de Dagon FC U21"
            title = box_soup.find("div", class_="title").get_text(strip=True)
            main_team_name = title.split(' vs. ')[0]

            # Resultado: "0 : 1"
            res_text = box_soup.find(string=re.compile(r"Res\s*:")).find_next("span").get_text(strip=True)
            res_raw = res_text.replace(' ', '').replace(':', '-')

            # Hándicap Asiático: "AH: 4"
            ah_text = box_soup.find(string=re.compile(r"AH\s*:")).find_next("span").get_text(strip=True)
            ah_num = parse_ah_to_number_of(ah_text)

            # Localía: "H" o "A"
            localia_text = box_soup.find(string=re.compile(r"Localía de")).find_next("span").get_text(strip=True)

            # Estadísticas
            stats = {}
            stats_table = box_soup.find("table") # Asumiendo que las stats están en una tabla
            rows = stats_table.find_all("tr")
            
            # Ejemplo de extracción de estadísticas (ajustar a la estructura real del HTML)
            # Esto es un placeholder, el código real podría necesitar ser más robusto
            stats['tiros_casa'] = rows[0].find_all('td')[0].text.strip()
            stats['tiros_fuera'] = rows[0].find_all('td')[2].text.strip()
            stats['tiros_puerta_casa'] = rows[1].find_all('td')[0].text.strip()
            stats['tiros_puerta_fuera'] = rows[1].find_all('td')[2].text.strip()
            stats['ataques_casa'] = rows[2].find_all('td')[0].text.strip()
            stats['ataques_fuera'] = rows[2].find_all('td')[2].text.strip()
            stats['ataques_peligrosos_casa'] = rows[3].find_all('td')[0].text.strip()
            stats['ataques_peligrosos_fuera'] = rows[3].find_all('td')[2].text.strip()

            return {
                "main_team": main_team_name,
                "resultado": res_text,
                "resultado_raw": res_raw,
                "ah_raw": ah_text,
                "ah_num": ah_num,
                "localia": localia_text,
                "stats": stats
            }
        except Exception:
            return None

    data["comp1"] = parse_comparison_box(comparativas_divs[0])
    data["comp2"] = parse_comparison_box(comparativas_divs[1])

    return data

# --- FUNCIÓN PRINCIPAL DE EXTRACCIÓN ---

def obtener_datos_completos_partido(match_id: str):
    """
    Función principal que orquesta todo el scraping y análisis para un ID de partido.
    Devuelve un diccionario con todos los datos necesarios para la plantilla HTML.
    """
    if not match_id or not match_id.isdigit():
        return {"error": "ID de partido inválido."}

    datos = {"match_id": match_id}

    try:
        soup_completo = _load_main_match_soup(match_id)
        if soup_completo is None:
            return {"error": "No se pudo cargar la página principal del partido."}
        datos['final_score'] = extract_final_score_of(soup_completo)

        # --- Extracción de Datos Primarios ---
        home_id, away_id, league_id, home_name, away_name, league_name = get_team_league_info_from_script_of(soup_completo)
        # Fecha/hora del partido (si está en el script)
        dt_info = get_match_datetime_from_script_of(soup_completo)
        datos.update({
            "home_name": home_name,
            "away_name": away_name,
            "league_name": league_name,
            "match_date": dt_info.get("match_date"),
            "match_time": dt_info.get("match_time"),
            "match_datetime": dt_info.get("match_datetime"),
        })

        # --- Recopilación de todos los datos en paralelo (donde sea posible) ---
        with ThreadPoolExecutor(max_workers=8) as executor:
            # Tareas síncronas (dependen del soup_completo)
            future_home_standings = executor.submit(extract_standings_data_from_h2h_page_of, soup_completo, home_name)
            future_away_standings = executor.submit(extract_standings_data_from_h2h_page_of, soup_completo, away_name)
            future_home_ou = executor.submit(extract_over_under_stats_from_div_of, soup_completo, 'home')
            future_away_ou = executor.submit(extract_over_under_stats_from_div_of, soup_completo, 'away')
            future_main_odds = executor.submit(extract_bet365_initial_odds_of, soup_completo)
            future_h2h_data = executor.submit(extract_h2h_data_of, soup_completo, home_name, away_name, None)
            future_last_home = executor.submit(extract_last_match_in_league_of, soup_completo, "table_v1", home_name, league_id, True)
            future_last_away = executor.submit(extract_last_match_in_league_of, soup_completo, "table_v2", away_name, league_id, False)
            
            # Tarea H2H Col3 (requiere nueva carga de página)
            key_id_a, rival_a_id, rival_a_name = get_rival_a_for_original_h2h_of(soup_completo, league_id)
            _, rival_b_id, rival_b_name = get_rival_b_for_original_h2h_of(soup_completo, league_id)
            future_h2h_col3 = executor.submit(
                get_h2h_details_for_original_logic_of,
                key_id_a,
                rival_a_id,
                rival_b_id,
                rival_a_name,
                rival_b_name,
            )
            
            # Obtener resultados
            datos["home_standings"] = future_home_standings.result()
            datos["away_standings"] = future_away_standings.result()
            datos["home_ou_stats"] = future_home_ou.result()
            datos["away_ou_stats"] = future_away_ou.result()
            main_match_odds_data = future_main_odds.result()
            h2h_data = future_h2h_data.result()
            datos["main_match_odds_data"] = main_match_odds_data
            datos["h2h_data"] = h2h_data
            last_home_match = future_last_home.result()
            last_away_match = future_last_away.result()
            details_h2h_col3 = future_h2h_col3.result()

            # --- Comparativas (dependen de los resultados anteriores) ---
            comp_L_vs_UV_A = extract_comparative_match_of(soup_completo, "table_v1", home_name, (last_away_match or {}).get('home_team'), league_id, True)
            comp_V_vs_UL_H = extract_comparative_match_of(soup_completo, "table_v2", away_name, (last_home_match or {}).get('away_team'), league_id, False)

            # --- Generar Análisis de Mercado ---
            datos["market_analysis_html"] = generar_analisis_completo_mercado(main_match_odds_data, h2h_data, home_name, away_name)

            # --- Estructurar datos para la plantilla ---
            datos["main_match_odds"] = {
                "ah_linea": format_ah_as_decimal_string_of(main_match_odds_data.get('ah_linea_raw', '?')),
                "goals_linea": format_ah_as_decimal_string_of(main_match_odds_data.get('goals_linea_raw', '?'))
            }
            
            # Recopilar todos los IDs de partidos históricos para obtener sus estadísticas de progresión
            match_ids_to_fetch_stats = {
                'last_home': (last_home_match or {}).get('match_id'),
                'last_away': (last_away_match or {}).get('match_id'),
                'h2h_col3': (details_h2h_col3 or {}).get('match_id'),
                'comp_L_vs_UV_A': (comp_L_vs_UV_A or {}).get('match_id'),
                'comp_V_vs_UL_H': (comp_V_vs_UL_H or {}).get('match_id'),
                'h2h_stadium': h2h_data.get('match1_id'),
                'h2h_general': h2h_data.get('match6_id')
            }
            
            # Obtener estadísticas de progresión en paralelo
            stats_futures = {key: executor.submit(get_match_progression_stats_data, match_id)
                             for key, match_id in match_ids_to_fetch_stats.items() if match_id}
                             
            stats_results = {key: future.result() for key, future in stats_futures.items()}

            # Empaquetar todo en el diccionario de datos final
            datos['last_home_match'] = {'details': last_home_match, 'stats': stats_results.get('last_home')}
            datos['last_away_match'] = {'details': last_away_match, 'stats': stats_results.get('last_away')}
            datos['h2h_col3'] = {'details': details_h2h_col3, 'stats': stats_results.get('h2h_col3')}
            datos['comp_L_vs_UV_A'] = {'details': comp_L_vs_UV_A, 'stats': stats_results.get('comp_L_vs_UV_A')}
            datos['comp_V_vs_UL_H'] = {'details': comp_V_vs_UL_H, 'stats': stats_results.get('comp_V_vs_UL_H')}
            datos['h2h_stadium'] = {'details': h2h_data, 'stats': stats_results.get('h2h_stadium')}
            datos['h2h_general'] = {'details': h2h_data, 'stats': stats_results.get('h2h_general')}

            # --- ANÁLISIS AVANZADO DE COMPARATIVAS INDIRECTAS ---
            # Extraer los datos de las comparativas indirectas
            indirect_comparison_data = extract_indirect_comparison_data(soup_completo)
            
            # Generar la nota de análisis
            datos["advanced_analysis_html"] = generar_analisis_comparativas_indirectas(indirect_comparison_data)
            
            # --- ANÁLISIS RECIENTE CON HANDICAP ---
            # Obtener la línea de handicap actual
            current_ah_line = parse_ah_to_number_of(main_match_odds_data.get('ah_linea_raw', '0'))
            
            # Analizar rendimiento reciente con handicap para equipo local
            rendimiento_local = analizar_rendimiento_reciente_con_handicap(soup_completo, home_name, True)
            datos["rendimiento_local_handicap"] = rendimiento_local
            
            # Analizar rendimiento reciente con handicap para equipo visitante
            rendimiento_visitante = analizar_rendimiento_reciente_con_handicap(soup_completo, away_name, False)
            datos["rendimiento_visitante_handicap"] = rendimiento_visitante
            
            # Comparar líneas de handicap recientes con la línea actual
            if current_ah_line is not None:
                comparacion_local = comparar_lineas_handicap_recientes(soup_completo, home_name, current_ah_line, True)
                datos["comparacion_lineas_local"] = comparacion_local
                
                comparacion_visitante = comparar_lineas_handicap_recientes(soup_completo, away_name, current_ah_line, False)
                datos["comparacion_lineas_visitante"] = comparacion_visitante
            
            # --- ANÁLISIS DE RIVALES COMUNES ---
            rivales_comunes = analizar_rivales_comunes(soup_completo, home_name, away_name)
            datos["rivales_comunes"] = rivales_comunes
            
            # --- ANÁLISIS CONTRA RIVAL DEL RIVAL ---
            # Obtener información de los rivales de los rivales
            rival_local_rival = (last_away_match or {}).get('home_team', 'N/A')
            rival_visitante_rival = (last_home_match or {}).get('away_team', 'N/A')
            
            if rival_local_rival != 'N/A' and rival_visitante_rival != 'N/A':
                analisis_contra_rival = analizar_contra_rival_del_rival(
                    soup_completo, home_name, away_name, rival_local_rival, rival_visitante_rival
                )
                datos["analisis_contra_rival_del_rival"] = analisis_contra_rival
            
            # --- ANÁLISIS DE RENDIMIENTO RECIENTE Y COMPARATIVAS INDIRECTAS ---
            # Generar resumen gráfico de rendimiento reciente y comparativas indirectas
            resumen_rendimiento = generar_resumen_rendimiento_reciente(soup_completo, home_name, away_name, current_ah_line)
            datos["resumen_rendimiento_reciente"] = resumen_rendimiento
            
            # --- FUNCIONES AUXILIARES PARA LA PLANTILLA ---
            # Añadir funciones auxiliares para el análisis gráfico
            from modules.funciones_auxiliares import (
                _calcular_estadisticas_contra_rival, 
                _analizar_over_under, 
                _analizar_ah_cubierto, 
                _analizar_desempeno_casa_fuera,
                _contar_victorias_h2h,
                _analizar_over_under_h2h,
                _contar_over_h2h,
                _contar_victorias_h2h_general
            )
            
            datos["_calcular_estadisticas_contra_rival"] = _calcular_estadisticas_contra_rival
            datos["_analizar_over_under"] = _analizar_over_under
            datos["_analizar_ah_cubierto"] = _analizar_ah_cubierto
            datos["_analizar_desempeno_casa_fuera"] = _analizar_desempeno_casa_fuera
            datos["_contar_victorias_h2h"] = _contar_victorias_h2h
            datos["_analizar_over_under_h2h"] = _analizar_over_under_h2h
            datos["_contar_over_h2h"] = _contar_over_h2h
            datos["_contar_victorias_h2h_general"] = _contar_victorias_h2h_general
        
        return datos

    except Exception as e:
        logger.exception("Error crítico en obtener_datos_completos_partido")
        return {"error": f"Error durante el scraping: {e}"}


# EN modules/estudio_scraper.py

# ... (al final del archivo, después de obtener_datos_completos_partido)

def obtener_datos_preview_rapido(match_id: str):
    """
    Scraper ultraligero y optimizado para obtener solo los datos de la vista previa.
    Usa 'requests' para ser extremadamente rápido y evitar Selenium.
    """
    if not match_id or not match_id.isdigit():
        return {"error": "ID de partido inválido."}

    url = f"{BASE_URL_OF}/match/h2h-{match_id}"
    try:
        soup = _load_main_match_soup(match_id)
        if soup is None:
            return {"error": "No se pudo cargar la página del partido para la vista previa."}

        # 2. Extraer identificadores y nombres (igual que en el scraper completo)
        _, _, league_id, home_name, away_name, _ = get_team_league_info_from_script_of(soup)
        dt_info = get_match_datetime_from_script_of(soup)

        # 2b. Extraer línea AH actual (Bet365 inicial)
        main_odds = extract_bet365_initial_odds_of(soup)
        ah_line_raw = main_odds.get('ah_linea_raw', '-')
        ah_line_num = parse_ah_to_number_of(ah_line_raw)
        favorito_actual = None
        if ah_line_num is not None:
            if ah_line_num > 0:
                favorito_actual = home_name
            elif ah_line_num < 0:
                favorito_actual = away_name

        # 3. Analizar Rendimiento Reciente (últimos 8 partidos)
        def analizar_rendimiento(tabla_id, equipo_nombre):
            tabla = soup.find("table", id=tabla_id)
            if not tabla: return {"wins": 0, "draws": 0, "losses": 0, "total": 0}
            partidos = tabla.find_all("tr", id=re.compile(rf"tr{tabla_id[-1]}_\d+"), limit=8)
            victorias = 0
            for r in partidos:
                celdas = r.find_all("td")
                if len(celdas) < 5: continue
                resultado_span = celdas[5].find("span")
                if resultado_span and 'win' in resultado_span.get('class', []):
                    victorias += 1
            return {"wins": victorias, "total": len(partidos)}

        rendimiento_local = analizar_rendimiento("table_v1", home_name)
        rendimiento_visitante = analizar_rendimiento("table_v2", away_name)

        # 4. Analizar H2H Directo (últimos 8 enfrentamientos)
        # 3. H2H directo, usando la misma función del flujo principal
        h2h_stats = {"home_wins": 0, "away_wins": 0, "draws": 0}
        last_h2h_cover = "DESCONOCIDO"
        try:
            h2h_data = extract_h2h_data_of(soup, home_name, away_name, None)
            # Contar wins/draws a partir de tabla (como antes)
            h2h_table = soup.find("table", id="table_v3")
            if h2h_table:
                partidos_h2h = h2h_table.find_all("tr", id=re.compile(r"tr3_\d+"), limit=8)
                for r in partidos_h2h:
                    tds = r.find_all("td")
                    if len(tds) < 5:
                        continue
                    home_h2h = tds[2].get_text(strip=True)
                    resultado_raw = tds[3].get_text(strip=True)
                    try:
                        goles_h, goles_a = map(int, resultado_raw.split("-"))
                        es_local_en_h2h = home_name.lower() in home_h2h.lower()
                        if goles_h == goles_a:
                            h2h_stats["draws"] += 1
                        elif (es_local_en_h2h and goles_h > goles_a) or (not es_local_en_h2h and goles_a > goles_h):
                            h2h_stats["home_wins"] += 1
                        else:
                            h2h_stats["away_wins"] += 1
                    except (ValueError, IndexError):
                        continue
            # Evaluar cobertura del favorito con el último H2H disponible
            res_raw = None
            h_home = None
            h_away = None
            if h2h_data.get('res1_raw') and h2h_data.get('res1_raw') != '?-?':
                res_raw = h2h_data['res1_raw']
                h_home = home_name
                h_away = away_name
            elif h2h_data.get('res6_raw') and h2h_data.get('res6_raw') != '?-?':
                res_raw = h2h_data['res6_raw']
                h_home = h2h_data.get('h2h_gen_home', home_name)
                h_away = h2h_data.get('h2h_gen_away', away_name)
            if favorito_actual and (ah_line_num is not None) and res_raw:
                ct, _ = check_handicap_cover(res_raw.replace(':', '-'), ah_line_num, favorito_actual, h_home, h_away, home_name)
                last_h2h_cover = ct
        except Exception:
            pass

        # 4. Datos de Rendimiento Reciente (último partido de cada uno) y H2H Rivales (Col3)
        recent_indirect = {"last_home": None, "last_away": None, "h2h_col3": None}
        try:
            # Último del local en liga
            last_home = extract_last_match_in_league_of(soup, "table_v1", home_name, league_id, True)
            last_home_stats = get_match_progression_stats_data(str(last_home.get('match_id'))) if last_home and last_home.get('match_id') else None
            def _df_to_rows(df):
                rows = []
                try:
                    if df is not None and not df.empty:
                        for idx, row in df.iterrows():
                            label = idx.replace('Shots on Goal', 'Tiros a Puerta').replace('Shots', 'Tiros').replace('Dangerous Attacks', 'Ataques Peligrosos').replace('Attacks', 'Ataques')
                            rows.append({"label": label, "home": row.get('Casa', ''), "away": row.get('Fuera', '')})
                except Exception:
                    pass
                return rows
            if last_home:
                recent_indirect["last_home"] = {
                    "home": last_home.get('home_team'),
                    "away": last_home.get('away_team'),
                    "score": last_home.get('score'),
                    "ah": format_ah_as_decimal_string_of(last_home.get('handicap_line_raw', '-') or '-'),
                    "ou": "-",
                    "stats_rows": _df_to_rows(last_home_stats),
                    "date": last_home.get('date')
                }
            # Último del visitante en liga
            last_away = extract_last_match_in_league_of(soup, "table_v2", away_name, league_id, False)
            last_away_stats = get_match_progression_stats_data(str(last_away.get('match_id'))) if last_away and last_away.get('match_id') else None
            if last_away:
                recent_indirect["last_away"] = {
                    "home": last_away.get('home_team'),
                    "away": last_away.get('away_team'),
                    "score": last_away.get('score'),
                    "ah": format_ah_as_decimal_string_of(last_away.get('handicap_line_raw', '-') or '-'),
                    "ou": "-",
                    "stats_rows": _df_to_rows(last_away_stats),
                    "date": last_away.get('date')
                }
            # H2H Rivales (Col3)
            key_id_a, rival_a_id, rival_a_name = get_rival_a_for_original_h2h_of(soup, league_id)
            _, rival_b_id, rival_b_name = get_rival_b_for_original_h2h_of(soup, league_id)
            if key_id_a and rival_a_id and rival_b_id:
                col3 = get_h2h_details_for_original_logic_of(key_id_a, rival_a_id, rival_b_id, rival_a_name, rival_b_name)
                if col3 and col3.get('status') == 'found':
                    score_line = f"{col3.get('h2h_home_team_name')} {col3.get('goles_home')}:{col3.get('goles_away')} {col3.get('h2h_away_team_name')}"
                    col3_stats = get_match_progression_stats_data(str(col3.get('match_id')))
                    recent_indirect["h2h_col3"] = {
                        "score_line": score_line,
                        "ah": format_ah_as_decimal_string_of(col3.get('handicap', '-') or '-'),
                        "ou": "-",
                        "stats_rows": _df_to_rows(col3_stats),
                        "date": col3.get('date')
                    }
        except Exception:
            pass

        # 5. Calcular H2H Indirecto (rivales comunes) de forma ligera
        indirect = {"home_better": 0, "away_better": 0, "draws": 0, "samples": []}
        try:
            table_v1 = soup.find("table", id="table_v1")
            table_v2 = soup.find("table", id="table_v2")

            def _parse_score_to_tuple(score_text):
                try:
                    gh, ga = map(int, score_text.strip().split("-"))
                    return gh, ga
                except Exception:
                    return None

            def _find_match_info(table, rival_name_lower, team_name_ref):
                if not table:
                    return None
                rows = table.find_all("tr", id=re.compile(r"tr[12]_\\d+"))
                for r in rows:
                    tds = r.find_all("td")
                    if len(tds) < 5:
                        continue
                    home_t = tds[2].get_text(strip=True)
                    away_t = tds[4].get_text(strip=True)
                    if away_t.lower() == rival_name_lower or home_t.lower() == rival_name_lower:
                        score_text = tds[3].get_text(strip=True)
                        score = _parse_score_to_tuple(score_text)
                        if not score:
                            continue
                        gh, ga = score
                        if home_t.lower() == team_name_ref.lower():
                            margin = gh - ga
                        elif away_t.lower() == team_name_ref.lower():
                            margin = ga - gh
                        else:
                            margin = gh - ga
                        return {"rival": rival_name_lower, "margin": margin}
                return None

            if table_v1 and table_v2:
                rivals_home = set()
                for r in table_v1.find_all("tr", id=re.compile(r"tr1_\\d+")):
                    tds = r.find_all("td")
                    if len(tds) >= 5:
                        rivals_home.add(tds[4].get_text(strip=True).lower())
                rivals_away = set()
                for r in table_v2.find_all("tr", id=re.compile(r"tr2_\\d+")):
                    tds = r.find_all("td")
                    if len(tds) >= 5:
                        rivals_away.add(tds[2].get_text(strip=True).lower())
                common = [rv for rv in rivals_home.intersection(rivals_away) if rv and rv != '?']
                common = common[:3]
                for rv in common:
                    home_info = _find_match_info(table_v1, rv, home_name)
                    away_info = _find_match_info(table_v2, rv, away_name)
                    if not home_info or not away_info:
                        continue
                    if home_info["margin"] > away_info["margin"]:
                        indirect["home_better"] += 1
                        verdict = "home"
                    elif home_info["margin"] < away_info["margin"]:
                        indirect["away_better"] += 1
                        verdict = "away"
                    else:
                        indirect["draws"] += 1
                        verdict = "draw"
                    indirect["samples"].append({
                        "rival": rv,
                        "home_margin": home_info["margin"],
                        "away_margin": away_info["margin"],
                        "verdict": verdict
                    })
        except Exception:
            # Ignorar errores de comparativas indirectas en la vista previa
            pass

        # 5b. Evaluar "muy superior" en ataques peligrosos desde comparativas indirectas (con la misma función)
        indirect_panels = extract_indirect_comparison_data(soup)
        ataques_peligrosos = {}
        favorite_da = None
        try:
            if indirect_panels and indirect_panels.get("comp1"):
                c1 = indirect_panels["comp1"]
                ap_home = int(c1['stats'].get('ataques_peligrosos_casa', 0) or 0)
                ap_away = int(c1['stats'].get('ataques_peligrosos_fuera', 0) or 0)
                own_ap, rival_ap = (ap_away, ap_home) if c1.get('localia') == 'A' else (ap_home, ap_away)
                ataques_peligrosos['team1'] = {
                    "name": c1['main_team'],
                    "own": own_ap,
                    "rival": rival_ap,
                    "very_superior": bool((own_ap - rival_ap) >= 5)
                }
            if indirect_panels and indirect_panels.get("comp2"):
                c2 = indirect_panels["comp2"]
                ap_home = int(c2['stats'].get('ataques_peligrosos_casa', 0) or 0)
                ap_away = int(c2['stats'].get('ataques_peligrosos_fuera', 0) or 0)
                own_ap, rival_ap = (ap_away, ap_home) if c2.get('localia') == 'A' else (ap_home, ap_away)
                ataques_peligrosos['team2'] = {
                    "name": c2['main_team'],
                    "own": own_ap,
                    "rival": rival_ap,
                    "very_superior": bool((own_ap - rival_ap) >= 5)
                }
            # Identificar el bloque correspondiente al favorito
            fav_name = (favorito_actual or '').lower()
            for key in ['team1','team2']:
                if key in ataques_peligrosos and ataques_peligrosos[key]['name'].lower() == fav_name:
                    favorite_da = {
                        "name": ataques_peligrosos[key]['name'],
                        "very_superior": ataques_peligrosos[key]['very_superior'],
                        "own": ataques_peligrosos[key]['own'],
                        "rival": ataques_peligrosos[key]['rival']
                    }
                    break
        except Exception:
            pass

        # 6. Montar el objeto de respuesta JSON
        result = {
            "home_team": home_name,
            "away_team": away_name,
            "recent_form": {
                "home": rendimiento_local,
                "away": rendimiento_visitante,
            },
            "recent_indirect": recent_indirect,
            "handicap": {
                "ah_line": format_ah_as_decimal_string_of(ah_line_raw),
                "favorite": favorito_actual or "",
                "cover_on_last_h2h": last_h2h_cover
            },
            "dangerous_attacks": ataques_peligrosos,
            "favorite_dangerous_attacks": favorite_da,
            "h2h_indirect": indirect,
            "h2h_stats": h2h_stats
        }

        return result

    except requests.Timeout:
        return {"error": "La fuente de datos (Nowgoal) tardó demasiado en responder."}
    except Exception as e:
        logger.exception("Error en scraper preview para %s", match_id)
        return {"error": f"No se pudieron obtener los datos de la vista previa: {type(e).__name__}"}



def obtener_datos_preview_ligero(match_id: str):
    """
    Vista previa LIGERA (solo on-click): usa requests + BeautifulSoup.
    Devuelve el mismo esquema que la versión 'rápida' con Selenium, pero sin abrir navegador.
    """
    if not match_id or not match_id.isdigit():
        return {"error": "ID de partido inválido."}

    url = f"{BASE_URL_OF}/match/h2h-{match_id}"
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/116.0.0.0 Safari/537.36"})
        response = session.get(url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')

        # Equipos
        _, _, league_id, home_name, away_name, _ = get_team_league_info_from_script_of(soup)
        dt_info = get_match_datetime_from_script_of(soup)

        # Línea AH (Bet365 inicial)
        main_odds = extract_bet365_initial_odds_of(soup)
        ah_line_raw = main_odds.get('ah_linea_raw', '-')
        ah_line_num = parse_ah_to_number_of(ah_line_raw)
        favorito_actual = None
        if ah_line_num is not None:
            if ah_line_num > 0:
                favorito_actual = home_name
            elif ah_line_num < 0:
                favorito_actual = away_name

        # Rendimiento reciente (últimos 8)
        def analizar_rendimiento(tabla_id, equipo_nombre):
            tabla = soup.find("table", id=tabla_id)
            if not tabla:
                return {"wins": 0, "draws": 0, "losses": 0, "total": 0}
            partidos = tabla.find_all("tr", id=re.compile(rf"tr{tabla_id[-1]}_\\d+"), limit=8)
            victorias = 0
            for r in partidos:
                celdas = r.find_all("td")
                if len(celdas) < 5:
                    continue
                resultado_span = celdas[5].find("span")
                if resultado_span and 'win' in resultado_span.get('class', []):
                    victorias += 1
            return {"wins": victorias, "total": len(partidos)}

        rendimiento_local = analizar_rendimiento("table_v1", home_name)
        rendimiento_visitante = analizar_rendimiento("table_v2", away_name)

        # H2H directo (usar función existente para coherencia)
        h2h_stats = {"home_wins": 0, "away_wins": 0, "draws": 0}
        last_h2h_cover = "DESCONOCIDO"
        try:
            h2h_data = extract_h2h_data_of(soup, home_name, away_name, None)
            h2h_table = soup.find("table", id="table_v3")
            if h2h_table:
                partidos_h2h = h2h_table.find_all("tr", id=re.compile(r"tr3_\\d+"), limit=8)
                for r in partidos_h2h:
                    tds = r.find_all("td")
                    if len(tds) < 5:
                        continue
                    home_h2h = tds[2].get_text(strip=True)
                    resultado_raw = tds[3].get_text(strip=True)
                    try:
                        goles_h, goles_a = map(int, resultado_raw.split("-"))
                        es_local_en_h2h = home_name.lower() in home_h2h.lower()
                        if goles_h == goles_a:
                            h2h_stats["draws"] += 1
                        elif (es_local_en_h2h and goles_h > goles_a) or (not es_local_en_h2h and goles_a > goles_h):
                            h2h_stats["home_wins"] += 1
                        else:
                            h2h_stats["away_wins"] += 1
                    except (ValueError, IndexError):
                        continue
            # Cobertura del favorito en el último H2H disponible
            res_raw = None
            h_home = None
            h_away = None
            if h2h_data.get('res1_raw') and h2h_data.get('res1_raw') != '?-?':
                res_raw = h2h_data['res1_raw']
                h_home = home_name
                h_away = away_name
            elif h2h_data.get('res6_raw') and h2h_data.get('res6_raw') != '?-?':
                res_raw = h2h_data['res6_raw']
                h_home = h2h_data.get('h2h_gen_home', home_name)
                h_away = h2h_data.get('h2h_gen_away', away_name)
            if favorito_actual and (ah_line_num is not None) and res_raw:
                ct, _ = check_handicap_cover(res_raw.replace(':', '-'), ah_line_num, favorito_actual, h_home, h_away, home_name)
                last_h2h_cover = ct
        except Exception:
            pass

        # Rendimiento Reciente (últimos partidos) y H2H Rivales (Col3) con peticiones ligeras
        recent_indirect = {"last_home": None, "last_away": None, "h2h_col3": None}
        try:
            # Últimos partidos
            last_home = extract_last_match_in_league_of(soup, "table_v1", home_name, league_id, True)
            last_away = extract_last_match_in_league_of(soup, "table_v2", away_name, league_id, False)
            def _df_to_rows(df):
                rows = []
                try:
                    if df is not None and not df.empty:
                        for idx, row in df.iterrows():
                            label = idx.replace('Shots on Goal', 'Tiros a Puerta').replace('Shots', 'Tiros').replace('Dangerous Attacks', 'Ataques Peligrosos').replace('Attacks', 'Ataques')
                            rows.append({"label": label, "home": row.get('Casa', ''), "away": row.get('Fuera', '')})
                except Exception:
                    pass
                return rows
            if last_home:
                lh_stats = get_match_progression_stats_data(str(last_home.get('match_id')))
                recent_indirect["last_home"] = {
                    "home": last_home.get('home_team'),
                    "away": last_home.get('away_team'),
                    "score": last_home.get('score'),
                    "ah": format_ah_as_decimal_string_of(last_home.get('handicap_line_raw', '-') or '-'),
                    "ou": "-",
                    "stats_rows": _df_to_rows(lh_stats),
                    "date": last_home.get('date')
                }
            if last_away:
                la_stats = get_match_progression_stats_data(str(last_away.get('match_id')))
                recent_indirect["last_away"] = {
                    "home": last_away.get('home_team'),
                    "away": last_away.get('away_team'),
                    "score": last_away.get('score'),
                    "ah": format_ah_as_decimal_string_of(last_away.get('handicap_line_raw', '-') or '-'),
                    "ou": "-",
                    "stats_rows": _df_to_rows(la_stats),
                    "date": last_away.get('date')
                }
            # H2H Rivales (Col3) sin Selenium: cargar la página del key_id_a
            key_id_a, rival_a_id, rival_a_name = get_rival_a_for_original_h2h_of(soup, league_id)
            _, rival_b_id, rival_b_name = get_rival_b_for_original_h2h_of(soup, league_id)
            if key_id_a and rival_a_id and rival_b_id:
                key_url = f"{BASE_URL_OF}/match/h2h-{key_id_a}"
                session2 = requests.Session()
                session2.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/116.0.0.0 Safari/537.36"})
                key_resp = session2.get(key_url, timeout=6)
                key_resp.raise_for_status()
                soup_key = BeautifulSoup(key_resp.text, 'lxml')
                table = soup_key.find("table", id="table_v2")
                if table:
                    for row in table.find_all("tr", id=re.compile(r"tr2_\\d+")):
                        links = row.find_all("a", onclick=True)
                        if len(links) < 2:
                            continue
                        m_h = re.search(r"team\((\d+)\)", links[0].get("onclick", ""))
                        m_a = re.search(r"team\((\d+)\)", links[1].get("onclick", ""))
                        if not (m_h and m_a):
                            continue
                        if {m_h.group(1), m_a.group(1)} == {str(rival_a_id), str(rival_b_id)}:
                            score_span = row.find("span", class_="fscore_2")
                            if not score_span or '-' not in score_span.text:
                                break
                            score_txt = score_span.text.strip().split("(")[0].strip()
                            try:
                                g_h, g_a = score_txt.split('-', 1)
                            except Exception:
                                break
                            tds = row.find_all("td")
                            ah_raw = "-"
                            if len(tds) > 11:
                                cell = tds[11]
                                ah_raw = (cell.get("data-o") or cell.text).strip() or "-"
                            match_id_col3 = row.get('index')
                            score_line = f"{links[0].text.strip()} {g_h}:{g_a} {links[1].text.strip()}"
                            col3_stats = get_match_progression_stats_data(str(match_id_col3))
                            # Fecha si existe
                            date_txt = None
                            try:
                                date_span = tds[1].find('span', attrs={'name': 'timeData'}) if len(tds) > 1 else None
                                date_txt = date_span.get_text(strip=True) if date_span else None
                            except Exception:
                                date_txt = None
                            recent_indirect["h2h_col3"] = {
                                "score_line": score_line,
                                "ah": format_ah_as_decimal_string_of(ah_raw or '-'),
                                "ou": "-",
                                "stats_rows": _df_to_rows(col3_stats),
                                "date": date_txt
                            }
                            break
        except Exception:
            pass

        # H2H indirecto ligero (rivales comunes)
        indirect = {"home_better": 0, "away_better": 0, "draws": 0, "samples": []}
        try:
            table_v1 = soup.find("table", id="table_v1")
            table_v2 = soup.find("table", id="table_v2")
            def _parse_score_to_tuple(score_text):
                try:
                    gh, ga = map(int, score_text.strip().split("-"))
                    return gh, ga
                except Exception:
                    return None
            def _find_match_info(table, rival_name_lower, team_name_ref):
                if not table:
                    return None
                rows = table.find_all("tr", id=re.compile(r"tr[12]_\\d+"))
                for r in rows:
                    tds = r.find_all("td")
                    if len(tds) < 5:
                        continue
                    home_t = tds[2].get_text(strip=True)
                    away_t = tds[4].get_text(strip=True)
                    if away_t.lower() == rival_name_lower or home_t.lower() == rival_name_lower:
                        score_text = tds[3].get_text(strip=True)
                        score = _parse_score_to_tuple(score_text)
                        if not score:
                            continue
                        gh, ga = score
                        if home_t.lower() == team_name_ref.lower():
                            margin = gh - ga
                        elif away_t.lower() == team_name_ref.lower():
                            margin = ga - gh
                        else:
                            margin = gh - ga
                        return {"rival": rival_name_lower, "margin": margin}
                return None
            if table_v1 and table_v2:
                rivals_home = set()
                for r in table_v1.find_all("tr", id=re.compile(r"tr1_\\d+")):
                    tds = r.find_all("td")
                    if len(tds) >= 5:
                        rivals_home.add(tds[4].get_text(strip=True).lower())
                rivals_away = set()
                for r in table_v2.find_all("tr", id=re.compile(r"tr2_\\d+")):
                    tds = r.find_all("td")
                    if len(tds) >= 5:
                        rivals_away.add(tds[2].get_text(strip=True).lower())
                common = [rv for rv in rivals_home.intersection(rivals_away) if rv and rv != '?']
                common = common[:3]
                for rv in common:
                    home_info = _find_match_info(table_v1, rv, home_name)
                    away_info = _find_match_info(table_v2, rv, away_name)
                    if not home_info or not away_info:
                        continue
                    if home_info["margin"] > away_info["margin"]:
                        indirect["home_better"] += 1
                        verdict = "home"
                    elif home_info["margin"] < away_info["margin"]:
                        indirect["away_better"] += 1
                        verdict = "away"
                    else:
                        indirect["draws"] += 1
                        verdict = "draw"
                    indirect["samples"].append({
                        "rival": rv,
                        "home_margin": home_info["margin"],
                        "away_margin": away_info["margin"],
                        "verdict": verdict
                    })
        except Exception:
            pass

        # Ataques peligrosos (comparativas indirectas)
        indirect_panels = extract_indirect_comparison_data(soup)
        ataques_peligrosos = {}
        favorite_da = None
        try:
            if indirect_panels and indirect_panels.get("comp1"):
                c1 = indirect_panels["comp1"]
                ap_home = int(c1['stats'].get('ataques_peligrosos_casa', 0) or 0)
                ap_away = int(c1['stats'].get('ataques_peligrosos_fuera', 0) or 0)
                own_ap, rival_ap = (ap_away, ap_home) if c1.get('localia') == 'A' else (ap_home, ap_away)
                ataques_peligrosos['team1'] = {
                    "name": c1['main_team'],
                    "own": own_ap,
                    "rival": rival_ap,
                    "very_superior": bool((own_ap - rival_ap) >= 5)
                }
            if indirect_panels and indirect_panels.get("comp2"):
                c2 = indirect_panels["comp2"]
                ap_home = int(c2['stats'].get('ataques_peligrosos_casa', 0) or 0)
                ap_away = int(c2['stats'].get('ataques_peligrosos_fuera', 0) or 0)
                own_ap, rival_ap = (ap_away, ap_home) if c2.get('localia') == 'A' else (ap_home, ap_away)
                ataques_peligrosos['team2'] = {
                    "name": c2['main_team'],
                    "own": own_ap,
                    "rival": rival_ap,
                    "very_superior": bool((own_ap - rival_ap) >= 5)
                }
            fav_name = (favorito_actual or '').lower()
            for key in ['team1','team2']:
                if key in ataques_peligrosos and ataques_peligrosos[key]['name'].lower() == fav_name:
                    favorite_da = {
                        "name": ataques_peligrosos[key]['name'],
                        "very_superior": ataques_peligrosos[key]['very_superior'],
                        "own": ataques_peligrosos[key]['own'],
                        "rival": ataques_peligrosos[key]['rival']
                    }
                    break
        except Exception:
            pass

        result = {
            "home_team": home_name,
            "away_team": away_name,
            "recent_form": {
                "home": rendimiento_local,
                "away": rendimiento_visitante,
            },
            "recent_indirect": recent_indirect,
            "handicap": {
                "ah_line": format_ah_as_decimal_string_of(ah_line_raw),
                "favorite": favorito_actual or "",
                "cover_on_last_h2h": last_h2h_cover
            },
            "dangerous_attacks": ataques_peligrosos,
            "favorite_dangerous_attacks": favorite_da,
            "h2h_indirect": indirect,
            "h2h_stats": h2h_stats
        }
        # Añadir campos de fecha/hora del partido a la respuesta
        result.update({
            "match_date": dt_info.get("match_date"),
            "match_time": dt_info.get("match_time"),
            "match_datetime": dt_info.get("match_datetime"),
        })
        return result
    except requests.Timeout:
        return {"error": "La fuente de datos (Nowgoal) tardó demasiado en responder."}
    except Exception as e:
        print(f"ERROR en scraper preview ligero para {match_id}: {e}")
        return {"error": f"No se pudieron obtener los datos de la vista previa (ligera): {type(e).__name__}"}
