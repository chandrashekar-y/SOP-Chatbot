# ══════════════════════════════════════════════════════════════════════════════
# SOP Chatbot — Production Dockerfile
# ══════════════════════════════════════════════════════════════════════════════
# Build:  docker build -t sop-chatbot .
# Run:    sudo docker-compose-v2 up -d  (see docker-compose.yml)
# ══════════════════════════════════════════════════════════════════════════════

FROM python:3.11

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies (cached layer — only rebuilds when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Streamlit configuration ───────────────────────────────────────────────────
ENV STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    PYTHONUNBUFFERED=1

EXPOSE 8501

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# ── Entry point ───────────────────────────────────────────────────────────────
CMD ["streamlit", "run", "streamlit_gemini.py", "--server.address=0.0.0.0"]
