# API image. Build context: the repository root.
# Pinned by digest (tag kept for reading); refresh when the security job reports fixable CVEs.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
 # fetch the DuckDB spatial extension at build time so the container needs no network at run time
 && python -c "import duckdb; duckdb.connect().execute('INSTALL spatial')" \
 # pip is not used at run time; the base image's pip 25.0.1 has fixable CVEs (Trivy: 5 MEDIUM, 1 LOW) and the
 # current pip vendors urllib3 2.7.0 and msgpack 1.1.2, which have their own, so it is removed from the image
 && python -m pip uninstall -y pip

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
