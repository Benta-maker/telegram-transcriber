FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        gcc \
        g++ \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

ENV WHISPER_MODEL=base
# Increase tmp space awareness; Railway provides /tmp with reasonable space
ENV TMPDIR=/tmp

RUN useradd -m botuser && mkdir -p /tmp && chmod 1777 /tmp
USER botuser

CMD ["python", "bot.py"]
