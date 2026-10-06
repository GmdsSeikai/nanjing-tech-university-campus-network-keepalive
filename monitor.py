"""GUI 与 --monitor 共用的串行保活、认证和注销运行器。"""

import ctypes
import logging
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config_manager import ConfigManager
from drcom_api import EPortalAPI, LoginResult, NetworkStatus, OperationCancelled


class SingleInstance:
    """同一 Windows 会话内所有桌面/后台入口共用一个命名 mutex。"""

    def __init__(self):
        self.handle = None

    def acquire(self):
        if self.handle:
            return True
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, False, "Local\\CampusNetLoginMonitor")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            kernel.CloseHandle(self.handle)
            self.handle = None


def _configure_logger():
    logger = logging.getLogger("campusnet.monitor")
    if not logger.handlers:
        directory = Path.home() / ".campus_net_login" / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / "monitor.log", maxBytes=1024 * 1024,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


@dataclass(frozen=True)
class MonitorSettings:
    username: str = ""
    password: str = ""
    service: str = "校园用户"
    auto_maintain: bool = True
    router_mode: bool = False
    portal_ip: str = "10.255.20.10"
    portal_port: int = 801

    @classmethod
    def from_config(cls, config):
        return cls(config.username, config.password, config.service,
                   config.auto_maintain, config.router_mode,
                   config.portal_ip, config.portal_port)


