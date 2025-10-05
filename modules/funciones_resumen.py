# modules/funciones_resumen.py
import re
from bs4 import BeautifulSoup
from modules.utils import parse_ah_to_number_of, format_ah_as_decimal_string_of, check_handicap_cover

def generar_resumen_rendimiento_reciente(soup, home_name, away_name, current_ah_line):
    """
    Genera un resumen gráfico del rendimiento reciente y comparativas indirectas,
    analizando la colocación de handicap de la misma manera que el apartado 
    "análisis de mercado vs histórico H2H".
    
    Args:
        soup: BeautifulSoup object con el contenido de la página
        home_name: Nombre del equipo local
        away_name: Nombre del equipo visitante
        current_ah_line: Línea de handicap actual (número)
    
    Returns:
        dict: Diccionario con el resumen del rendimiento reciente
    """
    # Obtener partidos recientes para ambos equipos
    partidos_local = _obtener_partidos_recientes(soup, "table_v1", home_name, True)
    partidos_visitante = _obtener_partidos_recientes(soup, "table_v2", away_name, False)
    
    # Analizar rendimiento reciente
    analisis_local = _analizar_rendimiento(partidos_local, current_ah_line, home_name)
    analisis_visitante = _analizar_rendimiento(partidos_visitante, current_ah_line, away_name)
    
    # Obtener comparativas indirectas
    comparativas = _obtener_comparativas_indirectas(soup)
    
    # Generar resumen
    resumen = {
        'equipo_local': {
            'nombre': home_name,
            'partidos': analisis_local['partidos'],
            'tendencia_handicap': analisis_local['tendencia'],
            'rendimiento_reciente': analisis_local['rendimiento']
        },
        'equipo_visitante': {
            'nombre': away_name,
            'partidos': analisis_visitante['partidos'],
            'tendencia_handicap': analisis_visitante['tendencia'],
            'rendimiento_reciente': analisis_visitante['rendimiento']
        },
        'comparativas_indirectas': comparativas,
        'analisis_comparativo': _generar_analisis_comparativo(analisis_local, analisis_visitante, current_ah_line)
    }
    
    return resumen

def _obtener_partidos_recientes(soup, table_id, team_name, is_home_team=True):
    """Obtiene los partidos recientes de un equipo."""
    table = soup.find("table", id=table_id)
    if not table:
        return []
    
    partidos = []
    score_selector = 'fscore_1' if is_home_team else 'fscore_2'
    
    for row in table.find_all("tr", id=re.compile(rf"tr{table_id[-1]}_\d+")):
        if len(partidos) >= 5:  # Limitar a 5 partidos recientes
            break
            
        cells = row.find_all('td')
        if len(cells) < 12:
            continue
            
        # Obtener nombres de equipos
        home_team = cells[2].get_text(strip=True)
        away_team = cells[4].get_text(strip=True)
        
        # Verificar si el equipo está en este partido
        if team_name.lower() not in [home_team.lower(), away_team.lower()]:
            continue
            
        # Obtener resultado
        score_cell = cells[3]
        score_span = score_cell.find('span', class_=score_selector)
        if not score_span:
            continue
            
        score_raw = score_span.get_text(strip=True)
        if '-' not in score_raw:
            continue
            
        # Obtener handicap
        ah_cell = cells[11]
        ah_line_raw = (ah_cell.get('data-o') or ah_cell.text).strip()
        
        # Determinar si el equipo era favorito
        ah_line_num = parse_ah_to_number_of(ah_line_raw)
        favorito = None
        if ah_line_num is not None:
            if ah_line_num > 0:
                favorito = home_team
            elif ah_line_num < 0:
                favorito = away_team
        
        partidos.append({
            'home_team': home_team,
            'away_team': away_team,
            'score': score_raw,
            'ah_line_raw': ah_line_raw,
            'ah_line_num': ah_line_num,
            'favorito': favorito,
            'equipo_es_favorito': team_name.lower() == favorito.lower() if favorito else False
        })
    
    return partidos

def _analizar_rendimiento(partidos, current_ah_line, team_name):
    """Analiza el rendimiento de un equipo en partidos recientes."""
    if not partidos:
        return {'partidos': [], 'tendencia': 'No hay datos', 'rendimiento': 'No hay datos'}
    
    # Calcular tendencia de handicap
    lineas_handicap = [p['ah_line_num'] for p in partidos if p['ah_line_num'] is not None]
    if lineas_handicap:
        promedio_linea = sum(lineas_handicap) / len(lineas_handicap)
        diferencia = current_ah_line - promedio_linea if current_ah_line is not None else 0
        
        if diferencia > 0.25:
            tendencia = 'Línea SUBIÓ significativamente'
        elif diferencia > 0:
            tendencia = 'Línea subió'
        elif diferencia < -0.25:
            tendencia = 'Línea BAJÓ significativamente'
        elif diferencia < 0:
            tendencia = 'Línea bajó'
        else:
            tendencia = 'Línea estable'
    else:
        tendencia = 'No hay datos de handicap'
        promedio_linea = None
    
    # Calcular rendimiento reciente
    victorias = 0
    total_partidos = len(partidos)
    
    for partido in partidos:
        if '-' in partido['score']:
            try:
                goles_local, goles_visitante = partido['score'].split('-')
                goles_local, goles_visitante = int(goles_local), int(goles_visitante)
                
                # Verificar si el equipo ganó
                if partido['home_team'].lower() == team_name.lower() and goles_local > goles_visitante:
                    victorias += 1
                elif partido['away_team'].lower() == team_name.lower() and goles_visitante > goles_local:
                    victorias += 1
            except (ValueError, IndexError):
                continue
    
    rendimiento = f"{victorias}/{total_partidos} victorias recientes"
    
    return {
        'partidos': partidos,
        'tendencia': tendencia,
        'rendimiento': rendimiento,
        'promedio_linea': promedio_linea
    }

