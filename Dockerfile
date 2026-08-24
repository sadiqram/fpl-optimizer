# Backend only (FastAPI + uvicorn). The frontend deploys separately to Vercel
# (Architecture §4.7/§6 M8) — this image is what Fly.io/Railway runs.
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /install /usr/local
COPY src/ src/
COPY config/ config/
COPY scripts/ scripts/

# The volume mount point (fly.toml) — same relative "data/" layout the code already
# assumes (DEFAULT_DB_PATH, config/default.yaml's relative paths, etc.), so nothing in
# storage/db.py or strategy/risk.py needs a deploy-specific path override.
RUN mkdir -p data/db data/raw data/artifacts data/logs

EXPOSE 8080
CMD ["uvicorn", "fpl_optimizer.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
