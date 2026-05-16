# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

Telegram → QQ 频道自动转发服务。监听 TG 频道消息，过滤清洗后以帖子形式发布到 QQ 频道子频道。

## 常用命令

```bash
# 启动所有服务（推荐）
docker compose up -d --build

# 修改 config.yaml 后重启业务服务
docker compose restart listen publish

# 查看日志
docker compose logs -f publish
docker compose logs -f listen

# 前端开发
cd frontend && npm run dev

# 前端生产构建
cd frontend && npm run build
```

## 配置

- `config.yaml` — 业务配置（频道映射、过滤规则、文案清洗等）
- `.env` — 敏感凭证（TG API、QQ Token、DB 密码等），参考 `.env.example`

修改 `config.yaml` 后需 `docker compose restart listen publish` 生效。

## 架构

```
Telegram (Telethon userbot)
  → listen 服务 (backend/app.py + FastAPI)
  → Redis list "queue"
  → publish 服务 (backend/worker.py)
  → QQ 频道帖子 API
```

**4 个 Docker 服务**：`postgres`、`redis`、`listen`、`publish`

**数据库**（PostgreSQL）：
- `processed` 表 — 消息去重
- `dead` 表 — 死信队列（发送失败的消息）

## 核心文件

| 文件 | 职责 |
|------|------|
| `backend/app.py` | TG userbot 监听 + FastAPI 管理 API |
| `backend/worker.py` | 消费 Redis 队列 → 发帖到 QQ（最核心） |
| `backend/qq_ws_keepalive.py` | QQ WS Gateway 保活 + 熔断逻辑 |
| `backend/qq_auth.py` | QQ AccessToken 自动刷新 |
| `backend/auth.py` | JWT 鉴权（管理 API） |
| `backend/db.py` | PostgreSQL 操作（processed/dead 表） |
| `frontend/src/` | Vue 3 管理界面（Ant Design Vue + Pinia） |

## 技术栈

- **后端**：Python 3.11、Telethon、FastAPI、Uvicorn
- **队列**：Redis 7（list 结构）
- **数据库**：PostgreSQL 15
- **图片处理**：Pillow
- **前端**：Vue 3 + Vite 5 + Ant Design Vue 4 + Pinia + Vue Router
- **部署**：Docker Compose v2
