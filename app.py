"""
校园网一键登录工具 - GUI 主程序
适配南京工业大学 Dr.COM 门户
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
import sys
import os
from datetime import datetime
import ctypes
import urllib.request
import urllib.error
import json
import argparse
import queue

from drcom_api import EPortalAPI, LoginResult, NetworkStatus, SERVICE_SUFFIXES
from tray_icon import SystemTrayIcon
from config_manager import ConfigManager
from monitor import MaintenanceMonitor, MonitorSettings, SingleInstance, _configure_logger

# 确认服务器配置
CONFIRM_SERVER = ""
SERVER_TIMEOUT = 1
SERVER_RETRY = 2


def check_server_permission(operation: str) -> bool:
    """本地部署不启用外部确认服务。"""
    if not CONFIRM_SERVER:
        return True
    for attempt in range(SERVER_RETRY):
        try:
            data = json.dumps({"operation": operation}).encode('utf-8')
            req = urllib.request.Request(
                f"{CONFIRM_SERVER}/confirm",
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=SERVER_TIMEOUT) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result.get("allowed", False)
        except Exception:
            if attempt < SERVER_RETRY - 1:
                time.sleep(0.1)
            continue
    # 服务器无响应，默认允许（降级策略）
    return True


def is_admin():
    """检查是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """请求管理员权限重启程序"""
    try:
        if sys.argv[0].endswith('.py'):
            # Python 脚本
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{sys.argv[0]}"', None, 1
            )
        else:
            # 打包后的 exe
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )
        sys.exit(0)
    except Exception:
        sys.exit(1)


class StatusIndicator(tk.Canvas):
    """状态指示灯"""

    COLORS = {
        "online": "#22c55e",     # 绿色 - 在线
        "offline": "#ef4444",    # 红色 - 离线
        "checking": "#f59e0b",   # 黄色 - 检测中
        "connecting": "#3b82f6", # 蓝色 - 连接中
        "need_login": "#ef4444",
        "network_error": "#ef4444",
        "unknown": "#f59e0b",
    }

    def __init__(self, parent, size=16, **kwargs):
        super().__init__(parent, width=size, height=size,
                         highlightthickness=0, **kwargs)
        self.size = size
        self._state = "offline"
        self._draw()

    def _draw(self):
        self.delete("all")
        color = self.COLORS.get(self._state, "#6b7280")
        pad = 2
        self.create_oval(pad, pad, self.size - pad, self.size - pad,
                         fill=color, outline="")

    def set_state(self, state: str):
        self._state = state
        self._draw()


