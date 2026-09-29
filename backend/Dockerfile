FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 loupe

COPY pyproject.toml ./
COPY loupe ./loupe
RUN pip install --no-cache-dir . && mkdir -p /data && chown loupe:loupe /data

COPY evals ./evals
COPY tests ./tests

USER loupe
ENV LOUPE_DATABASE_URL=sqlite:////data/loupe.db
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
CMD ["uvicorn", "loupe.main:app", "--host", "0.0.0.0", "--port", "8000"]
