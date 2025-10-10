import asyncio
import json

# Importamos las funciones de scraping desde el nuevo módulo
from scraping_logic import get_main_page_matches_async, get_main_page_finished_matches_async

async def main():
    """
    Función principal que ejecuta ambos scrapers y combina los resultados.
    """
    print("Iniciando el proceso de scraping principal...")
    
    # Obtenemos los partidos próximos y los finalizados en paralelo
    proximos, finalizados = await asyncio.gather(
        get_main_page_matches_async(limit=500), # Aumentamos el límite para tener más datos
        get_main_page_finished_matches_async(limit=500)
    )
    
    print(f"Scraping de listas finalizado. {len(proximos)} partidos próximos y {len(finalizados)} finalizados.")

    # Creamos un diccionario con todos los datos
    scraped_data = {
        "upcoming_matches": proximos,
        "finished_matches": finalizados
    }
    
    # Guardamos los datos en el archivo data.json
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(scraped_data, f, indent=2, ensure_ascii=False)
    
    print("Archivo data.json guardado correctamente.")

if __name__ == "__main__":
    asyncio.run(main())