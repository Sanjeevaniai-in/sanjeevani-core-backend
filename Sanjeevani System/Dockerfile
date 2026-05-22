# ─────────────────────────────────────────────────────────────────────────────
# SanjeevaniRxAI – Production Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# Multi-stage build:
#   Stage 1 (builder) – install Python deps into a virtual-env
#   Stage 2 (runtime) – copy only the venv + app code → lean final image
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System packages needed only during build
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create an isolated virtual environment so Stage 2 can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --upgrade pip wheel \
    && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: lean runtime image ───────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Metadata labels
LABEL maintainer="SanjeevaniRxAI Team" \
      version="1.0.0" \
      description="SanjeevaniRxAI Intelligent Pharmacy Backend"

# Non-root user for security
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Pull in the pre-built virtual environment
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY app/          ./app/
COPY static/       ./static/
COPY medicines_test_data.csv ./

# Environment defaults (override via docker-compose / Kubernetes env)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    ENV=production \
    PORT=10000 \
    LOG_LEVEL=INFO

# Data and Uploads directory setup
RUN mkdir -p /app/data /app/uploads && chown -R appuser:appgroup /app/data /app/uploads

USER appuser

# Expose port (Render ignores this but good for clarity)
EXPOSE 10000

# Production server: uvicorn (JSON array format for better signal handling)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000", "--workers", "2", "--log-level", "info", "--access-log"]
