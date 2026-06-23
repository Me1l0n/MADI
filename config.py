import os
import pathlib

def load_env(env_path=".env"):
    """
    Manually load environment variables from .env file.
    This avoids external dependencies like python-dotenv.
    """
    path = pathlib.Path(env_path)
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                # Strip spaces and optional quotes around values
                key = key.strip()
                val = val.strip().strip("'\"")
                os.environ[key] = val

# Load dotenv on module import
load_env()

# Bot & API Tokens
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Models
MAIN_MODEL = os.environ.get("MAIN_MODEL", "openrouter/free")
COMPRESSION_MODEL = os.environ.get("COMPRESSION_MODEL", "google/gemini-3.1-flash-lite")

# Constants & Settings
try:
    COMPRESSION_THRESHOLD = int(os.environ.get("COMPRESSION_THRESHOLD", "200"))
except ValueError:
    COMPRESSION_THRESHOLD = 200

try:
    KEEP_LAST_MESSAGES = int(os.environ.get("KEEP_LAST_MESSAGES", "10"))
except ValueError:
    KEEP_LAST_MESSAGES = 10

HUMANIZER_PATH = os.environ.get("HUMANIZER_PATH", "SKILL.md")
HISTORY_DIR = pathlib.Path("history_data")
HISTORY_DIR.mkdir(exist_ok=True)

# TTS (Text-to-Speech) Settings
TTS_ENABLED = os.environ.get("TTS_ENABLED", "true").lower() == "true"
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "silero").lower() # "silero", "xtts", "none"
TTS_VOICE = os.environ.get("TTS_VOICE", "aidar") # Silero voice name or path to XTTS reference wav
try:
    TTS_VOICE_PROBABILITY = float(os.environ.get("TTS_VOICE_PROBABILITY", "0.2"))
except ValueError:
    TTS_VOICE_PROBABILITY = 0.2

