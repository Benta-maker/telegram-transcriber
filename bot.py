import os
import logging
import tempfile
import asyncio
from pathlib import Path

import whisper
import ffmpeg
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Whisper model (loaded once at startup) ────────────────────────────────────
MODEL_SIZE = os.getenv("WHISPER_MODEL", "base")   # tiny | base | small | medium | large
logger.info(f"Loading Whisper model: {MODEL_SIZE} …")
model = whisper.load_model(MODEL_SIZE)
logger.info("Whisper model ready.")

# ── Helpers ───────────────────────────────────────────────────────────────────

def convert_to_wav(input_path: str, output_path: str) -> None:
    """Convert any video/audio file to 16-kHz mono WAV for Whisper."""
    (
        ffmpeg
        .input(input_path)
        .output(output_path, ar=16000, ac=1, format="wav")
        .overwrite_output()
        .run(quiet=True)
    )


def transcribe(file_path: str) -> str:
    """Run Whisper transcription and return the text."""
    result = model.transcribe(file_path, fp16=False)
    return result["text"].strip()


async def download_telegram_file(file, dest_path: str) -> None:
    """Download a Telegram file object to dest_path."""
    await file.download_to_drive(dest_path)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *TikTok / Video Transcriber Bot*\n\n"
        "Send me:\n"
        "• A *video file* (MP4, MOV, AVI …)\n"
        "• A *voice message* or *audio file*\n\n"
        "I'll transcribe it with OpenAI Whisper and send the text back to you.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ *How to use*\n\n"
        "1. Download your TikTok video (no watermark recommended).\n"
        "2. Send the video file directly to this chat.\n"
        "3. Wait a few seconds — transcription appears automatically.\n\n"
        f"_Current Whisper model: {MODEL_SIZE}_",
        parse_mode="Markdown",
    )


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming video, audio, voice, or document messages."""
    message = update.message

    # Determine what was sent
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
            # Download
            await download_telegram_file(tg_file, raw_path)
            await status_msg.edit_text("🔄 Converting to audio …")

            # Convert to WAV
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, convert_to_wav, raw_path, wav_path)

            await status_msg.edit_text("🧠 Transcribing with Whisper …")

            # Transcribe
            text = await loop.run_in_executor(None, transcribe, wav_path)

            if not text:
                await status_msg.edit_text("🤷 No speech detected in the file.")
                return

            # Send result — split if very long
            header = "📝 *Transcription:*\n\n"
            max_len = 4096 - len(header)

            await status_msg.delete()

            if len(text) <= max_len:
                await message.reply_text(header + text, parse_mode="Markdown")
            else:
                # Send in chunks
                await message.reply_text(header + text[:max_len], parse_mode="Markdown")
                for i in range(max_len, len(text), max_len):
                    await message.reply_text(text[i : i + max_len])

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    media_filter = (
        filters.VIDEO
        | filters.VIDEO_NOTE
        | filters.AUDIO
        | filters.VOICE
        | filters.Document.ALL
    )
    app.add_handler(MessageHandler(media_filter, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is polling …")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
