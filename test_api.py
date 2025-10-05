import requests
import json

# Probar la API de preview
url = "http://localhost:5000/api/preview/123456"  # Reemplaza con un ID válido
try:
    response = requests.get(url)
    print("Status Code:", response.status_code)
    print("Response JSON:")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
except Exception as e:
    print("Error:", e)