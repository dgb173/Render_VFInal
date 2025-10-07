# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1     GOOGLE_CHROME_BIN=/usr/bin/chromium     CHROMEDRIVER_PATH=/usr/bin/chromedriver

RUN apt-get update     && apt-get install -y --no-install-recommends         ca-certificates         curl         fonts-liberation         libasound2         libatk-bridge2.0-0         libatk1.0-0         libatspi2.0-0         libcairo2         libdrm2         libgbm1         libgtk-3-0         libnspr4         libnss3         libpango-1.0-0         libx11-xcb1         libxcomposite1         libxdamage1         libxext6         libxfixes3         libxkbcommon0         libxrandr2         libxrender1         libxshmfence1         libu2f-udev         libvulkan1         chromium         chromium-driver     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["sh", "-c", "exec gunicorn -k uvicorn.workers.UvicornWorker -w 4 app:asgi_app --bind 0.0.0.0:${PORT:-5000}"]
