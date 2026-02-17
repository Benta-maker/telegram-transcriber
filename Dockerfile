# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim

# System deps: ffmpeg + build tools for Whisper/torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy source
COPY bot.py .

# Whisper downloads models to ~/.cache/whisper on first run.
# Pre-download the default model so cold-starts are faster.
ARG WHISPER_MODEL=base
RUN python -c "import whisper; whisper.load_model('${WHISPER_MODEL}')"

# Environment
ENV WHISPER_MODEL=${WHISPER_MODEL}

# Non-root user for security
RUN useradd -m botuser
USER botuser

CMD ["python", "bot.py"]
