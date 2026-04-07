from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AUTH_APP_PATH = ROOT / "apps" / "auth_service" / "app.py"


@dataclass
class FakeAuthState:
    users_by_id: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    users_by_email: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    email_verification_codes: List[Dict[str, Any]] = field(default_factory=list)
    activation_codes_by_code: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    licenses_by_user_id: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    devices: List[Dict[str, Any]] = field(default_factory=list)
    audit_logs: List[Dict[str, Any]] = field(default_factory=list)
    redis_values: Dict[str, Any] = field(default_factory=dict)
    next_user_id: int = 1
    next_email_code_id: int = 1
    next_activation_code_id: int = 1
    next_license_id: int = 1
    next_device_id: int = 1
    next_audit_id: int = 1


class FakeRedis:
    def __init__(self, state: FakeAuthState):
        self.state = state

    def ping(self) -> bool:
        return True

    def setex(self, key: str, ttl_seconds: int, value: Any) -> None:
        self.state.redis_values[key] = value

    def get(self, key: str) -> Any:
        return self.state.redis_values.get(key, "")

    def delete(self, key: str) -> None:
        self.state.redis_values.pop(key, None)

    def incr(self, key: str) -> int:
        current = int(self.state.redis_values.get(key, 0) or 0) + 1
        self.state.redis_values[key] = current
        return current

    def expire(self, key: str, ttl_seconds: int) -> None:
        return None


