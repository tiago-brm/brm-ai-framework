# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.8.22 AS uv

FROM python:3.11-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/req-all.txt \
 && grep -Eiv '^(nvidia-|triton|cuda-|torch==)' /tmp/req-all.txt > /tmp/req.txt \
 && grep -Ei '^torch==' /tmp/req-all.txt | sed 's/;.*//' > /tmp/req-torch.txt \
 && uv venv /app/.venv \
 && uv pip install --python /app/.venv/bin/python --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match -r /tmp/req-torch.txt \
 && uv pip install --python /app/.venv/bin/python -r /tmp/req.txt
ENV HF_HOME=/opt/hf
RUN /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

FROM python:3.11-slim-bookworm AS runtime
RUN groupadd --gid 10001 brm \
 && useradd --uid 10001 --gid brm --no-create-home --shell /usr/sbin/nologin brm \
 && mkdir -p /data /app/clients \
 && chown brm:brm /data /app/clients
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/hf /opt/hf
COPY mcp_engine ./mcp_engine
COPY audit ./audit
COPY scripts ./scripts
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    CONFIG_ROOT=/app/clients \
    DATA_ROOT=/data \
    BRM_DATA_DIR=/data
USER brm
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status == 200 else 1)"]
CMD ["uvicorn", "mcp_engine.core.http_app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS backup
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends sqlite3 age \
 && rm -rf /var/lib/apt/lists/*
USER brm
ENTRYPOINT ["/bin/bash"]
CMD ["/app/scripts/backup.sh"]

FROM node:22-alpine AS dashboard-build
WORKDIR /dash
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS dashboard
COPY --from=dashboard-build /dash/dist /usr/share/nginx/html
COPY dashboard/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
