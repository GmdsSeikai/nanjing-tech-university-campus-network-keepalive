"""南京工业大学 Dr.COM 门户认证及连通性检测。"""

import base64
import ipaddress
import json
import random
import re
import socket
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlsplit

import requests


SERVICE_SUFFIXES = {"校园用户": "", "校园电信": "@dx", "校园联通": "@lt",
                    "校园其他": "", "中国移动": "@cmcc"}
SERVICE_ALIASES = {"": "校园用户", "默认": "校园用户", "校园网": "校园用户"}
PORTAL_ENTRY = "http://a.njtech.edu.cn/"
PROBES = (("http://www.msftconnecttest.com/connecttest.txt", "microsoft"),
          ("http://captive.apple.com/hotspot-detect.html", "apple"),
          ("http://connect.rom.miui.com/generate_204", "204"))


class OperationCancelled(Exception):
    pass


@dataclass
class AuthContext:
    user_ip: str
    user_mac: str
    page_url: str
    api_url: str
    user_ipv6: str = ""
    ac_ip: str = ""
    ac_name: str = ""
    vlan: str = "1"
    ssid: str = ""
    area_id: str = ""
    ap_mac: str = "000000000000"
    gw_id: str = "000000000000"
    gw_port: str = ""
    gw_address: str = ""
    config: dict = field(default_factory=dict)

    def identity_params(self):
        return {"wlan_user_ip": self.user_ip, "wlan_user_mac": self.user_mac,
                "wlan_user_ipv6": self.user_ipv6, "wlan_ac_ip": self.ac_ip,
                "wlan_ac_name": self.ac_name, "gw_id": self.gw_id,
                "gw_port": self.gw_port, "gw_address": self.gw_address}


@dataclass
class LoginResult:
    success: bool = False
    message: str = ""
    user_index: str = ""
    keepalive_interval: int = 0
    raw: dict = field(default_factory=dict)
    permanent_error: bool = False
    status: Optional["NetworkStatus"] = None


@dataclass
class NetworkStatus:
    online: bool = False
    need_login: bool = False
    portal_ip: str = ""
    redirect_url: str = ""
    user_index: str = ""
    message: str = ""
    debug_log: list = field(default_factory=list)
    state: str = "unknown"
    context: Optional[AuthContext] = None

    def __post_init__(self):
        if self.online:
            self.state = "online"
        elif self.need_login:
            self.state = "need_login"


