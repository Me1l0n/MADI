import aiohttp
import json
import logging
import asyncio
import re
import config
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

async def call_openrouter(
    api_key: str, 
    model: str, 
    messages: List[Dict[str, str]], 
    temperature: float = 0.8, 
    reasoning_level: Optional[str] = None,
    max_retries: int = 3,
    backoff_factor: float = 2.0
) -> str:
    """
    Calls the OpenRouter API with a list of messages.
    Includes exponential backoff retry logic for resilience.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/google/gemini",
        "X-Title": "Antigravity Telegram Bot"
    }

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature
    }

    if reasoning_level:
        payload["reasoning"] = {
            "effort": reasoning_level
        }

    # Use aiohttp to make the request
    for attempt in range(max_retries):
        try:
            proxy_url = config.PROXY if config.PROXY else None
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=60, proxy=proxy_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            return data["choices"][0]["message"]["content"]
                        else:
                            raise ValueError(f"Unexpected OpenRouter response structure: {data}")
                    elif response.status == 429:
                        wait_time = backoff_factor ** (attempt + 1)
                        logger.warning(f"Rate limited (429). Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    elif response.status >= 500:
                        wait_time = backoff_factor ** (attempt + 1)
                        logger.warning(f"Server error ({response.status}). Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        error_text = await response.text()
                        logger.error(f"HTTP Error {response.status}: {error_text}")
                        raise ValueError(f"OpenRouter API returned HTTP {response.status}: {error_text}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            wait_time = backoff_factor ** (attempt + 1)
            logger.warning(f"Network error: {e}. Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(wait_time)
    
    raise ValueError("Failed to get response from OpenRouter after maximum retries.")


def _normalize_parsed_json(parsed: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        return parsed
        
    if "needs_reply" not in parsed:
        parsed["needs_reply"] = bool(parsed.get("reply") or parsed.get("reaction") or parsed.get("actions"))
        
    if "actions" not in parsed or not isinstance(parsed["actions"], list):
        actions = []
        if parsed.get("reaction"):
            actions.append({
                "type": "reaction",
                "delay": 1,
                "content": parsed["reaction"]
            })
        if parsed.get("reply"):
            actions.append({
                "type": "message",
                "delay": 2,
                "content": parsed["reply"],
                "reply_mode": "reply"
            })
        parsed["actions"] = actions
    else:
        cleaned_actions = []
        for action in parsed["actions"]:
            if not isinstance(action, dict):
                continue
            act_type = action.get("type")
            if act_type not in ("message", "reaction"):
                continue
            
            try:
                delay = float(action.get("delay", 0))
            except (ValueError, TypeError):
                delay = 0.0
                
            content = action.get("content")
            if not content:
                continue
                
            reply_mode = action.get("reply_mode", "reply")
            if reply_mode not in ("reply", "standalone"):
                reply_mode = "reply"
                
            reply_to_message_id = action.get("reply_to_message_id")
            if reply_to_message_id is not None:
                try:
                    reply_to_message_id = int(reply_to_message_id)
                except (ValueError, TypeError):
                    reply_to_message_id = None
                
            act_reply_type = action.get("reply_type")
            if act_reply_type not in ("text", "voice", "both"):
                act_reply_type = None

            cleaned_actions.append({
                "type": act_type,
                "delay": delay,
                "content": content,
                "reply_mode": reply_mode,
                "reply_to_message_id": reply_to_message_id,
                "reply_type": act_reply_type
            })
        parsed["actions"] = cleaned_actions
        
    # Root fields fallback for older components
    if not parsed.get("reply"):
        for act in parsed["actions"]:
            if act["type"] == "message":
                parsed["reply"] = act["content"]
                break
    if not parsed.get("reaction"):
        for act in parsed["actions"]:
            if act["type"] == "reaction":
                parsed["reaction"] = act["content"]
                break
                
    return parsed


def parse_json_response(text: str) -> Dict[str, Any]:
    """
    Robust JSON parser for LLM responses. Extracts JSON from markdown blocks if necessary.
    """
    text = text.strip()
    
    # Try parsing directly
    try:
        return _normalize_parsed_json(json.loads(text))
    except json.JSONDecodeError:
        pass

    # Try finding JSON inside markdown code blocks ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return _normalize_parsed_json(json.loads(match.group(1).strip()))
        except json.JSONDecodeError:
            pass

    # Try finding the first '{' and the last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return _normalize_parsed_json(json.loads(text[start:end+1].strip()))
        except json.JSONDecodeError:
            pass
            
    # Regular expression fallback extraction if JSON is totally broken
    # E.g., we need needs_reply, reply, reaction, effect, reply_type, explanation
    result = {
        "needs_reply": False,
        "reply": None,
        "reaction": None,
        "effect": None,
        "reply_type": "text",
        "explanation": "Failed to parse JSON, regex fallback used."
    }
    
    # Look for needs_reply
    needs_reply_match = re.search(r'"needs_reply"\s*:\s*(true|false)', text, re.IGNORECASE)
    if needs_reply_match:
        result["needs_reply"] = needs_reply_match.group(1).lower() == "true"
        
    # Look for reply
    reply_match = re.search(r'"reply"\s*:\s*"(.*?)"', text, re.DOTALL)
    if reply_match:
        result["reply"] = reply_match.group(1)
        
    # Look for reaction
    reaction_match = re.search(r'"reaction"\s*:\s*"(.*?)"', text, re.DOTALL)
    if reaction_match:
        val = reaction_match.group(1).strip()
        if val == "null" or not val:
            result["reaction"] = None
        else:
            result["reaction"] = val

    # Look for effect
    effect_match = re.search(r'"effect"\s*:\s*"(.*?)"', text, re.DOTALL)
    if effect_match:
        val = effect_match.group(1).strip()
        if val == "null" or not val:
            result["effect"] = None
        else:
            result["effect"] = val

    # Look for reply_type
    reply_type_match = re.search(r'"reply_type"\s*:\s*"(.*?)"', text, re.DOTALL)
    if reply_type_match:
        val = reply_type_match.group(1).strip()
        if val == "null" or not val:
            result["reply_type"] = "text"
        else:
            result["reply_type"] = val
            
    # Look for explanation
    explanation_match = re.search(r'"explanation"\s*:\s*"(.*?)"', text, re.DOTALL)
    if explanation_match:
        result["explanation"] = explanation_match.group(1)
        
    return _normalize_parsed_json(result)


async def get_bot_decision(
    api_key: str,
    model: str,
    system_prompt: str,
    formatted_history: str,
    new_message_str: str
) -> Dict[str, Any]:
    """
    Requests a response decision from the main bot model.
    """
    prompt = (
        f"Вот история сообщений в чате:\n"
        f"====================\n"
        f"{formatted_history}\n"
        f"====================\n\n"
        f"Новое входящее сообщение, на которое нужно ответить или среагировать:\n"
        f"{new_message_str}\n\n"
        f"Выдай ответ строго в формате JSON, соответствующем схеме. Никакого дополнительного текста вне JSON."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]
    raw_response = await call_openrouter(api_key, model, messages, temperature=0.85)
    logger.info(f"Raw LLM response: {raw_response}")
    parsed = parse_json_response(raw_response)
    return parsed
async def compress_history(
    api_key: str,
    model: str,
    previous_summary: str,
    formatted_history_to_compress: str
) -> str:
    """
    Compresses chat history into a persistent personality/style profile.
    Uses Gemini-3.1-flash-lite on reasoning level 'medium'.
    """
    system_prompt = (
        "Ты — модель сжатия контекста для самообучающегося Telegram-бота.\n"
        "Твоя задача — проанализировать историю общения пользователей и ответов бота, чтобы выделить паттерны и пополнить профиль личности бота.\n"
        "Сконцентрируйся на следующем:\n"
        "1. Стиль общения в чате: какой сленг, манеру речи, запретные темы или шутки используют пользователи.\n"
        "2. Что бот делал ХОРОШО: какие его ответы или реакции вызвали смех, позитивные смайлики (лайки, огоньки, смех) или одобрение от пользователей.\n"
        "3. Что бот делал ПЛОХО: какие его ответы вызвали негатив, дизлайки, клоунов или обвинения в том, что он бездушная машина.\n"
        "4. Правила активности: в какие моменты боту стоило промолчать (например, когда идет серьезное обсуждение или пользователи общаются между собой), а в какие моменты его шутка зашла идеально.\n"
        "Напиши развернутый, подробный и структурированный профиль (на русском языке), основываясь на предыдущем профиле и новых логах."
    )

    user_prompt = (
        f"Предыдущий профиль личности бота:\n"
        f"--------------------\n"
        f"{previous_summary if previous_summary else 'Пока отсутствует. Бот только начинает обучение.'}\n"
        f"--------------------\n\n"
        f"Новая история сообщений с реакциями и объяснениями для анализа (200 сообщений):\n"
        f"====================\n"
        f"{formatted_history_to_compress}\n"
        f"====================\n\n"
        f"Сгенерируй новый, объединенный и уточненный профиль личности бота. Напиши его подробно, не упуская важных деталей."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # Call Gemini-3.1-flash-lite on medium reasoning
    new_summary = await call_openrouter(
        api_key, 
        model, 
        messages, 
        temperature=0.4, 
        reasoning_level="medium"
    )
    return new_summary.strip()
