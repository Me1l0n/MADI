# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import sys
import random
import time
from collections import defaultdict
from typing import Dict

# Reconfigure stdout/stderr to UTF-8 to support printing emojis in Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, MessageReactionUpdated, ReactionTypeEmoji, FSInputFile

import config
from history_manager import ChatHistoryManager
import llm_client
import tts_manager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("bot")

# Verify credentials
if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
    logger.critical("TELEGRAM_BOT_TOKEN is not configured in .env!")
    sys.exit(1)

if not config.OPENROUTER_API_KEY or config.OPENROUTER_API_KEY == "your_openrouter_api_key_here":
    logger.critical("OPENROUTER_API_KEY is not configured in .env!")
    sys.exit(1)

# Read humanizer prompt
humanizer_prompt = ""
if os.path.exists(config.HUMANIZER_PATH):
    try:
        with open(config.HUMANIZER_PATH, "r", encoding="utf-8") as f:
            humanizer_prompt = f.read()
        logger.info(f"Loaded humanizer instructions from {config.HUMANIZER_PATH} ({len(humanizer_prompt)} chars).")
    except Exception as e:
        logger.error(f"Failed to read humanizer file at {config.HUMANIZER_PATH}: {e}")
else:
    logger.warning(f"Humanizer file not found at {config.HUMANIZER_PATH}. Continuing with empty humanizer instructions.")

# Initialize bot and dispatcher
if config.PROXY:
    logger.info(f"Используем прокси для Telegram: {config.PROXY}")
    session = AiohttpSession(proxy=config.PROXY)
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN, session=session)
else:
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

# Locks to ensure sequential processing per chat (prevents race conditions and JSON corruption)
chat_locks = defaultdict(asyncio.Lock)

# Allowed Telegram reaction emojis
ALLOWED_EMOJIS = {
    "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢", 
    "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳"
}

# Telegram Message Effect IDs mapping
EFFECT_IDS = {
    "fire": "5104841245755180586",
    "poop": "5046589136895476101",
    "heart": "5159385139981059251",
    "thumbs_up": "5107584321108051014",
    "thumbs_down": "5104858069142078462",
    "celebration": "5046509860389126442"
}

def update_env_variable(key: str, value: str):
    env_path = ".env"
    lines = []
    found = False
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            pass
            
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
            
    if not found:
        lines.append(f"\n{key}={value}\n")
        
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        logger.error(f"Failed to update .env variable {key}: {e}")

