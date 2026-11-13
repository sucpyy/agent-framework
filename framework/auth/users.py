"""企业版用户管理 — 三级角色：admin / developer / guest"""

from __future__ import annotations
import json, os, time, uuid, hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    GUEST = "guest"


@dataclass
class User:
    user_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    username: str = ""
    password_hash: str = ""
    role: Role = Role.GUEST
    display_name: str = ""
    created_at: float = field(default_factory=time.time)
    last_login: float = 0.0
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id, "username": self.username,
            "role": self.role.value, "display_name": self.display_name or self.username,
            "created_at": self.created_at, "last_login": self.last_login, "active": self.active,
        }

    def to_safe_dict(self) -> dict:
        """不含密码哈希的安全输出。"""
        return self.to_dict()


class UserManager:
    def __init__(self, data_dir: str = ".auth"):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._users: dict[str, User] = {}
        self._tokens: dict[str, str] = {}  # token → user_id
        self._load()
        self._ensure_admin()

    def _ensure_admin(self):
        if not any(u.role == Role.ADMIN for u in self._users.values()):
            admin = User(username="admin", display_name="管理员", role=Role.ADMIN)
            admin.password_hash = self._hash("admin123")
            self._users[admin.user_id] = admin
            self._save()

    def _hash(self, pw: str) -> str:
        return hashlib.sha256(f"af{_SALT}{pw}".encode()).hexdigest()

    def _save(self):
        data = {uid: u.to_dict() | {"password_hash": u.password_hash}
                for uid, u in self._users.items()}
        (self._dir / "users.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load(self):
        path = self._dir / "users.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for uid, d in data.items():
                u = User(user_id=uid, username=d["username"],
                         password_hash=d["password_hash"],
                         role=Role(d.get("role", "guest")),
                         display_name=d.get("display_name", ""),
                         created_at=d.get("created_at", 0),
                         last_login=d.get("last_login", 0),
                         active=d.get("active", True))
                self._users[uid] = u

    # ── Auth ────────────────────────────────

    def login(self, username: str, password: str) -> tuple[str | None, User | None]:
        """返回 (token, user) 或 (None, None)。"""
        for u in self._users.values():
            if u.username == username and u.active:
                if u.password_hash == self._hash(password):
                    token = uuid.uuid4().hex
                    self._tokens[token] = u.user_id
                    u.last_login = time.time()
                    self._save()
                    return token, u
        return None, None

    def logout(self, token: str):
        self._tokens.pop(token, None)

    def get_user(self, token: str) -> User | None:
        uid = self._tokens.get(token)
        return self._users.get(uid) if uid else None

    # ── Admin ───────────────────────────────

    def check_admin(self, token: str) -> bool:
        u = self.get_user(token)
        return u is not None and u.role == Role.ADMIN

    def check_developer(self, token: str) -> bool:
        u = self.get_user(token)
        return u is not None and u.role in (Role.ADMIN, Role.DEVELOPER)

    def create_user(self, token: str, username: str, password: str, role: str, display_name: str = "") -> dict:
        if not self.check_admin(token):
            return {"error": "需要管理员权限"}
        if any(u.username == username for u in self._users.values()):
            return {"error": "用户名已存在"}
        u = User(username=username, display_name=display_name or username,
                 role=Role(role))
        u.password_hash = self._hash(password)
        self._users[u.user_id] = u
        self._save()
        return {"ok": True, "user": u.to_safe_dict()}

    def delete_user(self, token: str, user_id: str) -> dict:
        if not self.check_admin(token):
            return {"error": "需要管理员权限"}
        u = self._users.get(user_id)
        if not u:
            return {"error": "用户不存在"}
        if u.role == Role.ADMIN:
            return {"error": "不能删除管理员"}
        self._users.pop(user_id, None)
        # 清除该用户的 token
        self._tokens = {t: uid for t, uid in self._tokens.items() if uid != user_id}
        self._save()
        return {"ok": True}

    def set_role(self, token: str, user_id: str, role: str) -> dict:
        if not self.check_admin(token):
            return {"error": "需要管理员权限"}
        u = self._users.get(user_id)
        if not u:
            return {"error": "用户不存在"}
        u.role = Role(role)
        self._save()
        return {"ok": True, "user": u.to_safe_dict()}

    def list_users(self, token: str) -> list[dict]:
        if not self.check_admin(token):
            return []
        return [u.to_safe_dict() for u in self._users.values()]

    def revoke_action(self, token: str, action_id: str) -> dict:
        """撤销操作 — 管理员特权。"""
        if not self.check_admin(token):
            return {"error": "需要管理员权限"}
        # 记录撤销日志
        log_path = self._dir / "revoke_log.json"
        logs = []
        if log_path.exists():
            logs = json.loads(log_path.read_text(encoding="utf-8"))
        logs.append({"action_id": action_id, "revoked_at": time.time(), "revoked_by": self.get_user(token).username})
        log_path.write_text(json.dumps(logs, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "action_id": action_id}


_SALT = "af-enterprise-v1"
