FROM python:3.11-slim

# System deps: ffmpeg + build tools for Whisper/torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Fix pkg_resources error by upgrading setuptools first
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY bot.py .

# Pre-download Whisper model
ARG WHISPER_MODEL=base
RUN python -c "import whisper; whisper.load_model('${WHISPER_MODEL}')"

ENV WHISPER_MODEL=${WHISPER_MODEL}

RUN useradd -m botuser
USER botuser

CMD ["python", "bot.py"]
