# Build context must be the REPO ROOT (needs src/jobseeker too):
#   docker build -f webapp/Dockerfile .
# render.yaml sets dockerContext: . for exactly this reason.

# ---- 1. frontend: React + TypeScript -> static files -------------------
FROM node:22-slim AS frontend
WORKDIR /build
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY webapp/frontend/ ./
RUN npm run build

# ---- 2. backend: FastAPI serving the API + the built frontend ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY webapp/requirements.txt webapp/requirements.txt
RUN pip install -r webapp/requirements.txt

COPY src/ src/
COPY config/ config/
COPY webapp/ webapp/
COPY --from=frontend /build/dist webapp/frontend/dist

# Run from inside webapp/ so main.py's plain `from auth import ...` imports
# resolve the same way as local dev (`cd webapp && uvicorn main:app`).
WORKDIR /app/webapp
RUN useradd --system --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
# --proxy-headers: Render terminates TLS; trust its X-Forwarded-Proto.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
