# modules/analisis_reciente.py
import re
import math
from bs4 import BeautifulSoup
from modules.utils import parse_ah_to_number_of, format_ah_as_decimal_string_of, check_handicap_cover

def analizar_rendimiento_reciente_con_handicap(soup, team_name, is_home_team=True):
    """
    Analiza el rendimiento reciente de un equipo con respecto al handicap.
    
    Args:
        soup: BeautifulSoup object con el contenido de la página
        team_name: Nombre del equipo a analizar
        is_home_team: Booleano que indica si el equipo es local (True) o visitante (False)
    
    Returns:
        dict: Diccionario con el análisis del rendimiento reciente
    """
    # Determinar qué tabla usar según si es equipo local o visitante
    table_id = "table_v1" if is_home_team else "table_v2"
    table = soup.find("table", id=table_id)
    
    if not table:
        return {"error": "No se encontró la tabla de partidos recientes"}
    
    # Extraer los últimos 5 partidos del equipo
    matches = []
    score_selector = 'fscore_1' if is_home_team else 'fscore_2'
    
    for row in table.find_all("tr", id=re.compile(rf"tr{table_id[-1]}_\d+")):
        if len(matches) >= 5:  # Limitar a los últimos 5 partidos
            break
            
        # Extraer información del partido
        cells = row.find_all('td')
        if len(cells) < 12:
            continue
            
        # Obtener nombres de equipos
        home_team_cell = cells[2]
        away_team_cell = cells[4]
        
        home_team = home_team_cell.get_text(strip=True)
        away_team = away_team_cell.get_text(strip=True)
        
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
        
        matches.append({
            'home_team': home_team,
            'away_team': away_team,
            'score': score_raw,
            'ah_line_raw': ah_line_raw,
            'ah_line_num': parse_ah_to_number_of(ah_line_raw)
        })
    
    # Analizar el rendimiento
    analysis = {
        'team_name': team_name,
        'total_matches': len(matches),
        'covered': 0,
        'not_covered': 0,
        'push': 0,
        'details': []
    }
    
    for match in matches:
        # Determinar quién era el favorito
        favorito_name = None
        if match['ah_line_num'] is not None:
            if match['ah_line_num'] > 0:
                favorito_name = match['home_team']
            elif match['ah_line_num'] < 0:
                favorito_name = match['away_team']
                
        # Verificar si el equipo cubrió el handicap
        resultado = check_handicap_cover(
            match['score'], 
            match['ah_line_num'] if match['ah_line_num'] is not None else 0,
            favorito_name or "",
            match['home_team'],
            match['away_team'],
            team_name
        )
        
        # Contar resultados
        if resultado[1] is True:
            analysis['covered'] += 1
            result_text = "CUBIERTO"
        elif resultado[1] is False:
            analysis['not_covered'] += 1
            result_text = "NO CUBIERTO"
        else:
            analysis['push'] += 1
            result_text = "PUSH"
            
        analysis['details'].append({
            'home_team': match['home_team'],
            'away_team': match['away_team'],
            'score': match['score'],
            'ah_line': format_ah_as_decimal_string_of(match['ah_line_raw']) if match['ah_line_raw'] else '-',
            'result': result_text
        })
    
    return analysis

def comparar_lineas_handicap_recientes(soup, team_name, current_ah_line, is_home_team=True):
    """
    Compara las líneas de handicap recientes con la línea actual.
    
    Args:
        soup: BeautifulSoup object con el contenido de la página
        team_name: Nombre del equipo a analizar
        current_ah_line: Línea de handicap actual (número)
        is_home_team: Booleano que indica si el equipo es local (True) o visitante (False)
    
    Returns:
        dict: Diccionario con la comparación de líneas
    """
    # Obtener análisis de rendimiento reciente
    rendimiento = analizar_rendimiento_reciente_con_handicap(soup, team_name, is_home_team)
    
    if 'error' in rendimiento:
        return rendimiento
    
    # Extraer las líneas de handicap de los partidos recientes
    recent_lines = []
    for match in rendimiento['details']:
        if match['ah_line'] != '-':
            line_num = parse_ah_to_number_of(match['ah_line'])
            if line_num is not None:
                recent_lines.append(line_num)
    
    if not recent_lines:
        return {"error": "No se encontraron líneas de handicap en los partidos recientes"}
    
    # Calcular promedio de líneas recientes
    avg_recent_line = sum(recent_lines) / len(recent_lines)
    
    # Comparar con la línea actual
    comparison = {
        'current_line': current_ah_line,
        'avg_recent_line': avg_recent_line,
        'formatted_current': format_ah_as_decimal_string_of(str(current_ah_line)),
        'formatted_recent': format_ah_as_decimal_string_of(str(avg_recent_line)),
        'difference': current_ah_line - avg_recent_line,
        'trend': ''
    }
    
    # Determinar tendencia
    if comparison['difference'] > 0.25:
        comparison['trend'] = 'Línea SUBIÓ significativamente'
    elif comparison['difference'] > 0:
        comparison['trend'] = 'Línea subió ligeramente'
    elif comparison['difference'] < -0.25:
        comparison['trend'] = 'Línea BAJÓ significativamente'
    elif comparison['difference'] < 0:
        comparison['trend'] = 'Línea bajó ligeramente'
    else:
        comparison['trend'] = 'Línea estable'
    
    return comparison