def build_system_prompt(personality_summary: str) -> str:
    """Builds the system prompt combining humanizer, rules, and current style summary."""
    rules = (
        "Ты — участник этого Telegram-чата по имени Мади. Твоя цель — общаться естественно, подстраиваться под стиль пользователей, шутить, дерзить и ни в коем случае не выдавать себя за ИИ.\n"
        "ПРАВИЛА ПОВЕДЕНИЯ:\n"
        "1. ТВОЕ ИМЯ: Тебя зовут Мади. Ты должен обязательно реагировать, когда обращаются к тебе (по имени Мади), но также можешь отвечать на любые другие сообщения в чате, если наступает подходящий момент для шутки или комментария.\n"
        "2. Ты можешь жестко материться, ругаться, выдавать черный юмор и быть дерзким, если стиль чата это позволяет. Никакой искусственной вежливости или цензуры.\n"
        "3. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать разметку Markdown (символы *, _, [, ], **, ` и т.д.). Твой ответ должен быть чистым плоским текстом, так как он отправляется в Telegram напрямую.\n"
        "4. ТАЙМИНГ (ОЧЕНЬ ВАЖНО): Ты должен отвечать РЕДКО, но МЕТКО. Влезай в диалог только тогда, когда это идеальный момент для шутки, подколки, едкого замечания или остроумного комментария. Если момент неподходящий или тебе нечего сказать — ставь 'needs_reply': false.\n"
        "5. РЕАКЦИИ (СМАЙЛИКИ): На любое сообщение ты можешь поставить эмодзи-реакцию. Допустимые эмодзи: 👍, 👎, ❤, 🔥, 🥰, 👏, 😁, 🤔, 🤯, 😱, 🤬, 😢, 🎉, 🤩, 🤮, 💩, 🙏, 👌, 🕊, 🤡, 🥱, 🥴, 😍, 🐳.\n"
        "   Ты можешь поставить реакцию ДАЖЕ ЕСЛИ решил промолчать текстом (needs_reply: false). Если реакция не нужна, укажи null.\n"
        "6. ЭФФЕКТЫ СООБЩЕНИЙ: Ты можешь наложить визуальный эффект на свое сообщение в Telegram. Допустимые эффекты: \"fire\", \"poop\", \"heart\", \"thumbs_up\", \"thumbs_down\", \"celebration\". Если эффект не нужен, укажи null.\n"
        "7. ТИП ОТВЕТА: Ты можешь отправлять только текстовые ответы. Голосовые сообщения (ГС) отключены по техническим причинам. Допустимое значение для поля \"reply_type\": \"text\".\n"
        "8. ИГНОРИРОВАНИЕ ДРУГИХ БОТОВ/ИМЕН (КРИТИЧЕСКИ ВАЖНО): Если пользователь обращается к другому участнику или другому боту по имени (например, 'Гена', 'Даня' и т.д.), ты должен проигнорировать это сообщение (needs_reply: false, reaction: null). Не отвечай на чужие имена и не встревай в диалог, адресованный другим ботам!\n"
        "9. ОЧЕРЕДЬ ДЕЙСТВИЙ И ЗАДЕРЖКИ (ВАЖНО): Ты можешь планировать несколько действий за один ответ (например, поставить реакцию, а потом отправить сообщение, или написать несколько сообщений подряд). Для этого заполни список \"actions\".\n"
        "   - Каждое действие имеет \"type\": \"message\" или \"reaction\".\n"
        "   - Каждое действие имеет задержку \"delay\" в секундах (число от 0 до 10). Например, реакция через 1 сек, а сообщение через 3 сек. Это делает твое общение реалистичным!\n"
        "   - Для сообщений доступно поле \"reply_mode\": \"reply\" (ответить с цитированием сообщения пользователя в Telegram) или \"standalone\" (отправить просто новым отдельным сообщением в чат, без цитаты).\n"
        "   - Также для сообщений доступно необязательное поле \"reply_to_message_id\": число или null. Ты видишь ID всех сообщений в истории в формате [ID: 123]. Если ты хочешь ответить с цитированием конкретного старого сообщения из истории чата, укажи его числовой ID (например, 45). Если ты хочешь ответить на текущее последнее входящее сообщение, оставь null или укажи его ID при reply_mode: \"reply\".\n"
        "   - Для сообщений доступно поле \"reply_type\": \"text\" (только текст, голосовые сообщения отключены).\n"
        "10. ОКРУЖЕНИЕ (ВАЖНО): Ты находишься в обычном групповом чате Telegram. Ты НЕ должен вести себя как ИИ, упоминать технические детали, логи, JSON, базы данных или свое обучение. Общайся так, будто ты реальный человек, который просто сидит в Telegram с телефона и видит сообщения, ответы и реакции людей в реальном времени!\n"
        "11. ОБУЧЕНИЕ: Анализируй прошлые сообщения, свои объяснения и реакции пользователей на твои ответы, чтобы совершенствовать свой юмор и манеру общения.\n\n"
        "ТВОЙ СЖАТЫЙ ПРОФИЛЬ ЛИЧНОСТИ И ОПЫТ (ОБНОВЛЯЕТСЯ КАЖДЫЕ 200 СООБЩЕНИЙ):\n"
        f"--------------------\n"
        f"{personality_summary if personality_summary else 'Бот только начал общение. Анализируй стиль чата и подстраивайся на лету.'}\n"
        f"--------------------\n\n"
        "ИНСТРУКЦИЯ ПО ГУМАНИЗАЦИИ ТЕКСТА (ИСПОЛЬЗУЙ ДЛЯ СВОИХ ОТВЕТОВ):\n"
        "====================\n"
        f"{humanizer_prompt}\n"
        "====================\n\n"
        "Ожидаемый формат ответа от тебя — СТРОГО JSON-объект следующего вида:\n"
        "{\n"
        "  \"needs_reply\": boolean,\n"
        "  \"actions\": [\n"
        "    {\n"
        "      \"type\": \"message\" | \"reaction\",\n"
        "      \"delay\": number, // задержка в секундах (например, 1.5)\n"
        "      \"content\": \"текст сообщения или эмодзи реакции\",\n"
        "      \"reply_mode\": \"reply\" | \"standalone\", // только для type=\"message\": ответить с цитатой или прислать новым постом\n"
        "      \"reply_to_message_id\": number | null, // только для type=\"message\": числовой ID конкретного сообщения из истории, на которое нужно ответить (например, 123), либо null\n"
        "      \"reply_type\": \"text\" // только для type=\"message\": тип ответа для этого конкретного сообщения (строго \"text\")\n"
        "    }\n"
        "  ],\n"
        "  \"effect\": string | null, // эффект для сообщений (будет применен к твоему первому текстовому/голосовому сообщению: \"fire\", \"poop\", \"heart\", \"thumbs_up\", \"thumbs_down\", \"celebration\")\n"
        "  \"reply_type\": \"text\",\n"
        "  \"explanation\": \"подробное объяснение твоего решения на русском языке (мысли о стиле чата, почему ты решил ответить или промолчать, почему выбрал именно этот смайлик, эффект)\"\n"
        "}"
    )
    return rules