class MaintenanceMonitor:
    INTERVAL = 30
    BACKOFF = (30, 60, 120, 240, 300)

    def __init__(self, api, settings, callback=None, logger=None, clock=time.monotonic):
        self.api, self.settings = api, settings
        self.callback, self.logger, self.clock = callback, logger, clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._jobs = queue.Queue()
        self._state_lock = threading.Lock()
        self._thread = None
        self._generation = 0
        self._applied_generation = 0
        self.paused_reason = None
        self.failures = 0
        self._offline_since = None
        self.last_status = NetworkStatus(message="尚未检测")
        self._apply_settings(settings)

    def _apply_settings(self, settings):
        self.settings = settings
        self.api.router_mode = settings.router_mode
        self.api.portal_ip = settings.portal_ip
        self.api.portal_port = settings.portal_port
        self.api.base_url = f"http://{settings.portal_ip}:{settings.portal_port}"

    def _emit(self, kind, value):
        if not self._stop.is_set() and self.callback:
            self.callback(kind, value)

    def _log(self, message, level="info"):
        if self.logger:
            getattr(self.logger, "warning" if level == "warn" else "info")(message)
        self._emit("log", (message, level))

    def _status(self, status):
        self.last_status = status
        if self.logger and not status.online:
            for line in status.debug_log:
                self.logger.info(line)
        self._emit("status", status)

    def start(self):
        with self._state_lock:
            if self._stop.is_set():
                raise RuntimeError("已停止的监控器不能重启")
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="campusnet-monitor", daemon=False)
            self._thread.start()

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    def configure(self, settings):
        with self._state_lock:
            self._generation += 1
            generation = self._generation
        self._submit("configure", settings, generation)

    def pause(self):
        with self._state_lock:
            self._generation += 1
            self.paused_reason = "manual"
        self._wake.set()

    def request_login(self, username, password, service, force_relogin=False):
        with self._state_lock:
            self._generation += 1
            generation = self._generation
        self._submit("login", (username, password, service, force_relogin), generation)

    def request_logout(self):
        self.pause()
        self._submit("logout", None, self._generation)

    def request_check(self):
        self._submit("check", None, self._generation)

    def _submit(self, kind, value, generation):
        if self._stop.is_set():
            return
        self._jobs.put((kind, value, generation))
        self._wake.set()

    def request_stop(self):
        self._stop.set()
        self.api.cancel()
        self._wake.set()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def _cancelled(self, generation):
        return self._stop.is_set() or generation != self._generation

    def _failed(self):
        delay = self.BACKOFF[min(self.failures, len(self.BACKOFF) - 1)]
        self.failures += 1
        self._log(f"状态={self.last_status.state}；{delay} 秒后重新检测", "warn")
        return delay

    def _online(self):
        if self._offline_since is not None:
            self._log(f"外网恢复，耗时 {self.clock() - self._offline_since:.1f} 秒", "ok")
        self._offline_since, self.failures = None, 0
        return self.INTERVAL

    def step(self, force=False):
        """一个自动检测周期；认证前后检查本周期是否被暂停/配置变更取消。"""
        generation = self._generation
        if generation != self._applied_generation:
            return self.INTERVAL
        if self._stop.is_set() or (not force and not self.settings.auto_maintain):
            return self.INTERVAL
        if self.paused_reason == "manual":
            return self.INTERVAL
        status = self.api.detect_network_status()
        if self._cancelled(generation):
            return self.INTERVAL
        self._status(status)
        if status.online:
            return self._online()
        if self._offline_since is None:
            self._offline_since = self.clock()
            self._log(f"发现离线 {datetime.now().isoformat(timespec='seconds')}，"
                      f"认证状态={status.state}，原因未知", "warn")
        if (status.need_login and self.paused_reason != "permanent"
                and self.settings.username and self.settings.password):
            self._log("确认需要认证，尝试自动恢复", "warn")
            result = self.api.login(self.settings.username, self.settings.password,
                                    self.settings.service,
                                    cancelled=lambda: self._cancelled(generation))
            if self._cancelled(generation):
                return self.INTERVAL
            self._log(result.message, "ok" if result.success else "warn")
            if result.status:
                self._status(result.status)
            self._emit("login", result)
            if result.success:
                return self._online()
            if result.permanent_error:
                with self._state_lock:
                    if not self._cancelled(generation):
                        self.paused_reason = "permanent"
                self._log("账号错误：自动认证已暂停，修改配置或手动登录可恢复", "warn")
        return self._failed()

    def _job(self, kind, value, generation):
        if kind == "configure":
            self._apply_settings(value)
            with self._state_lock:
                self._applied_generation = generation
                if self.paused_reason == "permanent":
                    self.paused_reason = None
            self.failures = 0
            self._log("监控配置已更新")
        elif kind == "check":
            self._status(self.api.detect_network_status())
        elif kind == "logout":
            self._emit("logout", self.api.logout_by_ip())
        elif kind == "login":
            with self._state_lock:
                cancelled = self._cancelled(generation)
                if not cancelled:
                    self.paused_reason = None
                    self._applied_generation = generation
            if cancelled:
                self._emit("login", LoginResult(message="认证操作已取消"))
                return
            result = self.api.login(*value[:3], force_relogin=value[3],
                                    cancelled=lambda: self._cancelled(generation))
            with self._state_lock:
                cancelled = self._cancelled(generation)
                if not cancelled and result.permanent_error:
                    self.paused_reason = "permanent"
            if cancelled:
                result = LoginResult(message="认证操作已取消")
            if result.status:
                self._status(result.status)
            self._emit("login", result)

    def _run(self):
        deadline = self.clock()
        self._log("自动保活与重连监控器启动")
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    kind, value, generation = self._jobs.get_nowait()
                except queue.Empty:
                    kind = None
                try:
                    if kind:
                        self._job(kind, value, generation)
                        deadline = self.clock() + (0 if kind == "configure" else self.INTERVAL)
                        continue
                    if self.clock() >= deadline:
                        delay = self.step()
                        deadline = self.clock() + delay
                except OperationCancelled:
                    if self._stop.is_set():
                        break
                except Exception as exc:
                    # 不输出异常内容，requests 异常可包含密码 URL。
                    self._log(f"监控操作失败 ({type(exc).__name__})，认证状态未知", "warn")
                    self._status(NetworkStatus(message="监控操作失败，认证状态未知"))
                    if kind == "login":
                        self._emit("login", LoginResult(message="认证操作失败，详情见监控日志"))
                    elif kind == "logout":
                        self._emit("logout", False)
                    deadline = self.clock() + self._failed()
                self._wake.wait(max(0, deadline - self.clock()))
        finally:
            self.api.close()


def run_monitor(once=False, *, locked=False):
    instance = SingleInstance()
    if not locked and not instance.acquire():
        return 2
    monitor = None
    try:
        config = ConfigManager()
        api = EPortalAPI(portal_ip=config.portal_ip, portal_port=config.portal_port,
                         provider=config.provider, router_mode=config.router_mode)
        def callback(kind, value):
            if kind == "log" and sys.stdout:
                print(value[0], flush=True)
            elif kind == "status" and sys.stdout:
                print(f"CampusNetLogin: {value.state}: {value.message}", flush=True)
        monitor = MaintenanceMonitor(api, MonitorSettings.from_config(config), callback, _configure_logger())
        if once:
            try:
                monitor.step(force=True)
                return 0 if monitor.last_status.online else 1
            finally:
                api.close()
        monitor.start()
        try:
            while monitor.is_running():
                monitor.join(0.5)
        except KeyboardInterrupt:
            monitor.request_stop()
            monitor.join()
        return 0
    finally:
        if monitor and monitor.is_running():
            monitor.request_stop()
            monitor.join()
        if not locked:
            instance.release()
