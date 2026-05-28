FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARCHAGENT_HOST=0.0.0.0 \
    ARCHAGENT_PORT=8091

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin archagent

COPY . /app

RUN mkdir -p /app/exports /app/backups && chown -R archagent:archagent /app

USER archagent

EXPOSE 8091

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 /app/healthcheck.py

CMD ["python3", "archagent_server.py"]