def _obtener_comparativas_indirectas(soup):
    """Obtiene las comparativas indirectas."""
    # Buscar información de comparativas indirectas
    comparativas = []
    
    # Buscar en las tablas de partidos rivales
    table_v1 = soup.find("table", id="table_v1")  # Partidos del equipo local
    table_v2 = soup.find("table", id="table_v2")  # Partidos del equipo visitante
    
    if table_v1 and table_v2:
        # Obtener rivales del equipo local
        rivales_local = set()
        for row in table_v1.find_all("tr", id=re.compile(r"tr1_\d+")):
            cells = row.find_all('td')
            if len(cells) >= 5:
                rival = cells[4].get_text(strip=True)  # Equipo visitante
                if rival and rival != '?':
                    rivales_local.add(rival.lower())
        
        # Obtener rivales del equipo visitante
        rivales_visitante = set()
        for row in table_v2.find_all("tr", id=re.compile(r"tr2_\d+")):
            cells = row.find_all('td')
            if len(cells) >= 5:
                rival = cells[2].get_text(strip=True)  # Equipo local
                if rival and rival != '?':
                    rivales_visitante.add(rival.lower())
        
        # Encontrar rivales comunes
        rivales_comunes = rivales_local.intersection(rivales_visitante)
        
        # Para cada rival común, obtener información de partidos
        for rival in list(rivales_comunes)[:3]:  # Limitar a 3 rivales comunes
            # Buscar partido del equipo local contra este rival
            partido_local = None
            for row in table_v1.find_all("tr", id=re.compile(r"tr1_\d+")):
                cells = row.find_all('td')
                if len(cells) >= 5 and cells[4].get_text(strip=True).lower() == rival:
                    score = cells[3].get_text(strip=True)
                    ah_cell = cells[11] if len(cells) > 11 else None
                    handicap = ah_cell.get('data-o') or ah_cell.get_text(strip=True) if ah_cell else "-"
                    
                    partido_local = {
                        'equipo': 'local',
                        'rival': rival,
                        'resultado': score,
                        'handicap': handicap
                    }
                    break
            
            # Buscar partido del equipo visitante contra este rival
            partido_visitante = None
            for row in table_v2.find_all("tr", id=re.compile(r"tr2_\d+")):
                cells = row.find_all('td')
                if len(cells) >= 5 and cells[2].get_text(strip=True).lower() == rival:
                    score = cells[3].get_text(strip=True)
                    ah_cell = cells[11] if len(cells) > 11 else None
                    handicap = ah_cell.get('data-o') or ah_cell.get_text(strip=True) if ah_cell else "-"
                    
                    partido_visitante = {
                        'equipo': 'visitante',
                        'rival': rival,
                        'resultado': score,
                        'handicap': handicap
                    }
                    break
            
            if partido_local and partido_visitante:
                comparativas.append({
                    'rival': rival,
                    'partido_local': partido_local,
                    'partido_visitante': partido_visitante
                })
    
    return comparativas

def _generar_analisis_comparativo(analisis_local, analisis_visitante, current_ah_line):
    """Genera un análisis comparativo entre ambos equipos."""
    analisis = {
        'ventaja_handicap': '',
        'tendencia_favorable': '',
        'rendimiento_reciente': ''
    }
    
    # Comparar tendencias de handicap
    linea_local = analisis_local.get('promedio_linea')
    linea_visitante = analisis_visitante.get('promedio_linea')
    
    if linea_local is not None and linea_visitante is not None:
        if linea_local < linea_visitante:
            analisis['ventaja_handicap'] = f"El equipo local ({analisis_local['partidos'][0]['home_team']}) ha tenido líneas más bajas, lo que podría indicar menos favoritismo en partidos recientes."
        elif linea_visitante < linea_local:
            analisis['ventaja_handicap'] = f"El equipo visitante ({analisis_visitante['partidos'][0]['away_team']}) ha tenido líneas más bajas, lo que podría indicar menos favoritismo en partidos recientes."
        else:
            analisis['ventaja_handicap'] = "Ambos equipos han tenido líneas similares en partidos recientes."
    
    # Comparar rendimiento reciente
    rendimiento_local = analisis_local.get('rendimiento', '0/0')
    rendimiento_visitante = analisis_visitante.get('rendimiento', '0/0')
    
    # Extraer números de victorias
    try:
        local_victorias = int(rendimiento_local.split('/')[0])
        visitante_victorias = int(rendimiento_visitante.split('/')[0])
        
        if local_victorias > visitante_victorias:
            analisis['rendimiento_reciente'] = f"El equipo local tiene mejor rendimiento reciente ({rendimiento_local} vs {rendimiento_visitante})."
        elif visitante_victorias > local_victorias:
            analisis['rendimiento_reciente'] = f"El equipo visitante tiene mejor rendimiento reciente ({rendimiento_visitante} vs {rendimiento_local})."
        else:
            analisis['rendimiento_reciente'] = f"Ambos equipos tienen rendimiento similar ({rendimiento_local})."
    except (ValueError, IndexError):
        analisis['rendimiento_reciente'] = "No se puede comparar el rendimiento reciente por falta de datos."
    
    return analisis