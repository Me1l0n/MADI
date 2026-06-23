# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import logging
import torch

# Monkey patch torch.load to bypass PyTorch 2.6+ weights_only security restriction
_orig_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs["weights_only"] = False
    return _orig_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

# Monkey patch torchaudio.load to use soundfile to bypass broken torchcodec dependency
try:
    import torchaudio
    import soundfile as sf
    def _patched_torchaudio_load(uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, **kwargs):
        start_frame = frame_offset
        frames_to_read = num_frames
        data, samplerate = sf.read(uri, frames=frames_to_read, start=start_frame, dtype='float32')
        tensor = torch.from_numpy(data)
        if tensor.ndim == 1:
            tensor = tensor.unsqueeze(1)
        if channels_first:
            tensor = tensor.T
        return tensor, samplerate
    torchaudio.load = _patched_torchaudio_load
    logging.getLogger("bot.tts").info("Monkey-patched torchaudio.load with soundfile backend in tts_manager.")
except Exception as e:
    logging.getLogger("bot.tts").error(f"Failed to patch torchaudio.load in tts_manager: {e}")

# Auto-agree to Coqui TOS
os.environ["COQUI_TOS_AGREED"] = "1"

logger = logging.getLogger("bot.tts")

# Singletons for loaded models
_silero_model = None
_xtts_model = None

# Locks to prevent concurrent generation on the same model
_silero_lock = asyncio.Lock()
_xtts_lock = asyncio.Lock()

def _load_silero():
    global _silero_model
    if _silero_model is not None:
        return _silero_model
    
    local_file = os.path.join("silero_tts", "v4_ru.pt")
    if not os.path.exists(local_file):
        raise FileNotFoundError(f"Silero model file not found at {local_file}. Please ensure silero_tts folder setup is complete.")
    
    logger.info("Loading Silero TTS model...")
    model = torch.package.PackageImporter(local_file).load_pickle("tts_models", "model")
    device = torch.device('cpu')
    model.to(device)
    _silero_model = model
    logger.info("Silero TTS model loaded successfully.")
    return _silero_model

def _load_xtts():
    global _xtts_model
    if _xtts_model is not None:
        return _xtts_model
    
    logger.info("Loading XTTS-v2 model (this may take a few minutes on CPU)...")
    from TTS.api import TTS
    # gpu=False forces CPU
    model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
    _xtts_model = model
    logger.info("XTTS-v2 model loaded successfully.")
    return _xtts_model

async def generate_silero_audio(text: str, speaker: str, output_path: str) -> bool:
    async with _silero_lock:
        try:
            model = await asyncio.to_thread(_load_silero)
            
            def _gen():
                model.save_wav(
                    text=text,
                    speaker=speaker,
                    sample_rate=24000,
                    put_accent=True,
                    put_yo=True,
                    audio_path=output_path
                )
            await asyncio.to_thread(_gen)
            return True
        except Exception as e:
            logger.error(f"Error generating Silero audio: {e}")
            return False

async def generate_xtts_audio(text: str, speaker_wav: str, output_path: str) -> bool:
    async with _xtts_lock:
        try:
            tts = await asyncio.to_thread(_load_xtts)
            
            def _gen():
                tts.tts_to_file(
                    text=text,
                    speaker_wav=speaker_wav,
                    language="ru",
                    file_path=output_path
                )
            await asyncio.to_thread(_gen)
            return True
        except Exception as e:
            logger.error(f"Error generating XTTS audio: {e}")
            return False

async def generate_audio(text: str, provider: str, voice: str, output_path: str) -> bool:
    """
    Generates audio file from text using the selected provider.
    """
    try:
        if provider == "silero":
            speakers = ['aidar', 'baya', 'kseniya', 'xenia', 'eugene']
            speaker = voice if voice in speakers else "aidar"
            return await generate_silero_audio(text, speaker, output_path)
        elif provider == "xtts":
            ref_wav = voice
            if not os.path.exists(ref_wav):
                # Try fallback to silero aidar sample if it exists
                fallback = os.path.join("silero_tts", "aidar.wav")
                if os.path.exists(fallback):
                    ref_wav = fallback
                else:
                    logger.error(f"XTTS reference wav not found at {voice} and fallback {fallback} not found.")
                    return False
            return await generate_xtts_audio(text, ref_wav, output_path)
        else:
            logger.error(f"Unknown TTS provider: {provider}")
            return False
    except Exception as e:
        logger.error(f"Failed to generate audio using {provider}: {e}")
        return False
