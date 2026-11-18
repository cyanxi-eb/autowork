import json
import os
import re
from urllib.parse import urlsplit


DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY", "model": "", "effort": ""},
    "dsh": {"command": "dsh", "model": "", "effort": ""},
    "codex": {"command": "codex", "model": "", "effort": ""},
}


class Connections:
    def __init__(self, store):
        self.store = store

    def get(self, agent_id):
        defaults = DEFAULTS[agent_id]
        with self.store.connection() as db:
            row = db.execute("SELECT settings FROM agent_connections WHERE agent_id = ?", (agent_id,)).fetchone()
        settings = defaults | (json.loads(row["settings"]) if row else {})
        connection = {"id": agent_id, "kind": "api" if agent_id == "deepseek" else "cli", "mode": "mock", "settings": settings}
        if agent_id == "deepseek":
            connection["key_available"] = bool(os.environ.get(settings["key_env"]))
        return connection

    def save(self, agent_id, fields):
        defaults = DEFAULTS[agent_id]
        if set(fields) != set(defaults):
            raise ValueError("接口配置字段不匹配。API 密钥请通过环境变量提供。")
        settings = {}
        for name, value in fields.items():
            limit = 1024 if name in ("base_url", "command") else 128
            if not isinstance(value, str) or len(value) > limit or any(ord(character) < 32 for character in value):
                raise ValueError(f"{name} 的值无效。")
            value.encode("utf-8")
            settings[name] = value.strip()
        if agent_id == "deepseek":
            url = urlsplit(settings["base_url"])
            if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None or
                    url.password is not None or url.query or url.fragment or any(char.isspace() for char in url.netloc)):
                raise ValueError("请输入不带账号、令牌、查询参数的 HTTP(S) API 地址。")
            _ = url.port  # Validate any explicit port.
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", settings["key_env"]):
                raise ValueError("密钥环境变量名应由字母、数字或下划线组成，以字母或下划线开头。")
        elif not settings["command"]:
            raise ValueError("请输入本地命令名或可执行文件路径。")
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO agent_connections (agent_id, settings) VALUES (?, ?) "
                "ON CONFLICT(agent_id) DO UPDATE SET settings=excluded.settings",
                (agent_id, json.dumps(settings, ensure_ascii=False)),
            )
        return self.get(agent_id)