class FakeConnection:
    def __init__(self, state: FakeAuthState):
        self.state = state

    def cursor(self):
        return FakeCursor(self.state)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeCursor:
    def __init__(self, state: FakeAuthState):
        self.state = state
        self._result: Any = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def fetchone(self):
        if isinstance(self._result, list):
            return deepcopy(self._result[0]) if self._result else None
        return deepcopy(self._result)

    def fetchall(self):
        if isinstance(self._result, list):
            return deepcopy(self._result)
        if self._result is None:
            return []
        return [deepcopy(self._result)]

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None):
        params = tuple(params or ())
        norm = " ".join(sql.lower().split())
        self._result = None

        if norm.startswith("select 1 as ok"):
            self._result = {"ok": 1}
            return 1

        if "insert into users" in norm:
            email = str(params[0]).lower()
            created_at = params[1]
            last_login_at = params[2]
            row = self.state.users_by_email.get(email)
            if row:
                row["status"] = "active"
                row["last_login_at"] = last_login_at
            else:
                row = {
                    "id": self.state.next_user_id,
                    "email": email,
                    "status": "active",
                    "created_at": created_at,
                    "last_login_at": last_login_at,
                }
                self.state.next_user_id += 1
                self.state.users_by_id[row["id"]] = row
                self.state.users_by_email[email] = row
            return 1

        if norm.startswith("select id, email, status, created_at, last_login_at from users where email="):
            email = str(params[0]).lower()
            self._result = self.state.users_by_email.get(email)
            return 1

        if norm.startswith("select id, email, status, created_at, last_login_at from users where id="):
            user_id = int(params[0])
            self._result = self.state.users_by_id.get(user_id)
            return 1

        if "insert into email_verification_codes" in norm:
            row = {
                "id": self.state.next_email_code_id,
                "email": str(params[0]).lower(),
                "code_hash": params[1],
                "expire_at": params[2],
                "used_at": None,
                "request_ip": params[3],
                "created_at": params[4],
            }
            self.state.next_email_code_id += 1
            self.state.email_verification_codes.append(row)
            return 1

        if norm.startswith("select id, email, code_hash, expire_at, used_at, request_ip, created_at from email_verification_codes where email=%s and used_at is null"):
            email = str(params[0]).lower()
            rows = [item for item in self.state.email_verification_codes if item["email"] == email and item["used_at"] is None]
            rows.sort(key=lambda x: x["id"], reverse=True)
            self._result = rows[0] if rows else None
            return 1

        if norm.startswith("update email_verification_codes set used_at="):
            used_at, target = params
            if isinstance(target, str) and "@" in target:
                email = str(target).lower()
                for item in reversed(self.state.email_verification_codes):
                    if item["email"] == email and item["used_at"] is None:
                        item["used_at"] = used_at
                        return 1
                return 0
            code_id = int(target)
            for item in self.state.email_verification_codes:
                if item["id"] == code_id and item["used_at"] is None:
                    item["used_at"] = used_at
                    return 1
            return 0

        if "from licenses where user_id=" in norm and norm.startswith("select id, user_id, license_type"):
            user_id = int(params[0])
            self._result = self.state.licenses_by_user_id.get(user_id)
            return 1

        if norm.startswith("show columns from activation_codes like"):
            col = str(params[0])
            self._result = {"Field": col} if col in {"product_edition", "duration_minutes"} else None
            return 1

        if norm.startswith("show columns from licenses like"):
            col = str(params[0])
            self._result = {"Field": col} if col == "product_edition" else None
            return 1

        if norm.startswith("alter table activation_codes add column product_edition"):
            return 0

        if norm.startswith("alter table activation_codes add column duration_minutes"):
            return 0

        if norm.startswith("alter table licenses add column product_edition"):
            return 0

        if norm.startswith("update licenses set status='expired', updated_at="):
            updated_at, user_id = params
            user_id = int(user_id)
            lic = self.state.licenses_by_user_id.get(user_id)
            if lic:
                lic["status"] = "expired"
                lic["updated_at"] = updated_at
            return 1

        if "from devices" in norm and "where user_id=%s and active=1" in norm:
            user_id = int(params[0])
            rows = [item for item in self.state.devices if item["user_id"] == user_id and int(item.get("active") or 0) == 1]
            rows.sort(key=lambda x: x["created_at"])
            self._result = rows
            return len(rows)

        if norm.startswith("update devices set device_name="):
            device_name, platform, last_seen_at, user_id, device_id = params
            user_id = int(user_id)
            for item in self.state.devices:
                if item["user_id"] == user_id and item["device_id"] == device_id:
                    item["device_name"] = device_name
                    item["platform"] = platform
                    item["last_seen_at"] = last_seen_at
                    item["active"] = 1
                    break
            return 1

        if "insert into devices" in norm:
            row = {
                "id": self.state.next_device_id,
                "user_id": int(params[0]),
                "device_id": params[1],
                "device_name": params[2],
                "platform": params[3],
                "active": 1,
                "last_seen_at": params[4],
                "created_at": params[5],
            }
            self.state.next_device_id += 1
            self.state.devices.append(row)
            return 1

        if "from activation_codes" in norm and "where code=%s" in norm and "for update" in norm:
            code = str(params[0]).upper()
            self._result = self.state.activation_codes_by_code.get(code)
            return 1

        if norm.startswith("select id, code, type, duration_days, duration_minutes, status, product_edition from activation_codes where code="):
            code = str(params[0]).upper()
            self._result = self.state.activation_codes_by_code.get(code)
            return 1

        if "insert into activation_codes" in norm:
            row = {
                "id": self.state.next_activation_code_id,
                "code": str(params[0]).upper(),
                "type": params[1],
                "duration_days": int(params[2]),
                "duration_minutes": int(params[3]),
                "status": "unused",
                "product_edition": params[4],
                "used_by_user_id": None,
                "used_by_email": None,
                "used_at": None,
                "created_at": params[5],
            }
            self.state.next_activation_code_id += 1
            self.state.activation_codes_by_code[row["code"]] = row
            return 1

        if norm.startswith("update licenses set license_type="):
            license_type, product_edition, expire_at, source_activation_code_id, updated_at, user_id = params
            user_id = int(user_id)
            lic = self.state.licenses_by_user_id.get(user_id)
            if lic:
                lic["license_type"] = license_type
                lic["product_edition"] = product_edition
                lic["status"] = "active"
                lic["expire_at"] = expire_at
                lic["source_activation_code_id"] = source_activation_code_id
                lic["updated_at"] = updated_at
            return 1

        if "insert into licenses (" in norm and "values (%s, %s, %s, 'active'" in norm:
            row = {
                "id": self.state.next_license_id,
                "user_id": int(params[0]),
                "license_type": params[1],
                "product_edition": params[2],
                "status": "active",
                "start_at": params[3],
                "expire_at": params[4],
                "source_activation_code_id": params[5],
                "created_at": params[6],
                "updated_at": params[7],
            }
            self.state.next_license_id += 1
            self.state.licenses_by_user_id[row["user_id"]] = row
            return 1

        if "insert into licenses (" in norm and "'manual', 'universal', 'active'" in norm:
            row = {
                "id": self.state.next_license_id,
                "user_id": int(params[0]),
                "license_type": "manual",
                "product_edition": "universal",
                "status": "active",
                "start_at": params[1],
                "expire_at": params[2],
                "source_activation_code_id": None,
                "created_at": params[3],
                "updated_at": params[4],
            }
            self.state.next_license_id += 1
            self.state.licenses_by_user_id[row["user_id"]] = row
            return 1

        if norm.startswith("update activation_codes set status='used'"):
            user_id, used_at, code_id = params
            code_id = int(code_id)
            for row in self.state.activation_codes_by_code.values():
                if row["id"] == code_id:
                    row["status"] = "used"
                    row["used_by_user_id"] = int(user_id)
                    user = self.state.users_by_id.get(int(user_id))
                    row["used_by_email"] = user["email"] if user else None
                    row["used_at"] = used_at
                    break
            return 1

        if norm.startswith("select ac.id, ac.code, ac.type, ac.duration_days, ac.duration_minutes, ac.status, ac.product_edition, ac.used_by_user_id, u.email as used_by_email, ac.used_at, ac.created_at from activation_codes ac"):
            rows = list(self.state.activation_codes_by_code.values())
            rows.sort(key=lambda x: x["id"], reverse=True)
            self._result = rows[:500]
            return len(rows)

        if norm.startswith("update activation_codes set type=%s, duration_days=%s, duration_minutes=%s, status=%s, product_edition=%s where id=%s"):
            code_type, duration_days, duration_minutes, status, product_edition, code_id = params
            code_id = int(code_id)
            for row in self.state.activation_codes_by_code.values():
                if row["id"] == code_id:
                    row["type"] = code_type
                    row["duration_days"] = int(duration_days)
                    row["duration_minutes"] = int(duration_minutes)
                    row["status"] = status
                    row["product_edition"] = product_edition
                    break
            return 1

        if norm.startswith("select id, code, status, used_by_user_id from activation_codes where code=%s for update"):
            code = str(params[0]).upper()
            self._result = self.state.activation_codes_by_code.get(code)
            return 1

        if norm.startswith("delete from activation_codes where id=%s"):
            code_id = int(params[0])
            for code, row in list(self.state.activation_codes_by_code.items()):
                if row["id"] == code_id:
                    self.state.activation_codes_by_code.pop(code, None)
                    break
            return 1

        if "from users u" in norm and "active_device_count" in norm:
            rows = []
            for user in sorted(self.state.users_by_id.values(), key=lambda x: x["id"], reverse=True):
                lic = self.state.licenses_by_user_id.get(user["id"])
                rows.append(
                    {
                        "id": user["id"],
                        "email": user["email"],
                        "status": user["status"],
                        "created_at": user["created_at"],
                        "last_login_at": user["last_login_at"],
                        "license_type": lic["license_type"] if lic else None,
                        "product_edition": lic.get("product_edition") if lic else None,
                        "license_status": lic["status"] if lic else None,
                        "expire_at": lic["expire_at"] if lic else None,
                        "active_device_count": sum(1 for d in self.state.devices if d["user_id"] == user["id"] and int(d.get("active") or 0) == 1),
                    }
                )
            self._result = rows[:200]
            return len(rows)

        if norm.startswith("update licenses set status=%s, updated_at=%s where user_id=%s"):
            status, updated_at, user_id = params
            user_id = int(user_id)
            lic = self.state.licenses_by_user_id.get(user_id)
            if lic:
                lic["status"] = status
                lic["updated_at"] = updated_at
                return 1
            return 0

        if norm.startswith("update licenses set status='active', expire_at=%s, updated_at=%s where user_id=%s"):
            expire_at, updated_at, user_id = params
            user_id = int(user_id)
            lic = self.state.licenses_by_user_id.get(user_id)
            if lic:
                lic["status"] = "active"
                lic["expire_at"] = expire_at
                lic["updated_at"] = updated_at
            return 1

        if norm.startswith("insert into admin_audit_logs"):
            row = {
                "id": self.state.next_audit_id,
                "action": params[0],
                "target_type": params[1],
                "target_id": params[2],
                "operator_name": params[3],
                "detail_json": params[4],
                "created_at": params[5],
            }
            self.state.next_audit_id += 1
            self.state.audit_logs.append(row)
            return 1

        if "from devices d" in norm and "inner join users u" in norm:
            rows = []
            for item in self.state.devices:
                user = self.state.users_by_id.get(item["user_id"])
                lic = self.state.licenses_by_user_id.get(item["user_id"])
                if not user:
                    continue
                rows.append(
                    {
                        "id": item["id"],
                        "user_id": item["user_id"],
                        "email": user["email"],
                        "device_id": item["device_id"],
                        "device_name": item["device_name"],
                        "platform": item["platform"],
                        "active": item["active"],
                        "last_seen_at": item["last_seen_at"],
                        "created_at": item["created_at"],
                        "license_status": lic["status"] if lic else None,
                        "license_type": lic["license_type"] if lic else None,
                        "product_edition": lic.get("product_edition") if lic else None,
                        "expire_at": lic["expire_at"] if lic else None,
                    }
                )
            rows.sort(key=lambda x: (int(x["active"]), x["last_seen_at"], x["id"]), reverse=True)
            self._result = rows[:500]
            return len(rows)

        if norm.startswith("update devices set active=0, last_seen_at=%s where user_id=%s and device_id=%s"):
            last_seen_at, user_id, device_id = params
            user_id = int(user_id)
            for item in self.state.devices:
                if item["user_id"] == user_id and item["device_id"] == device_id:
                    item["active"] = 0
                    item["last_seen_at"] = last_seen_at
                    break
            return 1

        raise AssertionError(f"Unhandled SQL in fake cursor: {norm}\nparams={params!r}")


