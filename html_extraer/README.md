# Carpeta `HTML_extraer`

## Propósito

Esta carpeta contiene archivos HTML que sirven como **fuente de datos** para la aplicación. El archivo principal, `analisis_model.txt`, es una copia del código HTML de una página de análisis de partido específica de NowGoal. Este HTML es procesado por scripts de scraping para extraer información detallada sobre partidos, equipos, estadísticas y cuotas.

Estos archivos **NO** forman parte de la interfaz gráfica de la aplicación web. Son archivos de trabajo intermedios utilizados por los módulos de scraping (`modules/estudio_scraper.py`) para obtener y procesar la información que se muestra al usuario en la aplicación Flask.

---

## Estructura del Proyecto (Resumen)

Para entender mejor el contexto de esta carpeta, aquí está la estructura general del proyecto:

```
raíz_del_proyecto/
├── app.py                 # Servidor web principal (Flask)
├── scraper_partidos.py    # Scraper inicial para lista de partidos
├── modules/
│   ├── estudio_scraper.py # Módulo principal de scraping y análisis detallado
│   └── ...                # Otros módulos de análisis (rendimiento, rivales, etc.)
├── templates/
│   ├── index.html         # Plantilla principal de la lista de partidos
│   ├── estudio.html       # Plantilla para mostrar el análisis detallado
│   └── ...                # Otras plantillas parciales
├── HTML_extraer/          # <-- Esta carpeta
│   ├── analisis_model.txt # (Este archivo) Fuente de datos HTML para scraping
│   └── README.md          # (Este archivo) Documentación
└── ...
```

---

## Flujo de Trabajo de la Aplicación

1.  **Inicio:** El usuario accede a la aplicación Flask (por ejemplo, `http://localhost:5000/`).
2.  **Lista de Partidos:** `app.py` ejecuta `scraper_partidos.py` para obtener una lista de próximos partidos desde NowGoal.
3.  **Selección:** El usuario selecciona un partido de la lista.
4.  **Análisis Detallado:**
    *   `app.py` llama a la función `obtener_datos_completos_partido(match_id)` en `modules/estudio_scraper.py`.
    *   `estudio_scraper.py` utiliza Selenium para navegar a la URL del análisis del partido en NowGoal (`https://live18.nowgoal25.com/match/h2h-{match_id}`).
    *   El scraper selecciona "Bet365" en los menús desplegables de cuotas para obtener las líneas iniciales.
    *   El scraper descarga el HTML completo de la página.
5.  **Extracción de Datos:** `estudio_scraper.py` analiza el HTML descargado (similar a la estructura de `analisis_model.txt`) para extraer:
    *   Nombres de equipos y liga.
    *   Cuotas iniciales de Bet365 (Handicap Asiático y Línea de Goles).
    *   Datos de clasificación (Standings).
    *   Estadísticas de Over/Under.
    *   Información de partidos históricos (Rendimiento Reciente, H2H Directo, H2H Indirecto, Comparativas Indirectas).
    *   IDs de partidos históricos para obtener estadísticas en tiempo real.
6.  **Obtención de Estadísticas Adicionales:** Para ciertos partidos históricos, se realizan llamadas HTTP adicionales para obtener estadísticas de progresión (disparos, ataques, etc.).
7.  **Análisis y Generación de HTML:** El scraper realiza análisis comparativos y genera bloques de HTML personalizados (por ejemplo, para el "Análisis de Mercado").
8.  **Renderizado:** `app.py` pasa todos los datos extraídos y el HTML generado a la plantilla `templates/estudio.html`.
9.  **Visualización:** Flask renderiza la plantilla `estudio.html` con los datos y la presenta al usuario en el navegador.

---

## Contenido de `analisis_model.txt`

Este archivo es un ejemplo representativo del HTML que se descarga y procesa. Contiene secciones para:

*   **Encabezado del partido:** Equipos, resultado final, cuotas iniciales.
*   **Comparación de cuotas en vivo:** (No utilizada en el análisis detallado).
*   **Comparación de fuerza (Strength Comparison):** (No utilizada en el análisis detallado).
*   **Predicción de voto (Who will win?):** (No utilizada en el análisis detallado).
*   **Clasificación (Cup Standings):** Datos utilizados para la sección de clasificación.
*   **Estadísticas H2H Directas (Head to Head Statistics):** *Esta sección fue eliminada del análisis según instrucciones previas.*
*   **Estadísticas de Partidos Anteriores (Previous Scores Statistics):** Datos clave para "Rendimiento Reciente" y "H2H Indirecto". Incluye tablas para el equipo Local (`table_v1`) y el equipo Visitante (`table_v2`).
*   **Estadísticas de Cuotas Históricas (Same Historical Odds Statistics):** (No utilizada en el análisis detallado).
*   **Estadísticas de Cuotas (Odds Statistics):** (No utilizada en el análisis detallado).
*   **Estadísticas de Goles (Goal Scoring Statistics):** (No utilizada en el análisis detallado).
*   **Tiempo Medio de Gol (Half Time / Full Time):** (No utilizada en el análisis detallado).
*   **Calendario (Fixture):** (No utilizada en el análisis detallado).

---

## Nuevas Funcionalidades y Modificaciones

*   **Eliminación de "Análisis H2H Directo":** La sección correspondiente (`<div class="porletP" id="porletP5">` y su contenido) ha sido eliminada del archivo `analisis_model.txt`.
*   **Limitación de Partidos en "Rendimiento Reciente":** Los selectores `<select id="selectMatchCount1">` y `<select id="selectMatchCount2">` han sido modificados para que la opción "Last 2" esté seleccionada por defecto, limitando la visualización a los 2 últimos partidos de cada equipo.
*   **[Pendiente] Línea de Gol Inicial de Bet365:** Se solicita agregar la visualización de la línea de goles inicial de Bet365 en las tarjetas de los partidos mostrados en las secciones:
    *   Rendimiento Reciente
    *   H2H Indirecto
    *   Comparativas Indirectas
    *   Enfrentamientos Directos H2H
