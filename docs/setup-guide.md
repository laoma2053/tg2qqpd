# TG2QQPD 配置获取与填写指南（Setup Guide）

本指南用于解决两个问题：

1. 本项目需要你准备哪些“值”（主要是 `.env`）
2. 这些值分别如何获取、如何验证是否正确

> 重要结论（已更新为“纯 ENV 模式”）
> - **不再需要 `mapping.json`**（也不再需要你提前知道 `tg_chat_id=-100...`）。
> - 你只需要在 `.env` 里填写：
>   - 监听哪些 TG 频道（`TG_SOURCES=@a,@b,@c`）
>   - 统一发送到哪个 QQ 子频道（`QQ_TARGET_CHANNEL_ID=...`）
> - QQ 频道发消息仍然是发到 **某一个“文字子频道 channel_id”**，不能只给 `guild_id`。

---

## 1) 本项目需要你准备哪些“值”（总览）

### A. Telegram（写入 `.env`）

| 变量 | 必填 | 作用 | 获取方式概览 |
|---|---:|---|---|
| `TG_API_ID` | 是 | Telethon 登录所需 | 从 https://my.telegram.org 获取 |
| `TG_API_HASH` | 是 | Telethon 登录所需 | 从 https://my.telegram.org 获取 |
| `TG_SESSION` | 否 | session 文件名/登录态标识 | 随便填（建议固定别改） |
| `TG_SOURCES` | 是 | 监听的 TG 频道列表（username） | 直接填 `@用户名`，逗号分隔 |

### B. QQ 机器人（写入 `.env`，推荐 AccessToken 自动刷新）

| 变量 | 必填 | 作用 | 获取方式概览 |
|---|---:|---|---|
| `QQ_APP_ID` | 是 | QQ 机器人应用 ID | QQ 机器人开放平台后台 |
| `QQ_APP_SECRET` | 是 | 自动换取/刷新 access_token | QQ 机器人开放平台后台（clientSecret/AppSecret） |
| `QQ_ACCESS_TOKEN` | 否 | 手动 token（不自动刷新） | 不推荐长期使用 |
| `QQ_API_BASE` | 否 | OpenAPI base | 默认 `https://api.sgroup.qq.com` |
| `QQ_WS_INTENTS` | 否 | WS 订阅 intents（保活用） | 默认 `1`（最小 GUILDS） |
| `QQ_TARGET_CHANNEL_ID` | 是 | 统一发送到的 QQ“文字子频道 channel_id” | 见下文获取方式 |

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

---

## 2) Telegram：如何填写与验证

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

### 2.3 填写 `TG_SOURCES`（监听多个频道）

- 格式：多个 TG 频道 username，逗号分隔
- 示例：

```properties
TG_SOURCES=@Remux4KFilm,@Q_dianshiju,@Q_dongman,@Q_dianying
```

说明：
- 必须是你账号“能看到/已加入”的频道（至少要能访问）
- 支持写不带 `@` 的形式，但推荐统一带 `@`

---

## 3) QQ：如何获取 `QQ_TARGET_CHANNEL_ID`

本项目发送频道消息用的是：

- `POST https://api.sgroup.qq.com/channels/{channel_id}/messages`

因此 `.env` 里的 `QQ_TARGET_CHANNEL_ID` 必须是 **文字子频道** 的 `channel_id`。

> 你提到的 `guild_id` 是“大频道/频道服务器”的 ID：它不是发送消息的落点。

### 3.1 推荐获取方式

**方式 A：从 QQ 客户端/管理端复制子频道链接**
- 对目标“文字子频道”复制链接/分享链接
- 若链接里包含 `/channels/123...`，那串数字通常就是 `channel_id`

**方式 B：通过 OpenAPI 列出子频道（最稳）**
- 后续可添加一个脚本/API：给定 `guild_id`，列出该频道下所有子频道（name + channel_id + type）
- 从输出里挑目标子频道的 `channel_id`

---

## 4) `.env` 最小可运行范本（核心项）

```properties
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890
TG_SESSION=userbot

TG_SOURCES=@Remux4KFilm,@Q_dianshiju,@Q_dongman,@Q_dianying

QQ_APP_ID=102835488
QQ_APP_SECRET=xxxxx
QQ_TARGET_CHANNEL_ID=1234567890123456789

REDIS_HOST=redis
DATABASE_URL=postgresql://tg2qq:tg2qqpass@postgres:5432/tg2qq

JWT_SECRET=change_me_to_a_long_random_string
ADMIN_PASS=change_me_to_a_strong_password
```

---

## 5) 常见坑（务必看）

1. **必须填 `QQ_TARGET_CHANNEL_ID`（子频道），不能只填 `guild_id`**
   - 发消息的接口是 `/channels/{channel_id}/messages`

2. **首次 Telethon 登录需要交互**
   - 首次启动会要求输入验证码（或二步验证密码）
   - 登录态写入 Docker volume 之后就稳定了

3. **公开部署一定要设置强密码**
   - `ADMIN_PASS`、`JWT_SECRET` 不要用默认值

4. **WS 保活存在官方下线风险**
   - 当前为满足“频道发消息需在线”前置条件做的最小实现
   - 如未来不可用，需要迁移 webhook
