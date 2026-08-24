# Multi-stage build. Pip layer caches unless requirements.txt changes.
FROM python:3.11-slim AS builder
WORKDIR /app

ARG REQUIREMENTS_FILE=requirements.txt
ARG SKIP_ONNX=1
COPY ${REQUIREMENTS_FILE} requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip --default-timeout=300 --retries 5 install -r requirements.txt \
    && python -c "from hypercorn.config import Config; print('hypercorn ok')"

ENV HF_HOME=/app/.cache/chroma
# ONNX lives in the chroma_model_cache volume at runtime. Skip bake by default
# so a source-only rebuild does not re-download 79MB and kill Docker Desktop.
RUN if [ "$SKIP_ONNX" = "0" ]; then \
      python -c "import os; os.makedirs('/app/.cache/chroma', exist_ok=True); os.environ['ANONYMIZED_TELEMETRY']='FALSE'; os.environ['HF_HOME']='/app/.cache/chroma'; from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2; print('Downloading ONNX...'); ONNXMiniLM_L6_V2()(['warmup']); print('ONNX done')" \
      || echo "ONNX pre-download failed (non-fatal)"; \
    else \
      mkdir -p /app/.cache/chroma && echo "Skipping ONNX bake (SKIP_ONNX=1)"; \
    fi

FROM python:3.11-slim
WORKDIR /app
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /home/appuser && chown appuser:appuser /home/appuser
ENV HOME=/app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/.cache/chroma /app/.cache/chroma

ARG CACHE_BUSTER=1
COPY --chown=appuser:appuser . .
RUN mkdir -p /data /data/experiments && chown -R appuser:appuser /data /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
USER appuser
HEALTHCHECK --interval=10s --timeout=8s --start-period=120s --retries=6 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 5000), timeout=2).close()" || exit 1
CMD ["hypercorn", "main:app", "--bind", "0.0.0.0:5000", "--workers", "1"]
