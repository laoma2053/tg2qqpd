# TG2QQPD 项目交接说明书（给 VSCode Copilot GPT-5.2）

## 项目目标（必须实现）

做一个个人使用的服务，**监控指定 Telegram 频道消息**，当频道有新消息时自动触发转发到 **QQ 频道**。消息类型包含：**文字 + 图片**（支持图文；多图策略：只转发第一张图）。同时要提供一个 **Web 管理后台** 便于维护配置。

**核心能力（必须保留）：**

- TG → QQ **文字/图文转发**
- 多图/相册：**只转发第一张图片 + 文案**
- 图片失败：**压缩后重试一次**，仍失败则**降级只发文字**
- 关键词过滤只针对“文字内容”
    - 命中规则 → 整条消息（图+文）不转发
    - 图片没文字 → 默认不转（可配置）
- **PostgreSQL**：去重 / 状态 / 死信持久化
- **Redis**：队列（解耦、抗抖、避免阻塞）
- 单 Worker（个人使用足够，不做并发 worker）
- Web 管理（登录 + 映射 + 灰度 + 过滤规则 + 死信重放）
- Docker Compose 一键部署

**运维关注指标（Dashboard）：**

- Redis 队列长度
- 今日成功数（成功转发）
- 今日失败数（写死信）
- 当前死信总数

## 架构（必须按这个思路）

`Telegram (Telethon)-> backend（监听 + 判定 + 入 Redis 队列）-> Redis queue-> worker（出队 + 发送 QQ + 写数据库 + 死信）-> QQ Channel
PostgreSQL：processed（去重/成功记录） + dead（死信记录含 payload）
Web Admin：Vue3 + Vite + Ant Design Vue + Pinia + vue-router`

**稳定性优先**：不并发发送、不多 worker。

## 目录结构（当前）

顶层：TG2QQPD/

- backend/
    - app.py（TG 监听 + FastAPI app）
    - worker.py（消费 Redis 队列，发送 QQ）
    - auth.py（JWT 简单登录）
    - db.py（psycopg2，表：processed/dead）
    - api/
        - system.py（GET /api/system/stats）
        - deadletters.py（GET/POST 重放死信）
    - Dockerfile, requirements.txt
- frontend/
    - src/
        - main.ts, App.vue
        - router/index.ts（嵌套 AdminLayout：Dashboard/Mapping/Dead）
        - layouts/AdminLayout.vue（左侧菜单）
        - views/ Dashboard.vue, Mapping.vue, DeadLetters.vue, Login.vue
        - api/ auth.ts, system.ts, mapping.ts, deadletters.ts
        - store/auth.ts
    - package.json, vite.config.ts, index.html, Dockerfile
- nginx/nginx.conf（可能用于反代）
- docker-compose.yml
- mapping.json（频道映射配置）
- blacklist.json（频道黑名单）
- .env

## 关键设计决策（非常重要）

### 4.1 Mapping 主键（频道标识）

**强烈推荐**以 `tg_chat_id`（如 -100xxxx）为主键；`@username` 不稳定（可能没有/可变/私有频道），会导致问题。

因此 mapping.json 理想结构：

```
{
  "-1001234567890": {
    "enabled": true,
    "qq_channel_id": "xxxx",
    "remark": "xxx",
    "group": "资讯",
    "gray_ratio": 1,
    "template": {"prefix": "...", "suffix": "..."},
    "filter": {"mode": "block|allow", "keywords": [], "regex": []}
  }
}

```

- *白名单监听原则：**只监听 mapping.json 里配置的频道；TG 加很多频道≠程序都监听。

### 4.2 死信必须存 payload

重放必须依赖原始任务 payload，因此 `dead` 表必须包含 `payload JSONB`，否则“重放”无法恢复消息内容/图片。

### 4.3 登录接口参数必须是 JSON body

前端 login：`POST /api/login {password}`
 FastAPI 必须用 Pydantic 模型解析 body，否则 422。

## 后端接口契约（必须满足前端）

### 5.1 登录

- `POST /api/login`
- body: `{ "password": "xxx" }`
- resp: `{ "token": "<jwt>" }`

### 5.2 Dashboard

- `GET /api/system/stats`
- resp:

```
{
  "queue_length": 0,
  "success_today": 0,
  "failed_today": 0,
  "dead_count": 0
}

```

数据来源：

