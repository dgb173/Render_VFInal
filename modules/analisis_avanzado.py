# modules/analisis_avanzado.py
import re


def _colorear_stats(val1_str, val2_str):
    """Copia esta función helper de estudio_scraper.py para usarla aquí."""
    try:
        val1 = int(val1_str)
        val2 = int(val2_str)
        if val1 > val2:
            return f'<strong style="color: #28a745;">{val1}</strong>', f'<span style="color: #dc3545;">{val2}</span>'
        elif val2 > val1:
            return f'<span style="color: #dc3545;">{val1}</span>', f'<strong style="color: #28a745;">{val2}</strong>'
        else:
            return str(val1), str(val2)
    except (ValueError, TypeError):
        return val1_str, val2_str


def generar_analisis_comparativas_indirectas(data):
    """
    Genera una nota de análisis experta basada en los datos de las comparativas indirectas.
    """
    if not data or not data.get("comp1") or not data.get("comp2"):
        return ""


    comp1 = data["comp1"]
    comp2 = data["comp2"]


    # --- Lógica de Análisis para el Panel 1 (Yangon United FC U21) ---
    team1_name = comp1['main_team']
    team1_stats = comp1['stats']
    team1_localia = comp1['localia']


    # Determinar las estadísticas del equipo principal y su rival
    if team1_localia == 'H':
        team1_ap = team1_stats['ataques_peligrosos_casa']
        rival1_ap = team1_stats['ataques_peligrosos_fuera']
        team1_tp = team1_stats['tiros_puerta_casa']
        rival1_tp = team1_stats['tiros_puerta_fuera']
    else: # 'A'
        team1_ap = team1_stats['ataques_peligrosos_fuera']
        rival1_ap = team1_stats['ataques_peligrosos_casa']
        team1_tp = team1_stats['tiros_puerta_fuera']
        rival1_tp = team1_stats['tiros_puerta_casa']


    team1_ap_html, rival1_ap_html = _colorear_stats(team1_ap, rival1_ap)
    
    # Interpretación del resultado vs rendimiento
    goles_team1, goles_rival1 = map(int, comp1['resultado_raw'].split('-'))
    rendimiento_team1 = int(team1_ap) > int(rival1_ap)
    resultado_team1_positivo = goles_team1 > goles_rival1


    if rendimiento_team1 and not resultado_team1_positivo:
        analisis1 = f"perdió ({comp1['resultado']}) a pesar de <strong>dominar claramente el juego</strong> ({team1_ap_html} vs {rival1_ap_html} en ataques peligrosos). Esto sugiere una notable <strong>falta de efectividad o mala suerte</strong>, pero no una falta de generación de oportunidades."
    elif not rendimiento_team1 and not resultado_team1_positivo:
        analisis1 = f"fue superado tanto en el marcador ({comp1['resultado']}) como en el desarrollo del juego ({team1_ap_html} vs {rival1_ap_html} en ataques peligrosos), lo que indica una <strong>derrota merecida</strong>."
    else:
        analisis1 = f"obtuvo un resultado de {comp1['resultado']} con un rendimiento de {team1_ap_html} vs {rival1_ap_html} en ataques peligrosos."


    # Interpretación del Hándicap
    # Aquí asumimos que parse_ah_to_number_of y check_handicap_cover están disponibles o su lógica se replica
    # Para simplificar, hacemos una lógica directa:
    if comp1['ah_num'] and comp1['ah_num'] > 2: # Si era muy favorito
        analisis_ah1 = f"Además, partía como <strong>favorito abrumador (AH {comp1['ah_raw']})</strong>, haciendo que su incapacidad para ganar sea aún más significativa."
    else:
        analisis_ah1 = ""


    # --- Lógica de Análisis para el Panel 2 (Dagon FC U21) ---
    team2_name = comp2['main_team']
    team2_stats = comp2['stats']
    team2_localia = comp2['localia']
    
    if team2_localia == 'A':
        team2_ap = team2_stats['ataques_peligrosos_fuera']
        rival2_ap = team2_stats['ataques_peligrosos_casa']
    else: # 'H'
        team2_ap = team2_stats['ataques_peligrosos_casa']
        rival2_ap = team2_stats['ataques_peligrosos_fuera']


    team2_ap_html, rival2_ap_html = _colorear_stats(team2_ap, rival2_ap)
    
    goles_team2_raw = comp2['resultado_raw'].split('-')
    # Ajuste por localía: Shan (Casa) 3:0 Dagon (Fuera) -> goles_team2 = 0, goles_rival2 = 3
    goles_team2 = int(goles_team2_raw[1]) if team2_localia == 'A' else int(goles_team2_raw[0])
    goles_rival2 = int(goles_team2_raw[0]) if team2_localia == 'A' else int(goles_team2_raw[1])
    
    rendimiento_team2 = int(team2_ap) > int(rival2_ap)
    resultado_team2_positivo = goles_team2 > goles_rival2


    if not rendimiento_team2 and not resultado_team2_positivo:
        analisis2 = f"no solo perdió de forma contundente ({comp2['resultado']}), sino que también <strong>fue claramente superado en el campo</strong> ({team2_ap_html} vs {rival2_ap_html} en ataques peligrosos). Su derrota fue un reflejo fiel de su rendimiento."
    else:
        analisis2 = f"obtuvo un resultado de {comp2['resultado']} con un rendimiento de {team2_ap_html} vs {rival2_ap_html} en ataques peligrosos."
        
    # --- Construcción del HTML final ---
    html = f"""
    <div style="border-left: 4px solid #FF8C00; padding: 12px 15px; margin-top: 15px; background-color: #f0f2f6; border-radius: 5px; font-size: 0.95em;">
        <p style='margin-bottom: 12px;'><strong>📝 Nota del Analista: Comparativas Indirectas</strong></p>
        <ul style='margin: 5px 0 0 20px; padding-left: 0; list-style-type: "▸ ";'>
            <li style="margin-bottom: 8px;">
                <strong>{team1_name}:</strong> En su comparativa, {analisis1} {analisis_ah1}
            </li>
            <li>
                <strong>{team2_name}:</strong> Por su parte, {analisis2}
            </li>
        </ul>
        <p style="margin-top: 12px; font-style: italic; font-size: 0.9em; text-align: center;">
            <strong>Conclusión Clave:</strong> Mientras que la derrota de <strong>{team1_name}</strong> parece ser un tropiezo en la definición a pesar de generar juego, la de <strong>{team2_name}</strong> refleja una inferioridad más preocupante en su partido.
        </p>
    </div>
    """
    return html