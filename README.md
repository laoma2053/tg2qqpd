# TG2QQPD

**Telegram → QQ 频道** 自动转发服务。监听指定 Telegram 频道的新消息，经过过滤与文案清洗后，以**帖子**形式发布到 QQ 频道子频道。

> 运行形态：Docker Compose（PostgreSQL + Redis + listen + publish）  
> 最后更新：2026-03-03

---

## 功能特性

- **TG → QQ 帖子转发**：监听多个 Telegram 频道，自动转发到 QQ 频道的帖子子频道（type=10007）
- **帖子 API 发送**：使用 `PUT /channels/{channel_id}/threads` 发帖，支持纯文本（format=1）和 JSON RichText 图文（format=4）
- **图文支持**：TG 图片自动下载，上传至 QQ CDN 获取内部 URL，再以 JSON RichText `ImageElem` 嵌入帖子；支持 photo 和 document（大图/PNG）两种图片形式
- **备用图床**：QQ CDN 上传失败时，自动通过 imgbb 免费图床中转（需配置 `imgbb_api_key`）
- **帖子标题/正文分离**：自动取第一行作为帖子标题，其余作为正文，避免标题内容重复显示
- **多层降级策略**：
  1. 图文帖子（JSON RichText）发送失败 → 压缩图片重试
  2. 压缩仍失败 → 尝试 imgbb 备用图床
  3. 图床也失败 → 降级为纯文本帖子
  4. 图片文件丢失（容器重启后 `/tmp` 清空）→ 自动降级纯文本
- **YAML 驱动的文案清洗**：正则替换 + 追加模板，规则在 `config.yaml` 中配置，改完重启即生效
- **URL 自动删除**：QQ 频道禁止外部 URL，所有 `https://...` 链接在发送前自动清除
- **关键词/正则过滤**：支持黑名单（block）+ 白名单（allow）两种模式
- **去重**：PostgreSQL `processed` 表记录已转发的 `(tg_chat_id, tg_msg_id)`
- **死信队列**：发送失败写入 `dead` 表，支持查看与批量重放
- **静默时段**：QQ 频道 00:00~06:00 禁止机器人发主动消息，Publish 自动暂停，消息安全留在 Redis
- **限流保护**：检测到 QQ 发送频率限制（304045）时自动回退队列 + 等待恢复
- **WS 保活 + 熔断**：QQ 网关 WebSocket 在线保活，连续失败 5 次触发熔断（休眠 30 分钟），自动恢复
- **管理 API**：登录鉴权、运维指标、死信管理、频道调试接口

---

## 架构概览

```
Telegram (Telethon userbot)
  │
  ▼
listen (app.py)
  ├─ 监听 6 个 TG 频道
  ├─ 关键词/正则过滤
  ├─ 下载图片（photo + document）到共享 /tmp
  ├─ 入 Redis 队列
  │
  ▼
Redis list "queue"
  │
  ▼
publish (worker.py)
  ├─ 出队 → 文案清洗（YAML 规则引擎）
  ├─ 标题/正文分离（第一行 → 帖子标题）
  ├─ 静默时段 / WS 未就绪 → 暂停消费
  ├─ 有图片 → 上传 QQ CDN / imgbb → format=4 RichText 发帖
  ├─ 无图片 → format=1 纯文本发帖
  ├─ PUT /channels/{id}/threads 发帖
  ├─ 成功 → 写 processed 去重表
  └─ 失败 → 写 dead 死信表 / 限流回退队列

QQ 频道 "网盘追剧吧" (帖子子频道 type=10007)

PostgreSQL: processed（去重）+ dead（死信）
```

---

## 目录结构

```
tg2qqpd/
├── config.yaml              # 统一业务配置（TG 源、QQ 目标、过滤规则、清洗规则等）
├── .env                     # 敏感凭证（API Key/Secret/Token，不入 Git）
├── docker-compose.yml       # 一键部署 4 个服务
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py               # TG 监听 + FastAPI 管理 API（listen 服务）
│   ├── worker.py             # 消费队列 → 文案清洗 → 发帖到 QQ（publish 服务）
│   ├── config.py             # YAML 配置加载器（支持 ${ENV_VAR} 语法）
│   ├── db.py                 # PostgreSQL（processed / dead）
│   ├── auth.py               # JWT 登录鉴权
│   ├── qq_auth.py            # QQ AccessToken 自动刷新
│   ├── qq_ws_keepalive.py    # QQ 网关 WS 保活（熔断 + 配额保护）
│   └── api/
│       ├── system.py         # GET /api/system/stats
│       ├── deadletters.py    # 死信列表 / 重放
│       └── qq_debug.py       # QQ 频道调试（列频道、选子频道）
├── data/
│   ├── postgres/             # PostgreSQL 数据持久化
│   ├── tg_session/           # Telegram 登录态
│   └── tg_media/             # TG 媒体文件（共享给 publish）
├── docs/
│   ├── setup-guide.md        # 部署指南
│   ├── qq-channel-info.md    # QQ 频道/子频道信息与查询命令
│   └── jiaojie.md
├── logs/
└── nginx/
    └── nginx.conf
```

