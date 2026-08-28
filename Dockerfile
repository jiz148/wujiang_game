ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system --gid 10001 wujiang \
    && adduser --system --uid 10001 --ingroup wujiang --home /nonexistent --no-create-home wujiang

COPY --chown=wujiang:wujiang run.py /app/run.py
COPY --chown=wujiang:wujiang src /app/src
COPY --chown=wujiang:wujiang static /app/static
COPY --chown=wujiang:wujiang data /app/data

RUN mkdir -p /app/var /app/replays \
    && chown -R wujiang:wujiang /app/var /app/replays

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/api/health/live', headers={'X-Forwarded-Proto':'https','X-Forwarded-Host':'health.internal'}); raise SystemExit(0 if urllib.request.urlopen(r, timeout=3).status == 200 else 1)"]

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]