class EPortalAPI:
    def __init__(self, portal_ip="10.255.20.10", portal_port=801, timeout=8,
                 *, provider="drcom_new", session=None, router_mode=False):
        self.portal_ip = portal_ip
        self.portal_port = int(portal_port)
        self.timeout = timeout
        self.provider = provider
        self.router_mode = router_mode
        self.base_url = f"http://{portal_ip}:{self.portal_port}"
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Accept": "application/javascript,text/html,*/*;q=0.8"})
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._context = None

    def cancel(self):
        self._stop.set()

    def close(self):
        with self._lock:
            self.session.close()

    def _check_cancelled(self, cancelled=None):
        if self._stop.is_set() or (cancelled and cancelled()):
            raise OperationCancelled("操作已取消")

    def _get(self, url, **kwargs):
        self._check_cancelled()
        return self.session.get(url, timeout=self.timeout, **kwargs)

    def detect_network_status(self):
        with self._lock:
            status = NetworkStatus(portal_ip=self.portal_ip)
            redirected = ""
            responses = 0
            for url, kind in PROBES:
                try:
                    response = self._get(url, allow_redirects=False)
                    responses += 1
                    status.debug_log.append(f"连通性探测 {kind}: HTTP {response.status_code}")
                    if self._probe_matches(response, kind):
                        status.online, status.state = True, "online"
                        status.message = "外网连通性验证通过"
                        return status
                    location = urljoin(url, response.headers.get("Location", ""))
                    if response.status_code in (301, 302, 303, 307, 308) and self._is_portal(location):
                        redirected = location
                except requests.RequestException as exc:
                    status.debug_log.append(f"连通性探测 {kind} 失败: {type(exc).__name__}")
            try:
                context = self.refresh_context(status.debug_log, redirect_url=redirected)
                status.context = context
                payload = self._query_online(context)
                value = str(payload.get("result", ""))
                if value == "1":
                    status.state = "network_error"
                    status.message = "门户确认已认证，但外网探测失败；原因未知"
                    return status
                if value == "0" and self._explicit_offline(payload):
                    status.need_login, status.state = True, "need_login"
                    status.message = "门户确认当前出口未认证"
                else:
                    status.message = "外网探测失败，门户认证状态未知"
            except OperationCancelled:
                raise
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                status.debug_log.append(f"认证上下文/状态查询失败: {self._safe_error(exc)}")
                status.state = "unknown" if responses else "network_error"
                status.message = "无法确定认证状态；" + self._safe_error(exc)
            if redirected:
                status.need_login, status.state = True, "need_login"
                status.redirect_url = redirected
                status.message = "外网探测被重定向到校园认证门户"
            return status

    @staticmethod
    def _probe_matches(response, kind):
        if kind == "204":
            return response.status_code == 204 and not response.text.strip()
        if response.status_code != 200:
            return False
        if kind == "microsoft":
            return response.text.strip() == "Microsoft Connect Test"
        return bool(re.search(r"<title>\s*Success\s*</title>", response.text, re.I)
                    and re.search(r"<body>\s*Success\s*</body>", response.text, re.I))

    def _is_portal(self, url):
        parsed = urlsplit(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in {
            self.portal_ip.lower(), "a.njtech.edu.cn"}

    @staticmethod
    def _explicit_offline(payload):
        msg = str(payload.get("msg", "")).lower()
        return payload.get("online") is False or any(word in msg for word in (
            "未登录", "未认证", "不在线", "没有在线", "无在线", "未在线",
            "not online", "not logged", "no online", "用户在线信息不存在"))

    def refresh_context(self, debug=None, *, redirect_url=""):
        self._context = None
        if self.router_mode:
            response = self._get(PORTAL_ENTRY, allow_redirects=True)
            response.raise_for_status()
            if not self._is_portal(response.url):
                raise ValueError("宿舍门户重定向到未知地址")
            context = self._parse_context(response.url, response.text, self.portal_port)
            if redirect_url and self._is_portal(redirect_url):
                redirected = self._parse_context(redirect_url, response.text, self.portal_port)
                context.user_ip = redirected.user_ip or context.user_ip
                context.user_mac = redirected.user_mac or context.user_mac
                for key in ("ac_ip", "ac_name", "vlan", "ssid", "area_id", "ap_mac",
                            "gw_id", "gw_port", "gw_address", "user_ipv6"):
                    setattr(context, key, getattr(redirected, key))
            online = self._request_jsonp_url(urljoin(response.url, "/drcom/chkstatus"),
                                             {"callback": "dr1003"})
            self._merge_identity(context, online)
            self._validate_identity(context)
        else:
            context = AuthContext(self._get_local_ip(), self._get_local_mac(),
                                  f"http://{self.portal_ip}/", self.base_url)
        config = self._load_portal_config(context.user_ip, context.user_mac,
                                          debug, context=context)
        for key in ("login_method", "program_index", "page_index"):
            if key not in config or config[key] is None or not str(config[key]).strip():
                raise ValueError(f"门户配置缺少 {key}，未提交认证")
        if self.router_mode:
            for key in ("account_prefix", "check_online_method"):
                if str(config.get(key)) not in ("0", "1"):
                    raise ValueError(f"门户配置缺少有效 {key}，未提交认证")
        if str(config["login_method"]) != "1":
            raise ValueError("当前认证方式尚不支持，未提交认证")
        if str(config.get("en_md5", "0")) != "0":
            raise ValueError("当前门户要求额外密码处理，未提交认证")
        context.config = config
        self._context = context
        if debug is not None:
            debug.append(f"认证出口 IP={context.user_ip}, MAC={context.user_mac}")
        return context

    @staticmethod
    def _parse_context(page_url, text, port=801):
        scripts = "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.I | re.S))
        pattern = r"'[^'\n]*'|\"[^\"\n]*\"|//[^\n]*|/\*.*?\*/"
        scripts = re.sub(pattern, lambda m: "" if m.group(0).startswith(("//", "/*")) else m.group(0),
                         scripts, flags=re.S)
        variables = {}
        for match in re.finditer(r"\b([a-zA-Z_][\w]*)\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|(\d+))", scripts):
            variables[match.group(1).lower()] = next(v for v in match.groups()[1:] if v is not None)
        query = {k.lower(): v[-1] for k, v in parse_qs(urlsplit(page_url).query).items()}

        def value(keys, defaults=(), fallback=""):
            for key in keys:
                if query.get(key.lower()):
                    return query[key.lower()]
            for key in defaults:
                if variables.get(key.lower()):
                    return variables[key.lower()]
            return fallback

        user_ip = value(("wlan_user_ip", "ip", "wlanuserip", "userip", "user-ip", "client_ip", "uip", "station_ip"),
                        ("v46ip", "ss5", "v4ip"))
        if not user_ip and variables.get("ss3"):
            try:
                user_ip = str(ipaddress.IPv4Address(int(variables["ss3"], 16)))
            except ValueError:
                pass
        parsed = urlsplit(page_url)
        api_port = variables.get("ephttpport", str(port))
        context = AuthContext(user_ip, value(("wlan_user_mac", "mac", "usermac", "wlanusermac", "umac", "client_mac", "station_mac"),
                                             ("ss4", "olmac")),
                              page_url, f"{parsed.scheme}://{parsed.hostname}:{api_port}")
        context.ac_ip = value(("wlan_ac_ip", "wlanacip", "acip", "switchip", "nasip", "nas-ip"))
        context.ac_name = value(("wlan_ac_name", "wlanacname", "sysname", "nasname", "nas-name"))
        context.user_ipv6 = value(("wlan_user_ipv6", "userv6ip"), ("v6ip",))
        context.vlan = value(("wlan_vlan_id", "vlan", "vlanid"), ("vlanid",), "1")
        context.ssid = value(("ssid", "essid"))
        context.area_id = value(("areaid",))
        context.ap_mac = value(("apmac", "ap_mac"), fallback="000000000000")
        context.gw_id = value(("gw_id", "gw_mac"), fallback="000000000000")
        context.gw_port = value(("gw_port",))
        context.gw_address = value(("gw_address",))
        return context

    @staticmethod
    def _merge_identity(context, payload):
        def valid_ip(value):
            try:
                return not ipaddress.IPv4Address(value).is_unspecified
            except ValueError:
                return False
        if not valid_ip(context.user_ip):
            for key in ("v46ip", "v4ip", "wlan_user_ip"):
                if valid_ip(str(payload.get(key, ""))):
                    context.user_ip = str(payload[key])
                    break
        else:
            for key in ("v46ip", "v4ip", "wlan_user_ip"):
                candidate = str(payload.get(key, ""))
                if valid_ip(candidate) and candidate != context.user_ip:
                    raise ValueError("门户页面与在线查询的出口 IP 不一致，等待刷新")
        mac = re.sub(r"[:-]", "", context.user_mac).upper()
        if not re.fullmatch(r"[0-9A-F]{12}", mac) or mac in ("000000000000", "111111111111"):
            for key in ("ss4", "olmac", "wlan_user_mac"):
                candidate = re.sub(r"[:-]", "", str(payload.get(key, ""))).upper()
                if re.fullmatch(r"[0-9A-F]{12}", candidate) and candidate not in ("000000000000", "111111111111"):
                    mac = candidate
                    break
        context.user_mac = mac

    @staticmethod
    def _validate_identity(context):
        try:
            ip = ipaddress.IPv4Address(context.user_ip)
            if ip.is_unspecified or ip.is_loopback or ip.is_multicast:
                raise ValueError()
        except ValueError:
            raise ValueError("门户未提供有效出口 IP，未提交认证") from None
        if not re.fullmatch(r"[0-9A-F]{12}", context.user_mac) or context.user_mac in ("000000000000", "111111111111"):
            raise ValueError("门户未提供有效出口 MAC，未提交认证")

    def _query_online(self, context):
        if str(context.config.get("check_online_method", "0")) == "1":
            ip = int(ipaddress.IPv4Address(context.user_ip))
            return self._request_jsonp("/eportal/portal/online_list", {
                "callback": "dr1003", "user_account": "", "user_password": "",
                "wlan_user_mac": context.user_mac, "wlan_user_ip": ip,
                "curr_user_ip": ip, "jsVersion": "4.X"}, context=context)
        return self._request_jsonp_url(urljoin(context.page_url, "/drcom/chkstatus"),
                                       {"callback": "dr1003"})

    def login(self, username, password, service="", force_relogin=False, *, cancelled=None):
        with self._lock:
            result, debug = LoginResult(), []
            if not username or not password:
                return LoginResult(message="账号或密码为空")
            try:
                self._check_cancelled(cancelled)
                status = self.detect_network_status()
                if status.online and not force_relogin:
                    return LoginResult(success=True, message="当前已在线，无需重复认证", status=status)
                if not status.need_login and not (force_relogin and status.online):
                    return LoginResult(message=status.message, status=status)
                context = self.refresh_context(debug, redirect_url=status.redirect_url)
                self._check_cancelled(cancelled)
                if force_relogin:
                    if not self._logout_context(context):
                        return LoginResult(message="旧会话注销失败，未重新认证")
                    context = self.refresh_context(debug)
                self._check_cancelled(cancelled)
                params = context.identity_params()
                params.update({"callback": "dr1003", "login_method": str(context.config["login_method"]),
                    "user_account": self._account_for_service(username, service, context.config),
                    "user_password": password, "program_index": context.config["program_index"],
                    "page_index": context.config["page_index"], "jsVersion": "4.X",
                    "terminal_type": "1", "lang": "zh-cn", "v": str(random.randint(500, 9999))})
                payload = self._request_jsonp("/eportal/portal/login", params, context=context)
                message = self._redact(str(payload.get("msg") or payload.get("message") or "认证失败"), password)
                if str(payload.get("result", "")) in ("1", "ok", "success"):
                    self._check_cancelled(cancelled)
                    status = self.detect_network_status()
                    result.status, result.success = status, status.online
                    result.message = "认证后外网连通性验证通过" if status.online else "门户接受认证，但外网尚未恢复；原因未知"
                else:
                    result.message, result.permanent_error = message, self._permanent_error(message)
            except OperationCancelled:
                result.message = "认证操作已取消"
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                result.message = self._redact(self._safe_error(exc), password)
            result.raw = {"_debug_log": debug}
            return result

    @staticmethod
    def _redact(message, password):
        message = re.sub(r"https?://\S+", "[请求地址已隐藏]", message)
        return message.replace(password, "[密码已隐藏]") if password else message

    @staticmethod
    def _permanent_error(message):
        return any(word in message.lower() for word in (
            "密码错误", "密码不正确", "账号停用", "账户停用", "账号禁用", "用户被禁用",
            "账号停机", "账户停机", "欠费", "套餐", "账号不存在", "账户不存在",
            "账号状态异常", "账户状态异常", "password error", "incorrect password",
            "invalid password", "account disabled", "account suspended"))

    def keepalive(self, user_index=""):
        return self.detect_network_status().online

    def logout(self, user_index=""):
        return self.logout_by_ip()

    def logout_by_ip(self):
        with self._lock:
            try:
                return self._logout_context(self.refresh_context())
            except (requests.RequestException, ValueError, RuntimeError, OperationCancelled):
                return False

    def _logout_context(self, context):
        params = context.identity_params()
        params.update({"callback": "dr1003", "user_account": "",
                       "login_method": context.config["login_method"],
                       "ac_logout": context.config.get("ac_logout", "0"),
                       "jsVersion": "4.X", "v": str(random.randint(500, 9999))})
        payload = self._request_jsonp("/eportal/portal/logout", params, context=context)
        return str(payload.get("result", "")) in ("1", "ok", "success")

    def get_security_status(self, user_index):
        return {"error": "当前 Dr.COM 新版门户不支持该安全查询"}

    def cancel_mac(self, user_index):
        return {"result": "fail", "message": "当前门户不支持该操作"}

    def cancel_mac_for_device(self, user_id, user_mac):
        return {"result": "fail", "message": "当前门户不支持该操作"}

    def force_offline_device(self, user_id, user_mac):
        return {"result": "fail", "message": "当前门户不支持该操作"}

    def _load_portal_config(self, local_ip, local_mac, debug=None, *, context=None):
        params = {"callback": "dr1003", "program_index": "",
                  "wlan_vlan_id": context.vlan if context else "1",
                  "wlan_user_ip": self._base64_text(local_ip),
                  "wlan_user_ipv6": self._base64_text(context.user_ipv6) if context else "",
                  "wlan_user_ssid": context.ssid if context else "",
                  "wlan_user_areaid": context.area_id if context else "",
                  "wlan_ac_ip": self._base64_text(context.ac_ip) if context else "",
                  "wlan_ap_mac": context.ap_mac if context else "000000000000",
                  "gw_id": context.gw_id if context else "000000000000",
                  "wlan_user_mac": local_mac, "jsVersion": "4.X"}
        payload = self._request_jsonp("/eportal/portal/page/loadConfig", params, context=context)
        if str(payload.get("code")) != "1" or not isinstance(payload.get("data"), dict):
            raise ValueError("门户配置加载失败，未提交认证")
        if debug is not None:
            debug.append("门户配置加载成功")
        return payload["data"]

    def _request_jsonp(self, path, params, *, context=None):
        return self._request_jsonp_url((context.api_url if context else self.base_url) + path, params)

    def _request_jsonp_url(self, url, params):
        response = self._get(url, params=params, allow_redirects=False)
        response.raise_for_status()
        return self._parse_jsonp(response.text)

    @staticmethod
    def _safe_error(exc):
        if isinstance(exc, requests.RequestException):
            return f"网络请求失败 ({type(exc).__name__})"
        return EPortalAPI._redact(str(exc), "")

    @staticmethod
    def _parse_jsonp(text):
        cleaned = text.lstrip("\ufeff").strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.match(r"^[^(]*\(\s*(\{.*\})\s*\)\s*;?\s*$", cleaned, re.S)
            if not match:
                raise ValueError("门户返回了无法解析的响应") from None
            value = json.loads(match.group(1))
        if not isinstance(value, dict):
            raise ValueError("门户响应不是对象")
        return value

    @staticmethod
    def _base64_text(value):
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _account_for_service(username, service, config=None):
        key = SERVICE_ALIASES.get((service or "").strip(), (service or "").strip())
        if key not in SERVICE_SUFFIXES:
            raise ValueError("未知认证服务")
        account = re.sub(r"^,(?:0|1|a|b),", "", username.strip())
        suffix = SERVICE_SUFFIXES[key]
        if suffix and not account.lower().endswith(suffix):
            if "@" in account:
                raise ValueError("账号后缀与所选服务不一致，请填写原始学号")
            account += suffix
        prefix = ""
        if config and str(config.get("account_prefix", "0")) == "1":
            prefix = ",b," if str(config.get("custom_perceive", "0")) == "1" else ",0,"
        return prefix + account

    def _get_local_ip(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((self.portal_ip, self.portal_port))
            return sock.getsockname()[0]
        finally:
            sock.close()

    @staticmethod
    def _get_local_mac():
        return f"{uuid.getnode():012X}"
