# TG2QQPD 配置获取与填写指南（Setup Guide）

本指南用于解决两个问题：

1. 本项目需要你准备哪些“值”（`.env` / `mapping.json`）
2. 这些值分别如何获取、如何验证是否正确

> 重要结论：
> - `mapping.json` 的 key 必须是 **Telegram 频道的 `tg_chat_id`（通常是 `-100...`）**，不能直接用 `https://t.me/<username>`。
> - `mapping.json` 的 `qq_channel_id` 必须是 **QQ“文字子频道”的 `channel_id`**（用于 `POST /channels/{channel_id}/messages`），很多 `pdxxxx` 形式的值通常不是这个 ID。

---

## 1) 本项目需要你准备哪些“值”（总览）

### A. Telegram（写入 `.env`）

| 变量 | 必填 | 作用 | 获取方式概览 |
|---|---:|---|---|
| `TG_API_ID` | 是 | Telethon 登录所需 | 从 https://my.telegram.org 获取 |
| `TG_API_HASH` | 是 | Telethon 登录所需 | 从 https://my.telegram.org 获取 |
| `TG_SESSION` | 否 | session 文件名/登录态标识 | 随便填（建议固定别改） |

### B. QQ 机器人（写入 `.env`，推荐 AccessToken 自动刷新）

| 变量 | 必填 | 作用 | 获取方式概览 |
|---|---:|---|---|
| `QQ_APP_ID` | 是 | QQ 机器人应用 ID | QQ 机器人开放平台后台 |
| `QQ_APP_SECRET` | 是 | 自动换取/刷新 access_token | QQ 机器人开放平台后台（clientSecret/AppSecret） |
| `QQ_ACCESS_TOKEN` | 否 | 手动 token（不自动刷新） | 不推荐长期使用 |
| `QQ_API_BASE` | 否 | OpenAPI base | 默认 `https://api.sgroup.qq.com` |
| `QQ_WS_INTENTS` | 否 | WS 订阅 intents（保活用） | 默认 `1`（最小 GUILDS） |

> 本项目已经实现：
> - 自动获取/刷新 access_token（`QQ_APP_ID + QQ_APP_SECRET`）
> - 最小 WebSocket 在线保活（满足“频道发消息需要 WS 在线”的文档前置条件）

### C. Redis / PostgreSQL（写入 `.env`）

| 变量 | 必填 | 作用 | 说明 |
|---|---:|---|---|
| `REDIS_HOST` | 是 | 队列 | Docker Compose 内固定写 `redis` |
| `DATABASE_URL` | 是 | PostgreSQL | Docker Compose 内固定写 `postgres` |

### D. 管理 API 鉴权（写入 `.env`）

| 变量 | 必填 | 作用 | 说明 |
|---|---:|---|---|
| `JWT_SECRET` | 是（公开部署强烈建议） | JWT 校验密钥 | 需要长随机字符串 |
| `ADMIN_PASS` | 是（公开部署强烈建议） | 登录密码 | 建议强密码 |

> 公开部署时：只有 `POST /api/login` 免鉴权，其余 `/api/*` 默认都需要 Bearer token。

### E. 频道映射（写入 `mapping.json`）

| 字段 | 必填 | 作用 | 说明 |
|---|---:|---|---|
| key: `"-100..."` | 是 | TG 频道 chat_id | 必须是 `tg_chat_id`（字符串） |
| `qq_channel_id` | 是 | QQ 目标“文字子频道 channel_id” | 用于调用 `/channels/{channel_id}/messages` |
| `enabled` | 否 | 开关 | 默认 true |
| `gray_ratio` | 否 | 灰度比例 | 支持 0~1 或 0~100（后端已兼容） |
| `filter` | 否 | 文本过滤 | 仅对文字生效（block/allow + keywords/regex） |
| `template` | 否 | 模板 | prefix/suffix，支持 `{{channel_name}}` |

---

## 2) Telegram：如何获取需要的值

### 2.1 获取 `TG_API_ID` / `TG_API_HASH`

1. 打开 https://my.telegram.org
2. 登录你的 Telegram 账号
3. 进入 **API development tools**
4. 创建应用后可看到：
   - **App api_id** → `TG_API_ID`
   - **App api_hash** → `TG_API_HASH`

### 2.2 选择 `TG_SESSION`

- 随便写，例如 `userbot`
- 不要频繁改。改了会导致 Telethon 当作新会话，可能需要重新登录。

