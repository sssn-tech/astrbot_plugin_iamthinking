# astrbot_plugin_iamthinking

别吵，我在思考！
还在因为思考型模型反应太慢而烦恼？这个插件不能解决问题，但可以提升一点体验。
收到用户消息并准备调用 LLM 时，为这条用户消息贴一个表情表示处理中；发送回复后，再贴一个表情表示处理完成。

## 配置

插件配置通过 `_conf_schema.json` 提供，默认仅在 QQ OneBot (aiocqhttp) 平台生效：

- enabled：是否启用
- thinking_emoji_ids：处理中表情 ID 列表
- done_emoji_ids：完成表情 ID 列表
- remove_thinking_on_done：完成后是否移除处理中表情

## 说明

插件只在 AstrBot 准备调用 LLM 时触发，不会对普通消息、指令消息贴表情。
