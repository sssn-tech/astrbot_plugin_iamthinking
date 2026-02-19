from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig

try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
except Exception:
    AiocqhttpMessageEvent = None


class PluginConfig:
    def __init__(self, config: AstrBotConfig):
        self.enabled = bool(config.get("enabled", True))
        self.thinking_emoji_ids = self._as_int_list(config.get("thinking_emoji_ids", [66]))
        self.done_emoji_ids = self._as_int_list(config.get("done_emoji_ids", [74]))
        self.remove_thinking_on_done = bool(config.get("remove_thinking_on_done", True))

    @staticmethod
    def _as_int_list(value: Any) -> list[int]:
        if value is None:
            return []
        if isinstance(value, list):
            out: list[int] = []
            for i in value:
                try:
                    out.append(int(i))
                except Exception:
                    continue
            return out
        try:
            return [int(value)]
        except Exception:
            return []


@register("astrbot_plugin_iamthinking", "bytedance", "为 LLM 对话贴表情提示处理中/完成", "v0.1.0")
class IAmThinkingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config)

    def _is_aiocqhttp(self, event: AstrMessageEvent) -> bool:
        if getattr(event, "platform_meta", None) is None:
            return False
        return event.get_platform_name() == "aiocqhttp"

    def _get_bot(self, event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        if bot is None:
            return None
        if not hasattr(bot, "set_msg_emoji_like"):
            return None
        return bot

    async def _emoji_like(self, event: AstrMessageEvent, message_id: Any, emoji_ids: list[int], set_: bool) -> None:
        if not emoji_ids:
            return
        bot = self._get_bot(event)
        if bot is None:
            return
        for emoji_id in sorted(set(emoji_ids)):
            try:
                await bot.set_msg_emoji_like(message_id=message_id, emoji_id=emoji_id, set=set_)
            except Exception as e:
                logger.warning(f"贴表情失败: {e}")

    @filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent):
        if not self.cfg.enabled:
            return
        if not self._is_aiocqhttp(event):
            return
        if AiocqhttpMessageEvent is not None and not isinstance(event, AiocqhttpMessageEvent):
            return

        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if message_id is None:
            return

        event.set_extra("iamthinking_active", True)
        event.set_extra("iamthinking_message_id", message_id)
        await self._emoji_like(event, message_id=message_id, emoji_ids=self.cfg.thinking_emoji_ids, set_=True)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: Any):
        if event.get_extra("iamthinking_active", False):
            event.set_extra("iamthinking_llm_responded", True)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        if not self.cfg.enabled:
            return
        if not event.get_extra("iamthinking_active", False):
            return
        if not event.get_extra("iamthinking_llm_responded", False):
            return
        if event.get_extra("iamthinking_done", False):
            return
        if not self._is_aiocqhttp(event):
            return
        if AiocqhttpMessageEvent is not None and not isinstance(event, AiocqhttpMessageEvent):
            return

        message_id = event.get_extra("iamthinking_message_id")
        if message_id is None:
            return

        await self._emoji_like(event, message_id=message_id, emoji_ids=self.cfg.done_emoji_ids, set_=True)
        if self.cfg.remove_thinking_on_done:
            await self._emoji_like(
                event,
                message_id=message_id,
                emoji_ids=self.cfg.thinking_emoji_ids,
                set_=False,
            )
        event.set_extra("iamthinking_done", True)
