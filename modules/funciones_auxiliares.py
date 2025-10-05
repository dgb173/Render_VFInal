# modules/funciones_auxiliares.py
from modules.utils import parse_ah_to_number_of

def _calcular_estadisticas_contra_rival(matches, equipo):
    """
    Calcula estadísticas resumidas para un equipo contra un rival específico.
    
    Args:
        matches: Lista de partidos
        equipo: Nombre del equipo a analizar
        
    Returns:
        dict: Estadísticas resumidas
    """
    if not matches:
        return {'victorias': 0, 'total': 0, 'over': 0, 'ah_cubierto': 0}
    
    victorias = 0
    over = 0
    ah_cubierto = 0
    
    for match in matches:
        # Calcular victorias
        if '-' in match['score_raw']:
            try:
                goles_local, goles_visitante = map(int, match['score_raw'].split('-'))
                
                if match['home_team'].lower() == equipo.lower() and goles_local > goles_visitante:
                    victorias += 1
                elif match['away_team'].lower() == equipo.lower() and goles_visitante > goles_local:
                    victorias += 1
            except (ValueError, TypeError):
                pass
        
        # Calcular over/under
        over_under = _analizar_over_under(match['score_raw'])
        if over_under == 'Over':
            over += 1
            
        # Calcular handicap cubierto
        ah_result = _analizar_ah_cubierto(match['score_raw'], match['ah_line_raw'], equipo, match['home_team'], match['away_team'])
        if ah_result == 'Cubierto':
            ah_cubierto += 1
    
    return {
        'victorias': victorias,
        'total': len(matches),
        'over': over,
        'ah_cubierto': ah_cubierto
    }

def _analizar_over_under(resultado):
    """
    Analiza si un resultado fue over o under (más de 2.5 goles).
    
    Args:
        resultado: String con el resultado en formato "goles_local-goles_visitante"
        
    Returns:
        str: "Over", "Under" o "N/A"
    """
    if not resultado or '-' not in resultado:
        return "N/A"
    
    try:
        goles_local, goles_visitante = map(int, resultado.split('-'))
        total_goles = goles_local + goles_visitante
        
        if total_goles > 2.5:
            return "Over"
        elif total_goles < 2.5:
            return "Under"
        else:
            # Empate exacto en 2.5 goles - técnicamente sería Push
            return "Push"
    except (ValueError, TypeError):
        return "N/A"

def _analizar_ah_cubierto(resultado, handicap_raw, equipo_favorito, equipo_local, equipo_visitante):
    """
    Analiza si un equipo cubrió el handicap asiático.
    
    Args:
        resultado: String con el resultado en formato "goles_local-goles_visitante"
        handicap_raw: String con la línea de handicap
        equipo_favorito: Nombre del equipo considerado favorito
        equipo_local: Nombre del equipo local
        equipo_visitante: Nombre del equipo visitante
        
    Returns:
        str: "Cubierto", "No Cubierto", "Push" o "N/A"
    """
    if not resultado or '-' not in resultado or not handicap_raw:
        return "N/A"
    
    try:
        goles_local, goles_visitante = map(int, resultado.split('-'))
        handicap_num = parse_ah_to_number_of(handicap_raw)
        
        if handicap_num is None:
            return "N/A"
        
        # Determinar margen de victoria del favorito
        if equipo_favorito.lower() == equipo_local.lower():
            margen_favorito = goles_local - goles_visitante
        elif equipo_favorito.lower() == equipo_visitante.lower():
            margen_favorito = goles_visitante - goles_local
        else:
            return "N/A"
        
        # Verificar si se cubrió el handicap
        if abs(margen_favorito) - abs(handicap_num) > 0.05:
            return "Cubierto"
        elif abs(margen_favorito) - abs(handicap_num) < -0.05:
            return "No Cubierto"
        else:
            return "Push"
    except (ValueError, TypeError):
        return "N/A"

def _analizar_desempeno_casa_fuera(matches, equipo):
    """
    Analiza el desempeño de un equipo en casa y fuera.
    
    Args:
        matches: Lista de partidos
        equipo: Nombre del equipo a analizar
        
    Returns:
        dict: Estadísticas de desempeño en casa y fuera
    """
    casa = {'victorias': 0, 'total': 0}
    fuera = {'victorias': 0, 'total': 0}
    
    for match in matches:
        # Verificar si el equipo jugó como local o visitante
        if match['home_team'].lower() == equipo.lower():
            # Jugó como local
            casa['total'] += 1
            if '-' in match['score_raw']:
                try:
                    goles_local, goles_visitante = map(int, match['score_raw'].split('-'))
                    if goles_local > goles_visitante:
                        casa['victorias'] += 1
                except (ValueError, TypeError):
                    pass
        elif match['away_team'].lower() == equipo.lower():
            # Jugó como visitante
            fuera['total'] += 1
            if '-' in match['score_raw']:
                try:
                    goles_local, goles_visitante = map(int, match['score_raw'].split('-'))
                    if goles_visitante > goles_local:
                        fuera['victorias'] += 1
                except (ValueError, TypeError):
                    pass
    
    return {
        'casa': casa,
        'fuera': fuera
    }

def _contar_victorias_h2h(matches, equipo):
    """
    Cuenta las victorias de un equipo en partidos H2H.
    
    Args:
        matches: Lista de partidos H2H
        equipo: Nombre del equipo a analizar
        
    Returns:
        int: Número de victorias
    """
    victorias = 0
    for match in matches:
        if '-' in match['score_raw']:
            try:
                goles_local, goles_visitante = map(int, match['score_raw'].split('-'))
                if match['home_team'].lower() == equipo.lower() and goles_local > goles_visitante:
                    victorias += 1
                elif match['away_team'].lower() == equipo.lower() and goles_visitante > goles_local:
                    victorias += 1
            except (ValueError, TypeError):
                pass
    return victorias

def _analizar_over_under_h2h(resultado):
    """
    Analiza si un resultado H2H fue over o under.
    
    Args:
        resultado: String con el resultado en formato "goles_local-goles_visitante"
        
    Returns:
        str: "Over", "Under" o "N/A"
    """
    if not resultado or '-' not in resultado:
        return "N/A"
    
    try:
        goles_local, goles_visitante = map(int, resultado.split('-'))
        total_goles = goles_local + goles_visitante
        
        if total_goles > 2.5:
            return "Over"
        elif total_goles < 2.5:
            return "Under"
        else:
            # Empate exacto en 2.5 goles - técnicamente sería Push
            return "Push"
    except (ValueError, TypeError):
        return "N/A"

def _contar_over_h2h(matches):
    """
    Cuenta cuántos partidos H2H fueron over.
    
    Args:
        matches: Lista de partidos H2H
        
    Returns:
        int: Número de partidos over
    """
    over_count = 0
    for match in matches:
        if _analizar_over_under_h2h(match['score_raw']) == 'Over':
            over_count += 1
    return over_count

def _contar_victorias_h2h_general(matches, equipo):
    """
    Cuenta las victorias de un equipo en partidos H2H generales.
    
    Args:
        matches: Lista de partidos H2H generales
        equipo: Nombre del equipo a analizar
        
    Returns:
        int: Número de victorias
    """
    victorias = 0
    for match in matches:
        if '-' in match['score_raw']:
            try:
                goles_local, goles_visitante = map(int, match['score_raw'].split('-'))
                if match['home_team'].lower() == equipo.lower() and goles_local > goles_visitante:
                    victorias += 1
                elif match['away_team'].lower() == equipo.lower() and goles_visitante > goles_local:
                    victorias += 1
            except (ValueError, TypeError):
                pass
    return victorias