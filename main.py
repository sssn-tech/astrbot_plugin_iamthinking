from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig

try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
except ImportError:
    AiocqhttpMessageEvent = None


class PluginConfig:
    def __init__(self, config: AstrBotConfig):
        self.enabled = bool(config.get("enabled", True))
        self.thinking_emoji_ids = self._as_int_list(config.get("thinking_emoji_ids", [66]))
        self.done_emoji_ids = self._as_int_list(config.get("done_emoji_ids", [74]))
        self.remove_thinking_on_done = bool(config.get("remove_thinking_on_done", True))
        self.timeout_seconds = int(config.get("timeout_seconds", 120))
        self.error_emoji_ids = self._as_int_list(config.get("error_emoji_ids", [264]))
        self.remove_thinking_on_error = bool(config.get("remove_thinking_on_error", True))
        self.using_tool_emoji_ids = self._as_int_list(config.get("using_tool_emoji_ids", [270]))
        self.remove_thinking_on_tool = bool(config.get("remove_thinking_on_tool", True))

    @staticmethod
    def _as_int_list(value: Any) -> list[int]:
        if value is None:
            return []
        if isinstance(value, list):
            out: list[int] = []
            for i in value:
                try:
                    out.append(int(i))
                except (TypeError, ValueError):
                    continue
            return out
        try:
            return [int(value)]
        except (TypeError, ValueError):
            return []


class IAmThinkingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config)
        self._timeout_tasks: dict[Any, asyncio.Task] = {}
        logger.info(
            "[iamthinking] 配置加载: enabled=%s, thinking=%s, done=%s, "
            "remove_thinking=%s, timeout=%s, error=%s, remove_thinking_on_error=%s, "
            "using_tool=%s, remove_thinking_on_tool=%s",
            self.cfg.enabled,
            self.cfg.thinking_emoji_ids,
            self.cfg.done_emoji_ids,
            self.cfg.remove_thinking_on_done,
            self.cfg.timeout_seconds,
            self.cfg.error_emoji_ids,
            self.cfg.remove_thinking_on_error,
            self.cfg.using_tool_emoji_ids,
            self.cfg.remove_thinking_on_tool,
        )

    async def terminate(self):
        """插件卸载时清理所有未完成的超时任务。"""
        for message_id, task in self._timeout_tasks.items():
            task.cancel()
            logger.debug("[iamthinking] 取消超时任务: message_id=%s", message_id)
        self._timeout_tasks.clear()

    def _is_aiocqhttp(self, event: AstrMessageEvent) -> bool:
        if getattr(event, "platform_meta", None) is None:
            return False
        if event.get_platform_name() != "aiocqhttp":
            return False
        return bool(event.get_group_id())

    def _get_bot(self, event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        if bot is None:
            logger.debug("[iamthinking] bot 不存在，跳过贴表情")
            return None
        if not hasattr(bot, "set_msg_emoji_like"):
            logger.debug("[iamthinking] bot 不支持 set_msg_emoji_like，跳过贴表情")
            return None
        return bot

    async def _emoji_like(
        self,
        event: AstrMessageEvent,
        message_id: Any,
        emoji_ids: list[int],
        set_: bool,
    ) -> bool:
        if not emoji_ids:
            logger.debug("[iamthinking] emoji_ids 为空，跳过贴表情")
            return True
        bot = self._get_bot(event)
        if bot is None:
            return False
        all_ok = True
        for emoji_id in sorted(set(emoji_ids)):
            try:
                logger.debug(
                    "[iamthinking] 贴表情: message_id=%s emoji_id=%s set=%s",
                    message_id,
                    emoji_id,
                    set_,
                )
                await bot.set_msg_emoji_like(message_id=message_id, emoji_id=emoji_id, set=set_)
            except (TimeoutError, ConnectionError, OSError) as e:
                all_ok = False
                logger.warning(
                    "[iamthinking] 贴表情失败: message_id=%s emoji_id=%s set=%s event=%s err=%s",
                    message_id,
                    emoji_id,
                    set_,
                    type(event).__name__,
                    e,
                )
            except Exception as e:
                all_ok = False
                logger.warning(
                    "[iamthinking] 贴表情失败: message_id=%s emoji_id=%s set=%s event=%s err=%s",
                    message_id,
                    emoji_id,
                    set_,
                    type(event).__name__,
                    e,
                )
        return all_ok

    async def _transition_state(
        self,
        event: AstrMessageEvent,
        message_id: Any,
        *,
        remove_emoji_ids: list[int] | None = None,
        add_emoji_ids: list[int] | None = None,
    ) -> bool:
        """切换表情状态：先移除旧表情，再贴上新表情。"""
        all_ok = True
        if remove_emoji_ids:
            ok = await self._emoji_like(event, message_id, remove_emoji_ids, set_=False)
            all_ok = all_ok and ok
        if add_emoji_ids:
            ok = await self._emoji_like(event, message_id, add_emoji_ids, set_=True)
            all_ok = all_ok and ok
        return all_ok

    async def _handle_timeout(self, event: AstrMessageEvent, message_id: Any):
        """超时回调：移除思考表情，贴上失败表情。"""
        self._timeout_tasks.pop(message_id, None)

        if event.get_extra("iamthinking_done", False):
            logger.debug("[iamthinking] 超时触发但已完成，跳过: message_id=%s", message_id)
            return
        if event.get_extra("iamthinking_timed_out", False):
            logger.debug("[iamthinking] 超时触发但已标记超时，跳过: message_id=%s", message_id)
            return

        logger.info("[iamthinking] LLM 超时: message_id=%s", message_id)
        event.set_extra("iamthinking_timed_out", True)

        # 根据当前状态决定要移除哪些表情
        remove_ids: list[int] = []
        if event.get_extra("iamthinking_in_tool", False):
            remove_ids = self.cfg.using_tool_emoji_ids
        elif self.cfg.remove_thinking_on_error:
            remove_ids = self.cfg.thinking_emoji_ids

        await self._transition_state(
            event,
            message_id,
            remove_emoji_ids=remove_ids if remove_ids else None,
            add_emoji_ids=self.cfg.error_emoji_ids if self.cfg.error_emoji_ids else None,
        )

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

        # 启动超时检测
        if self.cfg.timeout_seconds > 0:
            task = asyncio.create_task(
                self._handle_timeout(event, message_id),
            )
            self._timeout_tasks[message_id] = task
            logger.debug(
                "[iamthinking] 启动超时检测: message_id=%s timeout=%ds",
                message_id,
                self.cfg.timeout_seconds,
            )

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        if not event.get_extra("iamthinking_active", False):
            return
        logger.debug("[iamthinking] on_llm_response 标记已响应")
        event.set_extra("iamthinking_llm_responded", True)

        # 取消超时任务
        message_id = event.get_extra("iamthinking_message_id")
        if message_id is not None and message_id in self._timeout_tasks:
            self._timeout_tasks.pop(message_id).cancel()
            logger.debug("[iamthinking] 取消超时任务: message_id=%s", message_id)

    @filter.on_using_llm_tool()
    async def on_using_llm_tool(self, event: AstrMessageEvent, tool: Any, tool_args: dict):
        if not self.cfg.enabled:
            return
        if not event.get_extra("iamthinking_active", False):
            return
        if event.get_extra("iamthinking_done", False):
            return
        if event.get_extra("iamthinking_timed_out", False):
            return
        if not self._is_aiocqhttp(event):
            return
        if AiocqhttpMessageEvent is not None and not isinstance(event, AiocqhttpMessageEvent):
            return

        message_id = event.get_extra("iamthinking_message_id")
        if message_id is None:
            return

        # 已在工具状态中，不重复切换
        if event.get_extra("iamthinking_in_tool", False):
            return

        logger.debug("[iamthinking] 切换到工具调用状态: message_id=%s", message_id)
        event.set_extra("iamthinking_in_tool", True)

        remove_ids = self.cfg.thinking_emoji_ids if self.cfg.remove_thinking_on_tool else None
        await self._transition_state(
            event,
            message_id,
            remove_emoji_ids=remove_ids,
            add_emoji_ids=self.cfg.using_tool_emoji_ids,
        )

    @filter.on_llm_tool_respond()
    async def on_llm_tool_respond(self, event: AstrMessageEvent, tool: Any, tool_args: dict, tool_result: Any):
        if not self.cfg.enabled:
            return
        if not event.get_extra("iamthinking_active", False):
            return
        if not event.get_extra("iamthinking_in_tool", False):
            return
        if event.get_extra("iamthinking_done", False):
            return
        if event.get_extra("iamthinking_timed_out", False):
            return
        if not self._is_aiocqhttp(event):
            return
        if AiocqhttpMessageEvent is not None and not isinstance(event, AiocqhttpMessageEvent):
            return

        message_id = event.get_extra("iamthinking_message_id")
        if message_id is None:
            return

        logger.debug("[iamthinking] 工具调用完成，切回思考状态: message_id=%s", message_id)
        event.set_extra("iamthinking_in_tool", False)

        await self._transition_state(
            event,
            message_id,
            remove_emoji_ids=self.cfg.using_tool_emoji_ids,
            add_emoji_ids=self.cfg.thinking_emoji_ids,
        )

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
        if event.get_extra("iamthinking_failed", False):
            logger.debug("[iamthinking] 已标记失败，跳过")
            return
        if event.get_extra("iamthinking_finishing", False):
            logger.debug("[iamthinking] 完成处理中，跳过")
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

        max_retry = 3
        retry_count = event.get_extra("iamthinking_finish_retry", 0) or 0
        if retry_count >= max_retry:
            logger.debug("[iamthinking] 完成表情处理失败次数过多，停止重试")
            event.set_extra("iamthinking_failed", True)
            return

        event.set_extra("iamthinking_finishing", True)

        # 判断是否之前已超时
        timed_out = event.get_extra("iamthinking_timed_out", False)
        in_tool = event.get_extra("iamthinking_in_tool", False)

        if timed_out:
            # 超时后恢复：移除 error 表情（和可能的 tool 表情），贴 done 表情
            logger.info("[iamthinking] 超时后恢复完成: message_id=%s", message_id)
            remove_ids: list[int] = list(self.cfg.error_emoji_ids)
            if in_tool and self.cfg.using_tool_emoji_ids:
                remove_ids.extend(self.cfg.using_tool_emoji_ids)
            elif self.cfg.thinking_emoji_ids:
                remove_ids.extend(self.cfg.thinking_emoji_ids)

            done_ok = await self._transition_state(
                event,
                message_id,
                remove_emoji_ids=remove_ids,
                add_emoji_ids=self.cfg.done_emoji_ids,
            )
        else:
            # 正常完成
            remove_ids = []
            if in_tool and self.cfg.using_tool_emoji_ids:
                remove_ids = list(self.cfg.using_tool_emoji_ids)
            elif self.cfg.remove_thinking_on_done:
                remove_ids = list(self.cfg.thinking_emoji_ids)

            done_ok = await self._transition_state(
                event,
                message_id,
                remove_emoji_ids=remove_ids if remove_ids else None,
                add_emoji_ids=self.cfg.done_emoji_ids,
            )

        if done_ok:
            event.set_extra("iamthinking_done", True)
            # 清理超时任务（可能 LLM 响应和 after_message_sent 几乎同时触发）
            self._timeout_tasks.pop(message_id, None)
        else:
            logger.debug("[iamthinking] 完成表情处理未全部成功，允许重试")
            event.set_extra("iamthinking_finish_retry", retry_count + 1)
            event.set_extra("iamthinking_finishing", False)