class AuthServiceApiTest(unittest.TestCase):
    maxDiff = None

    def _build_client(self):
        state = FakeAuthState()
        env = {
            "AUTH_AUTO_INIT_SCHEMA": "0",
            "AUTH_DEV_ECHO_CODES": "1",
            "AUTH_ADMIN_SECRET": "admin-secret",
            "AUTH_ADMIN_EMAIL": "814310111@qq.com",
            "AUTH_ADMIN_PASSWORD": "Cz123123",
            "MYSQL_HOST": "fake-mysql",
            "MYSQL_DB": "fake-db",
            "MYSQL_USER": "fake-user",
            "REDIS_HOST": "fake-redis",
        }
        module_name = f"auth_service_test_{id(state)}"
        pymysql_stub = types.ModuleType("pymysql")
        pymysql_cursors_stub = types.ModuleType("pymysql.cursors")
        pymysql_cursors_stub.DictCursor = object
        pymysql_stub.cursors = pymysql_cursors_stub
        pymysql_stub.connect = lambda **kwargs: FakeConnection(state)
        redis_stub = types.ModuleType("redis")
        redis_stub.Redis = lambda **kwargs: FakeRedis(state)

        with patch.dict(
            sys.modules,
            {
                "pymysql": pymysql_stub,
                "pymysql.cursors": pymysql_cursors_stub,
                "redis": redis_stub,
            },
            clear=False,
        ), patch.dict(os.environ, env, clear=False):
            spec = importlib.util.spec_from_file_location(module_name, AUTH_APP_PATH)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            module.create_db_connection = lambda settings: FakeConnection(state)
            module.create_redis_client = lambda settings: FakeRedis(state)
            app = module.create_app()
        app.config["TESTING"] = True
        return app.test_client(), state

    def _send_code(self, client, email: str, remote_addr: str = "127.0.0.1"):
        return client.post("/api/auth/email/send-code", json={"email": email}, environ_base={"REMOTE_ADDR": remote_addr})

    def _login(
        self,
        client,
        email: str,
        code: str,
        device_id: str = "dev-001",
        device_name: str = "Mac",
        platform: str = "macOS",
        product_edition: str = "lite",
    ):
        return client.post(
            "/api/auth/email/login",
            json={
                "email": email,
                "code": code,
                "device_id": device_id,
                "device_name": device_name,
                "platform": platform,
                "product_edition": product_edition,
            },
        )

    def test_email_login_me_and_logout_flow(self) -> None:
        client, _state = self._build_client()

        send_resp = self._send_code(client, "user@example.com")
        self.assertEqual(send_resp.status_code, 200)
        send_payload = send_resp.get_json()
        self.assertEqual(send_payload["ttl_seconds"], 300)
        self.assertRegex(send_payload["dev_code"], r"^\d{6}$")

        login_resp = self._login(client, "user@example.com", send_payload["dev_code"])
        self.assertEqual(login_resp.status_code, 200)
        login_payload = login_resp.get_json()
        self.assertTrue(login_payload["token"])
        self.assertEqual(login_payload["license"]["status"], "none")

        headers = {"Authorization": f"Bearer {login_payload['token']}"}
        me_resp = client.get("/api/auth/me", headers=headers)
        self.assertEqual(me_resp.status_code, 200)
        self.assertEqual(me_resp.get_json()["user"]["email"], "user@example.com")

        logout_resp = client.post("/api/auth/logout", headers=headers)
        self.assertEqual(logout_resp.status_code, 200)
        self.assertEqual(logout_resp.get_json()["ok"], True)

        after_logout = client.get("/api/auth/me", headers=headers)
        self.assertEqual(after_logout.status_code, 401)

    def test_email_login_uses_database_code_truth_and_respects_expiry(self) -> None:
        client, state = self._build_client()

        send_resp = self._send_code(client, "dbtruth@example.com")
        self.assertEqual(send_resp.status_code, 200)
        send_payload = send_resp.get_json()
        code = send_payload["dev_code"]

        # Simulate a stale legacy Redis value: login should still reject because DB says expired.
        state.redis_values["auth:code:dbtruth@example.com"] = code
        state.email_verification_codes[-1]["expire_at"] = datetime.utcnow() - timedelta(seconds=1)

        login_resp = self._login(client, "dbtruth@example.com", code)
        self.assertEqual(login_resp.status_code, 400)
        self.assertEqual(login_resp.get_json()["error"], "invalid code")

    def test_web_admin_console_routes_are_available(self) -> None:
        client, _state = self._build_client()

        auth_home = client.get("/", headers={"Host": "auth.miaoyichuhai.com"})
        self.assertEqual(auth_home.status_code, 200)
        self.assertIn("miaoyichuhai auth service ok", auth_home.get_data(as_text=True))

        admin_home = client.get("/", headers={"Host": "admin.miaoyichuhai.com"})
        self.assertEqual(admin_home.status_code, 200)
        self.assertIn("text/html", admin_home.content_type)
        self.assertIn("秒译出海授权管理台", admin_home.get_data(as_text=True))
        self.assertIn("code-keyword", admin_home.get_data(as_text=True))

        admin_route = client.get("/admin", headers={"Host": "auth.miaoyichuhai.com"})
        self.assertEqual(admin_route.status_code, 302)
        self.assertEqual(admin_route.headers["Location"], "http://admin.miaoyichuhai.com/")

    def test_admin_email_password_login_flow(self) -> None:
        client, _state = self._build_client()

        bad_login = client.post("/api/admin/login", json={"email": "814310111@qq.com", "password": "wrong"})
        self.assertEqual(bad_login.status_code, 401)

        login = client.post("/api/admin/login", json={"email": "814310111@qq.com", "password": "Cz123123"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.get_json()["user"]["email"], "814310111@qq.com")

        me = client.get("/api/admin/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["user"]["email"], "814310111@qq.com")
        self.assertEqual(me.get_json()["user"]["auth_type"], "session")

        logout = client.post("/api/admin/logout")
        self.assertEqual(logout.status_code, 200)

        after_logout = client.get("/api/admin/me")
        self.assertEqual(after_logout.status_code, 401)

    def test_rate_limit_and_admin_forbidden_contracts(self) -> None:
        client, _state = self._build_client()

        for _ in range(5):
            resp = self._send_code(client, "burst@example.com", remote_addr="10.0.0.1")
            self.assertEqual(resp.status_code, 200)

        blocked = self._send_code(client, "burst@example.com", remote_addr="10.0.0.1")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.get_json()["error"], "too many requests")

        forbidden = client.post("/api/admin/activation-codes", json={"count": 1, "duration_days": 30, "type": "monthly"})
        self.assertEqual(forbidden.status_code, 401)

    def test_activation_redeem_reuse_and_admin_freeze_extend_flow(self) -> None:
        client, state = self._build_client()

        code_resp = self._send_code(client, "licensed@example.com")
        dev_code = code_resp.get_json()["dev_code"]
        login_resp = self._login(client, "licensed@example.com", dev_code, device_id="licensed-dev")
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_code = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 30, "type": "monthly", "product_edition": "lite"},
        )
        self.assertEqual(create_code.status_code, 200)
        activation_code = create_code.get_json()["items"][0]["code"]

        redeem_resp = client.post("/api/license/redeem", headers=headers, json={"code": activation_code, "product_edition": "lite"})
        self.assertEqual(redeem_resp.status_code, 200)
        self.assertEqual(redeem_resp.get_json()["license"]["status"], "active")
        self.assertEqual(redeem_resp.get_json()["license"]["license_type"], "monthly")
        self.assertEqual(redeem_resp.get_json()["license"]["product_edition"], "lite")

        reuse_resp = client.post("/api/license/redeem", headers=headers, json={"code": activation_code, "product_edition": "lite"})
        self.assertEqual(reuse_resp.status_code, 400)

        freeze_resp = client.post(
            "/api/admin/licenses/freeze",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"email": "licensed@example.com", "freeze": True},
        )
        self.assertEqual(freeze_resp.status_code, 200)
        me_frozen = client.get("/api/auth/me", headers=headers)
        self.assertEqual(me_frozen.get_json()["license"]["status"], "frozen")

        extend_resp = client.post(
            "/api/admin/licenses/extend",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"email": "licensed@example.com", "days": 15},
        )
        self.assertEqual(extend_resp.status_code, 200)
        self.assertTrue(extend_resp.get_json()["expire_at"])

        unfreeze_resp = client.post(
            "/api/admin/licenses/freeze",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"email": "licensed@example.com", "freeze": False},
        )
        self.assertEqual(unfreeze_resp.status_code, 200)
        me_active = client.get("/api/auth/me", headers=headers)
        self.assertEqual(me_active.get_json()["license"]["status"], "active")

        users_resp = client.get("/api/admin/users", headers={"X-Admin-Secret": "admin-secret"})
        self.assertEqual(users_resp.status_code, 200)
        self.assertEqual(users_resp.get_json()["items"][0]["license_status"], "active")
        self.assertGreaterEqual(len(state.audit_logs), 3)

    def test_expired_license_can_renew_in_place(self) -> None:
        client, state = self._build_client()

        send_resp = self._send_code(client, "expired@example.com")
        login_resp = self._login(client, "expired@example.com", send_resp.get_json()["dev_code"], device_id="expired-dev")
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_code = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 30, "type": "trial", "product_edition": "lite"},
        )
        first_code = create_code.get_json()["items"][0]["code"]
        first_redeem = client.post("/api/license/redeem", headers=headers, json={"code": first_code, "product_edition": "lite"})
        self.assertEqual(first_redeem.status_code, 200)

        user_id = next(user["id"] for user in state.users_by_id.values() if user["email"] == "expired@example.com")
        state.licenses_by_user_id[user_id]["status"] = "active"
        state.licenses_by_user_id[user_id]["expire_at"] = datetime.utcnow() - timedelta(days=1)

        expired_me = client.get("/api/auth/me", headers=headers)
        self.assertEqual(expired_me.status_code, 200)
        self.assertEqual(expired_me.get_json()["license"]["status"], "expired")

        renewal_code_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 7, "type": "renewal", "product_edition": "lite"},
        )
        renewal_code = renewal_code_resp.get_json()["items"][0]["code"]
        renew_resp = client.post("/api/license/redeem", headers=headers, json={"code": renewal_code, "product_edition": "lite"})
        self.assertEqual(renew_resp.status_code, 200)
        self.assertEqual(renew_resp.get_json()["license"]["status"], "active")
        self.assertEqual(renew_resp.get_json()["license"]["license_type"], "renewal")
        self.assertEqual(renew_resp.get_json()["license"]["product_edition"], "lite")

    def test_active_license_can_extend_immediately_for_same_edition_only(self) -> None:
        client, state = self._build_client()

        send_resp = self._send_code(client, "renew-active@example.com")
        login_resp = self._login(client, "renew-active@example.com", send_resp.get_json()["dev_code"], device_id="renew-active-dev")
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        first_code_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 30, "type": "monthly", "product_edition": "lite"},
        )
        first_code = first_code_resp.get_json()["items"][0]["code"]
        first_redeem = client.post("/api/license/redeem", headers=headers, json={"code": first_code, "product_edition": "lite"})
        self.assertEqual(first_redeem.status_code, 200)

        user_id = next(user["id"] for user in state.users_by_id.values() if user["email"] == "renew-active@example.com")
        first_expire = state.licenses_by_user_id[user_id]["expire_at"]

        second_code_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 7, "type": "renewal", "product_edition": "lite"},
        )
        second_code = second_code_resp.get_json()["items"][0]["code"]
        second_redeem = client.post("/api/license/redeem", headers=headers, json={"code": second_code, "product_edition": "lite"})
        self.assertEqual(second_redeem.status_code, 200)
        self.assertGreater(state.licenses_by_user_id[user_id]["expire_at"], first_expire)
        self.assertEqual(second_redeem.get_json()["license"]["product_edition"], "lite")

        cross_code_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 30, "type": "monthly", "product_edition": "quality"},
        )
        cross_code = cross_code_resp.get_json()["items"][0]["code"]
        cross_redeem = client.post("/api/license/redeem", headers=headers, json={"code": cross_code, "product_edition": "quality"})
        self.assertEqual(cross_redeem.status_code, 400)
        self.assertIn("only same-edition renewal is allowed before expiry", cross_redeem.get_json()["error"])

    def test_activation_code_product_edition_must_match_client(self) -> None:
        client, _state = self._build_client()

        code_resp = self._send_code(client, "quality@example.com")
        login_resp = self._login(client, "quality@example.com", code_resp.get_json()["dev_code"], device_id="quality-dev", product_edition="quality")
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_code = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 30, "type": "monthly", "product_edition": "quality"},
        )
        activation_code = create_code.get_json()["items"][0]["code"]

        mismatch_resp = client.post("/api/license/redeem", headers=headers, json={"code": activation_code, "product_edition": "lite"})
        self.assertEqual(mismatch_resp.status_code, 400)
        self.assertIn("quality edition", mismatch_resp.get_json()["error"])

        ok_resp = client.post("/api/license/redeem", headers=headers, json={"code": activation_code, "product_edition": "quality"})
        self.assertEqual(ok_resp.status_code, 200)
        self.assertEqual(ok_resp.get_json()["license"]["product_edition"], "quality")

    def test_test_activation_code_can_expire_in_three_minutes_and_support_admin_editing(self) -> None:
        client, state = self._build_client()

        create_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "type": "test", "product_edition": "lite"},
        )
        self.assertEqual(create_resp.status_code, 200)
        item = create_resp.get_json()["items"][0]
        self.assertEqual(item["type"], "test")
        self.assertEqual(item["duration_minutes"], 3)
        test_code = item["code"]

        edit_resp = client.patch(
            f"/api/admin/activation-codes/{test_code}",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"duration_minutes": 5, "product_edition": "quality"},
        )
        self.assertEqual(edit_resp.status_code, 200)
        self.assertEqual(edit_resp.get_json()["item"]["duration_minutes"], 5)
        self.assertEqual(edit_resp.get_json()["item"]["product_edition"], "quality")

        invalidate_resp = client.patch(
            f"/api/admin/activation-codes/{test_code}",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"invalidate": True},
        )
        self.assertEqual(invalidate_resp.status_code, 200)
        self.assertEqual(invalidate_resp.get_json()["item"]["status"], "invalidated")

        code_resp = self._send_code(client, "test-code@example.com")
        login_resp = self._login(client, "test-code@example.com", code_resp.get_json()["dev_code"], device_id="test-minutes-dev", product_edition="quality")
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        invalid_redeem = client.post("/api/license/redeem", headers=headers, json={"code": test_code, "product_edition": "quality"})
        self.assertEqual(invalid_redeem.status_code, 400)

        recreate_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "type": "test", "product_edition": "quality"},
        )
        active_test_code = recreate_resp.get_json()["items"][0]["code"]
        redeem_resp = client.post("/api/license/redeem", headers=headers, json={"code": active_test_code, "product_edition": "quality"})
        self.assertEqual(redeem_resp.status_code, 200)

        user_id = next(user["id"] for user in state.users_by_id.values() if user["email"] == "test-code@example.com")
        expire_at = state.licenses_by_user_id[user_id]["expire_at"]
        delta = expire_at - datetime.utcnow()
        self.assertLessEqual(delta.total_seconds(), 3 * 60 + 5)
        self.assertGreater(delta.total_seconds(), 0)

    def test_unused_activation_code_can_be_deleted_but_used_code_cannot(self) -> None:
        client, _state = self._build_client()

        create_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 30, "type": "monthly", "product_edition": "lite"},
        )
        code = create_resp.get_json()["items"][0]["code"]

        delete_resp = client.delete(f"/api/admin/activation-codes/{code}", headers={"X-Admin-Secret": "admin-secret"})
        self.assertEqual(delete_resp.status_code, 200)

        list_resp = client.get("/api/admin/activation-codes", headers={"X-Admin-Secret": "admin-secret"})
        self.assertEqual(list_resp.status_code, 200)
        self.assertFalse(any(item["code"] == code for item in list_resp.get_json()["items"]))

        send_resp = self._send_code(client, "used-delete@example.com")
        login_resp = self._login(client, "used-delete@example.com", send_resp.get_json()["dev_code"], device_id="used-delete-dev")
        token = login_resp.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_used_resp = client.post(
            "/api/admin/activation-codes",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"count": 1, "duration_days": 30, "type": "monthly", "product_edition": "lite"},
        )
        used_code = create_used_resp.get_json()["items"][0]["code"]
        redeem_resp = client.post("/api/license/redeem", headers=headers, json={"code": used_code, "product_edition": "lite"})
        self.assertEqual(redeem_resp.status_code, 200)

        delete_used_resp = client.delete(f"/api/admin/activation-codes/{used_code}", headers={"X-Admin-Secret": "admin-secret"})
        self.assertEqual(delete_used_resp.status_code, 409)

    def test_device_limit_and_admin_unbind_flow(self) -> None:
        client, _state = self._build_client()

        send_resp = self._send_code(client, "devices@example.com")
        dev_code = send_resp.get_json()["dev_code"]

        login1 = self._login(client, "devices@example.com", dev_code, device_id="dev-1", device_name="Mac-1")
        self.assertEqual(login1.status_code, 200)

        send_resp_2 = self._send_code(client, "devices@example.com")
        login2 = self._login(client, "devices@example.com", send_resp_2.get_json()["dev_code"], device_id="dev-2", device_name="Mac-2")
        self.assertEqual(login2.status_code, 200)

        send_resp_3 = self._send_code(client, "devices@example.com")
        login3 = self._login(client, "devices@example.com", send_resp_3.get_json()["dev_code"], device_id="dev-3", device_name="Mac-3")
        self.assertEqual(login3.status_code, 409)
        self.assertEqual(login3.get_json()["error"], "device limit reached")
        self.assertEqual(login3.get_json()["device_limit"], 2)

        devices_resp = client.get("/api/admin/devices", headers={"X-Admin-Secret": "admin-secret"})
        self.assertEqual(devices_resp.status_code, 200)
        self.assertEqual(len(devices_resp.get_json()["items"]), 2)

        unbind_resp = client.post(
            "/api/admin/devices/unbind",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"email": "devices@example.com", "device_id": "dev-1"},
        )
        self.assertEqual(unbind_resp.status_code, 200)

        # A device-limit rejection should not consume the email code.
        login4 = self._login(client, "devices@example.com", send_resp_3.get_json()["dev_code"], device_id="dev-3", device_name="Mac-3")
        self.assertEqual(login4.status_code, 200)

    def test_admin_freeze_requires_existing_license(self) -> None:
        client, _state = self._build_client()

        send_resp = self._send_code(client, "nolicense@example.com")
        login_resp = self._login(client, "nolicense@example.com", send_resp.get_json()["dev_code"], device_id="nolicense-dev")
        self.assertEqual(login_resp.status_code, 200)
        self.assertEqual(login_resp.get_json()["license"]["status"], "none")

        freeze_resp = client.post(
            "/api/admin/licenses/freeze",
            headers={"X-Admin-Secret": "admin-secret"},
            json={"email": "nolicense@example.com", "freeze": True},
        )
        self.assertEqual(freeze_resp.status_code, 404)
        self.assertEqual(freeze_resp.get_json()["error"], "license not found")


if __name__ == "__main__":
    unittest.main()
