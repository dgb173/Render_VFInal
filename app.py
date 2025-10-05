# app.py - Servidor web principal (Flask)
from flask import Flask, render_template, abort, request
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import datetime
import re
import math
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ¡Importante! Importa tu nuevo módulo de scraping
from modules.estudio_scraper import (
    obtener_datos_completos_partido, 
    format_ah_as_decimal_string_of, 
    obtener_datos_preview_rapido, 
    obtener_datos_preview_ligero, 
    generar_analisis_mercado_simplificado,
    check_handicap_cover,
    parse_ah_to_number_of
)
from flask import jsonify # Asegúrate de que jsonify está importado

app = Flask(__name__)

# --- Mantén tu lógica para la página principal ---
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
                await page.wait_for_timeout(2000)
                if filter_state is not None:
                    try:
                        await page.evaluate("(state) => { if (typeof HideByState === 'function') { HideByState(state); } }", filter_state)
                        await page.wait_for_timeout(1000)
                    except Exception as eval_err:
                        print(f"Advertencia al aplicar HideByState({filter_state}) en {target_url}: {eval_err}")
                return await page.content()
            finally:
                await browser.close()
    except Exception as browser_exc:
        print(f"Error al obtener la pagina con Playwright ({target_url}): {browser_exc}")
    return None

def _parse_number_clean(s: str):
    if s is None:
        return None
    txt = str(s).strip()
    txt = txt.replace('−', '-')  # unicode minus
    txt = txt.replace(',', '.')
    txt = txt.replace('+', '')
    txt = txt.replace(' ', '')
    m = re.search(r"^[+-]?\d+(?:\.\d+)?$", txt)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None

def _parse_number(s: str):
    if s is None:
        return None
    # Normaliza separadores y signos
    txt = str(s).strip()
    txt = txt.replace('−', '-')  # minus unicode
    txt = txt.replace(',', '.')
    txt = txt.replace(' ', '')
    # Coincide con un número decimal con signo
    m = re.search(r"^[+-]?\d+(?:\.\d+)?$", txt)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None

def _parse_handicap_to_float(text: str):
    if text is None:
        return None
    t = str(text).strip()
    if '/' in t:
        parts = [p for p in re.split(r"/", t) if p]
        nums = []
        for p in parts:
            v = _parse_number_clean(p)
            if v is None:
                return None
            nums.append(v)
        if not nums:
            return None
        return sum(nums) / len(nums)
    # Si viene como cadena normal (ej. "+0.25" o "-0,75")
    return _parse_number_clean(t.replace('+', ''))

def _bucket_to_half(value: float) -> float:
    if value is None:
        return None
    if value == 0:
        return 0.0
    sign = -1.0 if value < 0 else 1.0
    av = abs(value)
    base = math.floor(av + 1e-9)
    frac = av - base
    # Mapea 0.25/0.75/0.5 a .5, 0.0 queda .0
    def close(a, b):
        return abs(a - b) < 1e-6
    if close(frac, 0.0):
        bucket = float(base)
    elif close(frac, 0.5) or close(frac, 0.25) or close(frac, 0.75):
        bucket = base + 0.5
    else:
        # fallback: redondeo al múltiplo de 0.5 más cercano
        bucket = round(av * 2) / 2.0
        # si cae justo en entero, desplazar a .5 para respetar la preferencia de .25/.75 → .5
        f = bucket - math.floor(bucket)
        if close(f, 0.0) and (abs(av - (math.floor(bucket) + 0.25)) < 0.26 or abs(av - (math.floor(bucket) + 0.75)) < 0.26):
            bucket = math.floor(bucket) + 0.5
    return sign * bucket

def normalize_handicap_to_half_bucket_str(text: str):
    v = _parse_handicap_to_float(text)
    if v is None:
        return None
    b = _bucket_to_half(v)
    if b is None:
        return None
    # Formato con un decimal
    return f"{b:.1f}"

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
        del match['time_obj']

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
        del match['time_obj']

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

@app.route('/')
async def index():
    try:
        print("Recibida petición para Próximos Partidos...")
        hf = request.args.get('handicap')
        matches = await get_main_page_matches_async(handicap_filter=hf)
        print(f"Scraper finalizado. {len(matches)} partidos encontrados.")
        opts = sorted({
            normalize_handicap_to_half_bucket_str(m.get('handicap'))
            for m in matches if normalize_handicap_to_half_bucket_str(m.get('handicap')) is not None
        }, key=lambda x: float(x))
        return render_template('index.html', matches=matches, handicap_filter=hf, handicap_options=opts, page_mode='upcoming', page_title='Próximos Partidos')
    except Exception as e:
        print(f"ERROR en la ruta principal: {e}")
        return render_template('index.html', matches=[], error=f"No se pudieron cargar los partidos: {e}", page_mode='upcoming', page_title='Próximos Partidos')

