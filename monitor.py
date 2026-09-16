"""后台保活与断线重连运行器。"""

import ctypes
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from config_manager import ConfigManager
from drcom_api import EPortalAPI


_mutex_handle: Optional[int] = None


def _log_dir() -> Path:
    path = Path(__file__).resolve().parent / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger("campusnet.monitor")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        _log_dir() / "monitor.log",
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _acquire_single_instance() -> bool:
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, False, "Local\\CampusNetLoginMonitor")
    if not _mutex_handle:
        return False
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        return False
    return True


def _console(message: str):
    if sys.stdout is not None:
        try:
            print(message, flush=True)
        except Exception:
            pass


def run_monitor(once: bool = False) -> int:
    logger = _configure_logger()
    if not once and not _acquire_single_instance():
        logger.info("监控进程已存在，本次启动退出")
        return 0

    config = ConfigManager()
    api = EPortalAPI(
        portal_ip=config.portal_ip,
        portal_port=config.portal_port,
        provider=config.provider,
    )
    base_interval = max(15, config.reconnect_interval)
    delay = base_interval
    failures = 0

    logger.info("监控启动，Portal=%s:%s", config.portal_ip, config.portal_port)
    while True:
        if not config.has_credentials():
            logger.warning("尚未保存账号密码，等待用户在 GUI 中完成配置")
            _console("CampusNetLogin: 尚未保存账号，请先运行配置界面")
            if once:
                return 1
            delay = base_interval
        else:
            status = api.detect_network_status()
            if status.online:
                logger.info("网络在线，无需认证")
                _console("CampusNetLogin: 已在线")
                failures = 0
                delay = base_interval
                if once:
                    return 0
            elif status.need_login:
                result = api.login(
                    config.username, config.password, config.service
                )
                if result.success:
                    logger.info("认证成功: %s", result.message)
                    _console(f"CampusNetLogin: {result.message}")
                    failures = 0
                    delay = base_interval
                    if once:
                        return 0
                else:
                    failures += 1
                    logger.error("认证失败: %s", result.message)
                    _console(f"CampusNetLogin: 认证失败: {result.message}")
                    delay = min(300, base_interval * (2 ** min(failures, 4)))
                    if once:
                        return 1
            else:
                failures += 1
                logger.warning("校园网不可达: %s", status.message)
                _console(f"CampusNetLogin: 校园网不可达: {status.message}")
                delay = min(300, base_interval * (2 ** min(failures, 4)))
                if once:
                    return 1

        time.sleep(delay)
