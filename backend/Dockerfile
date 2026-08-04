# Multi-stage build for smaller final image
FROM python:3.11-slim AS builder

WORKDIR /app

# Allow choosing a requirements file at build time for fast validation builds
ARG REQUIREMENTS_FILE=requirements.txt

# Copy only requirements first for better layer caching
COPY ${REQUIREMENTS_FILE} requirements.txt

# Install dependencies and validate ASGI server import
RUN pip --default-timeout=300 --retries 5 install --no-cache-dir -r requirements.txt \
    && python -c "from hypercorn.config import Config; print(f'hypercorn install verified: {Config.__module__}')"

# Pre-download the ChromaDB ONNX embedding model at build time so it is baked
# into the image. Without this, the 79 MB all-MiniLM-L6-v2 download happens
# synchronously on the first request and blocks the single Hypercorn worker,
# causing every HTTP endpoint to time out for ~30+ seconds on cold start.
RUN python -c "\
import os; \
os.environ.setdefault('ANONYMIZED_TELEMETRY', 'FALSE'); \
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2; \
print('Downloading ONNX embedding model...'); \
ef = ONNXMiniLM_L6_V2(); \
ef(['warmup']); \
print('ONNX model pre-download complete.')" \
    || echo "ONNX pre-download failed (non-fatal; model will download at runtime)"

# Final stage with minimal runtime
FROM python:3.11-slim

WORKDIR /app

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy Python dependencies from builder (includes cached ONNX model)
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# Copy pre-downloaded ONNX model cache from builder stage
COPY --from=builder /root/.cache/chroma /root/.cache/chroma

# Copy application code
ARG CACHE_BUSTER=1
COPY --chown=appuser:appuser . .

# Create data directory and set permissions
RUN mkdir -p /data && chown -R appuser:appuser /data /app && mkdir -p /data/experiments && chown -R appuser:appuser /data/experiments

# Add entrypoint script to fix permissions at runtime
ARG ENTRYPOINT_REV=2
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Switch to non-root user
USER appuser

# Add health check: verify API port is accepting connections
HEALTHCHECK --interval=10s --timeout=8s --start-period=90s --retries=6 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 5000), timeout=2).close()" || exit 1

# Run ASGI app with a single worker so in-memory startup/progress state remains consistent
CMD ["hypercorn", "main:app", "--bind", "0.0.0.0:5000", "--workers", "1"]
