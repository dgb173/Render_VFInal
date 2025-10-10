# modules/utils.py
import re
import math

def get_match_details_from_row_of(row_element, score_class_selector='score', source_table_type='h2h'):
    """Extrae detalles de un partido desde una fila de la tabla."""
    try:
        cells = row_element.find_all('td')
        home_idx, score_idx, away_idx, ah_idx = 2, 3, 4, 11
        if len(cells) <= ah_idx: 
            return None
        date_span = cells[1].find('span', attrs={'name': 'timeData'})
        date_txt = date_span.get_text(strip=True) if date_span else ''
        
        def get_cell_txt(idx):
            a = cells[idx].find('a')
            return a.get_text(strip=True) if a else cells[idx].get_text(strip=True)
            
        home, away = get_cell_txt(home_idx), get_cell_txt(away_idx)
        if not home or not away: 
            return None
            
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

def parse_ah_to_number_of(ah_line_str: str):
    """Convierte una línea de handicap asiático de string a número."""
    if not isinstance(ah_line_str, str): 
        return None
    s = ah_line_str.strip().replace(' ', '')
    if not s or s in ['-', '?']: 
        return None
        
    try:
        if '/' in s:
            parts = s.split('/')
            if len(parts) != 2: 
                return None
            p1_str, p2_str = parts[0], parts[1]
            val1 = float(p1_str)
            val2 = float(p2_str)
            # Ajustar signo para fracciones mixtas
            if val1 < 0 and val2 > 0:
                val2 = -abs(val2)
            return (val1 + val2) / 2.0
        else:
            return float(s)
    except (ValueError, IndexError):
        return None

def format_ah_as_decimal_string_of(ah_line_str: str, for_sheets=False):
    """Formatea una línea de handicap asiático como string decimal."""
    if not isinstance(ah_line_str, str) or not ah_line_str.strip() or ah_line_str.strip() in ['-', '?']:
        return ah_line_str.strip() if isinstance(ah_line_str, str) and ah_line_str.strip() in ['-','?'] else '-'
        
    numeric_value = parse_ah_to_number_of(ah_line_str)
    if numeric_value is None:
        return ah_line_str.strip() if ah_line_str.strip() in ['-','?'] else '-'
        
    if numeric_value == 0.0: 
        return "0"
        
    # Redondear y formatear
    sign = -1 if numeric_value < 0 else 1
    abs_num = abs(numeric_value)
    
    # Redondeo especial para líneas asiáticas
    if abs_num % 1 == 0.0:
        result = int(abs_num)
    elif abs_num % 1 == 0.5:
        result = abs_num
    elif abs_num % 1 == 0.25 or abs_num % 1 == 0.75:
        result = abs_num
    else:
        # Redondeo al 0.25 más cercano
        result = round(abs_num * 4) / 4
        
    final_value = sign * result
    
    # Formatear como string
    if final_value == int(final_value):
        output_str = str(int(final_value))
    else:
        output_str = f"{final_value:.2f}".rstrip('0').rstrip('.')
        
    return output_str

def check_handicap_cover(resultado_raw: str, ah_line_num: float, favorite_team_name: str, 
                        home_team_in_h2h: str, away_team_in_h2h: str, main_home_team_name: str):
    """Verifica si un equipo cubrió el handicap en un partido."""
    try:
        goles_h, goles_a = map(int, resultado_raw.split('-'))
        
        # Caso de handicap asiático 0
        if ah_line_num == 0.0:
            if main_home_team_name.lower() == home_team_in_h2h.lower():
                if goles_h > goles_a: 
                    return ("CUBIERTO", True)
                elif goles_a > goles_h: 
                    return ("NO CUBIERTO", False)
                else: 
                    return ("PUSH", None)
            else:
                if goles_a > goles_h: 
                    return ("CUBIERTO", True)
                elif goles_h > goles_a: 
                    return ("NO CUBIERTO", False)
                else: 
                    return ("PUSH", None)
        
        # Determinar margen de victoria del favorito
        if favorite_team_name.lower() == home_team_in_h2h.lower():
            favorite_margin = goles_h - goles_a
        elif favorite_team_name.lower() == away_team_in_h2h.lower():
            favorite_margin = goles_a - goles_h
        else:
            return ("indeterminado", None)
        
        # Verificar si se cubrió el handicap
        if favorite_margin - abs(ah_line_num) > 0.05:
            return ("CUBIERTO", True)
        elif favorite_margin - abs(ah_line_num) < -0.05:
            return ("NO CUBIERTO", False)
        else:
            return ("PUSH", None)
    except (ValueError, TypeError, AttributeError):
        return ("indeterminado", None)

def check_goal_line_cover(resultado_raw: str, goal_line_num: float = 2.5):
    """Verifica si un partido superó la línea de goles."""
    try:
        goles_h, goles_a = map(int, resultado_raw.split('-'))
        total_goles = goles_h + goles_a
        
        if total_goles > goal_line_num:
            return ("SUPERADA (Over)", True)
        elif total_goles < goal_line_num:
            return ("NO SUPERADA (Under)", False)
        else:
            return ("PUSH (Empate)", None)
    except (ValueError, TypeError):
        return ("indeterminado", None)

def extract_final_score_of(soup):
    """
    Extrae el resultado final del partido desde el header.
    """
    try:
        score_div = soup.find("div", id="mScore")
        if not score_div:
            return "vs"

        # Intento 1: Selector específico para partidos finalizados
        scores = score_div.select("div.end div.score")
        if len(scores) == 2:
            return f"{scores[0].text.strip()} - {scores[1].text.strip()}"

        # Intento 2: Selector más general dentro de mScore
        scores = score_div.find_all("div", class_="score")
        if len(scores) == 2:
            return f"{scores[0].text.strip()} - {scores[1].text.strip()}"

        # Si no se encuentra, verificar el estado del partido
        state_div = score_div.find("div", class_="state")
        if state_div and "Finished" not in state_div.text:
            return "vs" # Not finished
            
    except Exception:
        return "vs"
    
    return "vs"