class CampusNetApp:
    """主应用程序"""

    def __init__(self):
        self.config = ConfigManager()
        self.api = EPortalAPI(
            portal_ip=self.config.portal_ip,
            portal_port=self.config.portal_port,
            provider=self.config.provider,
            router_mode=self.config.router_mode,
        )
        self._closing = False
        self._ui_events = queue.Queue()
        self.monitor = MaintenanceMonitor(
            self.api, MonitorSettings.from_config(self.config),
            self._monitor_event, _configure_logger(),
        )
        self.tray_icon = None
        self._tray_polling = False
        self.current_user_index = ""
        self._build_ui()

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("校园网一键登录")
        self.root.geometry("520x820")
        self.root.resizable(True, True)
        self.root.configure(bg="#f8fafc")
        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # 居中显示
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 480) // 2
        y = (self.root.winfo_screenheight() - 700) // 2
        self.root.geometry(f"480x700+{x}+{y}")

        self._build_header()
        self._build_status_panel()

        # 选项卡
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # Tab1: 登录
        tab_login = tk.Frame(self.notebook, bg="#f8fafc")
        self.notebook.add(tab_login, text="  登录  ")
        self._build_credentials_panel(tab_login)
        self._build_action_buttons(tab_login)
        self._build_settings_panel(tab_login)

        self._build_log_panel()

        self.root.after(100, self._poll_ui_events)
        self.root.after(500, self._auto_start_monitor)

    def _build_header(self):
        header = tk.Frame(self.root, bg="#1e40af", height=60)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="🌐 校园网一键登录",
                 font=("微软雅黑", 16, "bold"),
                 fg="white", bg="#1e40af").pack(side="left", expand=True, pady=15)

        tk.Button(
            header,
            text="退出程序",
            font=("微软雅黑", 9),
            bg="#1e3a8a",
            fg="white",
            activebackground="#172554",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            command=self._exit_application,
        ).pack(side="right", padx=(0, 12), pady=14)

    def _build_status_panel(self):
        frame = tk.LabelFrame(self.root, text=" 网络状态 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=15, pady=8)
        frame.pack(fill="x", padx=15, pady=(10, 5))

        row = tk.Frame(frame, bg="#f8fafc")
        row.pack(fill="x")

        self.status_light = StatusIndicator(row, size=16, bg="#f8fafc")
        self.status_light.pack(side="left", padx=(0, 8))

        self.status_label = tk.Label(row, text="检测中...",
                                     font=("微软雅黑", 11),
                                     fg="#334155", bg="#f8fafc")
        self.status_label.pack(side="left")

        self.status_detail = tk.Label(frame, text="",
                                      font=("微软雅黑", 9),
                                      fg="#64748b", bg="#f8fafc")
        self.status_detail.pack(anchor="w", pady=(3, 0))

    def _build_credentials_panel(self, parent):
        frame = tk.LabelFrame(parent, text=" 账号设置 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=15, pady=8)
        frame.pack(fill="x", padx=15, pady=5)

        # 用户名
        tk.Label(frame, text="用户名:", font=("微软雅黑", 10),
                 fg="#334155", bg="#f8fafc").grid(row=0, column=0, sticky="w", pady=3)
        self.username_var = tk.StringVar(value=self.config.username)
        self.username_entry = ttk.Entry(frame, textvariable=self.username_var, width=28)
        self.username_entry.grid(row=0, column=1, padx=(10, 0), pady=3)

        # 密码
        tk.Label(frame, text="密  码:", font=("微软雅黑", 10),
                 fg="#334155", bg="#f8fafc").grid(row=1, column=0, sticky="w", pady=3)
        self.password_var = tk.StringVar(value=self.config.password)
        self.password_entry = ttk.Entry(frame, textvariable=self.password_var,
                                        show="●", width=28)
        self.password_entry.grid(row=1, column=1, padx=(10, 0), pady=3)

        # 服务(可选)
        tk.Label(frame, text="服  务:", font=("微软雅黑", 10),
                 fg="#334155", bg="#f8fafc").grid(row=2, column=0, sticky="w", pady=3)
        service_value = self.config.service or "校园用户"
        if service_value not in SERVICE_SUFFIXES:
            service_value = "校园用户"
        self.service_var = tk.StringVar(value=service_value)
        self.service_entry = ttk.Combobox(
            frame,
            textvariable=self.service_var,
            values=tuple(SERVICE_SUFFIXES),
            state="readonly",
            width=26,
        )
        self.service_entry.grid(row=2, column=1, padx=(10, 0), pady=3)

        tk.Label(frame, text="(选择认证服务)",
                 font=("微软雅黑", 8), fg="#94a3b8",
                 bg="#f8fafc").grid(row=2, column=2, sticky="w", padx=5)

        # 保存按钮
        save_btn = tk.Button(frame, text="💾 保存账号",
                             font=("微软雅黑", 9),
                             bg="#e2e8f0", fg="#334155",
                             relief="flat", cursor="hand2",
                             command=self._save_credentials)
        save_btn.grid(row=3, column=1, sticky="e", pady=(5, 0), padx=(10, 0))

    def _build_action_buttons(self, parent):
        frame = tk.Frame(parent, bg="#f8fafc")
        frame.pack(fill="x", padx=15, pady=8)

        # 一键登录按钮
        self.login_btn = tk.Button(
            frame, text="⚡ 一键登录",
            font=("微软雅黑", 14, "bold"),
            bg="#1e40af", fg="white",
            activebackground="#1e3a8a", activeforeground="white",
            relief="flat", cursor="hand2",
            height=1, width=15,
            command=self._login_async
        )
        self.login_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))

        # 注销按钮
        self.logout_btn = tk.Button(
            frame, text="🔌 注销",
            font=("微软雅黑", 11),
            bg="#dc2626", fg="white",
            activebackground="#b91c1c", activeforeground="white",
            relief="flat", cursor="hand2",
            height=1, width=8,
            command=self._logout_async
        )
        self.logout_btn.pack(side="right", padx=(5, 0))

        # 刷新状态按钮
        frame2 = tk.Frame(parent, bg="#f8fafc")
        frame2.pack(fill="x", padx=15, pady=(0, 5))

        self.refresh_btn = tk.Button(
            frame2, text="🔄 刷新状态",
            font=("微软雅黑", 9),
            bg="#e2e8f0", fg="#334155",
            relief="flat", cursor="hand2",
            command=self._check_status_async
        )
        self.refresh_btn.pack(side="left")

        # 注销并重新登录 (已在线时使用)
        self.relogin_btn = tk.Button(
            frame2, text="🔁 注销并重新登录",
            font=("微软雅黑", 9),
            bg="#7c3aed", fg="white",
            relief="flat", cursor="hand2",
            command=lambda: self._login_async(force_relogin=True)
        )
        self.relogin_btn.pack(side="right")

    def _build_settings_panel(self, parent):
        frame = tk.LabelFrame(parent, text=" 高级选项 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=15, pady=5)
        frame.pack(fill="x", padx=15, pady=3)

        self.maintain_var = tk.BooleanVar(value=self.config.auto_maintain)
        tk.Checkbutton(frame, text="自动保活与重连 (每30秒检测)",
                       variable=self.maintain_var,
                       font=("微软雅黑", 9), bg="#f8fafc",
                       command=self._on_maintain_toggle
                       ).pack(anchor="w")

        self.router_var = tk.BooleanVar(value=self.config.router_mode)
        tk.Checkbutton(frame, text="通过路由器连接 (从宿舍门户获取出口身份)",
                       variable=self.router_var,
                       font=("微软雅黑", 9), bg="#f8fafc",
                       command=self._on_router_toggle
                       ).pack(anchor="w")

        # Portal IP 配置
        ip_row = tk.Frame(frame, bg="#f8fafc")
        ip_row.pack(fill="x", pady=(3, 0))
        tk.Label(ip_row, text="Portal IP:",
                 font=("微软雅黑", 9), fg="#64748b",
                 bg="#f8fafc").pack(side="left")
        self.portal_ip_var = tk.StringVar(value=self.config.portal_ip)
        ttk.Entry(ip_row, textvariable=self.portal_ip_var,
                  width=18).pack(side="left", padx=5)
        tk.Button(ip_row, text="应用", font=("微软雅黑", 8),
                  bg="#e2e8f0", relief="flat",
                  command=self._apply_portal_ip).pack(side="left")

    def _build_security_panel(self, parent):
        """安全管理选项卡 - 无感认证控制 + 设备管理"""

        # ---- 无感认证 & 自动连接 ----
        mac_frame = tk.LabelFrame(parent, text=" 无感认证 & 自动连接 ",
                                  font=("微软雅黑", 10, "bold"),
                                  bg="#f8fafc", fg="#b91c1c",
                                  padx=15, pady=8)
        mac_frame.pack(fill="x", padx=12, pady=(8, 5))

        # 警告提示
        warn_row = tk.Frame(mac_frame, bg="#fef2f2", relief="groove", bd=1)
        warn_row.pack(fill="x", pady=(0, 8))
        tk.Label(warn_row,
                 text="  安全建议: 关闭无感认证和自动连接，防止他人盗用你的网络",
                 font=("微软雅黑", 9), fg="#991b1b", bg="#fef2f2",
                 wraplength=380, justify="left").pack(padx=8, pady=6)

        # 本机无感认证状态
        status_row = tk.Frame(mac_frame, bg="#f8fafc")
        status_row.pack(fill="x", pady=2)
        tk.Label(status_row, text="本机无感认证:",
                 font=("微软雅黑", 10), fg="#334155",
                 bg="#f8fafc").pack(side="left")
        self.mac_status_label = tk.Label(status_row, text="未知",
                                         font=("微软雅黑", 10, "bold"),
                                         fg="#64748b", bg="#f8fafc")
        self.mac_status_label.pack(side="left", padx=8)

        # 本机MAC地址显示
        mac_row = tk.Frame(mac_frame, bg="#f8fafc")
        mac_row.pack(fill="x", pady=2)
        tk.Label(mac_row, text="本机MAC地址:",
                 font=("微软雅黑", 9), fg="#64748b",
                 bg="#f8fafc").pack(side="left")
        self.local_mac_label = tk.Label(mac_row, text="--",
                                        font=("Consolas", 10),
                                        fg="#334155", bg="#f8fafc")
        self.local_mac_label.pack(side="left", padx=8)

        # 操作按钮
        btn_row = tk.Frame(mac_frame, bg="#f8fafc")
        btn_row.pack(fill="x", pady=(8, 3))

        self.cancel_mac_btn = tk.Button(
            btn_row, text="关闭本机无感认证",
            font=("微软雅黑", 10, "bold"),
            bg="#dc2626", fg="white",
            activebackground="#b91c1c", activeforeground="white",
            relief="flat", cursor="hand2", width=18,
            command=self._cancel_mac_async
        )
        self.cancel_mac_btn.pack(side="left", padx=(0, 8))

        self.refresh_sec_btn = tk.Button(
            btn_row, text="刷新安全状态",
            font=("微软雅黑", 9),
            bg="#e2e8f0", fg="#334155",
            relief="flat", cursor="hand2",
            command=self._refresh_security_async
        )
        self.refresh_sec_btn.pack(side="left")

        # 第二行按钮: 本机下线
        btn_row2 = tk.Frame(mac_frame, bg="#f8fafc")
        btn_row2.pack(fill="x", pady=(4, 3))

        self.offline_btn = tk.Button(
            btn_row2, text="🔌 本机下线 (注销网络)",
            font=("微软雅黑", 10, "bold"),
            bg="#7c3aed", fg="white",
            activebackground="#6d28d9", activeforeground="white",
            relief="flat", cursor="hand2", width=22,
            command=self._go_offline_async
        )
        self.offline_btn.pack(side="left")

        # ---- 设备管理 ----
        dev_frame = tk.LabelFrame(parent, text=" 已绑定设备 (无感认证) ",
                                  font=("微软雅黑", 10, "bold"),
                                  bg="#f8fafc", fg="#1e40af",
                                  padx=12, pady=8)
        dev_frame.pack(fill="x", padx=12, pady=5)

        # 设备列表表头
        header_row = tk.Frame(dev_frame, bg="#e2e8f0")
        header_row.pack(fill="x")
        tk.Label(header_row, text="设备名", width=10,
                 font=("微软雅黑", 9, "bold"), bg="#e2e8f0",
                 anchor="w").pack(side="left", padx=5)
        tk.Label(header_row, text="MAC地址", width=16,
                 font=("微软雅黑", 9, "bold"), bg="#e2e8f0",
                 anchor="w").pack(side="left", padx=5)
        tk.Label(header_row, text="操作", width=8,
                 font=("微软雅黑", 9, "bold"), bg="#e2e8f0",
                 anchor="center").pack(side="right", padx=5)

        # 设备列表容器 (直接用Frame，不用Canvas)
        self.device_list_frame = tk.Frame(dev_frame, bg="#f8fafc")
        self.device_list_frame.pack(fill="x", pady=(2, 0))

        # 底部: 设备数量 + 全部取消按钮
        bottom_row = tk.Frame(dev_frame, bg="#f8fafc")
        bottom_row.pack(fill="x", pady=(6, 0))

        self.device_count_label = tk.Label(
            bottom_row, text="设备数: 0",
            font=("微软雅黑", 9), fg="#64748b", bg="#f8fafc")
        self.device_count_label.pack(side="left")

        self.kick_all_btn = tk.Button(
            bottom_row, text="全部取消绑定并下线",
            font=("微软雅黑", 9, "bold"),
            bg="#7c2d12", fg="white",
            activebackground="#431407", activeforeground="white",
            relief="flat", cursor="hand2",
            command=self._kick_all_devices_async
        )
        self.kick_all_btn.pack(side="right")

    def _build_log_panel(self):
        frame = tk.LabelFrame(self.root, text=" 日志 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=10, pady=5)
        frame.pack(fill="both", expand=True, padx=15, pady=(3, 10))

        # 日志工具栏
        log_toolbar = tk.Frame(frame, bg="#f8fafc")
        log_toolbar.pack(fill="x", pady=(0, 3))

        tk.Button(log_toolbar, text="📋 复制日志",
                  font=("微软雅黑", 8), bg="#e2e8f0", fg="#334155",
                  relief="flat", cursor="hand2",
                  command=self._copy_log).pack(side="left")

        tk.Button(log_toolbar, text="🗑 清空",
                  font=("微软雅黑", 8), bg="#e2e8f0", fg="#334155",
                  relief="flat", cursor="hand2",
                  command=self._clear_log).pack(side="left", padx=4)

        self.log_text = tk.Text(frame, height=10, wrap="word",
                                font=("Consolas", 9),
                                bg="#1e293b", fg="#e2e8f0",
                                insertbackground="white",
                                relief="flat", padx=8, pady=5)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    # ==================== 业务逻辑 ====================

    def _copy_log(self):
        """复制日志到剪贴板"""
        self.log_text.configure(state="normal")
        content = self.log_text.get("1.0", "end-1c")
        self.log_text.configure(state="disabled")
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._log("日志已复制到剪贴板", "ok")

    def _clear_log(self):
        """清空日志"""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _log(self, msg: str, level: str = "info"):
        """写入日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "ℹ", "ok": "✅", "err": "❌", "warn": "⚠"}.get(level, "·")
        line = f"[{timestamp}] {prefix} {msg}\n"

        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _save_credentials(self):
        if not check_server_permission("save_credentials"):
            self._log("服务器拒绝：保存账号", "warn")
            return

        username = self.username_var.get().strip()
        password = self.password_var.get()
        service = self.service_var.get().strip()

        if not username or not password:
            self._log("请输入用户名和密码", "warn")
            return

        self.config.username = username
        self.config.password = password
        self.config.service = service
        self._log("账号信息已加密保存", "ok")
        self.monitor.configure(MonitorSettings.from_config(self.config))

    def _update_status_ui(self, state: str, text: str, detail: str = ""):
        """更新状态显示"""
        self.status_light.set_state(state)
        self.status_label.config(text=text)
        self.status_detail.config(text=detail)

    def _post(self, callback, *args):
        if not self._closing:
            self._ui_events.put((callback, args))

    def _monitor_event(self, kind, value):
        handlers = {
            "status": self._on_status_result,
            "login": self._on_login_result,
            "logout": self._on_logout_result,
        }
        if kind == "log":
            self._post(self._log, *value)
        elif kind in handlers:
            self._post(handlers[kind], value)

    def _poll_ui_events(self):
        if self._closing:
            return
        try:
            while True:
                callback, args = self._ui_events.get_nowait()
                callback(*args)
                if self._closing:
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ui_events)

    def _check_status_async(self):
        if self._closing:
            return
        self._update_status_ui("checking", "检测中...", "正在探测网络状态")
        self.refresh_btn.config(state="disabled")
        self.monitor.request_check()

    def _on_status_result(self, status: NetworkStatus):
        self.refresh_btn.config(state="normal")
        labels = {"online": "✅ 已在线", "need_login": "需要认证",
                  "network_error": "网络故障", "unknown": "状态未知"}
        self._update_status_ui(status.state, labels[status.state], status.message)
        for line in status.debug_log:
            self._log(line)
        self._log(f"{labels[status.state]}：{status.message}",
                  "ok" if status.online else "warn")

    def _login_async(self, force_relogin=False):
        if self._closing or not check_server_permission("login"):
            return
        username = self.username_var.get().strip()
        password = self.password_var.get()
        service = self.service_var.get()
        if not username or not password:
            self._log("请先输入用户名和密码", "warn")
            return
        self._save_credentials()
        self.login_btn.config(state="disabled", text="⏳ 登录中...")
        self._update_status_ui("connecting", "正在登录...", "认证后将验证外网连通性")
        self.monitor.request_login(username, password, service, force_relogin)

    def _on_login_result(self, result: LoginResult):
        self.login_btn.config(state="normal", text="⚡ 一键登录")
        for line in result.raw.get("_debug_log", []):
            self._log(line)
        if result.success:
            self._update_status_ui("online", "✅ 已在线", result.message)
            self._log(result.message, "ok")
        else:
            state = result.status.state if result.status else "unknown"
            labels = {"not_submitted": "未提交认证", "failed": "认证失败",
                      "accepted": "认证已接受，等待外网恢复"}
            self._update_status_ui(state, labels[result.phase], result.message)
            self._log(result.message, "warn")
            if result.permanent_error:
                self._log("自动认证已暂停，请修改账号配置或手动登录", "warn")

    def _logout_async(self):
        if self._closing or not check_server_permission("logout"):
            return
        self.logout_btn.config(state="disabled")
        self._log("自动认证已暂停，正在注销...")
        self.monitor.request_logout()

    def _on_logout_result(self, success: bool):
        self.logout_btn.config(state="normal")
        self._update_status_ui("need_login" if success else "unknown",
                               "已注销" if success else "注销状态未知",
                               "自动监控已暂停，手动登录后恢复")
        self._log("注销成功" if success else "注销未确认，请刷新状态检查",
                  "ok" if success else "warn")

    def _auto_start_monitor(self):
        if self._closing:
            return
        self.monitor.start()
        if not self.config.auto_maintain or not self.config.has_credentials():
            self.monitor.request_check()
        if not self.config.has_credentials():
            self._log("尚未保存账号，保存后按设置启用自动保活与重连", "warn")

    def _on_maintain_toggle(self):
        self.config.auto_maintain = self.maintain_var.get()
        self.monitor.configure(MonitorSettings.from_config(self.config))
        self._log("自动保活与重连已开启" if self.config.auto_maintain else "自动保活与重连已关闭")

    def _on_router_toggle(self):
        self.config.router_mode = self.router_var.get()
        self.monitor.configure(MonitorSettings.from_config(self.config))
        self._log("已切换为路由器出口认证" if self.config.router_mode else "已切换为电脑直连认证")

    def _apply_portal_ip(self):
        new_ip = self.portal_ip_var.get().strip()
        if new_ip:
            import ipaddress
            try:
                ipaddress.IPv4Address(new_ip)
            except ValueError:
                self._log("请输入有效的 Portal IPv4 地址", "warn")
                return
            self.config.portal_ip = new_ip
            self.monitor.configure(MonitorSettings.from_config(self.config))
            self._log(f"Portal IP 已更新: {new_ip}", "ok")
            self._check_status_async()

    # ==================== 安全管理逻辑 ====================

    def _go_offline_async(self):
        self._logout_async()

    def _refresh_security_async(self):
        """异步刷新安全状态"""
        if not self.current_user_index:
            self._log("请先通过「一键登录」登录后再查看安全状态", "warn")
            return
        self._log(f"使用userIndex: {self.current_user_index[:30]}...")
        self.refresh_sec_btn.config(state="disabled")
        self._log("正在获取安全状态...")
        threading.Thread(target=self._refresh_security_worker, daemon=True).start()

    def _refresh_security_worker(self):
        sec = self.api.get_security_status(self.current_user_index)
        self._post(self._on_security_result, sec)

    def _on_security_result(self, sec: dict):
        self.refresh_sec_btn.config(state="normal")

        if "error" in sec:
            self._log(f"获取安全状态失败: {sec['error']}", "err")
            if "raw" in sec:
                self._log(f"原始响应: {sec['raw'][:200]}", "info")
            return

        # 更新无感认证状态
        has_mab = sec.get("hasMabInfo", False)
        user_mac = sec.get("userMac", "")

        if has_mab:
            self.mac_status_label.config(text="已开启 (有风险!)", fg="#dc2626")
        else:
            self.mac_status_label.config(text="已关闭 (安全)", fg="#16a34a")

        if user_mac:
            self.local_mac_label.config(text=user_mac.upper())
        else:
            self.local_mac_label.config(text="未获取")

        # 更新设备列表
        devices = sec.get("devices", [])
        self._populate_device_list(devices, user_mac)
        self.device_count_label.config(
            text=f"设备数: {len(devices)} / 最大: {sec.get('mabInfoMaxCount', '?')}")

        self._log(f"安全状态已刷新: 无感认证={'开启' if has_mab else '关闭'}, "
                  f"绑定设备={len(devices)}台, MAC={user_mac}", "ok")
        self._log(f"  [调试] hasMabInfo(raw)={sec.get('_debug_hasMab_raw')}, "
                  f"mabInfo={sec.get('_debug_mabInfo_raw', '')[:80]}", "info")

    def _populate_device_list(self, devices: list, local_mac: str = ""):
        """填充设备列表"""
        # 清空现有列表
        for widget in self.device_list_frame.winfo_children():
            widget.destroy()

        if not devices:
            tk.Label(self.device_list_frame, text="无绑定设备",
                     font=("微软雅黑", 10), fg="#94a3b8",
                     bg="#f8fafc").pack(pady=20)
            return

        for i, dev in enumerate(devices):
            bg = "#ffffff" if i % 2 == 0 else "#f1f5f9"
            row = tk.Frame(self.device_list_frame, bg=bg)
            row.pack(fill="x", pady=1)

            mac = dev.get("userMac", "")
            name = dev.get("deviceName", "") or f"设备{i+1}"
            is_local = local_mac and mac.upper() == local_mac.upper()

            # 设备名
            name_text = f"{'⬤ ' if is_local else ''}{name}"
            tk.Label(row, text=name_text, width=10,
                     font=("微软雅黑", 9, "bold" if is_local else ""),
                     fg="#1e40af" if is_local else "#334155",
                     bg=bg, anchor="w").pack(side="left", padx=(5, 2), pady=4)

            # MAC地址
            tk.Label(row, text=mac.upper(),
                     font=("Consolas", 9), fg="#475569",
                     bg=bg, anchor="w").pack(side="left", padx=2)

            # 操作按钮区
            user_id = dev.get("userId", "")
            btn_frame = tk.Frame(row, bg=bg)
            btn_frame.pack(side="right", padx=3, pady=2)

            # 强制下线按钮
            tk.Button(
                btn_frame, text="下线",
                font=("微软雅黑", 8, "bold"),
                bg="#7c3aed", fg="white", relief="flat", cursor="hand2",
                command=lambda uid=user_id, umac=mac: self._force_offline_device_async(uid, umac)
            ).pack(side="left", padx=(0, 3))

            # 取消绑定按钮
            tk.Button(
                btn_frame, text="解绑",
                font=("微软雅黑", 8),
                bg="#ef4444", fg="white", relief="flat", cursor="hand2",
                command=lambda uid=user_id, umac=mac: self._kick_device_async(uid, umac)
            ).pack(side="left")

    def _cancel_mac_async(self):
        """关闭本机无感认证"""
        if not check_server_permission("cancel_mac"):
            self._log("服务器拒绝：关闭无感认证", "warn")
            return

        if not self.current_user_index:
            self._log("请先登录", "warn")
            return

        self.cancel_mac_btn.config(state="disabled")
        self._log("正在关闭本机无感认证...")
        threading.Thread(target=self._cancel_mac_worker, daemon=True).start()

    def _cancel_mac_worker(self):
        result = self.api.cancel_mac(self.current_user_index)
        self._post(self._on_cancel_mac_result, result)

    def _on_cancel_mac_result(self, result: dict):
        self.cancel_mac_btn.config(state="normal")
        if result.get("result") == "success":
            self.mac_status_label.config(text="已关闭 (安全)", fg="#16a34a")
            self._log("本机无感认证已关闭!", "ok")
            # 刷新设备列表
            self._refresh_security_async()
        else:
            msg = result.get("message", "未知错误")
            self._log(f"关闭无感认证失败: {msg}", "err")

    def _force_offline_device_async(self, user_id: str, user_mac: str):
        """强制指定设备下线"""
        if not check_server_permission("force_offline_device"):
            self._log("服务器拒绝：强制设备下线", "warn")
            return

        self._log(f"正在强制下线设备 {user_mac}...")

        def worker():
            result = self.api.force_offline_device(user_id, user_mac)
            self._post(_on_result, result)

        def _on_result(result):
            status = result.get("result", "fail")
            msg = result.get("message", "")
            details = result.get("details", [])
            if status == "success":
                self._log(f"✅ {msg}", "ok")
                if details:
                    self._log(f"  详情: {'; '.join(details)}", "info")
                self._refresh_security_async()
            else:
                self._log(f"设备下线结果: {msg}", "warn")
                if details:
                    self._log(f"  详情: {'; '.join(details)}", "info")

        threading.Thread(target=worker, daemon=True).start()

    def _kick_device_async(self, user_id: str, user_mac: str):
        """取消指定设备的无感认证绑定"""
        if not check_server_permission("kick_device"):
            self._log("服务器拒绝：取消设备绑定", "warn")
            return

        self._log(f"正在取消设备 {user_mac} 的绑定...")
        threading.Thread(
            target=self._kick_device_worker,
            args=(user_id, user_mac),
            daemon=True
        ).start()

    def _kick_device_worker(self, user_id, user_mac):
        result = self.api.cancel_mac_for_device(user_id, user_mac)
        self._post(self._on_kick_device_result, result, user_mac)

    def _on_kick_device_result(self, result: dict, user_mac: str):
        if result.get("result") == "success":
            self._log(f"设备 {user_mac} 已取消绑定!", "ok")
            self._refresh_security_async()
        else:
            msg = result.get("message", "未知错误")
            self._log(f"取消设备 {user_mac} 失败: {msg}", "err")

    def _kick_all_devices_async(self):
        """取消所有设备的无感认证绑定"""
        if not check_server_permission("kick_all_devices"):
            self._log("服务器拒绝：全部取消绑定", "warn")
            return

        if not self.current_user_index:
            self._log("请先登录", "warn")
            return

        self.kick_all_btn.config(state="disabled")
        self._log("正在取消所有设备绑定...", "warn")
        threading.Thread(target=self._kick_all_worker, daemon=True).start()

    def _kick_all_worker(self):
        # 先获取所有设备
        sec = self.api.get_security_status(self.current_user_index)
        devices = sec.get("devices", [])

        # 先取消本机无感认证
        self.api.cancel_mac(self.current_user_index)
        self._post(self._log, "本机无感认证已关闭", "ok")

        # 逐个取消其他设备
        for dev in devices:
            user_id = dev.get("userId", "")
            user_mac = dev.get("userMac", "")
            if user_id and user_mac:
                result = self.api.cancel_mac_for_device(user_id, user_mac)
                status = "ok" if result.get("result") == "success" else "err"
                self._post(self._log,
                                f"设备 {user_mac}: {'已取消' if status == 'ok' else '失败'}",
                                status)

        self._post(self._on_kick_all_done)

    def _on_kick_all_done(self):
        self.kick_all_btn.config(state="normal")
        self._log("全部设备处理完成!", "ok")
        self._refresh_security_async()

    def _hide_to_tray(self):
        """隐藏主窗口并保留托盘图标和进程。"""
        if self._closing:
            return
        if self.tray_icon is None:
            try:
                self.tray_icon = SystemTrayIcon("CampusNetLogin 校园网助手")
                self.tray_icon.start()
            except Exception as exc:
                self.tray_icon = None
                self._log(f"系统托盘不可用，改为最小化窗口: {exc}", "warn")
                self.root.iconify()
                return

        self.root.withdraw()
        if not self._tray_polling:
            self._tray_polling = True
            self.root.after(100, self._poll_tray_events)

    def _restore_window(self):
        """从托盘恢复主窗口。"""
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _poll_tray_events(self):
        """在主线程处理托盘菜单事件。"""
        if self._closing:
            return
        if self.tray_icon is None:
            self._tray_polling = False
            return

        should_continue = True
        try:
            while True:
                event = self.tray_icon.events.get_nowait()
                if event == "restore":
                    self._restore_window()
                elif event == "exit":
                    should_continue = False
                    self._exit_application()
                    break
        except queue.Empty:
            pass

        if should_continue and self.tray_icon is not None:
            self.root.after(100, self._poll_tray_events)
        else:
            self._tray_polling = False

    def _exit_application(self):
        if self._closing:
            return
        self._closing = True
        self._cancel_timers()
        self.monitor.request_stop()
        self._update_status_ui("checking", "正在退出...", "等待当前请求结束")
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self._tray_polling = False
        self._finish_exit()

    def _cancel_timers(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)

    def _finish_exit(self):
        if self.monitor.is_running():
            self.root.after(50, self._finish_exit)
            return
        self.monitor.join()
        if self.monitor._thread is None:
            self.api.close()
        self._cancel_timers()
        self.root.destroy()

    def run(self):
        """启动应用"""
        self._log("校园网登录工具已启动")
        self._log(f"Portal: {self.config.portal_ip}")
        if self.config.has_credentials():
            self._log(f"已加载保存的账号: {self.config.username}")
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="CampusNetLogin 本地客户端")
    parser.add_argument("--monitor", action="store_true", help="后台保活模式")
    parser.add_argument("--once", action="store_true", help="执行一次检测并按需认证")
    args = parser.parse_args()

    instance = SingleInstance()
    if not instance.acquire():
        if not (args.monitor or args.once):
            from tkinter import messagebox
            messagebox.showinfo("校园网助手", "程序已在运行，请从系统托盘打开；如已启动后台模式，请先停止后台进程。")
        return 2 if (args.monitor or args.once) else 0
    try:
        if args.monitor or args.once:
            from monitor import run_monitor
            return run_monitor(once=args.once, locked=True)
        app = CampusNetApp()
        try:
            app.run()
        finally:
            app.monitor.request_stop()
            app.monitor.join()
        return 0
    finally:
        instance.release()


if __name__ == "__main__":
    sys.exit(main())
