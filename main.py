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
        logger.info(
            "[iamthinking] 配置加载: enabled=%s, thinking=%s, done=%s, remove_thinking=%s",
            self.cfg.enabled,
            self.cfg.thinking_emoji_ids,
            self.cfg.done_emoji_ids,
            self.cfg.remove_thinking_on_done,
        )

    def _is_aiocqhttp(self, event: AstrMessageEvent) -> bool:
        if getattr(event, "platform_meta", None) is None:
            return False
        return event.get_platform_name() == "aiocqhttp"

    def _get_bot(self, event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        if bot is None:
            logger.debug("[iamthinking] bot 不存在，跳过贴表情")
            return None
        if not hasattr(bot, "set_msg_emoji_like"):
            logger.debug("[iamthinking] bot 不支持 set_msg_emoji_like，跳过贴表情")
            return None
        return bot

    async def _emoji_like(self, event: AstrMessageEvent, message_id: Any, emoji_ids: list[int], set_: bool) -> None:
        if not emoji_ids:
            logger.debug("[iamthinking] emoji_ids 为空，跳过贴表情")
            return
        bot = self._get_bot(event)
        if bot is None:
            return
        for emoji_id in sorted(set(emoji_ids)):
            try:
                logger.debug(
                    "[iamthinking] 贴表情: message_id=%s emoji_id=%s set=%s",
                    message_id,
                    emoji_id,
                    set_,
                )
                await bot.set_msg_emoji_like(message_id=message_id, emoji_id=emoji_id, set=set_)
            except Exception as e:
                logger.warning(f"贴表情失败: {e}")

    @filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent):
        logger.debug("[iamthinking] on_waiting_llm_request 触发")
        if not self.cfg.enabled:
            logger.debug("[iamthinking] 插件未启用，跳过")
            return
        if not self._is_aiocqhttp(event):
            logger.debug("[iamthinking] 非 aiocqhttp 平台，跳过")
            return
        if AiocqhttpMessageEvent is not None and not isinstance(event, AiocqhttpMessageEvent):
            logger.debug("[iamthinking] 事件类型不匹配 AiocqhttpMessageEvent，跳过")
            return

        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if message_id is None:
            logger.debug("[iamthinking] message_id 为空，跳过")
            return

        logger.debug("[iamthinking] 记录事件状态: message_id=%s", message_id)
        event.set_extra("iamthinking_active", True)
        event.set_extra("iamthinking_message_id", message_id)
        await self._emoji_like(event, message_id=message_id, emoji_ids=self.cfg.thinking_emoji_ids, set_=True)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: Any):
        if event.get_extra("iamthinking_active", False):
            logger.debug("[iamthinking] on_llm_response 标记已响应")
            event.set_extra("iamthinking_llm_responded", True)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        logger.debug("[iamthinking] after_message_sent 触发")
        if not self.cfg.enabled:
            logger.debug("[iamthinking] 插件未启用，跳过")
            return
        if not event.get_extra("iamthinking_active", False):
            logger.debug("[iamthinking] 未处于激活状态，跳过")
            return
        if not event.get_extra("iamthinking_llm_responded", False):
            logger.debug("[iamthinking] LLM 未响应，跳过")
            return
        if event.get_extra("iamthinking_done", False):
            logger.debug("[iamthinking] 已完成标记，跳过")
            return
        if not self._is_aiocqhttp(event):
            logger.debug("[iamthinking] 非 aiocqhttp 平台，跳过")
            return
        if AiocqhttpMessageEvent is not None and not isinstance(event, AiocqhttpMessageEvent):
            logger.debug("[iamthinking] 事件类型不匹配 AiocqhttpMessageEvent，跳过")
            return

        message_id = event.get_extra("iamthinking_message_id")
        if message_id is None:
            logger.debug("[iamthinking] message_id 为空，跳过")
            return

        await self._emoji_like(event, message_id=message_id, emoji_ids=self.cfg.done_emoji_ids, set_=True)
        if self.cfg.remove_thinking_on_done:
            logger.debug("[iamthinking] 移除处理中表情")
            await self._emoji_like(
                event,
                message_id=message_id,
                emoji_ids=self.cfg.thinking_emoji_ids,
                set_=False,
            )
        event.set_extra("iamthinking_done", True)
