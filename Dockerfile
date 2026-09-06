FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

RUN pip install --no-cache-dir -e .

EXPOSE 8000

# Migrations run before the server starts — Render's free tier is a single
# instance, so this is safe without a separate release-phase step.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