@app.route('/resultados')
async def resultados():
    try:
        print("Recibida petición para Partidos Finalizados...")
        hf = request.args.get('handicap')
        matches = await get_main_page_finished_matches_async(handicap_filter=hf)
        print(f"Scraper finalizado. {len(matches)} partidos encontrados.")
        opts = sorted({
            normalize_handicap_to_half_bucket_str(m.get('handicap'))
            for m in matches if normalize_handicap_to_half_bucket_str(m.get('handicap')) is not None
        }, key=lambda x: float(x))
        return render_template('index.html', matches=matches, handicap_filter=hf, handicap_options=opts, page_mode='finished', page_title='Resultados Finalizados')
    except Exception as e:
        print(f"ERROR en la ruta de resultados: {e}")
        return render_template('index.html', matches=[], error=f"No se pudieron cargar los partidos: {e}", page_mode='finished', page_title='Resultados Finalizados')

@app.route('/api/matches')
async def api_matches():
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 5))
        limit = min(limit, 50)
        matches = await get_main_page_matches_async(limit, offset, request.args.get('handicap'))
        return jsonify({'matches': matches})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/finished_matches')
async def api_finished_matches():
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 5))
        limit = min(limit, 50)
        matches = await get_main_page_finished_matches_async(limit, offset, request.args.get('handicap'))
        return jsonify({'matches': matches})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/proximos')
async def proximos():
    try:
        print("Recibida petición. Ejecutando scraper de partidos...")
        hf = request.args.get('handicap')
        matches = await get_main_page_matches_async(25, 0, hf)
        print(f"Scraper finalizado. {len(matches)} partidos encontrados.")
        opts = sorted({
            normalize_handicap_to_half_bucket_str(m.get('handicap'))
            for m in matches if normalize_handicap_to_half_bucket_str(m.get('handicap')) is not None
        }, key=lambda x: float(x))
        return render_template('index.html', matches=matches, handicap_filter=hf, handicap_options=opts)
    except Exception as e:
        print(f"ERROR en la ruta principal: {e}")
        return render_template('index.html', matches=[], error=f"No se pudieron cargar los partidos: {e}")

# --- NUEVA RUTA PARA MOSTRAR EL ESTUDIO DETALLADO ---
@app.route('/estudio/<string:match_id>')
def mostrar_estudio(match_id):
    """
    Esta ruta se activa cuando un usuario visita /estudio/ID_DEL_PARTIDO.
    """
    print(f"Recibida petición para el estudio del partido ID: {match_id}")
    
    # Llama a la función principal de tu módulo de scraping
    datos_partido = obtener_datos_completos_partido(match_id)
    
    if not datos_partido or "error" in datos_partido:
        # Si hay un error, puedes mostrar una página de error
        print(f"Error al obtener datos para {match_id}: {datos_partido.get('error')}")
        abort(500, description=datos_partido.get('error', 'Error desconocido'))

    # Si todo va bien, renderiza la plantilla HTML pasándole los datos
    print(f"Datos obtenidos para {datos_partido['home_name']} vs {datos_partido['away_name']}. Renderizando plantilla...")
    return render_template('estudio.html', data=datos_partido, format_ah=format_ah_as_decimal_string_of)

