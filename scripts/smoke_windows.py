"""用临时空账号配置验证真实 EXE 的 GUI、托盘、退出和双入口锁。"""

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]


kernel = ctypes.WinDLL('kernel32', use_last_error=True)
user = ctypes.WinDLL('user32', use_last_error=True)
kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user.IsWindowVisible.argtypes = [wintypes.HWND]
user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user.FindWindowExW.restype = wintypes.HWND


def children(parent):
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    links = {}
    try:
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            links[entry.pid] = entry.parent
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    found = {parent}
    while True:
        expanded = found | {pid for pid, ppid in links.items() if ppid in found}
        if expanded == found:
            return found
        found = expanded


def windows(parent):
    pids = children(parent)
    found = []
    @callback_type
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(hwnd, name, len(name))
            found.append((hwnd, name.value))
        return True
    user.EnumWindows(callback, 0)
    message_window = None
    while True:
        message_window = user.FindWindowExW(wintypes.HWND(-3), message_window, None, None)
        if not message_window:
            break
        callback(message_window, 0)
    return found


def await_condition(condition, message, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        threading.Event().wait(0.05)
    raise AssertionError(message)


def main():
    exe = Path(sys.argv[1] if len(sys.argv) > 1 else 'release/CampusNetLogin.exe').resolve()
    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive('PYZ.pyz')
    assert {'drcom_api', 'monitor', 'config_manager', 'tray_icon'} <= set(pyz.toc)
    files = {key.replace('\\', '/') for key in archive.toc}
    assert {'_tk_data/tk.tcl', '_tcl_data/init.tcl', '_tkinter.pyd'} <= files
    print('Archive modules and Tcl/Tk data: PASS')
    with tempfile.TemporaryDirectory(prefix='campusnet-package-') as directory:
        env = os.environ.copy()
        env['USERPROFILE'] = directory
        config_dir = Path(directory) / '.campus_net_login'
        config_dir.mkdir()
        config_file = config_dir / 'config.json'
        config_file.write_text(json.dumps({'username': '', 'password_encrypted': '',
                                          'router_mode': True, 'auto_maintain': False,
                                          'service': '中国移动'}), encoding='utf-8')
        result = subprocess.run([str(exe), '--once'], env=env, capture_output=True, timeout=60)
        assert result.returncode == 0, f'--once did not verify online: {result.returncode}'
        assert not json.loads(config_file.read_text(encoding='utf-8'))['password_encrypted']
        print('Packaged --once, real read-only connectivity, isolated empty credentials: PASS')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen([str(exe)], env=env, startupinfo=startup)
        try:
            def top_window():
                assert process.poll() is None, f'GUI exited early: {process.returncode}'
                return next((hwnd for hwnd, cls in windows(process.pid) if cls == 'TkTopLevel'), None)
            hwnd = await_condition(top_window, 'Tk GUI did not start')
            for mode in ('--monitor', '--once'):
                result = subprocess.run([str(exe), mode], env=env, capture_output=True, timeout=20)
                assert result.returncode == 2, f'Duplicate {mode} lock not rejected'
            print('Packaged GUI and shared lock against --monitor/--once: PASS')
            user.PostMessageW(hwnd, 0x0010, 0, 0)
            await_condition(lambda: not user.IsWindowVisible(hwnd), 'Window did not hide to tray')
            tray = await_condition(lambda: next((h for h, cls in windows(process.pid)
                                  if cls.startswith('CampusNetLoginTray_')), None), 'Tray window not created')
            user.PostMessageW(tray, 0x0111, 1001, 0)
            await_condition(lambda: user.IsWindowVisible(hwnd), 'Tray restore did not show GUI')
            user.PostMessageW(hwnd, 0x0010, 0, 0)
            await_condition(lambda: not user.IsWindowVisible(hwnd), 'Second close did not hide GUI')
            user.PostMessageW(tray, 0x0111, 1002, 0)
            assert process.wait(timeout=20) == 0, 'Tray exit failed'
            assert not windows(process.pid), 'Owned windows remain after exit'
            print('Packaged close-to-tray, restore, second close, exit and cleanup: PASS')
        finally:
            if process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=10)
        result = subprocess.run([str(exe), '--once'], env=env, capture_output=True, timeout=60)
        assert result.returncode == 0, 'Mutex was not released on tray exit'
        print('Packaged restart after exit: PASS')
    print('EXE bytes:', exe.stat().st_size)
    print('EXE SHA256:', hashlib.sha256(exe.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
