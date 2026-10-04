"""配置管理模块，使用 Windows DPAPI 保护本地密码。"""

import base64
import ctypes
import hashlib
import json
import os
import secrets
from ctypes import wintypes
from pathlib import Path
from typing import Optional


DPAPI_PREFIX = "dpapi:"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


crypt32 = ctypes.windll.crypt32
kernel32 = ctypes.windll.kernel32
crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(DATA_BLOB),
    wintypes.LPCWSTR,
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptProtectData.restype = wintypes.BOOL
crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptUnprotectData.restype = wintypes.BOOL
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p


def _blob_from_bytes(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    )
    return blob, buffer


def _dpapi_protect(data: bytes) -> bytes:
    in_blob, buffer = _blob_from_bytes(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    del buffer
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob, buffer = _blob_from_bytes(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    del buffer
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _get_config_dir() -> Path:
    config_dir = Path.home() / ".campus_net_login"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    encrypted = _dpapi_protect(plaintext.encode("utf-8"))
    return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")


def _legacy_key(config_dir: Path) -> bytes:
    salt_file = config_dir / ".salt"
    if salt_file.exists():
        salt = salt_file.read_bytes()
    else:
        salt = secrets.token_bytes(32)
    machine_info = f"{os.getlogin()}@{os.environ.get('COMPUTERNAME', 'unknown')}"
    return hashlib.pbkdf2_hmac("sha256", machine_info.encode(), salt, 100000)


def _legacy_decrypt(value: str, config_dir: Path) -> str:
    key = _legacy_key(config_dir)
    encrypted = base64.b64decode(value)
    data = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
    return data[16:].decode("utf-8")


def decrypt_value(ciphertext: str, config_dir: Optional[Path] = None) -> str:
    if not ciphertext:
        return ""
    try:
        if ciphertext.startswith(DPAPI_PREFIX):
            raw = base64.b64decode(ciphertext[len(DPAPI_PREFIX) :])
            return _dpapi_unprotect(raw).decode("utf-8")
        if config_dir is not None:
            return _legacy_decrypt(ciphertext, config_dir)
    except Exception:
        return ""
    return ""


class ConfigManager:
    """配置管理器，新密码使用 DPAPI，旧格式仅用于迁移读取。"""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = Path(config_dir) if config_dir else _get_config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self._config = self._load()

    def _default_config(self) -> dict:
        return {
            "provider": "drcom_new",
            "portal_ip": "10.255.20.10",
            "portal_port": 801,
            "username": "",
            "password_encrypted": "",
            "service": "校园用户",
            "auto_maintain": True,
            "router_mode": True,
            "reconnect_interval": 30,
            "start_minimized": True,
            "last_user_index": "",
        }

    def _load(self) -> dict:
        config = self._default_config()
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    config.update(loaded)
                    if "auto_maintain" not in loaded:
                        config["auto_maintain"] = bool(loaded.get("auto_keepalive", True)
                                                       or loaded.get("auto_reconnect", True))
                    if "router_mode" not in loaded:
                        config["router_mode"] = False
                    password = config.get("password_encrypted", "")
                    if password and not password.startswith(DPAPI_PREFIX):
                        legacy_plain = decrypt_value(password, self.config_dir)
                        if legacy_plain:
                            config["password_encrypted"] = encrypt_value(legacy_plain)
            except Exception:
                pass
        self._config = config
        self._save()
        return config

    def _save(self):
        temporary = self.config_file.with_suffix(".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self._config, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.config_file)

    @property
    def provider(self) -> str:
        return self._config.get("provider", "drcom_new")

    @provider.setter
    def provider(self, value: str):
        self._config["provider"] = value
        self._save()

    @property
    def portal_ip(self) -> str:
        return self._config.get("portal_ip", "10.255.20.10")

    @portal_ip.setter
    def portal_ip(self, value: str):
        self._config["portal_ip"] = value
        self._save()

    @property
    def portal_port(self) -> int:
        return int(self._config.get("portal_port", 801))

    @portal_port.setter
    def portal_port(self, value: int):
        self._config["portal_port"] = int(value)
        self._save()

    @property
    def username(self) -> str:
        return self._config.get("username", "")

    @username.setter
    def username(self, value: str):
        self._config["username"] = value
        self._save()

    @property
    def password(self) -> str:
        return decrypt_value(
            self._config.get("password_encrypted", ""), self.config_dir
        )

    @password.setter
    def password(self, value: str):
        self._config["password_encrypted"] = encrypt_value(value)
        self._save()

    @property
    def service(self) -> str:
        return self._config.get("service", "校园用户")

    @service.setter
    def service(self, value: str):
        self._config["service"] = value
        self._save()

    @property
    def auto_keepalive(self) -> bool:
        return self.auto_maintain

    @auto_keepalive.setter
    def auto_keepalive(self, value: bool):
        self.auto_maintain = value

    @property
    def auto_reconnect(self) -> bool:
        return self.auto_maintain

    @auto_reconnect.setter
    def auto_reconnect(self, value: bool):
        self.auto_maintain = value

    @property
    def auto_maintain(self) -> bool:
        return bool(self._config["auto_maintain"])

    @auto_maintain.setter
    def auto_maintain(self, value: bool):
        self._config["auto_maintain"] = bool(value)
        self._config["auto_keepalive"] = bool(value)
        self._config["auto_reconnect"] = bool(value)
        self._save()

    @property
    def router_mode(self) -> bool:
        return bool(self._config["router_mode"])

    @router_mode.setter
    def router_mode(self, value: bool):
        self._config["router_mode"] = bool(value)
        self._save()

    @property
    def reconnect_interval(self) -> int:
        return int(self._config.get("reconnect_interval", 30))

    @reconnect_interval.setter
    def reconnect_interval(self, value: int):
        self._config["reconnect_interval"] = max(15, int(value))
        self._save()

    @property
    def start_minimized(self) -> bool:
        return bool(self._config.get("start_minimized", True))

    @property
    def last_user_index(self) -> str:
        return self._config.get("last_user_index", "")

    @last_user_index.setter
    def last_user_index(self, value: str):
        self._config["last_user_index"] = value
        self._save()

    def has_credentials(self) -> bool:
        return bool(self.username and self.password)
