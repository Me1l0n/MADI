import json
import pathlib
import logging
import time
from typing import Dict, Any, List, Optional
import config
import llm_client

logger = logging.getLogger(__name__)

class ChatHistoryManager:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.file_path = config.HISTORY_DIR / f"history_{chat_id}.json"
        self.data = self._load_data()

    def _load_data(self) -> Dict[str, Any]:
        """Loads chat history from JSON file or creates a default structure."""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Verify fields are present
                    if "personality_summary" not in data:
                        data["personality_summary"] = ""
                    if "messages" not in data:
                        data["messages"] = []
                    return data
            except Exception as e:
                logger.error(f"Error reading history file for chat {self.chat_id}: {e}. Creating new.")
                
        return {
            "chat_id": self.chat_id,
            "personality_summary": "",
            "messages": []
        }

    def save(self):
        """Saves current data back to the JSON file."""
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving history file for chat {self.chat_id}: {e}")

    def add_user_message(self, message_id: int, user_id: int, username: Optional[str], display_name: str, text: str, reply_to_message_id: Optional[int] = None):
        """Adds a user message to the history."""
        self.data["messages"].append({
            "message_id": message_id,
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "text": text,
            "timestamp": int(time.time()),
            "reply_to_message_id": reply_to_message_id,
            "is_bot": False
        })
        self.save()

    def add_bot_action(self, message_id: Optional[int], needs_reply: bool, reply: Optional[str], reaction: Optional[str], explanation: str, reply_to_message_id: Optional[int] = None):
        """Adds a bot action (reply and/or reaction and explanation) to the history."""
        self.data["messages"].append({
            "message_id": message_id,  # Can be None if bot did not send a text message
            "needs_reply": needs_reply,
            "reply": reply,
            "reaction": reaction,
            "explanation": explanation,
            "timestamp": int(time.time()),
            "reply_to_message_id": reply_to_message_id,
            "user_reactions": [],
            "is_bot": True
        })
        self.save()

    def update_bot_message_id_after_send(self, sent_message_id: int):
        """If the bot sent a message, we update the last bot action entry with the real message_id."""
        for msg in reversed(self.data["messages"]):
            if msg.get("is_bot") and msg.get("message_id") is None and msg.get("needs_reply"):
                msg["message_id"] = sent_message_id
                self.save()
                break

    def update_reactions_on_any_message(self, message_id: int, user_id: int, username: Optional[str], display_name: str, emojis: List[str]):
        """Updates reactions set by a specific user on any message (user's or bot's)."""
        updated = False
        for msg in reversed(self.data["messages"]):
            if msg.get("message_id") == message_id:
                if "reactions" not in msg:
                    # Upgrade old bot user_reactions field if present
                    if "user_reactions" in msg and msg["user_reactions"]:
                        msg["reactions"] = [{"user_id": 0, "username": None, "display_name": "Кто-то", "emoji": e} for e in msg["user_reactions"]]
                    else:
                        msg["reactions"] = []
                
                # Remove previous reactions by this user
                msg["reactions"] = [r for r in msg["reactions"] if r.get("user_id") != user_id]
                
                # Add new reactions by this user
                for emoji in emojis:
                    msg["reactions"].append({
                        "user_id": user_id,
                        "username": username,
                        "display_name": display_name,
                        "emoji": emoji
                    })
                updated = True
                break
        if updated:
            self.save()

    def _get_message_by_id(self, message_id: int) -> Optional[Dict[str, Any]]:
        for msg in self.data.get("messages", []):
            if msg.get("message_id") == message_id:
                return msg
        return None

    def get_personality_summary(self) -> str:
        return self.data.get("personality_summary", "")

    def get_messages_count(self) -> int:
        return len(self.data.get("messages", []))

    def format_history_for_llm(self, keep_silent_thoughts_count: int = 5) -> str:
        """
        Formats history into a clean conversation transcript.
        Includes user names, bot replies, reactions with user details, and replied-to context.
        """
        messages = self.data.get("messages", [])
        lines = []
        
        # Determine which silent bot thoughts to keep
        silent_indices_to_keep = set()
        silent_count = 0
        
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.get("is_bot"):
                reply_text = msg.get("reply")
                reaction = msg.get("reaction")
                is_silent = not reply_text and not reaction
                if is_silent:
                    if silent_count < keep_silent_thoughts_count:
                        silent_indices_to_keep.add(idx)
                        silent_count += 1
                        
        for idx, msg in enumerate(messages):
            # Format reactions on this message
            reactions = msg.get("reactions", [])
            if not reactions and "user_reactions" in msg and msg["user_reactions"]:
                reactions = [{"user_id": 0, "username": None, "display_name": "Кто-то", "emoji": e} for e in msg["user_reactions"]]
            
            react_suffix = ""
            if reactions:
                react_parts = []
                for r in reactions:
                    disp = r.get("display_name", "Кто-то")
                    uname = r.get("username")
                    emoji = r.get("emoji")
                    user_info = f"{disp} (@{uname})" if uname else disp
                    react_parts.append(f"{user_info} поставил {emoji}")
                react_suffix = f" [Реакции: {', '.join(react_parts)}]"

            # Format reply-to context
            reply_to_id = msg.get("reply_to_message_id")
            reply_context = ""
            if reply_to_id:
                replied_msg = self._get_message_by_id(reply_to_id)
                if replied_msg:
                    if replied_msg.get("is_bot"):
                        author = "Мади"
                        replied_text = replied_msg.get("reply") or "[Медиа/Голосовое]"
                    else:
                        disp = replied_msg.get("display_name")
                        uname = replied_msg.get("username")
                        author = f"{disp} (@{uname})" if uname else (disp or f"user_{replied_msg.get('user_id')}")
                        replied_text = replied_msg.get("text") or "[Медиа/Голосовое]"
                    
                    if len(replied_text) > 50:
                        replied_text = replied_text[:47] + "..."
                    reply_context = f" (Ответ на сообщение от {author}: \"{replied_text}\")"

            msg_id = msg.get("message_id")
            id_prefix = f"[ID: {msg_id}] " if msg_id else ""

            if not msg.get("is_bot"):
                # User message
                display_name = msg.get("display_name")
                username = msg.get("username")
                
                name_parts = []
                if display_name:
                    name_parts.append(display_name)
                if username:
                    name_parts.append(f"@{username}")
                
                if name_parts:
                    user_str = " ".join(name_parts)
                else:
                    user_str = f"user_{msg.get('user_id')}"
                
                text = msg.get("text", "")
                lines.append(f"{id_prefix}[Пользователь {user_str}]{reply_context}: {text}{react_suffix}")
            else:
                # Bot action
                reply_text = msg.get("reply")
                reaction = msg.get("reaction")
                explanation = msg.get("explanation", "")
                
                if reply_text:
                    bot_react_str = f" [Мади поставил реакцию: {reaction}]" if reaction else ""
                    lines.append(f"{id_prefix}[Мади]{reply_context}: {reply_text}{bot_react_str}{react_suffix}")
                    if explanation:
                        lines.append(f"  *(Внутренние мысли бота: {explanation})*")
                elif reaction:
                    lines.append(f"[Мади]{reply_context}: [Промолчал текстом, но поставил реакцию: {reaction}]{react_suffix}")
                    if explanation:
                        lines.append(f"  *(Внутренние мысли бота: {explanation})*")
                else:
                    if idx in silent_indices_to_keep:
                        lines.append(f"[Мади]{reply_context}: [Промолчал, реакцию не ставил]{react_suffix}")
                        if explanation:
                            lines.append(f"  *(Внутренние мысли бота: {explanation})*")
                            
        return "\n".join(lines)

    async def compress_context_if_needed(self, api_key: str):
        """
        Checks if the message count exceeds the threshold.
        If yes, runs context compression, updates personality_summary,
        and keeps only the last KEEP_LAST_MESSAGES.
        """
        messages = self.data.get("messages", [])
        if len(messages) < config.COMPRESSION_THRESHOLD:
            return False

        logger.info(f"Chat {self.chat_id} history size is {len(messages)}. Triggering context compression...")
        
        # Split history
        keep_count = config.KEEP_LAST_MESSAGES
        messages_to_compress = messages[:-keep_count]
        messages_to_keep = messages[-keep_count:]
        
        # Format history to compress (using a large limit for silent thoughts since we want full info for compression)
        temp_data = {
            "personality_summary": self.data.get("personality_summary", ""),
            "messages": messages_to_compress
        }
        
        # We temporarily load this slice to format it
        temp_manager = ChatHistoryManager(self.chat_id)
        temp_manager.data = temp_data
        formatted_history_str = temp_manager.format_history_for_llm(keep_silent_thoughts_count=100)
        
        try:
            # Run compression
            new_summary = await llm_client.compress_history(
                api_key=api_key,
                model=config.COMPRESSION_MODEL,
                previous_summary=self.data.get("personality_summary", ""),
                formatted_history_to_compress=formatted_history_str
            )
            
            # Save updated data
            self.data["personality_summary"] = new_summary
            self.data["messages"] = messages_to_keep
            self.save()
            logger.info(f"Context compression successfully completed for chat {self.chat_id}.")
            return True
        except Exception as e:
            logger.error(f"Failed to compress context for chat {self.chat_id}: {e}")
            return False