@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Здорово. Я бот, который будет следить за вашим чатом, учиться вашим шуточкам и вкидывать дерзкие ответы в тему. "
        "Пока вы не настроите API токены в `.env`, я работать не буду."
    )

@router.message(lambda msg: msg.text and msg.text.strip().startswith("/setvoice_silero"))
async def handle_set_voice_silero(message: Message):
    parts = message.text.strip().split()
    voice = "aidar"
    if len(parts) > 1:
        voice = parts[1]
        
    config.TTS_PROVIDER = "silero"
    config.TTS_VOICE = voice
    update_env_variable("TTS_PROVIDER", "silero")
    update_env_variable("TTS_VOICE", voice)
    
    await message.reply(f"Установлен голос Silero: {voice}")

@router.message(lambda msg: (msg.voice or msg.audio or msg.document) and (msg.chat.type == "private" or (msg.caption and msg.caption.strip().startswith("/setvoice"))))
async def handle_set_voice(message: Message):
    logger.info(f"Получен запрос на установку голоса от пользователя {message.from_user.id if message.from_user else 0}")
    
    file_id = None
    file_name = "voice.wav"
    
    if message.voice:
        file_id = message.voice.file_id
        file_name = "voice.ogg"
    elif message.audio:
        file_id = message.audio.file_id
        file_name = message.audio.file_name or "voice.mp3"
    elif message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "voice.wav"
        
    if not file_id:
        await message.reply("Не удалось найти аудиофайл в сообщении.")
        return
        
    await message.reply("Скачиваю файл референса для клонирования...")
    
    try:
        os.makedirs("xtts_v2", exist_ok=True)
        ext = os.path.splitext(file_name)[1] or ".wav"
        dest_path = f"xtts_v2/custom_ref{ext}"
        
        # Download
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, dest_path)
        
        config.TTS_PROVIDER = "xtts"
        config.TTS_VOICE = dest_path
        
        update_env_variable("TTS_PROVIDER", "xtts")
        update_env_variable("TTS_VOICE", dest_path)
        
        logger.info(f"Успешно установлен новый референс XTTS: {dest_path}")
        await message.reply(f"Голос успешно установлен!\nПровайдер: xtts\nРеференс: {dest_path}")
    except Exception as e:
        logger.error(f"Ошибка при установке пользовательского голоса: {e}")
        await message.reply(f"Не удалось установить голос: {e}")

