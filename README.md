# 🎙️ Telegram Video Transcriber Bot

A Telegram bot that transcribes TikTok videos (and any video/audio file) using OpenAI Whisper.

## Features

- 📹 Accepts MP4, MOV, AVI, MKV, WebM, MP3, M4A, OGG, WAV, FLAC
- 🎤 Handles voice messages and video notes
- 🧠 Transcribes with OpenAI Whisper (offline, no API key needed)
- 🐳 Fully Dockerised
- ☁️ One-click deploy to Render.com (free tier)

## Quick Start (local)

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/telegram-transcriber.git
cd telegram-transcriber

# 2. Install deps (needs ffmpeg on your system)
pip install -r requirements.txt

# 3. Run
TELEGRAM_BOT_TOKEN=your_token_here python bot.py
```

## Deploy to Render.com

See the [Deployment Guide](#deployment) below.

## Whisper Models

Set the `WHISPER_MODEL` env var to control accuracy vs. speed:

| Model  | Size   | Speed  | Accuracy |
|--------|--------|--------|----------|
| tiny   | 75 MB  | ⚡⚡⚡ | ★★☆☆☆  |
| base   | 145 MB | ⚡⚡   | ★★★☆☆  |
| small  | 461 MB | ⚡     | ★★★★☆  |
| medium | 1.5 GB | 🐢     | ★★★★★  |

Default is `base` — a good balance for free-tier RAM limits.

## Deployment

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com) → New → Blueprint.
3. Connect your GitHub repo.
4. In Environment Variables, set `TELEGRAM_BOT_TOKEN` to your bot token.
5. Click **Deploy**. Done!
