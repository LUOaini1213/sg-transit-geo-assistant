# API image. Build context: the repository root.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
 # fetch the DuckDB spatial extension at build time so the container needs no network at run time
 && python -c "import duckdb; duckdb.connect().execute('INSTALL spatial')"

COPY backend/app backend/app
COPY backend/__init__.py backend/__init__.py
COPY backend/docker-entrypoint.sh backend/docker-entrypoint.sh
COPY data/fixture data/fixture

RUN useradd --create-home --uid 10001 app && chown -R app /app \
 && cp -r /root/.duckdb /home/app/.duckdb && chown -R app /home/app/.duckdb
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
ENTRYPOINT ["sh", "backend/docker-entrypoint.sh"]
