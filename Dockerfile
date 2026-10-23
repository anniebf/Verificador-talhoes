# Verificador de Talhões
#   docker build -t verificador-talhoes .
#   docker run -d -p 8080:8080 --name verificador-talhoes verificador-talhoes
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# dependências primeiro: só reinstalam quando o requirements.txt muda
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY analise.py correcao.py app.py fazendas.csv ./
COPY static ./static

# não roda como root
RUN useradd --system --uid 10001 --no-create-home verificador
USER verificador

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=4)"

CMD ["python", "app.py", "--host", "0.0.0.0", "--porta", "8080"]
