FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY config ./config
COPY bot ./bot
COPY web ./web
COPY data ./data
COPY eval ./eval
COPY mcp_server ./mcp_server

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
# Eyni image: api (standart), worker, citizen-bot, staff-bot, migrate (docker-compose.yml)
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
