FROM python:3.10-slim

# ─── System dependencies ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ─── Non-root user ────────────────────────────────────────────────────────────
RUN useradd -m -u 1000 appuser
WORKDIR /app
RUN mkdir -p uploads logs && chown -R appuser:appuser /app
USER appuser
ENV PATH="/home/appuser/.local/bin:$PATH"

# ─── Python dependencies ─────────────────────────────────────────────────────
COPY --chown=appuser requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─── Application code ────────────────────────────────────────────────────────
COPY --chown=appuser . .

# ─── Expose & Config ─────────────────────────────────────────────────────────
EXPOSE 8001

# Health check for container orchestrators
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# ─── Start command ───────────────────────────────────────────────────────────
# For production: 2 uvicorn workers via gunicorn
CMD ["gunicorn", "main:app", \
    "--worker-class", "uvicorn.workers.UvicornWorker", \
    "--workers", "2", \
    "--bind", "0.0.0.0:8001", \
    "--timeout", "120", \
    "--access-logfile", "-", \
    "--error-logfile", "-"]
