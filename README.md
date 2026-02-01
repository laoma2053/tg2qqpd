# TG2QQPD

一个个人使用的 **Telegram → QQ 频道** 自动转发服务，支持 **文字 + 图片（图文）**，并提供最小化的管理 API（登录、指标、死信查看/重放）。

> 运行形态：Docker Compose（PostgreSQL + Redis + backend + worker）

---

## 功能特性

- **TG → QQ 转发**：监听指定 Telegram 频道的新消息，转发到指定 QQ 频道子频道
- **图文支持（真实图片上传）**：使用 QQ 频道接口 `multipart/form-data` 上传 `file_image` 实现真正的图片发送
- **多图/相册策略**：只转发第一张图 + 文案（当前按 Telethon 行为做“首图/单图”简化策略）
- **失败降级策略**：
  - 图片发送失败 → 压缩后重试一次
  - 仍失败 → 降级只发文字
- **关键词/正则过滤（只看文字）**：支持 `block/allow` 两种模式
- **去重**：PostgreSQL `processed` 记录已成功转发的 `(tg_chat_id, tg_msg_id)`，避免重复
- **死信队列**：失败写入 `dead(payload JSONB)`，支持查看与重放
- **运维指标（Dashboard API）**：队列长度、今日成功/失败、死信总量

---

## 架构概览

```
Telegram (Telethon)
  -> backend (监听 + 规则判定 + 入 Redis 队列)
  -> Redis list queue
  -> worker (出队 + WS 保活 + 发送 QQ 图文 + 写 DB + 死信)
  -> QQ Channel

PostgreSQL: processed（去重/成功记录） + dead（死信，含 payload）
```

---

## 目录结构

- `backend/`
  - `app.py`：Telethon 监听 + FastAPI 管理 API
  - `worker.py`：消费 Redis 队列，发送 QQ（图文）、压缩重试、降级、写库
  - `db.py`：PostgreSQL（processed/dead）
  - `auth.py`：JWT 登录鉴权
  - `qq_auth.py`：QQ AccessToken 自动刷新缓存器
  - `qq_ws_keepalive.py`：QQ 网关 WebSocket 在线保活（满足频道发消息“在线”前置条件）
  - `api/`
    - `system.py`：`GET /api/system/stats`
    - `deadletters.py`：死信列表与重放
- `docker-compose.yml`：一键部署
- `.env`：环境变量配置
- `mapping.json`：频道映射配置（白名单监听）
- `blacklist.json`：黑名单（优先级最高）

---

## 环境变量（.env）

> 下列为关键项，完整示例请参考仓库根目录的 `.env`。

### Telegram

- `TG_API_ID`：Telegram API ID
- `TG_API_HASH`：Telegram API HASH
- `TG_SESSION`：Session 名称（会持久化到 Docker volume）

### QQ（推荐使用 AccessToken 自动刷新）

- `QQ_APP_ID`
- `QQ_APP_SECRET`：用于自动刷新 access_token（必须）
- `QQ_API_BASE`：默认 `https://api.sgroup.qq.com`
- `QQ_WS_INTENTS`：WS intents，默认最小 `1`（仅保活）

> 兼容：你也可以手动填写 `QQ_ACCESS_TOKEN`，此时程序会优先使用它（不自动刷新）。

### 数据库/队列

- `REDIS_HOST=redis`
- `DATABASE_URL=postgresql://tg2qq:tg2qqpass@postgres:5432/tg2qq`

### 后台鉴权（公开部署务必设置强密码）

- `JWT_SECRET`
- `ADMIN_PASS`

### 文案清洗（发送到 QQ 前）

- `ZJ_BASE_URL`：默认 `www.zhuiju.us`
- `ZJ_SUFFIX_NOTE`：默认 `访问搜影片名或进QQ群搜索`

---

## 映射配置（mapping.json）

**强烈建议使用 `tg_chat_id`（例如 `-100xxxx`）作为主键**，不要用 `@username`。

示例：

```json
{
  "-1001234567890": {
    "enabled": true,
    "qq_channel_id": "1234567890123456789",
    "remark": "示例频道",
    "gray_ratio": 1,
    "template": {"prefix": "", "suffix": ""},
    "filter": {"mode": "block", "keywords": [], "regex": []}
  }
}
```

说明：
- `gray_ratio` 支持两种写法：
  - `0~1`（推荐，概率）
  - `0~100`（百分比，兼容旧写法）

---

## 管理 API（公开部署默认需 JWT）

当前后端对外暴露 `8000`，并做了最小加固：

- `POST /api/login`：免鉴权，获取 token
- 其他 `/api/*`：都需要 `Authorization: Bearer <token>`

### 登录

- `POST /api/login`
- body: `{ "password": "<ADMIN_PASS>" }`
- resp: `{ "token": "<jwt>" }`

### 指标

- `GET /api/system/stats`

### 死信

- `GET /api/deadletters`
- `POST /api/deadletters/{id}/retry`
- `POST /api/deadletters/retry`（body: `{ "ids": [1,2,3] }`）

---

## 部署（Docker Compose）

### 1) 准备配置

1. 填写 `.env`（Telegram + QQ + 鉴权）
2. 填写 `mapping.json`（至少一个 tg_chat_id → qq_channel_id）
3. `blacklist.json` 可保持 `[]`

### 2) 启动

在项目根目录执行：

- `docker compose up -d --build`

### 3) Telegram 首次登录

首次启动 `backend` 需要在日志/控制台完成 Telethon 登录（验证码/二步验证）。
登录态会保存到 `tg_session` volume，后续重启无需重复登录。

---

## 文案清洗规则（当前版本）

发送到 QQ 前会对文本做最小清洗：

- 遇到 `来自/频道/群组/投稿` 行（可含图标前缀）则**截断并删除该行及后续内容**
- 仅在“网盘链接行”（包含 `https://pan.quark.cn/s/...`）做替换：
  - URL 替换为 `ZJ_BASE_URL`
  - 行前缀统一为 `网盘资源链接：`
  - 行末追加 `ZJ_SUFFIX_NOTE`

---

## 注意事项

- QQ 文档提示：频道发消息要求机器人保持 WebSocket 在线。本项目已实现最小 WS 保活。
- WebSocket 链路在官方文档中存在“逐步下线”的提示；如未来不可用，需要迁移 webhook 模式。
- 建议云服务器上不要公开暴露 Postgres/Redis 端口。

---

## License

MIT