# --- NUEVA RUTA PARA ANALIZAR PARTIDOS FINALIZADOS ---
@app.route('/analizar_partido', methods=['GET', 'POST'])
def analizar_partido():
    """
    Ruta para analizar partidos finalizados por ID.
    """
    if request.method == 'POST':
        match_id = request.form.get('match_id')
        if match_id:
            print(f"Recibida petición para analizar partido finalizado ID: {match_id}")
            
            # Llama a la función principal de tu módulo de scraping
            datos_partido = obtener_datos_completos_partido(match_id)
            
            if not datos_partido or "error" in datos_partido:
                # Si hay un error, mostrarlo en la página
                print(f"Error al obtener datos para {match_id}: {datos_partido.get('error')}")
                return render_template('analizar_partido.html', error=datos_partido.get('error', 'Error desconocido'))
            
            # --- ANÁLISIS SIMPLIFICADO ---
            # Extraer los datos necesarios para el análisis simplificado
            main_odds = datos_partido.get("main_match_odds_data")
            h2h_data = datos_partido.get("h2h_data")
            home_name = datos_partido.get("home_name")
            away_name = datos_partido.get("away_name")

            analisis_simplificado_html = ""
            if all([main_odds, h2h_data, home_name, away_name]):
                analisis_simplificado_html = generar_analisis_mercado_simplificado(main_odds, h2h_data, home_name, away_name)

            # Si todo va bien, renderiza la plantilla HTML pasándole los datos
            print(f"Datos obtenidos para {datos_partido['home_name']} vs {datos_partido['away_name']}. Renderizando plantilla...")
            return render_template('estudio.html', 
                                   data=datos_partido, 
                                   format_ah=format_ah_as_decimal_string_of,
                                   analisis_simplificado_html=analisis_simplificado_html)
        else:
            return render_template('analizar_partido.html', error="Por favor, introduce un ID de partido válido.")
    
    # Si es GET, mostrar el formulario
    return render_template('analizar_partido.html')

# --- NUEVA RUTA API PARA LA VISTA PREVIA RÁPIDA ---
@app.route('/api/preview/<string:match_id>')
def api_preview(match_id):
    """
    Endpoint para la vista previa. Llama al scraper LIGERO y RÁPIDO.
    Devuelve los datos en formato JSON.
    """
    try:
        # Por defecto usa la vista previa LIGERA (requests). Si ?mode=selenium, usa la completa.
        mode = request.args.get('mode', 'light').lower()
        if mode in ['full', 'selenium']:
            preview_data = obtener_datos_preview_rapido(match_id)
        else:
            preview_data = obtener_datos_preview_ligero(match_id)
        if "error" in preview_data:
            return jsonify(preview_data), 500
        return jsonify(preview_data)
    except Exception as e:
        print(f"Error en la ruta /api/preview/{match_id}: {e}")
        return jsonify({'error': 'Ocurrió un error interno en el servidor.'}), 500


