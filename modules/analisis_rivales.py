# modules/analisis_rivales.py
import re
from bs4 import BeautifulSoup
from modules.utils import get_match_details_from_row_of

def analizar_rivales_comunes(soup, team_a, team_b):
    """
    Analiza los rivales comunes entre dos equipos.
    
    Args:
        soup: BeautifulSoup object con el contenido de la página
        team_a: Nombre del primer equipo
        team_b: Nombre del segundo equipo
    
    Returns:
        dict: Diccionario con el análisis de rivales comunes
    """
    # Buscar tablas de partidos para ambos equipos
    table_v1 = soup.find("table", id="table_v1")  # Partidos de team_a como local
    table_v2 = soup.find("table", id="table_v2")  # Partidos de team_b como visitante
    
    if not table_v1 or not table_v2:
        return {"error": "No se encontraron las tablas de partidos"}
    
    # Extraer rivales de team_a (como local)
    rivals_a = set()
    for row in table_v1.find_all("tr", id=re.compile(r"tr1_\d+")):
        details = get_match_details_from_row_of(row, score_class_selector='fscore_1', source_table_type='hist')
        if details and team_a.lower() in details['home'].lower():
            rivals_a.add(details['away'].lower())
    
    # Extraer rivales de team_b (como visitante)
    rivals_b = set()
    for row in table_v2.find_all("tr", id=re.compile(r"tr2_\d+")):
        details = get_match_details_from_row_of(row, score_class_selector='fscore_2', source_table_type='hist')
        if details and team_b.lower() in details['away'].lower():
            rivals_b.add(details['home'].lower())
    
    # Encontrar rivales comunes
    common_rivals = rivals_a.intersection(rivals_b)
    
    # Obtener detalles de partidos contra rivales comunes
    common_matches = []
    
    # Partidos de team_a contra rivales comunes
    for row in table_v1.find_all("tr", id=re.compile(r"tr1_\d+")):
        details = get_match_details_from_row_of(row, score_class_selector='fscore_1', source_table_type='hist')
        if details and details['away'].lower() in common_rivals:
            common_matches.append({
                'team': team_a,
                'opponent': details['away'],
                'home_team': details['home'],
                'away_team': details['away'],
                'score': details['score'],
                'score_raw': details['score_raw'],
                'ah_line': details['ahLine'],
                'ah_line_raw': details['ahLine_raw'],
                'date': details['date']
            })
    
    # Partidos de team_b contra rivales comunes
    for row in table_v2.find_all("tr", id=re.compile(r"tr2_\d+")):
        details = get_match_details_from_row_of(row, score_class_selector='fscore_2', source_table_type='hist')
        if details and details['home'].lower() in common_rivals:
            common_matches.append({
                'team': team_b,
                'opponent': details['home'],
                'home_team': details['home'],
                'away_team': details['away'],
                'score': details['score'],
                'score_raw': details['score_raw'],
                'ah_line': details['ahLine'],
                'ah_line_raw': details['ahLine_raw'],
                'date': details['date']
            })
    
    # Ordenar por fecha
    common_matches.sort(key=lambda x: x['date'], reverse=True)
    
    return {
        'team_a': team_a,
        'team_b': team_b,
        'common_rivals': list(common_rivals),
        'common_rivals_count': len(common_rivals),
        'matches': common_matches[:10]  # Limitar a 10 partidos más recientes
    }

def analizar_contra_rival_del_rival(soup, team_a, team_b, rival_a_rival, rival_b_rival):
    """
    Analiza el rendimiento de cada equipo contra el rival del otro equipo.
    
    Args:
        soup: BeautifulSoup object con el contenido de la página
        team_a: Nombre del primer equipo
        team_b: Nombre del segundo equipo
        rival_a_rival: Rival del equipo A
        rival_b_rival: Rival del equipo B
    
    Returns:
        dict: Diccionario con el análisis contra el rival del rival
    """
    # Buscar tablas de partidos
    table_v1 = soup.find("table", id="table_v1")  # Partidos de team_a como local
    table_v2 = soup.find("table", id="table_v2")  # Partidos de team_b como visitante
    
    if not table_v1 or not table_v2:
        return {"error": "No se encontraron las tablas de partidos"}
    
    # Buscar partidos de team_a contra rival_b_rival
    matches_a_vs_rival_b_rival = []
    for row in table_v1.find_all("tr", id=re.compile(r"tr1_\d+")):
        details = get_match_details_from_row_of(row, score_class_selector='fscore_1', source_table_type='hist')
        if details and (
            (team_a.lower() in details['home'].lower() and rival_b_rival.lower() in details['away'].lower()) or
            (team_a.lower() in details['away'].lower() and rival_b_rival.lower() in details['home'].lower())
        ):
            matches_a_vs_rival_b_rival.append({
                'team': team_a,
                'home_team': details['home'],
                'away_team': details['away'],
                'score': details['score'],
                'score_raw': details['score_raw'],
                'ah_line': details['ahLine'],
                'ah_line_raw': details['ahLine_raw'],
                'date': details['date']
            })
    
    # Buscar partidos de team_b contra rival_a_rival
    matches_b_vs_rival_a_rival = []
    for row in table_v2.find_all("tr", id=re.compile(r"tr2_\d+")):
        details = get_match_details_from_row_of(row, score_class_selector='fscore_2', source_table_type='hist')
        if details and (
            (team_b.lower() in details['home'].lower() and rival_a_rival.lower() in details['away'].lower()) or
            (team_b.lower() in details['away'].lower() and rival_a_rival.lower() in details['home'].lower())
        ):
            matches_b_vs_rival_a_rival.append({
                'team': team_b,
                'home_team': details['home'],
                'away_team': details['away'],
                'score': details['score'],
                'score_raw': details['score_raw'],
                'ah_line': details['ahLine'],
                'ah_line_raw': details['ahLine_raw'],
                'date': details['date']
            })
    
    return {
        'team_a': team_a,
        'team_b': team_b,
        'rival_a_rival': rival_a_rival,
        'rival_b_rival': rival_b_rival,
        'matches_a_vs_rival_b_rival': matches_a_vs_rival_b_rival,
        'matches_b_vs_rival_a_rival': matches_b_vs_rival_a_rival
    }