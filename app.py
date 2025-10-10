
# app.py - Servidor web principal (Flask) - VERSIÓN LIGERA
from flask import Flask, render_template, abort, request, jsonify
import json
import os

# Las funciones de scraping en tiempo real para las vistas de "estudio" siguen aquí
from modules.estudio_scraper import (
    obtener_datos_completos_partido, 
    format_ah_as_decimal_string_of, 
    obtener_datos_preview_rapido, 
    obtener_datos_preview_ligero, 
    generar_analisis_mercado_simplificado,
)
# La lógica de normalización de handicap está en su propio módulo
from app_utils import normalize_handicap_to_half_bucket_str

app = Flask(__name__)

DATA_FILE = 'data.json'

def load_data_from_file():
    """Carga los datos desde el archivo JSON."""
    if not os.path.exists(DATA_FILE):
        return {"upcoming_matches": [], "finished_matches": []}
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"upcoming_matches": [], "finished_matches": []}

@app.route('/')
def index():
    """Muestra los próximos partidos desde el archivo de datos."""
    try:
        hf = request.args.get('handicap')
        all_data = load_data_from_file()
        matches = all_data.get('upcoming_matches', [])
        
        # Asegurarse de que los datos de handicap existen antes de procesar
        opts = sorted({
            normalize_handicap_to_half_bucket_str(m.get('handicap'))
            for m in matches if m and normalize_handicap_to_half_bucket_str(m.get('handicap')) is not None
        }, key=lambda x: float(x))

        if hf:
            target = normalize_handicap_to_half_bucket_str(hf)
            if target is not None:
                matches = [m for m in matches if m and normalize_handicap_to_half_bucket_str(m.get('handicap', '')) == target]

        return render_template('index.html', matches=matches, handicap_filter=hf, handicap_options=opts, page_mode='upcoming', page_title='Próximos Partidos')
    except Exception as e:
        print(f"ERROR en la ruta principal: {e}")
        return render_template('index.html', matches=[], error=f"No se pudieron cargar los partidos: {e}", page_mode='upcoming', page_title='Próximos Partidos')

@app.route('/resultados')
def resultados():
    """Muestra los partidos finalizados desde el archivo de datos."""
    try:
        hf = request.args.get('handicap')
        all_data = load_data_from_file()
        matches = all_data.get('finished_matches', [])

        opts = sorted({
            normalize_handicap_to_half_bucket_str(m.get('handicap'))
            for m in matches if m and normalize_handicap_to_half_bucket_str(m.get('handicap')) is not None
        }, key=lambda x: float(x))

        if hf:
            target = normalize_handicap_to_half_bucket_str(hf)
            if target is not None:
                matches = [m for m in matches if m and normalize_handicap_to_half_bucket_str(m.get('handicap', '')) == target]

        return render_template('index.html', matches=matches, handicap_filter=hf, handicap_options=opts, page_mode='finished', page_title='Resultados Finalizados')
    except Exception as e:
        print(f"ERROR en la ruta de resultados: {e}")
        return render_template('index.html', matches=[], error=f"No se pudieron cargar los partidos: {e}", page_mode='finished', page_title='Resultados Finalizados')


@app.route('/api/matches')
def api_matches():
    """Devuelve un fragmento de los próximos partidos para paginación."""
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 10))
        hf = request.args.get('handicap')
        
        all_data = load_data_from_file()
        matches = all_data.get('upcoming_matches', [])

        if hf:
            target = normalize_handicap_to_half_bucket_str(hf)
            if target is not None:
                matches = [m for m in matches if m and normalize_handicap_to_half_bucket_str(m.get('handicap', '')) == target]

        paginated_matches = matches[offset:offset+limit]
        return jsonify({'matches': paginated_matches})
    except Exception as e:
        print(f"Error en la ruta /api/matches: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/finished_matches')
def api_finished_matches():
    """Devuelve un fragmento de los partidos finalizados para paginación."""
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 10))
        hf = request.args.get('handicap')
        
        all_data = load_data_from_file()
        matches = all_data.get('finished_matches', [])

        if hf:
            target = normalize_handicap_to_half_bucket_str(hf)
            if target is not None:
                matches = [m for m in matches if m and normalize_handicap_to_half_bucket_str(m.get('handicap', '')) == target]

        paginated_matches = matches[offset:offset+limit]
        return jsonify({'matches': paginated_matches})
    except Exception as e:
        print(f"Error en la ruta /api/finished_matches: {e}")
        return jsonify({'error': str(e)}), 500

