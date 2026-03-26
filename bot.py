import os
import re
import logging
import tempfile
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import ffmpeg
import yt_dlp
from faster_whisper import WhisperModel
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Whisper model ─────────────────────────────────────────────────────────────
MODEL_SIZE = os.getenv("WHISPER_MODEL", "base")
model: WhisperModel | None = None

def load_model() -> None:
    global model
    logger.info(f"Loading Whisper model: {MODEL_SIZE} …")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    logger.info("Whisper model ready.")

# ── URL detection ─────────────────────────────────────────────────────────────
URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/|tiktok\.com/)"
    r"[^\s]+",
    re.IGNORECASE,
)

SUPPORTED_DOMAINS = (
    "youtube.com", "youtu.be", "tiktok.com", "twitter.com",
    "x.com", "instagram.com", "reddit.com", "vimeo.com",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def convert_to_wav(input_path: str, output_path: str) -> None:
    (
        ffmpeg
        .input(input_path)
        .output(output_path, ar=16000, ac=1, format="wav")
        .overwrite_output()
        .run(quiet=True)
    )


def transcribe(file_path: str) -> str:
    if model is None:
        raise RuntimeError("Whisper model not loaded.")
    segments, _ = model.transcribe(file_path, beam_size=5)
    return " ".join(segment.text for segment in segments).strip()


def download_url(url: str, output_dir: str) -> str:
    """Download audio from a URL using yt-dlp. Returns path to downloaded file."""
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "downloaded.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        # Limit to 2 hours to avoid runaway downloads
        "match_filter": yt_dlp.utils.match_filter_func("duration < 7200"),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Find the downloaded file
    for fname in os.listdir(output_dir):
        if fname.startswith("downloaded"):
            return os.path.join(output_dir, fname)
    raise FileNotFoundError("yt-dlp did not produce an output file.")


def is_supported_url(text: str) -> str | None:
    """Return the URL if the text contains a supported video URL, else None."""
    match = URL_PATTERN.search(text)
    if match:
        return match.group(0)
    # Broader check for any yt-dlp-supported site
    for domain in SUPPORTED_DOMAINS:
        if domain in text.lower():
            url_match = re.search(r"https?://\S+", text)
            if url_match:
                return url_match.group(0)
    return None


async def send_long_text(message, text: str) -> None:
    """Send text, splitting into chunks if needed."""
    header = "📝 *Transcription:*\n\n"
    chunk_size = 4096 - len(header)
    first = True
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        if first:
            await message.reply_text(header + chunk, parse_mode="Markdown")
            first = False
        else:
            await message.reply_text(chunk)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *TikTok / Video Transcriber Bot*\n\n"
        "Send me:\n"
        "• A *video file* (MP4, MOV, AVI …)\n"
        "• A *voice message* or *audio file*\n"
        "• A *YouTube, TikTok, or Reels URL*\n\n"
        "I'll transcribe it with Whisper and send the text back to you.\n\n"
        "Type /help for more info.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ *How to use*\n\n"
        "*Option A — Upload a file:*\n"
        "Send a video/audio file directly to this chat.\n\n"
        "*Option B — Paste a URL:*\n"
        "Paste a YouTube, TikTok, Instagram Reels, Twitter/X, or Vimeo link.\n\n"
        "*Supported file types:*\n"
        "MP4, MOV, AVI, MKV, WebM, MP3, M4A, OGG, WAV, FLAC\n\n"
        f"_Whisper model: {MODEL_SIZE}_",
        parse_mode="Markdown",
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages that contain a video URL."""
    text = update.message.text or ""
    url = is_supported_url(text)
    if not url:
        await update.message.reply_text(
            "Please send a *video/audio file* or a supported URL "
            "(YouTube, TikTok, Instagram, Twitter/X, Vimeo).",
            parse_mode="Markdown",
        )
        return

    status_msg = await update.message.reply_text("⏳ Downloading from URL …")

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")
        try:
            loop = asyncio.get_event_loop()

            # Download in thread pool so we don't block the event loop
            downloaded = await asyncio.wait_for(
                loop.run_in_executor(None, download_url, url, tmpdir),
                timeout=300,  # 5 min download timeout
            )

            await status_msg.edit_text("🔄 Converting to audio …")
            await asyncio.wait_for(
                loop.run_in_executor(None, convert_to_wav, downloaded, wav_path),
                timeout=120,
            )

            await status_msg.edit_text("🧠 Transcribing with Whisper … (this may take a while for long videos)")

            text_result = await asyncio.wait_for(
                loop.run_in_executor(None, transcribe, wav_path),
                timeout=600,  # 10 min transcription timeout
            )

            if not text_result:
                await status_msg.edit_text("🤷 No speech detected in the video.")
                return

            await status_msg.delete()
            await send_long_text(update.message, text_result)

        except asyncio.TimeoutError:
            await status_msg.edit_text("⏱️ Operation timed out. Try a shorter video.")
        except yt_dlp.utils.DownloadError as e:
            logger.exception("yt-dlp download error")
            err = str(e).split("\n")[0][:200]
            await status_msg.edit_text(f"❌ Download failed:\n`{err}`", parse_mode="Markdown")
        except ffmpeg.Error as e:
            logger.exception("FFmpeg error")
            stderr = (e.stderr or b"").decode(errors="replace")[:200]
            await status_msg.edit_text(f"❌ Audio conversion failed:\n`{stderr}`", parse_mode="Markdown")
        except Exception as e:
            logger.exception("URL transcription error")
            await status_msg.edit_text(f"❌ Something went wrong: {e}")


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message

    if message.video:
        tg_file = await message.video.get_file()
        ext = ".mp4"
        media_type = "video"
    elif message.video_note:
        tg_file = await message.video_note.get_file()
        ext = ".mp4"
        media_type = "video note"
    elif message.audio:
        tg_file = await message.audio.get_file()
        ext = Path(message.audio.file_name or "audio.mp3").suffix or ".mp3"
        media_type = "audio"
    elif message.voice:
        tg_file = await message.voice.get_file()
        ext = ".ogg"
        media_type = "voice"
    elif message.document:
        doc = message.document
        fname = doc.file_name or "file"
        ext = Path(fname).suffix.lower()
        allowed = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".m4a", ".ogg", ".wav", ".flac"}
        if ext not in allowed:
            await message.reply_text("❌ Unsupported file type. Please send a video or audio file.")
            return
        tg_file = await doc.get_file()
        media_type = "document"
    else:
        await message.reply_text("❓ Please send a video or audio file for transcription.")
        return

    status_msg = await message.reply_text(f"⏳ Downloading your {media_type} …")

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = os.path.join(tmpdir, f"input{ext}")
        wav_path = os.path.join(tmpdir, "audio.wav")

        try:
            await tg_file.download_to_drive(raw_path)
            await status_msg.edit_text("🔄 Converting to audio …")

            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, convert_to_wav, raw_path, wav_path),
                timeout=120,
            )
            await status_msg.edit_text("🧠 Transcribing with Whisper … (this may take a while for long videos)")

            text = await asyncio.wait_for(
                loop.run_in_executor(None, transcribe, wav_path),
                timeout=600,
            )

            if not text:
                await status_msg.edit_text("🤷 No speech detected in the file.")
                return

            await status_msg.delete()
            await send_long_text(message, text)

        except asyncio.TimeoutError:
            await status_msg.edit_text("⏱️ Operation timed out. Try a shorter video.")
        except ffmpeg.Error as e:
            logger.exception("FFmpeg error")
            stderr = (e.stderr or b"").decode(errors="replace")[:200]
            await status_msg.edit_text(f"❌ Could not process the file:\n`{stderr}`", parse_mode="Markdown")
        except Exception as e:
            logger.exception("Transcription error")
            await status_msg.edit_text(f"❌ Something went wrong: {e}")


# ── Health check server ───────────────────────────────────────────────────────

def run_health_server() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Respond OK to any path (covers /, /health, /healthz, etc.)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass  # Suppress access logs

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    logger.info(f"Health check server listening on port {port}")
    server.serve_forever()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    # Start health server FIRST so Railway's healthcheck passes immediately
    # while the Whisper model downloads/loads in the background
    threading.Thread(target=run_health_server, daemon=True).start()

    load_model()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    media_filter = (
        filters.VIDEO | filters.VIDEO_NOTE | filters.AUDIO
        | filters.VOICE | filters.Document.ALL
    )
    app.add_handler(MessageHandler(media_filter, handle_media))
    # URL handler — must come before generic text handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    logger.info("Bot is polling …")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
