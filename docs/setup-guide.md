# TG2QQPD 配置获取与填写指南（Setup Guide）

> 最后更新：2026-03-03

本指南帮你解决：

1. 本项目需要准备哪些凭证和配置
2. 这些值分别如何获取
3. 如何验证配置是否正确

---

## 配置架构

本项目采用 **双层配置** 模式：

| 文件 | 内容 | 是否入 Git |
|---|---|---|
| `.env` | 敏感凭证（API Key / Secret / Token / 密码） | ❌ 不入 |
| `config.yaml` | 所有业务配置（TG 源、QQ 目标、过滤规则、清洗规则等） | ✅ 入 |

`config.yaml` 通过 `${ENV_VAR}` 语法引用 `.env` 中的敏感值，实现凭证与业务逻辑分离。

---

## 1) 需要准备的凭证总览

### A. Telegram（写入 `.env`）

| 变量 | 必填 | 作用 | 获取方式 |
|---|:---:|---|---|
| `TG_API_ID` | ✅ | Telethon 登录 | [my.telegram.org](https://my.telegram.org) |
| `TG_API_HASH` | ✅ | Telethon 登录 | [my.telegram.org](https://my.telegram.org) |

> TG 监听源（频道列表）已移至 `config.yaml → telegram.sources`，不再写在 `.env` 里。

### B. QQ 频道 Bot（写入 `.env`）

| 变量 | 必填 | 作用 | 获取方式 |
|---|:---:|---|---|
| `QQ_APP_ID` | ✅ | 机器人应用 ID | [QQ 开放平台](https://bot.q.qq.com) |
| `QQ_APP_SECRET` | ✅ | 自动刷新 access_token | QQ 开放平台后台（clientSecret） |
| `QQ_BOT_TOKEN` | ✅ | Bot Token | QQ 开放平台后台 |

> QQ 目标频道/子频道 ID 已移至 `config.yaml → qq.target_guild_id / target_channel_id`。
>
> access_token 由程序自动获取和刷新，**无需手动填写**。

### B2. 备用图床（写入 `config.yaml`，可选）

| 配置项 | 必填 | 作用 | 获取方式 |
|---|:---:|---|---|
| `qq.imgbb_api_key` | ❌ | QQ CDN 上传图片失败时，用 imgbb 图床中转 | 注册 [imgbb API](https://api.imgbb.com/) 获取免费 Key |

> 如果 QQ CDN 上传正常工作，此项无需配置。留空则跳过 imgbb 备用方案，图片上传失败时直接降级为纯文本帖子。

### C. 后台管理鉴权（写入 `.env`）

| 变量 | 必填 | 作用 | 说明 |
|---|:---:|---|---|
| `JWT_SECRET` | ✅ | JWT 签名密钥 | 长随机字符串，公开部署务必设强值 |
| `ADMIN_PASS` | ✅ | 管理后台登录密码 | 公开部署务必设强密码 |

### D. 基础设施（写入 `.env`，一般不需要改）

| 变量 | 必填 | 默认值 | 说明 |
|---|:---:|---|---|
| `REDIS_HOST` | ✅ | `redis` | Docker Compose 内部服务名 |
| `DATABASE_URL` | ✅ | `postgresql://tg2qq:tg2qqpass@postgres:5432/tg2qq` | Docker Compose 内部连接 |

---

## 2) Telegram 凭证获取

### 2.1 获取 `TG_API_ID` / `TG_API_HASH`

1. 打开 [my.telegram.org](https://my.telegram.org)
2. 用你的 Telegram 账号登录
3. 进入 **API development tools**
4. 创建应用（App title / Short name 随便填）
5. 获取：
   - **App api_id** → 写入 `.env` 的 `TG_API_ID`
   - **App api_hash** → 写入 `.env` 的 `TG_API_HASH`

### 2.2 配置监听源（`config.yaml`）

在 `config.yaml → telegram.sources` 中填写要监听的 TG 频道：

```yaml
telegram:
  api_id: ${TG_API_ID}
  api_hash: ${TG_API_HASH}
  session: userbot
  session_dir: /app/sessions

  sources:
    - "@Q_dianshiju"      # 支持 @username 格式
    - "@Q_dongman"
    - "@Q_dianying"
    - "@Q_jilupian"
    - "@Aliyun_4K_Movies"
```

> 说明：
> - 必须是你的 Telegram 账号**已加入或可访问**的频道
> - 支持 `@username`、`t.me/xxx` 链接、数值 ID 三种格式
> - Session 名称建议固定为 `userbot`，不要频繁修改

### 2.3 首次 Telethon 登录

首次启动需要在终端完成交互式登录（输入验证码/二步验证密码）：

```bash
docker compose run --rm backend python -c "
from telethon.sync import TelegramClient
import os
c = TelegramClient('/app/sessions/userbot', int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'])
c.start()
c.disconnect()
"
```

登录态保存在 `data/tg_session/` 目录，后续重启**无需重复登录**。

---

## 3) QQ 频道 Bot 凭证获取

### 3.1 创建机器人

1. 打开 [QQ 开放平台](https://bot.q.qq.com)
2. 创建一个**私域机器人**（目前帖子 API 仅私域可用）
3. 在应用详情页获取：
   - **AppID** → `QQ_APP_ID`
   - **AppSecret (clientSecret)** → `QQ_APP_SECRET`
   - **Bot Token** → `QQ_BOT_TOKEN`

> ⚠️ 私域机器人创建后需要**先将机器人从频道移除，再重新添加**，帖子 API 才会生效。

### 3.2 获取目标频道 guild_id 和子频道 channel_id

本项目发消息使用的是 **帖子 API**：

```
PUT https://api.sgroup.qq.com/channels/{channel_id}/threads
```

你需要知道目标**帖子子频道**的 `channel_id`。

#### 方式 A：通过管理 API 查询（推荐）

部署启动后，通过内置的调试接口查询：

```bash
# 1. 获取管理 Token
TOKEN=$(curl -s -X POST http://localhost:8000/api/login \
  -H "Content-Type: application/json" \
  -d '{"password":"你的ADMIN_PASS"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 2. 查看 Bot 加入的所有频道（获取 guild_id）
curl -s http://localhost:8000/api/qq/guilds \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 3. 查看频道下所有子频道（获取 channel_id）
curl -s "http://localhost:8000/api/qq/channels?guild_id=你的guild_id" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 4. 或者让系统自动选择可发言子频道
curl -s "http://localhost:8000/api/qq/pick-default-channel?guild_id=你的guild_id" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

#### 方式 B：直接调用 QQ OpenAPI

```bash
# 获取 access_token
ACCESS_TOKEN=$(curl -s -X POST https://bots.qq.com/app/getAppAccessToken \
  -H 'Content-Type: application/json' \
  -d '{"appId":"你的APP_ID","clientSecret":"你的APP_SECRET"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 列出所有子频道
curl -s "https://api.sgroup.qq.com/guilds/你的guild_id/channels" \
  -H "Authorization: QQBot $ACCESS_TOKEN" \
  | python3 -c "
import sys,json
channels = json.load(sys.stdin)
for ch in channels:
    print(f\"id={ch.get('id')}  name={ch.get('name')}  type={ch.get('type')}  speak={ch.get('speak_permission')}\")
"
```

> 输出中 **type=10007** 的是帖子子频道，选择你想要发帖的那个，记下 `id`。

### 3.3 配置目标频道（`config.yaml`）

```yaml
qq:
  app_id: ${QQ_APP_ID}
  app_secret: ${QQ_APP_SECRET}
  bot_token: ${QQ_BOT_TOKEN}

  target_guild_id: "3628508121088643592"    # 频道服务器 ID
  target_channel_id: "717979188"            # 帖子子频道 ID（留空则自动选择）

  send_interval: 2          # 发送间隔（秒），防风控
  imgbb_api_key: ""         # 备用图床 API Key（可选，留空则跳过）
  quiet_hours_start: 0      # 静默时段开始（0 点）
  quiet_hours_end: 6        # 静默时段结束（6 点）
```

> **关于 `target_channel_id`**：
> - 填写具体 channel_id → 固定发到该子频道
> - 留空 `""` → 自动选择 guild 下第一个可发言的帖子子频道

---

## 4) `.env` 完整范本

```properties
# === Telegram ===
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890

# === QQ 频道 Bot ===
QQ_APP_ID=102835488
QQ_APP_SECRET=your_app_secret_here
QQ_BOT_TOKEN=your_bot_token_here

# === 后台管理 ===
JWT_SECRET=change_me_to_a_long_random_string
ADMIN_PASS=change_me_to_a_strong_password

# === 基础设施（Docker Compose 内部，一般不改）===
REDIS_HOST=redis
DATABASE_URL=postgresql://tg2qq:tg2qqpass@postgres:5432/tg2qq
```

---

## 5) QQ 频道子频道类型说明

> ⚠️ QQ 频道目前已**不支持新建 type=0 的纯文字子频道**，所有新建子频道默认为帖子类型。

| type 值 | 含义 | 可发消息 | API |
|---|---|---|---|
| 0 | 文字子频道（旧版，已无法新建） | ✅ | `POST /channels/{id}/messages` |
| 4 | 分类（不可发消息） | ❌ | — |
| 10007 | **帖子子频道** | ✅ | `PUT /channels/{id}/threads` |
| 10011 | 日程子频道 | — | — |

| speak_permission | 含义 |
|---|---|
| 1 | 所有人可发言 |
| 2 | 仅管理员/指定身份组可发言 |

本项目使用 **帖子 API** (`PUT /channels/{channel_id}/threads`)，`target_channel_id` 必须指向 **type=10007** 的子频道。

---

## 6) 快速部署流程

```bash
# 1. 准备配置文件
cp .env.example .env
vim .env              # 填写凭证
vim config.yaml       # 配置 TG 源、QQ 目标、过滤规则

# 2. 首次 Telegram 登录（交互式，输入验证码）
docker compose run --rm backend python -c "
from telethon.sync import TelegramClient; import os
c = TelegramClient('/app/sessions/userbot', int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'])
c.start(); c.disconnect()
"

# 3. 启动所有服务
docker compose up -d --build

# 4. 验证服务状态
docker compose ps
docker compose logs -f --tail=100
```

---

## 7) 配置验证清单

部署后逐项检查：

| # | 验证项 | 命令/方法 | 预期结果 |
|---|---|---|---|
| 1 | 服务全部运行 | `docker compose ps` | 4 个服务均 Up |
| 2 | 健康检查 | `curl http://localhost:8000/healthz` | `{"ok":true,...}` |
| 3 | 管理登录 | `POST /api/login` | 返回 JWT token |
| 4 | QQ 频道可达 | `GET /api/qq/guilds` | 返回频道列表 |
| 5 | 子频道正确 | `GET /api/qq/pick-default-channel?guild_id=...` | 返回 type=10007 的子频道 |
| 6 | WS 保活正常 | `docker compose logs worker` | 日志出现 `READY! session established` |
| 7 | TG 监听正常 | `docker compose logs backend` | 日志出现 `listening on X sources` |
| 8 | 消息转发正常 | TG 频道发一条测试消息 | Worker 日志出现 `sent ok`，QQ 子频道出现帖子 |

---

## 8) 常见问题

### Q: 消息显示 `sent ok` 但 QQ 频道看不到？

- 检查 `target_channel_id` 是否指向了 **type=10007 的帖子子频道**（不是 type=4 的分类）
- 帖子发布后在 QQ 频道的"帖子广场"/"资源"等帖子板块中查看，不是聊天消息
- 确认私域机器人已从频道移除后重新添加（帖子 API 需要此步骤生效）

### Q: 报错 `304003 — 请求参数不允许包含url`？

- QQ 频道禁止消息中包含外部 URL
- 确认 `config.yaml → rules.transforms` 中配置了 URL 删除规则（规则 7-9）

### Q: 报错 `304022 — 主动消息不能在 00:00~06:00 推送`？

- QQ 频道限制机器人在凌晨时段发消息
- 已内置静默时段保护（`qq.quiet_hours_start/end`），消息会留在 Redis 队列，6 点后自动恢复

### Q: 报错 `304045 — push channel message reach limit`？

- QQ 频道消息发送数量达到上限
- Worker 会自动将消息推回队列，等待 5 分钟后重试，无需手动干预

### Q: WS 连接反复断开重连？

- 检查 Worker 日志中的 `remaining` 值（WS 连接配额，每天 1500 次）
- 已内置熔断机制：连续失败 5 次后休眠 30 分钟，配额耗尽则等待重置

### Q: 首次 Telethon 登录报 `EOFError`？

- 不能用 `docker compose up` 启动后再登录，必须用 `docker compose run --rm` 交互式登录
- 登录成功后，session 文件保存在 `data/tg_session/`，后续正常 `docker compose up` 即可

### Q: 图片发送失败？

- 帖子 API 不支持直接上传图片文件，本项目采用以下策略：
  1. 先通过 `POST /channels/{channel_id}/messages` 上传图片获取 QQ CDN URL
  2. QQ CDN 失败时，使用 imgbb 免费图床中转（需在 `config.yaml → qq.imgbb_api_key` 配置 API Key）
  3. 获取到图片 URL 后，用 `format=4` (JSON RichText) 的 `ImageElem.third_url` 嵌入帖子
  4. 所有图片上传方式都失败时，自动降级为纯文本帖子
- TG 端支持两种图片形式：`photo`（压缩图）和 `document`（原图/PNG/GIF，MIME 为 `image/*` 时自动识别）
- 容器重启后 `/tmp` 中的图片文件会丢失，死信重发时自动降级为纯文本
- 图片通过共享卷 `./data/tg_media:/tmp` 在 backend 和 worker 之间传递
