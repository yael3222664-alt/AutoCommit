# Single-service image: FastAPI serves the API and the built React frontend.
ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${NODE_IMAGE} AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/app backend/app
COPY --from=frontend /app/frontend/dist frontend/dist
RUN useradd --system --uid 10001 --no-create-home autocommit
USER autocommit

ENV ENVIRONMENT=production \
    DEBUG=false \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    PORT=8000
EXPOSE 8000
# Proxy headers let login rate limiting see the client IP behind the platform load balancer.
CMD ["sh", "-c", "exec uvicorn backend.app.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