---

## 配置说明

### 敏感凭证（`.env`）

> 不入 Git，仅在部署服务器上维护。

```env
# Telegram
TG_API_ID=12345678
TG_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# QQ 频道 Bot
QQ_APP_ID=102835488
QQ_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
QQ_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# imgbb 图床（可选，留空则跳过）
IMGBB_API_KEY=your_imgbb_api_key_here

# 后台管理
JWT_SECRET=your-jwt-secret
ADMIN_PASS=your-admin-password

# 基础设施（Docker 内部网络，一般不需要改）
REDIS_HOST=redis
DATABASE_URL=postgresql://tg2qq:tg2qqpass@postgres:5432/tg2qq
```

### 业务配置（`config.yaml`）

所有业务逻辑配置集中在 `config.yaml`，通过 `${ENV_VAR}` 语法引用 `.env` 中的敏感值。

主要配置块：

| 配置块 | 说明 |
|---|---|
| `telegram.sources` | 监听的 TG 频道列表（@username / 数值 ID） |
| `qq.target_guild_id` | 目标 QQ 频道 guild_id |
| `qq.target_channel_id` | 目标帖子子频道 channel_id（留空自动选择） |
| `qq.send_interval` | 发送间隔（秒），防风控，当前设为 2 |
| `qq.imgbb_api_key` | 备用图床 API Key（从 .env 注入，可选） |
| `qq.quiet_hours_start/end` | 静默时段（默认 0~6 点） |
| `rules.filter` | 黑名单/白名单过滤规则 |
| `rules.transforms` | 文案清洗规则（正则替换 + 追加模板） |
| `forward.enabled` | 转发总开关 |
| `forward.gray_ratio` | 灰度比例 0~1 |

详细配置项和注释见 [`config.yaml`](config.yaml)。

---

## 文案清洗规则

发送到 QQ 前，文本经过 `config.yaml → rules.transforms` 中定义的规则链依次处理：

| # | 类型 | 作用 |
|---|---|---|
| 1 | regex_replace | `名称：` 前缀替换为 `🎬已更新：` |
| 2 | regex_replace | 去除 Markdown 加粗标记 `**` |
| 3 | regex_replace | 删除 `链接：https://...` 行 |
| 4 | regex_replace | 删除 `阿里：https://...` 行 |
| 5 | regex_replace | 删除 `夸克：https://...` 行 |
| 6 | regex_replace | 删除 `百度：https://...` 行 |
| 7 | regex_replace | 删除 `� 大小：...` 行 |
| 8 | regex_replace | 删除 `🏷 标签：...` 行 |
| 9 | regex_replace | 删除 `🗂 信息` 块（体积/标签等） |
| 10 | regex_replace | 删除投稿人/频道/群组等尾部导流 |
| 11 | regex_replace | 删除原文 `📤 资源链接：...` 提示行 |
| 12 | regex_replace | 删除所有剩余 `https://...` 链接 |
| 13 | append | 追加资源说明模板（QQ 群号 + 网盘链接） |

内置收尾处理：多空行收敛（≥3 换行→2）+ 首尾去空白。

---

## Publish 保护机制

Publish 服务（`worker.py`）内置多层保护，确保消息不丢失：

| 保护 | 触发条件 | 行为 |
|---|---|---|
| **静默时段** | 00:00~06:00（可配置） | 暂停消费队列，消息留在 Redis |
| **WS 未就绪** | QQ WebSocket 未连接/未 READY | 暂停消费，等待 WS 恢复 |
| **限流回退** | QQ 返回 304045 (reach limit) | 消息推回队列头部，等待 5 分钟 |
| **鉴权重试** | QQ 返回 401/403 | 刷新 token + 等待 WS → 重试一次 |
| **图片降级** | 图片发送失败 / 文件丢失 | 压缩重试 → imgbb 备用图床 → 降级纯文本帖子 |
| **死信兜底** | 所有重试都失败 | 写入 dead 表，支持后续手动重放 |

### WS 保活熔断（`qq_ws_keepalive.py`）