### 2.3 获取 `tg_chat_id`（mapping.json 的 key）

你给的 `https://t.me/NSFW_Quark` 只是 **username**，本项目实际监听用的是 `event.chat_id`。

最简单的获取方式：

1. 先确保你的 Telegram 账号 **已加入/可访问** 该频道 `@NSFW_Quark`
2. 启动 `backend` 后
3. 在该频道出现一条新消息时，从日志里读取 `event.chat_id`（通常形如 `-100...`）

> 如果你希望更省事：建议后续加一个 DEBUG 日志或小工具，专门打印 chat_id / username / title。

---

## 3) QQ：如何获取需要的值

### 3.1 获取 `QQ_APP_ID` / `QQ_APP_SECRET`

在 QQ 机器人开放平台/管理后台找到你的机器人应用：

- `QQ_APP_ID`：应用 ID（机器人 ID）
- `QQ_APP_SECRET`：`AppSecret` / `clientSecret`

### 3.2 access_token 如何产生（你不需要手动算）

本项目使用官方接口自动获取并按过期时间刷新：

- `POST https://bots.qq.com/app/getAppAccessToken`
- body: `{ "appId": "QQ_APP_ID", "clientSecret": "QQ_APP_SECRET" }`
- resp: `{ "access_token": "...", "expires_in": 7200 }`

调用 OpenAPI 统一鉴权 Header：

- `Authorization: QQBot {access_token}`

### 3.3 你需要的 `qq_channel_id` 是什么？

本项目发送频道消息用的是：

- `POST https://api.sgroup.qq.com/channels/{channel_id}/messages`

因此 `mapping.json` 里的 `qq_channel_id` 必须是 **文字子频道** 的 `channel_id`。

> 你提到的 `pd41891326` 很可能不是 `channel_id`。

### 3.4 如何获取“文字子频道 channel_id”

推荐两种方式（按你方便程度选）：

**方式 A：从 QQ 客户端/管理端复制子频道链接**
- 对目标“文字子频道”复制链接/分享链接
- 若链接里包含 `/channels/123...`，那串数字通常就是 `channel_id`

**方式 B：通过 OpenAPI 列出子频道（最稳）**
- 后续可添加一个脚本/API：给定 guild_id，列出该频道下所有子频道（name + channel_id + type）
- 你从输出里挑目标子频道的 `channel_id`

---

## 4) `mapping.json` 正确范本

### 4.1 注释版（示例，含解释；不要直接用于运行）

```js
{
  // key：Telegram 频道 chat_id，必须是字符串（推荐 -100...）
  "-1001234567890": {
    "enabled": true,

    // QQ 目标：文字子频道 channel_id（用于 /channels/{channel_id}/messages）
    "qq_channel_id": "1234567890123456789",

    // 备注（仅用于你自己）
    "remark": "NSFW_Quark -> QQ子频道",

    // 灰度（推荐 0~1；兼容 0~100）
    "gray_ratio": 1,

    // 模板（发送前拼 prefix/suffix，支持 {{channel_name}}）
    "template": {"prefix": "", "suffix": ""},

    // 文本过滤（只对文字生效）
    "filter": {"mode": "block", "keywords": [], "regex": []}
  }
}
```

### 4.2 可运行版（JSON 标准不支持注释）

```json
{
  "-1001234567890": {
    "enabled": true,
    "qq_channel_id": "1234567890123456789",
    "remark": "NSFW_Quark -> QQ子频道",
    "gray_ratio": 1,
    "template": {"prefix": "", "suffix": ""},
    "filter": {"mode": "block", "keywords": [], "regex": []}
  }
}
```

---

## 5) 常见坑（务必看）

1. **TG 频道链接不是 chat_id**
   - `https://t.me/NSFW_Quark` 不是 `-100...`

2. **QQ 的“频道号 / guild_id / 子频道 channel_id”不是一回事**
   - 本项目需要的是“文字子频道 channel_id”

3. **公开部署一定要设置强密码**
   - `ADMIN_PASS`、`JWT_SECRET` 不要用默认值

4. **首次 Telethon 登录需要交互**
   - 登录态写入 Docker volume 之后就稳定了

5. **WS 保活存在官方下线风险**
   - 当前为满足“频道发消息需在线”前置条件做的最小实现
   - 如未来不可用，需要迁移 webhook
