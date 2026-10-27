# Verificador de Talhões v2 (backend FastAPI + tela React), numa imagem só.
#   docker compose up -d --build        -> http://<servidor>:8080
# 1) compila a tela
FROM node:20-alpine AS tela
WORKDIR /tela
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2) aplicação Python (as rodas do pyogrio/pyproj já trazem GDAL e PROJ)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VT_DADOS=/dados \
    VT_FRONTEND=/app/frontend/dist
# bsdtar (libarchive) abre os .rar enviados
RUN apt-get update && apt-get install -y --no-install-recommends libarchive-tools && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY --from=tela /tela/dist /app/frontend/dist

# não roda como root; o banco, o segredo dos tokens e nada mais ficam em /dados (volume)
RUN useradd --system --uid 10001 --no-create-home verificador && mkdir -p /dados && chown verificador /dados
USER verificador
VOLUME ["/dados"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/saude', timeout=4)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "30"]