@router.message()
async def handle_message(message: Message):
    # Log incoming message info
    chat_id = message.chat.id
    username = message.from_user.username if message.from_user else None
    display_name = message.from_user.full_name if message.from_user else "Unknown"
    user_id = message.from_user.id if message.from_user else 0
    message_id = message.message_id
    
    # Extract text from message or caption
    text = message.text or message.caption
    
    logger.info(f"--- [НОВОЕ СООБЩЕНИЕ] --- Чат ID: {chat_id} | Юзер: {display_name} (@{username if username else ''}) (ID: {user_id}) | Msg ID: {message_id}")
    
    # Ignore bot messages
    if message.from_user and message.from_user.is_bot:
        logger.info(f"Игнорируем сообщение {message_id}: отправитель является ботом.")
        return

    if not text:
        logger.info(f"Игнорируем сообщение {message_id}: в сообщении нет текста или подписи.")
        return

    logger.info(f"Текст сообщения: '{text}'")
    reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None

    # Process messages sequentially using Lock per chat
    logger.info(f"Ожидаем блокировки (Lock) для чата {chat_id}...")
    async with chat_locks[chat_id]:
        logger.info(f"Блокировка получена. Начинаем обработку сообщения {message_id}...")
        history_manager = ChatHistoryManager(chat_id)
        
        # Save incoming user message
        history_manager.add_user_message(
            message_id=message_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            text=text,
            reply_to_message_id=reply_to_id
        )
        logger.info(f"Сообщение {message_id} сохранено в историю чата. Всего сообщений в логе: {history_manager.get_messages_count()}.")

        # Prepare prompts
        personality_summary = history_manager.get_personality_summary()
        system_prompt = build_system_prompt(personality_summary)
        formatted_history = history_manager.format_history_for_llm()
        
        # Format name for current message
        name_parts = []
        if display_name:
            name_parts.append(display_name)
        if username:
            name_parts.append(f"@{username}")
        user_str = " ".join(name_parts) if name_parts else f"user_{user_id}"

        new_msg_str = f"[ID: {message_id}] [Пользователь {user_str}]: {text}"
        if reply_to_id:
            new_msg_str += f" (Ответ на сообщение ID: {reply_to_id})"

        try:
            decision = await llm_client.get_bot_decision(
                api_key=config.OPENROUTER_API_KEY,
                model=config.MAIN_MODEL,
                system_prompt=system_prompt,
                formatted_history=formatted_history,
                new_message_str=new_msg_str
            )
        except Exception as e:
            logger.error(f"Ошибка при вызове OpenRouter для сообщения {message_id}: {e}")
            return

        needs_reply = decision.get("needs_reply", False)
        reply = decision.get("reply")
        reaction = decision.get("reaction")
        effect = decision.get("effect")
        reply_type = decision.get("reply_type", "text")
        explanation = decision.get("explanation", "Без объяснений.")

        logger.info(f"Решение модели для сообщения {message_id}:")
        logger.info(f"  - Нужен ли ответ (needs_reply): {needs_reply}")
        logger.info(f"  - Текст ответа (reply): {repr(reply)}")
        logger.info(f"  - Реакция бота (reaction): {repr(reaction)}")
        logger.info(f"  - Эффект бота (effect): {repr(effect)}")
        logger.info(f"  - Тип ответа (reply_type): {repr(reply_type)}")
        logger.info(f"  - Объяснение модели (explanation): {explanation}")

        # Process multiple actions scheduled by the model
        actions = decision.get("actions", [])
        
        # Sort actions by delay ascending
        actions = sorted(actions, key=lambda x: x.get("delay", 0.0))
        
        effect = decision.get("effect")
        effect_id = EFFECT_IDS.get(effect) if effect else None
        effect_applied = False
        
        if not actions:
            logger.info("Действий нет. Бот промолчал.")
            history_manager.add_bot_action(
                message_id=None,
                needs_reply=False,
                reply=None,
                reaction=None,
                explanation=explanation,
                reply_to_message_id=None
            )
        else:
            # 1. Pre-generate all voice files upfront if TTS is enabled
            if config.TTS_ENABLED:
                incoming_is_voice = (message.voice is not None or message.audio is not None)
                for action in actions:
                    if action.get("type") == "message" and action.get("content"):
                        act_reply_type = action.get("reply_type") or reply_type
                        has_voice_intent = (act_reply_type in ("voice", "both"))
                        random_chance = (random.random() < config.TTS_VOICE_PROBABILITY)
                        
                        if has_voice_intent or incoming_is_voice or random_chance:
                            temp_dir = "temp_voice"
                            os.makedirs(temp_dir, exist_ok=True)
                            output_path = os.path.join(temp_dir, f"reply_{chat_id}_{message_id}_{int(time.time())}_{random.randint(1000, 9999)}.wav")
                            
                            logger.info(f"[PRE-GEN] Генерируем голосовой ответ в {output_path}...")
                            success = await tts_manager.generate_audio(
                                text=action["content"],
                                provider=config.TTS_PROVIDER,
                                voice=config.TTS_VOICE,
                                output_path=output_path
                            )
                            if success and os.path.exists(output_path):
                                action["voice_file_path"] = output_path
                                logger.info(f"[PRE-GEN] Голосовой файл успешно подготовлен: {output_path}")
                            else:
                                logger.warning(f"[PRE-GEN] Не удалось сгенерировать аудио для: '{action['content']}'")

            # 2. Execute actions loop with precise delays using relative sleep
            prev_delay = 0.0
            is_first_action = True
            for action in actions:
                act_type = action.get("type")
                delay = action.get("delay", 0.0)
                content = action.get("content")
                
                # Apply delay relative to previous action
                sleep_time = max(0.0, delay - prev_delay)
                if sleep_time > 0:
                    logger.info(f"Ожидаем задержку {sleep_time} сек (абсолютная задержка {delay} сек)...")
                    await asyncio.sleep(sleep_time)
                prev_delay = delay
                
                if act_type == "reaction":
                    # Handle reaction (emoji)
                    if content:
                        logger.info(f"Ставим эмодзи-реакцию '{content}' на сообщение {message_id}...")
                        try:
                            await message.react(reaction=[ReactionTypeEmoji(emoji=content)])
                            logger.info(f"Реакция '{content}' успешно установлена.")
                            # Log reaction to history
                            history_manager.add_bot_action(
                                message_id=None,
                                needs_reply=False,
                                reply=None,
                                reaction=content,
                                explanation=explanation if is_first_action else "",
                                reply_to_message_id=message_id
                            )
                        except Exception as e:
                            logger.error(f"Не удалось поставить реакцию '{content}' на сообщение {message_id}: {e}")
                
                elif act_type == "message":
                    # Handle message (text and/or voice)
                    if content:
                        # Set message effect if not yet applied
                        current_effect_id = None
                        if not effect_applied and effect_id:
                            current_effect_id = effect_id
                            effect_applied = True
                            
                        # Reply mode & target message ID
                        reply_mode = action.get("reply_mode", "reply")
                        reply_to_id = action.get("reply_to_message_id")
                        if reply_to_id is None:
                            reply_to_id = message_id if reply_mode == "reply" else None
                        
                        voice_file_path = action.get("voice_file_path")
                        act_reply_type = action.get("reply_type") or reply_type
                        
                        sent_msg = None
                        if voice_file_path and os.path.exists(voice_file_path):
                            try:
                                voice_file = FSInputFile(voice_file_path)
                                
                                # Helper to send text (for 'both' mode)
                                async def try_send_text_both(use_effect, use_reply):
                                    eff = current_effect_id if use_effect else None
                                    rep = reply_to_id if use_reply else None
                                    if rep == message_id:
                                        return await message.reply(text=content, parse_mode=None, message_effect_id=eff)
                                    else:
                                        return await message.bot.send_message(chat_id=chat_id, text=content, parse_mode=None, reply_to_message_id=rep, message_effect_id=eff)

                                # Helper to send voice
                                async def try_send_voice(use_effect, use_reply):
                                    eff = current_effect_id if use_effect else None
                                    rep = reply_to_id if use_reply else None
                                    if rep == message_id:
                                        return await message.reply_voice(voice=voice_file, message_effect_id=eff)
                                    else:
                                        return await message.bot.send_voice(chat_id=chat_id, voice=voice_file, reply_to_message_id=rep, message_effect_id=eff)

                                # If both requested, send text first
                                text_sent = False
                                if act_reply_type == "both":
                                    logger.info(f"Отправляем сопутствующий текст перед голосовым (цель: {reply_to_id})...")
                                    try:
                                        try:
                                            await try_send_text_both(use_effect=True, use_reply=True)
                                            text_sent = True
                                        except Exception as text_err:
                                            err_str = str(text_err).lower()
                                            is_reply_err = "reply" in err_str or "not found" in err_str
                                            logger.warning(f"Не удалось отправить сопутствующий текст: {text_err}. Пробуем без эффекта...")
                                            try:
                                                await try_send_text_both(use_effect=False, use_reply=not is_reply_err)
                                                text_sent = True
                                            except Exception as text_err2:
                                                logger.error(f"Не удалось отправить сопутствующий текст даже без эффекта: {text_err2}")
                                    except Exception as text_outer_err:
                                        logger.error(f"Не удалось отправить сопутствующий текст: {text_outer_err}")

                                # Now send voice message
                                logger.info(f"Отправляем голосовое сообщение в чат (цель: {reply_to_id})...")
                                try:
                                    sent_msg = await try_send_voice(use_effect=(not text_sent), use_reply=True)
                                except Exception as err:
                                    err_str = str(err).lower()
                                    is_reply_err = "reply" in err_str or "not found" in err_str
                                    is_effect_err = "effect" in err_str or "bad request" in err_str
                                    
                                    if is_reply_err or is_effect_err:
                                        logger.warning(f"Ошибка при отправке голоса: {err}. Пробуем варианты обхода...")
                                        try:
                                            sent_msg = await try_send_voice(use_effect=False, use_reply=not is_reply_err)
                                        except Exception as err2:
                                            logger.warning(f"Повторная ошибка при отправке голоса: {err2}. Отправляем чистый standalone...")
                                            sent_msg = await try_send_voice(use_effect=False, use_reply=False)
                                    else:
                                        raise
                                        
                                logger.info(f"Голосовой ответ успешно отправлен (Msg ID: {sent_msg.message_id})")
                            except Exception as voice_send_err:
                                logger.error(f"Не удалось отправить голосовое сообщение: {voice_send_err}. Отправляем текст...")
                            finally:
                                try:
                                    os.remove(voice_file_path)
                                except Exception as rm_err:
                                    logger.warning(f"Не удалось удалить временный файл {voice_file_path}: {rm_err}")
                                    
                        if sent_msg is None:
                            # Send only text (or fallback if voice generation failed or voice send threw exception)
                            logger.info(f"Отправляем текстовый ответ в чат (цель: {reply_to_id})...")
                            
                            async def try_send_text(use_effect, use_reply):
                                eff = current_effect_id if use_effect else None
                                rep = reply_to_id if use_reply else None
                                if rep == message_id:
                                    return await message.reply(text=content, parse_mode=None, message_effect_id=eff)
                                else:
                                    return await message.bot.send_message(chat_id=chat_id, text=content, parse_mode=None, reply_to_message_id=rep, message_effect_id=eff)

                            try:
                                sent_msg = await try_send_text(use_effect=True, use_reply=True)
                            except Exception as err:
                                err_str = str(err).lower()
                                is_reply_err = "reply" in err_str or "not found" in err_str
                                is_effect_err = "effect" in err_str or "bad request" in err_str
                                
                                if is_reply_err or is_effect_err:
                                    logger.warning(f"Ошибка при отправке текста: {err}. Пробуем варианты обхода...")
                                    try:
                                        sent_msg = await try_send_text(use_effect=False, use_reply=not is_reply_err)
                                    except Exception as err2:
                                        logger.warning(f"Повторная ошибка при отправке текста: {err2}. Отправляем чистый standalone...")
                                        sent_msg = await try_send_text(use_effect=False, use_reply=False)
                                else:
                                    raise
                            logger.info(f"Ответ успешно отправлен (Msg ID: {sent_msg.message_id}): '{content}'")
                                
                        # Log message action to history
                        history_manager.add_bot_action(
                            message_id=sent_msg.message_id if sent_msg else None,
                            needs_reply=True,
                            reply=content,
                            reaction=None,
                            explanation=explanation if is_first_action else "",
                            reply_to_message_id=reply_to_id
                        )
                
                is_first_action = False

        # 3. Compress context if messages threshold reached
        await history_manager.compress_context_if_needed(config.OPENROUTER_API_KEY)
        logger.info(f"Обработка сообщения {message_id} завершена. Освобождаем Lock чата.")


@router.message_reaction()
async def handle_reaction_update(event: MessageReactionUpdated):
    """Tracks when users react to any message and updates history logs with user details."""
    chat_id = event.chat.id
    message_id = event.message_id
    user = event.user
    user_id = user.id if user else 0
    username = user.username if user else None
    display_name = user.full_name if user else "Кто-то"
    
    # Extract emojis
    emojis = []
    for r in event.new_reaction:
        if r.type == "emoji" and hasattr(r, "emoji"):
            emojis.append(r.emoji)

    logger.info(f"--- [ОБНОВЛЕНИЕ РЕАКЦИЙ] --- Чат ID: {chat_id} | Msg ID: {message_id} | Юзер: {display_name} (@{username if username else ''}) | Реакции: {emojis}")

    async with chat_locks[chat_id]:
        history_manager = ChatHistoryManager(chat_id)
        # Update reactions on any message in history (bot or user message)
        history_manager.update_reactions_on_any_message(
            message_id=message_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            emojis=emojis
        )
        logger.info(f"Реакции от {display_name} на сообщение {message_id} в чате {chat_id} обновлены: {emojis}")

async def main():
    dp.include_router(router)
    logger.info("Starting Telegram Bot on aiogram...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
