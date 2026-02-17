import os
import logging
import tempfile
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import ffmpeg
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
logger.info(f"Loading Whisper model: {MODEL_SIZE} …")
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
logger.info("Whisper model ready.")

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
    segments, _ = model.transcribe(file_path)
    return " ".join(segment.text for segment in segments).strip()


async def download_telegram_file(file, dest_path: str) -> None:
    await file.download_to_drive(dest_path)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *TikTok / Video Transcriber Bot*\n\n"
        "Send me:\n"
        "• A *video file* (MP4, MOV, AVI …)\n"
        "• A *voice message* or *audio file*\n\n"
        "I'll transcribe it with Whisper and send the text back to you.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ *How to use*\n\n"
        "1. Download your TikTok video.\n"
        "2. Send the video file directly to this chat.\n"
        "3. Wait a few seconds — transcription appears automatically.\n\n"
        f"_Current Whisper model: {MODEL_SIZE}_",
        parse_mode="Markdown",
    )


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
            await download_telegram_file(tg_file, raw_path)
            await status_msg.edit_text("🔄 Converting to audio …")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, convert_to_wav, raw_path, wav_path)
            await status_msg.edit_text("🧠 Transcribing with Whisper …")

            text = await loop.run_in_executor(None, transcribe, wav_path)

            if not text:
                await status_msg.edit_text("🤷 No speech detected in the file.")
                return

            header = "📝 *Transcription:*\n\n"
            max_len = 4096 - len(header)
            await status_msg.delete()

            if len(text) <= max_len:
                await message.reply_text(header + text, parse_mode="Markdown")
            else:
                await message.reply_text(header + text[:max_len], parse_mode="Markdown")
                for i in range(max_len, len(text), max_len):
                    await message.reply_text(text[i: i + max_len])

        except ffmpeg.Error as e:
            logger.exception("FFmpeg error")
            await status_msg.edit_text(f"❌ Could not process the file:\n`{e.stderr.decode()}`", parse_mode="Markdown")
        except Exception as e:
            logger.exception("Transcription error")
            await status_msg.edit_text(f"❌ Something went wrong: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Please send a *video* or *audio file* — I can't transcribe text! 😄",
        parse_mode="Markdown",
    )


# ── Health check server ───────────────────────────────────────────────────────

def run_health_server() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    logger.info(f"Health check server on port {port}")
    server.serve_forever()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    media_filter = (
        filters.VIDEO | filters.VIDEO_NOTE | filters.AUDIO
        | filters.VOICE | filters.Document.ALL
    )
    app.add_handler(MessageHandler(media_filter, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    threading.Thread(target=run_health_server, daemon=True).start()

    logger.info("Bot is polling …")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
