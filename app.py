# app.py - Servidor web principal (Flask)
import logging
import os
import threading

from flask import Flask, abort, jsonify, render_template, request

from modules.estudio_scraper import (
    generar_analisis_mercado_simplificado,
    obtener_datos_completos_partido,
    obtener_datos_preview_ligero,
    obtener_datos_preview_rapido,
)
from modules.utils import (
    check_handicap_cover,
    format_ah_as_decimal_string_of,
    normalize_handicap_to_half_bucket_str,
    parse_ah_to_number_of,
)
from scraper_partidos import (
    TARGET_URL,
    fetch_with_requests,
    fetch_html_via_playwright_sync,
    get_finished_matches,
    get_upcoming_matches,
    html_has_rows,
)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("app")

app = Flask(__name__)


def _build_handicap_options(matches):
    buckets = {
        match.get("handicap_bucket")
        or normalize_handicap_to_half_bucket_str(match.get("handicap"))
        for match in matches
    }
    return sorted(
        (bucket for bucket in buckets if bucket is not None),
        key=lambda x: float(x),
    )


@app.get("/")
def index():
    try:
        logger.info("Recibida petición para Próximos Partidos...")
        hf = request.args.get("handicap")
        matches = get_upcoming_matches(handicap_filter=hf)
        logger.info("Scraper finalizado. %s partidos encontrados.", len(matches))
        opts = _build_handicap_options(matches)
        return render_template(
            "index.html",
            matches=matches,
            handicap_filter=hf,
            handicap_options=opts,
            page_mode="upcoming",
            page_title="Próximos Partidos",
        )
    except Exception as exc:
        logger.exception("Error en la ruta principal")
        return render_template(
            "index.html",
            matches=[],
            error=f"No se pudieron cargar los partidos: {exc}",
            page_mode="upcoming",
            page_title="Próximos Partidos",
        )

@app.get("/resultados")
def resultados():
    try:
        logger.info("Recibida petición para Partidos Finalizados...")
        hf = request.args.get("handicap")
        matches = get_finished_matches(handicap_filter=hf)
        logger.info("Scraper finalizado. %s partidos encontrados.", len(matches))
        opts = _build_handicap_options(matches)
        return render_template(
            "index.html",
            matches=matches,
            handicap_filter=hf,
            handicap_options=opts,
            page_mode="finished",
            page_title="Resultados Finalizados",
        )
    except Exception as exc:
        logger.exception("Error en la ruta de resultados")
        return render_template(
            "index.html",
            matches=[],
            error=f"No se pudieron cargar los partidos: {exc}",
            page_mode="finished",
            page_title="Resultados Finalizados",
        )

@app.get("/api/matches")
def api_matches():
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
        limit = min(int(request.args.get("limit", 5)), 50)
        matches = get_upcoming_matches(
            limit=limit,
            offset=offset,
            handicap_filter=request.args.get("handicap"),
        )
        return jsonify({"matches": matches})
    except Exception as exc:
        logger.exception("Error en /api/matches")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/finished_matches")
def api_finished_matches():
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
        limit = min(int(request.args.get("limit", 5)), 50)
        matches = get_finished_matches(
            limit=limit,
            offset=offset,
            handicap_filter=request.args.get("handicap"),
        )
        return jsonify({"matches": matches})
    except Exception as exc:
        logger.exception("Error en /api/finished_matches")
        return jsonify({"error": str(exc)}), 500


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.get("/debug")
def debug():
    try:
        html = fetch_with_requests(TARGET_URL, timeout=20)
        info = {
            "method": "requests",
            "status": "ok",
            "len": len(html or ""),
            "has_rows": bool(html and html_has_rows(html)),
        }
        if not info["has_rows"]:
            html2 = fetch_html_via_playwright_sync(TARGET_URL, timeout_ms=20_000)
            info.update({
                "method": "playwright",
                "len": len(html2 or ""),
                "has_rows": bool(html2 and html_has_rows(html2)),
            })
        return jsonify(info), 200
    except Exception as exc:
        logger.exception("debug error")
        return jsonify({"error": str(exc)}), 500


@app.get("/proximos")
def proximos():
    try:
        logger.info("Recibida petición. Ejecutando scraper de partidos...")
        hf = request.args.get("handicap")
        matches = get_upcoming_matches(limit=25, handicap_filter=hf)
        logger.info("Scraper finalizado. %s partidos encontrados.", len(matches))
        opts = _build_handicap_options(matches)
        return render_template("index.html", matches=matches, handicap_filter=hf, handicap_options=opts)
    except Exception as exc:
        logger.exception("Error en la ruta proximos")
        return render_template("index.html", matches=[], error=f"No se pudieron cargar los partidos: {exc}")

# --- NUEVA RUTA PARA MOSTRAR EL ESTUDIO DETALLADO ---
@app.route('/estudio/<string:match_id>')
def mostrar_estudio(match_id):
    """
    Esta ruta se activa cuando un usuario visita /estudio/ID_DEL_PARTIDO.
    """
    logger.info("Recibida petición para el estudio del partido ID: %s", match_id)
    
    # Llama a la función principal de tu módulo de scraping
    datos_partido = obtener_datos_completos_partido(match_id)
    
    if not datos_partido or "error" in datos_partido:
        # Si hay un error, puedes mostrar una página de error
        logger.error("Error al obtener datos para %s: %s", match_id, datos_partido.get('error'))
        abort(500, description=datos_partido.get('error', 'Error desconocido'))

    # Si todo va bien, renderiza la plantilla HTML pasándole los datos
    logger.info(
        "Datos obtenidos para %s vs %s. Renderizando plantilla...",
        datos_partido['home_name'],
        datos_partido['away_name'],
    )
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
            logger.info("Recibida petición para analizar partido finalizado ID: %s", match_id)
            
            # Llama a la función principal de tu módulo de scraping
            datos_partido = obtener_datos_completos_partido(match_id)
            
            if not datos_partido or "error" in datos_partido:
                # Si hay un error, mostrarlo en la página
                logger.error("Error al obtener datos para %s: %s", match_id, datos_partido.get('error'))
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
            logger.info(
                "Datos obtenidos para %s vs %s. Renderizando plantilla...",
                datos_partido['home_name'],
                datos_partido['away_name'],
            )
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
        logger.exception("Error en la ruta /api/preview/%s", match_id)
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
        logger.exception("Error en la ruta /api/analisis/%s", match_id)
        return jsonify({'error': 'Ocurrió un error interno en el servidor.'}), 500

@app.route('/start_analysis_background', methods=['POST'])
def start_analysis_background():
    match_id = request.json.get('match_id')
    if not match_id:
        return jsonify({'status': 'error', 'message': 'No se proporcionó match_id'}), 400

    def analysis_worker(app, match_id):
        with app.app_context():
            logger.info("Iniciando análisis en segundo plano para el ID: %s", match_id)
            try:
                obtener_datos_completos_partido(match_id)
                logger.info("Análisis en segundo plano finalizado para el ID: %s", match_id)
            except Exception as e:
                logger.exception("Error en el hilo de análisis para el ID %s", match_id)

    thread = threading.Thread(target=analysis_worker, args=(app, match_id))
    thread.start()

    return jsonify({'status': 'success', 'message': f'Análisis iniciado para el partido {match_id}'})

if __name__ == "__main__":
    # Solo para desarrollo local:
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")), debug=True)