@app.route('/api/analisis/<string:match_id>')
def api_analisis(match_id):
    """
    Servicio de analisis profundo bajo demanda.
    Devuelve tanto el payload complejo como el HTML simplificado.
    """
    try:
        datos = obtener_datos_completos_partido(match_id)
        if not datos or (isinstance(datos, dict) and datos.get('error')):
            return jsonify({'error': (datos or {}).get('error', 'No se pudieron obtener datos.')}), 500

        # --- Lógica para el payload complejo (la original) ---
        def df_to_rows(df):
            rows = []
            try:
                if df is not None and hasattr(df, 'iterrows'):
                    for idx, row in df.iterrows():
                        label = str(idx)
                        label = label.replace('Shots on Goal', 'Tiros a Puerta')                                     .replace('Shots', 'Tiros')                                     .replace('Dangerous Attacks', 'Ataques Peligrosos')                                     .replace('Attacks', 'Ataques')
                        try:
                            home_val = row['Casa']
                        except Exception:
                            home_val = ''
                        try:
                            away_val = row['Fuera']
                        except Exception:
                            away_val = ''
                        rows.append({'label': label, 'home': home_val or '', 'away': away_val or ''})
            except Exception:
                pass
            return rows

        payload = {
            'home_team': datos.get('home_name', ''),
            'away_team': datos.get('away_name', ''),
            'final_score': datos.get('score'),
            'match_date': datos.get('match_date'),
            'match_time': datos.get('match_time'),
            'match_datetime': datos.get('match_datetime'),
            'recent_indirect_full': {
                'last_home': None,
                'last_away': None,
                'h2h_col3': None
            },
            'comparativas_indirectas': {
                'left': None,
                'right': None
            }
        }
        
        # --- START COVERAGE CALCULATION ---
        main_odds = datos.get("main_match_odds_data")
        home_name = datos.get("home_name")
        away_name = datos.get("away_name")
        ah_actual_num = parse_ah_to_number_of(main_odds.get('ah_linea_raw', ''))
        
        favorito_actual_name = "Ninguno (línea en 0)"
        if ah_actual_num is not None:
            if ah_actual_num > 0: favorito_actual_name = home_name
            elif ah_actual_num < 0: favorito_actual_name = away_name

        def get_cover_status_vs_current(details):
            if not details or ah_actual_num is None:
                return 'NEUTRO'
            try:
                score_str = details.get('score', '').replace(' ', '').replace(':', '-')
                if not score_str or '?' in score_str:
                    return 'NEUTRO'

                h_home = details.get('home_team')
                h_away = details.get('away_team')
                
                status, _ = check_handicap_cover(score_str, ah_actual_num, favorito_actual_name, h_home, h_away, home_name)
                return status
            except Exception:
                return 'NEUTRO'
                
        # --- Análisis mejorado de H2H Rivales ---
        def analyze_h2h_rivals(home_result, away_result):
            if not home_result or not away_result:
                return None
                
            try:
                # Obtener resultados de los partidos
                home_goals = list(map(int, home_result.get('score', '0-0').split('-')))
                away_goals = list(map(int, away_result.get('score', '0-0').split('-')))
                
                # Calcular diferencia de goles
                home_goal_diff = home_goals[0] - home_goals[1]
                away_goal_diff = away_goals[0] - away_goals[1]
                
                # Comparar resultados
                if home_goal_diff > away_goal_diff:
                    return "Contra rivales comunes, el Equipo Local ha obtenido mejores resultados"
                elif away_goal_diff > home_goal_diff:
                    return "Contra rivales comunes, el Equipo Visitante ha obtenido mejores resultados"
                else:
                    return "Los rivales han tenido resultados similares"
            except Exception:
                return None
                
        # --- Análisis de Comparativas Indirectas ---
        def analyze_indirect_comparison(result, team_name):
            if not result:
                return None
                
            try:
                # Determinar si el equipo cubrió el handicap
                status = get_cover_status_vs_current(result)
                
                if status == 'CUBIERTO':
                    return f"Contra este rival, {team_name} habría cubierto el handicap"
                elif status == 'NO CUBIERTO':
                    return f"Contra este rival, {team_name} no habría cubierto el handicap"
                else:
                    return f"Contra este rival, el resultado para {team_name} sería indeterminado"
            except Exception:
                return None
        # --- END COVERAGE CALCULATION ---

        last_home = (datos.get('last_home_match') or {})
        last_home_details = last_home.get('details') or {}
        if last_home_details:
            payload['recent_indirect_full']['last_home'] = {
                'home': last_home_details.get('home_team'),
                'away': last_home_details.get('away_team'),
                'score': (last_home_details.get('score') or '').replace(':', ' : '),
                'ah': format_ah_as_decimal_string_of(last_home_details.get('handicap_line_raw') or '-'),
                'ou': last_home_details.get('ouLine') or '-',
                'stats_rows': df_to_rows(last_home.get('stats')),
                'date': last_home_details.get('date'),
                'cover_status': get_cover_status_vs_current(last_home_details)
            }

        last_away = (datos.get('last_away_match') or {})
        last_away_details = last_away.get('details') or {}
        if last_away_details:
            payload['recent_indirect_full']['last_away'] = {
                'home': last_away_details.get('home_team'),
                'away': last_away_details.get('away_team'),
                'score': (last_away_details.get('score') or '').replace(':', ' : '),
                'ah': format_ah_as_decimal_string_of(last_away_details.get('handicap_line_raw') or '-'),
                'ou': last_away_details.get('ouLine') or '-',
                'stats_rows': df_to_rows(last_away.get('stats')),
                'date': last_away_details.get('date'),
                'cover_status': get_cover_status_vs_current(last_away_details)
            }

        h2h_col3 = (datos.get('h2h_col3') or {})
        h2h_col3_details = h2h_col3.get('details') or {}
        if h2h_col3_details and h2h_col3_details.get('status') == 'found':
            h2h_col3_details_adapted = {
                'score': f"{h2h_col3_details.get('goles_home')}:{h2h_col3_details.get('goles_away')}",
                'home_team': h2h_col3_details.get('h2h_home_team_name'),
                'away_team': h2h_col3_details.get('h2h_away_team_name')
            }
            payload['recent_indirect_full']['h2h_col3'] = {
                'home': h2h_col3_details.get('h2h_home_team_name'),
                'away': h2h_col3_details.get('h2h_away_team_name'),
                'score': f"{h2h_col3_details.get('goles_home')} : {h2h_col3_details.get('goles_away')}",
                'ah': format_ah_as_decimal_string_of(h2h_col3_details.get('handicap_line_raw') or '-'),
                'ou': h2h_col3_details.get('ou_result') or '-',
                'stats_rows': df_to_rows(h2h_col3.get('stats')),
                'date': h2h_col3_details.get('date'),
                'cover_status': get_cover_status_vs_current(h2h_col3_details_adapted),
                'analysis': analyze_h2h_rivals(last_home_details, last_away_details)
            }

        h2h_general = (datos.get('h2h_general') or {})
        h2h_general_details = h2h_general.get('details') or {}
        if h2h_general_details:
            score_text = h2h_general_details.get('res6') or ''
            cover_input = {
                'score': score_text,
                'home_team': h2h_general_details.get('h2h_gen_home'),
                'away_team': h2h_general_details.get('h2h_gen_away')
            }
            payload['recent_indirect_full']['h2h_general'] = {
                'home': h2h_general_details.get('h2h_gen_home'),
                'away': h2h_general_details.get('h2h_gen_away'),
                'score': score_text.replace(':', ' : '),
                'ah': h2h_general_details.get('ah6') or '-',
                'ou': h2h_general_details.get('ou_result6') or '-',
                'stats_rows': df_to_rows(h2h_general.get('stats')),
                'date': h2h_general_details.get('date'),
                'cover_status': get_cover_status_vs_current(cover_input) if score_text else 'NEUTRO'
            }

        comp_left = (datos.get('comp_L_vs_UV_A') or {})
        comp_left_details = comp_left.get('details') or {}
        if comp_left_details:
            payload['comparativas_indirectas']['left'] = {
                'title_home_name': datos.get('home_name'),
                'title_away_name': datos.get('away_name'),
                'home_team': comp_left_details.get('home_team'),
                'away_team': comp_left_details.get('away_team'),
                'score': (comp_left_details.get('score') or '').replace(':', ' : '),
                'ah': format_ah_as_decimal_string_of(comp_left_details.get('ah_line') or '-'),
                'ou': comp_left_details.get('ou_line') or '-',
                'localia': comp_left_details.get('localia') or '',
                'stats_rows': df_to_rows(comp_left.get('stats')),
                'cover_status': get_cover_status_vs_current(comp_left_details),
                'analysis': analyze_indirect_comparison(comp_left_details, datos.get('home_name'))
            }

        comp_right = (datos.get('comp_V_vs_UL_H') or {})
        comp_right_details = comp_right.get('details') or {}
        if comp_right_details:
            payload['comparativas_indirectas']['right'] = {
                'title_home_name': datos.get('home_name'),
                'title_away_name': datos.get('away_name'),
                'home_team': comp_right_details.get('home_team'),
                'away_team': comp_right_details.get('away_team'),
                'score': (comp_right_details.get('score') or '').replace(':', ' : '),
                'ah': format_ah_as_decimal_string_of(comp_right_details.get('ah_line') or '-'),
                'ou': comp_right_details.get('ou_line') or '-',
                'localia': comp_right_details.get('localia') or '',
                'stats_rows': df_to_rows(comp_right.get('stats')),
                'cover_status': get_cover_status_vs_current(comp_right_details),
                'analysis': analyze_indirect_comparison(comp_right_details, datos.get('away_name'))
            }

        # --- Lógica para el HTML simplificado ---
        h2h_data = datos.get("h2h_data")
        simplified_html = ""
        if all([main_odds, h2h_data, home_name, away_name]):
            simplified_html = generar_analisis_mercado_simplificado(main_odds, h2h_data, home_name, away_name)
        
        payload['simplified_html'] = simplified_html

        return jsonify(payload)

    except Exception as e:
        print(f"Error en la ruta /api/analisis/{match_id}: {e}")
        return jsonify({'error': 'Ocurrió un error interno en el servidor.'}), 500

@app.route('/start_analysis_background', methods=['POST'])
def start_analysis_background():
    match_id = request.json.get('match_id')
    if not match_id:
        return jsonify({'status': 'error', 'message': 'No se proporcionó match_id'}), 400

    def analysis_worker(app, match_id):
        with app.app_context():
            print(f"Iniciando análisis en segundo plano para el ID: {match_id}")
            try:
                obtener_datos_completos_partido(match_id)
                print(f"Análisis en segundo plano finalizado para el ID: {match_id}")
            except Exception as e:
                print(f"Error en el hilo de análisis para el ID {match_id}: {e}")

    thread = threading.Thread(target=analysis_worker, args=(app, match_id))
    thread.start()

    return jsonify({'status': 'success', 'message': f'Análisis iniciado para el partido {match_id}'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) # debug=True es útil para desarrollar

