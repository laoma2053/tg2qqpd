"""
统一配置加载器 — 从 config.yaml 读取所有业务配置。

使用方式：
    from config import CFG
    CFG["telegram"]["sources"]       # TG 监听源列表
    CFG["qq"]["target_guild_id"]     # QQ guild_id
    CFG["rules"]["filter"]           # 过滤规则 dict
    CFG["rules"]["transforms"]       # 清洗规则列表

设计原则：
1. YAML 中 ${ENV_VAR} 占位符会被替换为同名环境变量的值（用于敏感凭证）。
2. YAML 路径通过环境变量 CONFIG_YAML_PATH 指定，默认 /app/config.yaml。
3. 模块级别一次性加载；修改配置后重启容器即可。
"""

import os
import re
import yaml

_CONFIG_PATH = os.getenv("CONFIG_YAML_PATH", "/app/config.yaml")

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env_vars(obj):
    """递归替换 YAML 值中的 ${ENV_VAR} 占位符。"""
    if isinstance(obj, str):
        def _replacer(m):
            name = m.group(1)
            return os.getenv(name, "")
        return _ENV_VAR_RE.sub(_replacer, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(item) for item in obj]
    return obj


def _load(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"config.yaml not found: {path}\n"
            "请确保 docker-compose.yml 中已挂载 ./config.yaml:/app/config.yaml:ro"
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _resolve_env_vars(raw)


CFG: dict = _load(_CONFIG_PATH)


# ── 便捷取值函数 ──────────────────────────────────────────────

def get(path: str, default=None):
    """用 dot 路径取值，例如 get("telegram.sources", [])"""
    keys = path.split(".")
    node = CFG
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            return default
        if node is None:
            return default
    return node
