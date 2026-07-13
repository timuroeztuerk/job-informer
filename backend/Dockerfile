FROM node:20-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend .

ARG VITE_API_BASE=/
ARG VITE_API_TOKEN=
ENV VITE_API_BASE=${VITE_API_BASE}
ENV VITE_API_TOKEN=${VITE_API_TOKEN}

RUN npm run build


FROM python:3.11-slim

WORKDIR /app
RUN addgroup --system --gid 1001 app && \
    adduser --system --ingroup app --uid 1001 app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_TOKEN= \
    JOBS_DB_PATH=/app/data/jobs.db

# Install dependencies
COPY --chown=app:app backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

# Preserve the backend package layout used by local execution and tests.
COPY --chown=app:app backend /app/backend
# Copy frontend build output
COPY --chown=app:app --from=frontend-build /frontend/dist /app/backend/frontend/dist

RUN mkdir -p /app/data /app/backend/logs \
    && chown -R app:app /app/data /app/backend/logs

USER app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
