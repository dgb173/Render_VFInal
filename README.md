# Proyecto Nowgoal Flask

Aplicación Flask que expone análisis de partidos con scraping desde Nowgoal.

## Deploy en Render

**Build Command**

```
pip install -r requirements.txt && python -m playwright install --with-deps chromium
```

**Start Command**

```
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120
```

**Health check path:** `/healthz`

**Env vars sugeridas:** `LOG_LEVEL=INFO`, `TZ=UTC`, `RENDER=1`

**Notas:**

- `--with-deps` instala paquetes de sistema para Chromium.
- Playwright se lanza con `--no-sandbox --disable-dev-shm-usage`.
- Si el origen bloquea IPs de datacenter, incluso con Playwright puede no aparecer la tabla. Es comportamiento del sitio (sin workarounds de evasión).
