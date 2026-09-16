"""南京工业大学 Dr.COM 4.0 新版门户适配。"""

import base64
import json
import random
import re
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests


SERVICE_SUFFIXES = {
    "校园用户": "",
    "校园电信": "@dx",
    "校园联通": "@lt",
    "校园其他": "",
}

SERVICE_ALIASES = {
    "": "校园用户",
    "默认": "校园用户",
    "校园网": "校园用户",
}


@dataclass
class LoginResult:
    success: bool = False
    message: str = ""
    user_index: str = ""
    keepalive_interval: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class NetworkStatus:
    online: bool = False
    need_login: bool = False
    portal_ip: str = ""
    redirect_url: str = ""
    user_index: str = ""
    message: str = ""
    debug_log: list = field(default_factory=list)


class EPortalAPI:
    """适配当前南京工业大学 Dr.COM 4.0 门户的最小客户端。"""

    def __init__(
        self,
        portal_ip: str = "10.255.20.10",
        portal_port: int = 801,
        timeout: int = 8,
        *,
        provider: str = "drcom_new",
        session: Optional[requests.Session] = None,
    ):
        self.portal_ip = portal_ip
        self.portal_port = int(portal_port)
        self.timeout = timeout
        self.provider = provider
        self.base_url = f"http://{portal_ip}:{self.portal_port}"
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/javascript,text/javascript,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
        )

    def detect_network_status(self) -> NetworkStatus:
        status = NetworkStatus(portal_ip=self.portal_ip)
        checks = (
            ("http://www.msftconnecttest.com/connecttest.txt", "Microsoft Connect Test"),
            ("http://captive.apple.com/hotspot-detect.html", "Success"),
            ("http://connect.rom.miui.com/generate_204", None),
        )

        for url, expected_text in checks:
            try:
                response = self.session.get(
                    url, timeout=self.timeout, allow_redirects=False
                )
                status.debug_log.append(f"GET {url} -> HTTP {response.status_code}")
                if response.status_code in (301, 302):
                    location = response.headers.get("Location", "")
                    status.debug_log.append(f"重定向 -> {location[:160]}")
                    if self.portal_ip in location or "eportal" in location.lower():
                        status.need_login = True
                        status.redirect_url = location
                        status.message = "未登录，检测到校园网门户重定向"
                        return status

                expected_status = 204 if expected_text is None else 200
                if response.status_code == expected_status and (
                    expected_text is None or expected_text.lower() in response.text.lower()
                ):
                    status.online = True
                    status.message = "已联网"
                    return status
            except requests.RequestException as exc:
                status.debug_log.append(f"GET {url} 失败: {exc}")

        try:
            config = self._load_portal_config(self._get_local_ip(), "", status.debug_log)
            if config:
                status.need_login = True
                status.message = "未登录，门户可访问"
        except Exception as exc:
            status.debug_log.append(f"门户探测失败: {exc}")

        if not status.message:
            status.message = "无法访问外网或校园网门户"
        return status

    def login(
        self,
        username: str,
        password: str,
        service: str = "",
        force_relogin: bool = False,
    ) -> LoginResult:
        result = LoginResult()
        if not username or not password:
            result.message = "账号或密码为空"
            return result

        debug: list = []
        if not force_relogin:
            status = self.detect_network_status()
            debug.extend(status.debug_log)
            if status.online:
                result.success = True
                result.user_index = "dddddddddddddddddddddddd"
                result.message = "当前已在线，无需重复认证"
                result.raw = {"_debug_log": debug}
                return result
            if not status.need_login:
                result.message = status.message or "校园网暂不可达"
                result.raw = {"_debug_log": debug}
                return result

        try:
            local_ip = self._get_local_ip()
            local_mac = self._get_local_mac()
            config = self._load_portal_config(local_ip, local_mac, debug)
            account = self._account_for_service(username, service)
            payload = self._request_jsonp(
                "/eportal/portal/login",
                {
                    "callback": "dr1003",
                    "login_method": str(config.get("login_method") or "1"),
                    "user_account": account,
                    "user_password": password,
                    "wlan_user_ip": local_ip,
                    "wlan_user_ipv6": "",
                    "wlan_user_mac": local_mac,
                    "wlan_ac_ip": "",
                    "wlan_ac_name": "",
                    "program_index": config.get("program_index", ""),
                    "page_index": config.get("page_index", ""),
                    "jsVersion": "4.X",
                    "v": str(random.randint(500, 9999)),
                },
            )
            login_result = str(payload.get("result", ""))
            if login_result in ("1", "2", "ok", "success"):
                result.success = True
                result.user_index = "dddddddddddddddddddddddd"
                result.message = payload.get("msg") or "认证成功"
            else:
                result.message = payload.get("msg") or payload.get("message") or "认证失败"
            result.raw = {"response": payload, "_debug_log": debug}
        except requests.RequestException as exc:
            result.message = f"连接门户失败: {exc}"
            result.raw = {"_debug_log": debug}
        except Exception as exc:
            result.message = f"认证异常: {exc}"
            result.raw = {"_debug_log": debug}
        return result

    def keepalive(self, user_index: str) -> bool:
        del user_index
        return self.detect_network_status().online

    def logout(self, user_index: str) -> bool:
        del user_index
        return self.logout_by_ip()

    def logout_by_ip(self) -> bool:
        try:
            payload = self._request_jsonp(
                "/eportal/portal/logout",
                {
                    "callback": "dr1003",
                    "user_account": "",
                    "wlan_user_ip": self._get_local_ip(),
                    "jsVersion": "4.X",
                    "v": str(random.randint(500, 9999)),
                },
            )
            value = str(payload.get("result", ""))
            return value in ("1", "ok", "success")
        except Exception:
            return False

    def get_security_status(self, user_index: str) -> Dict[str, Any]:
        del user_index
        return {"error": "当前 Dr.COM 新版门户不支持该安全查询"}

    def cancel_mac(self, user_index: str) -> Dict[str, str]:
        del user_index
        return {"result": "fail", "message": "当前门户不支持该操作"}

    def cancel_mac_for_device(self, user_id: str, user_mac: str) -> Dict[str, str]:
        del user_id, user_mac
        return {"result": "fail", "message": "当前门户不支持该操作"}

    def force_offline_device(self, user_id: str, user_mac: str) -> Dict[str, str]:
        del user_id, user_mac
        return {"result": "fail", "message": "当前门户不支持该操作"}

    def _load_portal_config(
        self, local_ip: str, local_mac: str, debug: Optional[list] = None
    ) -> Dict[str, Any]:
        params = {
            "callback": "dr1003",
            "program_index": "",
            "wlan_vlan_id": "0",
            "wlan_user_ip": self._base64_text(local_ip),
            "wlan_user_ipv6": "",
            "wlan_user_ssid": "",
            "wlan_user_areaid": "",
            "wlan_ac_ip": "",
            "wlan_ap_mac": "",
            "gw_id": "",
            "wlan_user_mac": local_mac,
            "jsVersion": "4.X",
            "v": str(random.randint(500, 9999)),
        }
        payload = self._request_jsonp("/eportal/portal/page/loadConfig", params)
        if str(payload.get("code")) != "1" or not isinstance(payload.get("data"), dict):
            raise RuntimeError(payload.get("msg") or "门户配置加载失败")
        if debug is not None:
            debug.append("门户配置加载成功")
        return payload["data"]

    def _request_jsonp(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        response = self.session.get(
            self.base_url + path,
            params=params,
            timeout=self.timeout,
            allow_redirects=True,
            headers={"Referer": self.base_url + "/"},
        )
        response.raise_for_status()
        return self._parse_jsonp(response.text)

    @staticmethod
    def _parse_jsonp(text: str) -> Dict[str, Any]:
        cleaned = text.lstrip("\ufeff").strip()
        try:
            value = json.loads(cleaned)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

        match = re.match(r"^[^(]*\(\s*(\{.*\})\s*\)\s*;?\s*$", cleaned, re.S)
        if not match:
            raise ValueError("门户返回了无法解析的响应")
        value = json.loads(match.group(1))
        if not isinstance(value, dict):
            raise ValueError("门户响应不是对象")
        return value

    @staticmethod
    def _base64_text(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _account_for_service(username: str, service: str) -> str:
        key = (service or "").strip()
        key = SERVICE_ALIASES.get(key, key)
        if key not in SERVICE_SUFFIXES:
            key = "校园用户"
        return username + SERVICE_SUFFIXES[key]

    def _get_local_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((self.portal_ip, self.portal_port))
            return sock.getsockname()[0]
        finally:
            sock.close()

    @staticmethod
    def _get_local_mac() -> str:
        return f"{uuid.getnode():012X}"