- queue_length：Redis llen("queue")
- success_today：processed 表按 created_at 当天 count
- failed_today：dead 表按 created_at 当天 count
- dead_count：dead 表 count(*)

### 5.3 死信

- `GET /api/deadletters`
    - 返回死信列表（含 content 预览、created_at、error、tg_chat_id、tg_msg_id、payload）
- `POST /api/deadletters/{id}/retry`
    - 单条重放：把 payload 推回 Redis queue，并从 dead 删除
- `POST /api/deadletters/retry`
    - body: `{ "ids": [1,2,3] }`
    - 批量重放：同上

### 5.4 Mapping（目前后端可能缺失，需要补齐）

前端现在有 mapping CRUD，但后端尚未实现：

- `GET /api/mappings`
- `POST /api/mappings`
- `PUT /api/mappings/{id}`
- `DELETE /api/mappings/{id}`**建议直接实现为读写 mapping.json**（个人项目够用），并在后端加 file lock 防并发写。

后续需求：后台“一键添加当前频道”（从 Telethon dialogs 列出频道，选择后写 mapping.json）。

## 当前工程问题（VSCode 红点）

前端红点主要可能来自：

1. `frontend/src/api/*` 中引用了不存在的 `./request` 或者路径别名 `@/` 未配置；
2. TypeScript 缺少 `tsconfig.json` 或 Vite 配置缺少 alias；
3. `auth.ts`/`request.ts` axios 实例重复/命名不一致；
4. ant-design-vue 版本/类型声明不匹配；
5. router/layouts 路径存在但文件名或大小写不一致（Windows/WSL差异）；
6. 由于没有 `env.d.ts` 导致 `.vue` 类型报错；
7. VSCode 未识别 Vue SFC，需要 `shims-vue.d.ts`。

### 需要 Copilot GPT-5.2 做的第一优先级工作

- **目标：消除所有 TypeScript/路径红线**，确保 `npm run dev` 成功。
- 建议检查并补齐这些文件（常见缺失）：
    - `frontend/tsconfig.json`
    - `frontend/src/env.d.ts`（或 `shims-vue.d.ts`）
    - `frontend/vite.config.ts` 中配置 alias：`@` -> `/src`
- 统一 axios：
    - 新建 `frontend/src/api/request.ts`，baseURL=`/api`，拦截器加 Bearer token
    - `auth.ts` 仅做 login，其他 API 文件都从 request.ts 引入
- 检查 router 导入：`import router from "./router/index"`（main.ts）
- 检查 ant-design-vue CSS：`ant-design-vue/dist/reset.css` 正确（v4）

## 运行方式（Docker）

- `.env` 提供：TG_API_ID/TG_API_HASH/TG_SESSION；QQ_APP_ID/QQ_BOT_TOKEN；REDIS_HOST=redis；DATABASE_URL 指向 postgres service；JWT_SECRET/ADMIN_PASS。
- Docker Compose 应包含：postgres、redis、backend、worker、frontend(nginx)。
- TG 首次登录需要在 backend 容器日志里输入验证码（session 持久化）。

## 功能完成度（目前）

已做：

- 前端：Login + AdminLayout + Dashboard/Mapping/DeadLetters 页面骨架
- 后端：system.py、deadletters.py 路由文件存在（但可能未完全对齐 JSON/鉴权/路径）
- worker/backed 基础逻辑存在

未完成/待对齐：

- Mapping 后端 API（建议读写 mapping.json）
- Dashboard 指标真实计算（需要 processed.created_at、dead.payload）
- 死信表结构需含 payload（否则重放不可用）
- QQ 图片上传接口可能还是占位（如需真实上传需再接 QQ API）

## Copilot GPT-5.2 交付要求（请按这个做）

1. 先让前端工程 **无红线**：补齐 tsconfig、env.d.ts、alias、axios request 文件等。
2. 保证 `npm i && npm run dev` 正常运行；页面能访问 `/login`、`/`、`/mapping`、`/dead`。
3. 对齐后端接口：`/api/login`、`/api/system/stats`、`/api/deadletters*` 能正常返回。
4. 如果 Mapping 后端缺失，补齐 mapping.json CRUD API。
5. 所有修改给出：**文件路径 + 完整可复制代码**。

---

## 附：前端期望依赖（package.json）

- vue 3
- vite
- typescript
- vue-router 4
- pinia
- axios
- ant-design-vue 4