# --- Las rutas de API y estudio que dependen de scraping en tiempo real se mantienen ---
# --- Estas rutas seguirán haciendo scraping bajo demanda si es necesario ---


@app.route('/estudio/<string:match_id>')
def mostrar_estudio(match_id):
    print(f"Recibida petición para el estudio del partido ID: {match_id}")
    datos_partido = obtener_datos_completos_partido(match_id)
    if not datos_partido or "error" in datos_partido:
        print(f"Error al obtener datos para {match_id}: {datos_partido.get('error')}")
        abort(500, description=datos_partido.get('error', 'Error desconocido'))
    print(f"Datos obtenidos para {datos_partido['home_name']} vs {datos_partido['away_name']}. Renderizando plantilla...")
    return render_template('estudio.html', data=datos_partido, format_ah=format_ah_as_decimal_string_of)

@app.route('/analizar_partido', methods=['GET', 'POST'])
def analizar_partido():
    if request.method == 'POST':
        match_id = request.form.get('match_id')
        if match_id:
            print(f"Recibida petición para analizar partido finalizado ID: {match_id}")
            datos_partido = obtener_datos_completos_partido(match_id)
            if not datos_partido or "error" in datos_partido:
                return render_template('analizar_partido.html', error=datos_partido.get('error', 'Error desconocido'))
            
            main_odds = datos_partido.get("main_match_odds_data")
            h2h_data = datos_partido.get("h2h_data")
            home_name = datos_partido.get("home_name")
            away_name = datos_partido.get("away_name")

            analisis_simplificado_html = ""
            if all([main_odds, h2h_data, home_name, away_name]):
                analisis_simplificado_html = generar_analisis_mercado_simplificado(main_odds, h2h_data, home_name, away_name)

            print(f"Datos obtenidos para {datos_partido['home_name']} vs {datos_partido['away_name']}. Renderizando plantilla...")
            return render_template('estudio.html', 
                                   data=datos_partido, 
                                   format_ah=format_ah_as_decimal_string_of,
                                   analisis_simplificado_html=analisis_simplificado_html)
        else:
            return render_template('analizar_partido.html', error="Por favor, introduce un ID de partido válido.")
    return render_template('analizar_partido.html')

@app.route('/api/preview/<string:match_id>')
def api_preview(match_id):
    try:
        mode = request.args.get('mode', 'light').lower()
        if mode in ['full', 'selenium']:
            # La versión con Playwright es más pesada y propensa a fallar en servidores
            preview_data = obtener_datos_preview_rapido(match_id)
        else:
            # La versión ligera con requests es preferible
            preview_data = obtener_datos_preview_ligero(match_id)
        
        # Si la propia función de scraping devuelve un error, lo pasamos
        if isinstance(preview_data, dict) and "error" in preview_data:
            return jsonify(preview_data), 500
            
        return jsonify(preview_data)

    except Exception as e:
        # Si ocurre cualquier otra excepción, la capturamos y devolvemos un error detallado
        error_message = f"Ocurrió una excepción inesperada: {str(e)}"
        print(f"Error en la ruta /api/preview/{match_id}: {error_message}")
        return jsonify({'error': error_message}), 500

if __name__ == '__main__':
    # Para desarrollo local, puedes ejecutar esto.
    # Render usará Gunicorn, así que no usará este bloque en producción.
    app.run(host='0.0.0.0', port=8080, debug=True)
