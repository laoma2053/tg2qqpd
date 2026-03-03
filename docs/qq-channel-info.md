# QQ 频道信息查询与子频道记录

> 维护日期：2026-03-03

---

## 一、频道基本信息

| 项目 | 值 |
|---|---|
| 频道名称 | 网盘追剧吧 |
| guild_id | `3628508121088643592` |
| 成员数 | 17 / 30 |
| 加入时间 | 2026-02-09 |

---

## 二、子频道列表

| channel_id | 名称 | type | 类型说明 | speak_permission |
|---|---|---|---|---|
| `717979188` | 帖子广场 | 10007 | 帖子频道 | 1（所有人可发言）|
| `717979189` | 日程 | 10011 | 日程频道 | 1（所有人可发言）|
| `725240617` | 资源 | 10007 | 帖子频道 | 1（所有人可发言）|
| `725474187` | 其他 | 10007 | 帖子频道 | 1（所有人可发言）|
| `719359864` | AI提问 | 4 | 分类 | — |
| `717979191` | 帖子广场 | 4 | 分类 | — |
| `717979192` | 直播 | 4 | 分类 | — |
| `717979193` | 日程 | 4 | 分类 | — |
| `717979194` | 语音房 | 4 | 分类 | — |
| `717979195` | 签到 | 4 | 分类 | — |
| `717979190` | （空） | 4 | 分类（根节点）| — |

### type 类型说明

| type 值 | 含义 |
|---|---|
| 0 | 文字子频道（旧版，目前已无法新建）|
| 2 | 语音子频道 |
| 4 | 分类（不可发消息）|
| 10007 | 帖子子频道（论坛/帖子广场）|
| 10011 | 日程子频道 |

### speak_permission 说明

| 值 | 含义 |
|---|---|
| 1 | 所有人可发言 |
| 2 | 仅管理员/指定身份组可发言 |

---

## 三、查询命令

以下命令在服务器上执行，需要先获取管理 Token。

### 0. 获取管理 Token（后续命令都需要）

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/login \
  -H "Content-Type: application/json" \
  -d '{"password":"tg2qqpd"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
```

### 1. 查看机器人加入的所有频道（guilds）

```bash
curl -s http://localhost:8000/api/qq/guilds \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

返回示例：
```json
[
  {
    "id": "3628508121088643592",
    "name": "网盘追剧吧",
    "member_count": 17,
    "max_members": 30
  }
]
```

### 2. 查看自动选择的默认子频道

```bash
curl -s "http://localhost:8000/api/qq/pick-default-channel?guild_id=3628508121088643592" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

返回示例：
```json
{
  "channel_id": "717979188",
  "channel": {
    "id": "717979188",
    "name": "帖子广场",
    "type": 10007,
    "speak_permission": 1
  }
}
```

### 3. 查看指定频道下所有子频道（直接调用 QQ OpenAPI）

```bash
curl -s "https://api.sgroup.qq.com/guilds/3628508121088643592/channels" \
  -H "Authorization: QQBot $(curl -s -X POST https://bots.qq.com/app/getAppAccessToken \
    -H 'Content-Type: application/json' \
    -d '{"appId":"102835488","clientSecret":"j9a1Tv0rLqLrNuRzX6fFpQ2eHuYCrWCs"}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")" \
  | python3 -c "
import sys,json
channels = json.load(sys.stdin)
for ch in channels:
    print(f\"id={ch.get('id')}  name={ch.get('name')}  type={ch.get('type')}  speak={ch.get('speak_permission')}\")
"
```

---

## 四、配置说明

当前 `config.yaml` 中的目标子频道配置：

```yaml
qq:
  target_guild_id: "3628508121088643592"
  target_channel_id: "717979188"    # 帖子广场
```

如需切换到其他子频道，修改 `target_channel_id` 后重启 worker：

```bash
docker compose restart worker
```

### 可用的帖子子频道（type=10007）

| 用途建议 | channel_id | 名称 | 备注 |
|---|---|---|---|
| **当前使用** | `717979188` | 帖子广场 | speak=1，所有人可见 |
| 备选 | `725240617` | 资源 | speak=1，所有人可见 |
| 备选 | `725474187` | 其他 | speak=1，所有人可见 |

> ⚠️ 注意：所有子频道均为 **帖子类型（type=10007）**，发送消息使用 `PUT /channels/{channel_id}/threads` 帖子 API，而非普通的 `POST /channels/{channel_id}/messages`。
