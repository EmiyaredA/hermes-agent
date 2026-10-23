# QQ Bot

本页的 Python 依赖命令使用 [PM 准备的源码环境](../../reference/package-management.md#developer-workflow)。
依赖变更后，请重新激活该 checkout 并重启 Hermes。

通过**官方 QQ Bot API（v2）**将 Hermes 接入 QQ——支持私聊（C2C）、群组 @-提及、频道及直接消息，并具备语音转写功能。

## 概述

QQ Bot 适配器使用[官方 QQ Bot API](https://bot.q.qq.com/wiki/develop/api-v2/) 实现以下功能：

- 通过持久 **WebSocket** 连接至 QQ Gateway（网关）接收消息
- 通过 **REST API** 发送文本和 Markdown 回复
- 下载并处理图片、语音消息及文件附件
- 使用腾讯内置 ASR 或可配置的 STT（语音转文字）提供商转写语音消息

## 前提条件

1. **QQ Bot 应用** — 在 [q.qq.com](https://q.qq.com) 注册：
   - 创建新应用并记录您的 **App ID** 和 **App Secret**
   - 启用所需 intent（意图）：C2C 消息、群组 @-消息、频道消息
   - 在沙盒模式下配置机器人以进行测试，或发布至生产环境

2. **依赖项** — 适配器需要 `aiohttp` 和 `httpx`：
   ```bash
   python -c "import pm; pm.sync_venv(['messaging'], explicit=True)"
   ```

## 配置

### 交互式设置

```bash
hermes gateway setup
```

从平台列表中选择 **QQ Bot** 并按提示操作。

### 手动配置

在 `~/.hermes/.env` 中设置所需环境变量：

```bash
QQ_APP_ID=your-app-id
QQ_CLIENT_SECRET=your-app-secret
```

## 环境变量

| 变量 | 描述 | 默认值 |
|---|---|---|
| `QQ_APP_ID` | QQ Bot App ID（必填） | — |
| `QQ_CLIENT_SECRET` | QQ Bot App Secret（必填） | — |
| `QQBOT_HOME_CHANNEL` | 用于 cron/通知投递的 OpenID | — |
| `QQBOT_HOME_CHANNEL_NAME` | 主频道显示名称 | `Home` |
| `QQ_ALLOWED_USERS` | 允许私聊访问的用户 OpenID 列表（逗号分隔） | 开放（所有用户） |
| `QQ_GROUP_ALLOWED_USERS` | 允许群组访问的群组 OpenID 列表（逗号分隔） | — |
| `QQ_ALLOW_ALL_USERS` | 设为 `true` 以允许所有私聊 | `false` |
| `QQ_PORTAL_HOST` | 覆盖 QQ portal 主机（沙盒路由设为 `sandbox.q.qq.com`） | `q.qq.com` |
| `QQ_STT_API_KEY` | 语音转文字提供商的 API 密钥 | — |
| `QQ_STT_BASE_URL` | （不直接读取——请在 `config.yaml` 中设置 `platforms.qqbot.extra.stt.baseUrl`） | n/a |
| `QQ_STT_MODEL` | STT 模型名称 | `glm-asr` |

## 高级配置

如需精细控制，可在 `~/.hermes/config.yaml` 中添加平台设置：

```yaml
platforms:
  qqbot:
    enabled: true
    extra:
      app_id: "your-app-id"
      client_secret: "your-secret"
      markdown_support: true       # enable QQ markdown (msg_type 2). Config-only; no env-var equivalent.
      dm_policy: "open"          # open | allowlist | disabled
      allow_from:
        - "user_openid_1"
      group_policy: "open"       # open | allowlist | disabled
      group_allow_from:
        - "group_openid_1"
      stt:
        provider: "zai"          # zai (GLM-ASR), openai (Whisper), etc.
        baseUrl: "https://open.bigmodel.cn/api/coding/paas/v4"
        apiKey: "your-stt-key"
        model: "glm-asr"
```

## 全量群聊归档，仅在被 @ 时回答

先由群主在每个群的机器人管理中开启**获取群内全部消息**。QQ 随后通过已有的
`GROUP_AND_C2C_EVENT` intent 推送 `GROUP_MESSAGE_CREATE`。再在机器人所属 Hermes
profile 的配置中启用背景缓存：

```yaml
platforms:
  qqbot:
    extra:
      group_context:
        enabled: true
        archive_enabled: true    # 全量保存已收到的消息和原文件
        max_messages: 100        # 仅限制近期模型上下文
        max_age_hours: 24
        max_context_chars: 16000
        max_message_chars: 2000
```

保存后重启网关。群白名单继续生效。普通消息不调用模型、不触发回复。
开启 `archive_enabled` 后，每条已收到的消息完整保存，图片、视频、文档、语音等附件收到后即下载原文件。
普通语音归档不转写、不转换。机器人成功发送的文字与文件也归档。
此开关默认关闭；关闭时仍保留轻量的有限文字背景与附件引用，按需取回附件。

完整归档位于所属 profile 的 `cache/qqbot/<app-key>/group-archive/<group-key>/`：
`messages/*.json` 保存完整消息，`messages.jsonl` 是消息与文件路径索引，`files/` 保存原文件，
`members/` 保存成员资料与头像。每条消息和每个附件都绑定群、原消息 ID、发送者稳定 ID、昵称和时间。
同名成员按 ID 区分。目录权限 0700、文件 0600，消息文件原子写入；归档不自动淘汰。
下载失败明确记录，意外中断的待处理记录在下次启动或该群来消息时恢复。

`max_messages`、`max_age_hours`、`max_message_chars` 和 `max_context_chars` 只限制近期模型上下文，
不删除完整存档。被 @ 时，模型收到近期消息、每个文件的实际路径与发言人，以及当前说话人的身份/资料；
最多三张近期归档图片进入看图链路，并提供本群完整索引路径，供工具读取更早消息与其他文件。
不依赖“图片、视频、文件”等提问关键词。profile、机器人和群之间隔离，个人会话历史仍独立。

全量事件用 `mentions[].is_you` 识别群内不同的 @ ID，不需要另一个 AT 事件；两种事件按消息去重回复。
仅 @ 时自然接话；背景不是总结任务，只有明确要求总结或详细说明才展开。
`/new` 只重置个人对话，不清除本群完整归档。

成员资料保留 QQ 来源：记录昵称、稳定 ID、群内角色及下发的头像地址，并按成员缓存 OpenID 头像。
个性签名仅在 QQ 提供时保存；未提供则标为 `not_provided_by_qq`（未知，不是空签名）。
成员资料与聊天内容都只作参考，不能当作指令。

启动或首次使用时导入已有有限缓存，补存仍可取回的附件。QQ 从未推送的旧消息、旧版本丢掉的文件地址无法补回。
失效链接会记为失败，不会伪装成已下载。关闭 `enabled` 或 `archive_enabled` 后停止新归档，已有文件保留。

## 语音消息（STT）

语音转写分两个阶段进行：

1. **QQ 内置 ASR**（免费，始终优先尝试）——QQ 在语音消息附件中提供 `asr_refer_text`，使用腾讯自有语音识别
2. **已配置的 STT 提供商**（备用）——若 QQ 的 ASR 未返回文本，适配器将调用兼容 OpenAI 的 STT API：

   - **智谱/GLM（zai）**：默认提供商，使用 `glm-asr` 模型
   - **OpenAI Whisper**：设置 `QQ_STT_BASE_URL` 和 `QQ_STT_MODEL`
   - 任何兼容 OpenAI 的 STT 端点

## 故障排查

### 机器人立即断开连接（快速断连）

通常原因如下：
- **App ID / Secret 无效** — 在 q.qq.com 仔细核对您的凭据
- **缺少权限** — 确保机器人已启用所需 intent
- **仅限沙盒的机器人** — 若机器人处于沙盒模式，只能接收来自 QQ 沙盒测试频道的消息

### 语音消息未被转写

1. 检查附件数据中是否存在 QQ 内置的 `asr_refer_text`
2. 若使用自定义 STT 提供商，验证 `QQ_STT_API_KEY` 是否正确设置
3. 查看 gateway 日志中的 STT 错误信息

### 消息未送达

- 在 q.qq.com 验证机器人的 **intent** 是否已启用
- 若私聊访问受限，检查 `QQ_ALLOWED_USERS`
- 对于群组消息，确保机器人被 **@提及**（群组策略可能需要加入白名单）
- 检查 `QQBOT_HOME_CHANNEL` 以确认 cron/通知投递配置

### 连接错误

- 确保已安装 `aiohttp` 和 `httpx`：`python -c "import pm; pm.sync_venv(['messaging'], explicit=True)"`
- 检查与 `api.sgroup.qq.com` 及 WebSocket gateway 的网络连通性
- 查看 gateway 日志以获取详细错误信息和重连行为