| 参数 | 值 | 说明 |
|---|---|---|
| 连续失败阈值 | 5 次 | 超过后触发熔断 |
| 熔断休眠时间 | 30 分钟 | 避免耗尽连接配额（1500/天） |
| 低配额警告 | remaining < 20 | 日志打印警告 |
| 配额耗尽保护 | remaining = 0 | 等待 reset_after 后再连接 |

---

## 管理 API

后端暴露 `8000` 端口，所有 `/api/*` 接口（除登录外）需要 JWT 鉴权。

### 鉴权

```bash
# 获取 Token
TOKEN=$(curl -s -X POST http://<IP>:8000/api/login \
  -H "Content-Type: application/json" \
  -d '{"password":"<ADMIN_PASS>"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
```

### 接口列表

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/healthz` | 健康检查 | ❌ |
| POST | `/api/login` | 获取 JWT | ❌ |
| GET | `/api/system/stats` | 运维指标（队列长度、成功/失败数、死信总量） | ✅ |
| GET | `/api/deadletters` | 死信列表 | ✅ |
| POST | `/api/deadletters/{id}/retry` | 重放单条死信 | ✅ |
| POST | `/api/deadletters/retry` | 批量重放（body: `{"ids":[1,2,3]}`） | ✅ |
| GET | `/api/qq/guilds` | 列出 Bot 加入的所有 QQ 频道 | ✅ |
| GET | `/api/qq/channels?guild_id=...` | 列出指定频道下所有子频道 | ✅ |
| GET | `/api/qq/pick-default-channel?guild_id=...` | 自动选择可发言子频道 | ✅ |

---

## 部署

### 前置条件

- Docker + Docker Compose v2
- Telegram 账号 + API credentials（[my.telegram.org](https://my.telegram.org)）
- QQ 频道私域机器人（[QQ 开放平台](https://bot.q.qq.com)）

### 1) 准备配置

```bash
# 填写敏感凭证
cp .env.example .env
vim .env

# 编辑业务配置（TG 源、QQ 目标、过滤规则等）
vim config.yaml
```

### 2) 启动

```bash
docker compose up -d --build
```

### 3) Telegram 首次登录

首次启动 listen 需要在终端完成 Telethon 交互式登录（验证码/二步验证）：

```bash
docker compose run --rm listen python -c "
from telethon.sync import TelegramClient
c = TelegramClient('/app/sessions/userbot', API_ID, API_HASH)
c.start()
c.disconnect()
"
```

登录态保存在 `data/tg_session/`，后续重启无需重复登录。

### 4) 常用运维命令

```bash
# 查看所有服务状态
docker compose ps

# 查看日志（实时跟踪）
docker compose logs -f --tail=200

# 只看 publish 日志
docker compose logs -f publish

# 重启（修改 config.yaml 后）
docker compose restart listen publish

# 重建（修改代码/Dockerfile 后）
docker compose up -d --build
```

---

## QQ 频道子频道类型说明

> ⚠️ QQ 频道目前已**不支持创建 type=0 的纯文字子频道**，所有新建子频道默认为 **type=10007（帖子频道）**。

| type | 含义 | 可发消息 | 使用的 API |
|---|---|---|---|
| 0 | 文字子频道（旧版，已无法新建） | ✅ | `POST /channels/{id}/messages` |
| 4 | 分类 | ❌ | — |
| 10007 | 帖子子频道 | ✅ | `PUT /channels/{id}/threads` |
| 10011 | 日程子频道 | — | — |

本项目使用 **帖子 API** (`PUT /channels/{channel_id}/threads`) 发送消息。

更多子频道详情见 [`docs/qq-channel-info.md`](docs/qq-channel-info.md)。

---

## QQ 频道已知限制

| 限制 | 错误码 | 说明 |
|---|---|---|
| 外部 URL 禁止 | 304003 | 消息内容不能包含外部链接，需在发送前删除 |
| 夜间禁发 | 304022 | 00:00~06:00 禁止主动消息 |
| 发送频率限制 | 304045 | 达到消息发送数量上限，需等待重置 |
| WS 连接配额 | — | 每日 1500 次，快速重连会耗尽 |

---

## 技术栈

| 组件 | 技术 |
|---|---|
| TG 监听 | Python 3.11 + Telethon (userbot) |
| Web API | FastAPI + Uvicorn |
| 消息队列 | Redis 7 (list) |
| 数据库 | PostgreSQL 15 |
| QQ 发送 | requests (帖子 API) |
| QQ 保活 | websocket-client (WS Gateway) |
| 图片处理 | Pillow |
| 部署 | Docker Compose v2 |
| 时区 | Asia/Shanghai (所有容器) |

---

## License

MIT
