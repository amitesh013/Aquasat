FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY requirements-api.txt ./
RUN python -m pip install --no-cache-dir -r requirements-api.txt

COPY aquasat_api.py aquasat_database.py sync_aquasat_database.py ./
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY rainfall_gpm_candidate_result.json ./
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn aquasat_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
