import requests
import json

# Probar la API de preview
url = "http://127.0.0.1:5000/api/preview/123456"  # Un ID de prueba
try:
    response = requests.get(url)
    print("Status Code:", response.status_code)
    print("Response Headers:", response.headers)
    print("Response Text:", response.text)
    
    # Intentar parsear como JSON
    try:
        data = response.json()
        print("Response JSON:", json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        print("Error parsing JSON:", e)
        
except Exception as e:
    print("Error making request:", e)