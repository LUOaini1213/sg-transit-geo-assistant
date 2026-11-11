# API image. Build context: the repository root.
# Pinned by digest (tag kept for reading); refresh when the security job reports fixable CVEs.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# The pinned image can lag Debian's security fixes (2026-11-11: 2 fixable HIGH in zlib1g five days after the
# last re-pin), so apply Debian's package updates at build time, as the web image does with apk.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \n && rm -rf /var/lib/apt/lists/*

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
