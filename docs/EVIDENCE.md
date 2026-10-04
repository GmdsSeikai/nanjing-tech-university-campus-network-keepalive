# 中国移动与路由器保活验证记录

日期：2026-10-04，Asia/Shanghai。风险等级 Tier 3（认证和并发）。

规格：[SPEC.md](SPEC.md)。授权为用户要求直接实施所附计划；spec approval: not obtained (autonomous run)，没有另行审批规格文本。Independent verification: not performed。结论为本地源码及 Windows 产物验证通过，现场真实重新认证和长时效果未验收。

源基线：`852e1d28225a605e9acb939b57bfbfb2c9283d74`，分支 `main`，验证对象是未提交工作树。开始时已有 `app.py` 未提交修改；其启动监控、主动注销暂停和退出停止的行为整合进统一监控器，没有 reset/stash/提交。手机端、远控和路由器不在改动范围。

可执行源码指纹：对 `app.py`、`config_manager.py`、`drcom_api.py`、`monitor.py`、`tray_icon.py` 按文件名排序，依次拼接 UTF-8 文件名、零字节、原始文件内容、零字节后计算 SHA256，结果为 `4b822d4c4f3633a593cbc23211ac338fd6887befe3f3c6ecdc0eed103062e95d`。

## 最终可复现入口

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_windows.ps1
```

该入口创建构建隔离环境、执行 52 项 unittest、编译检查、PyInstaller 构建、真实 EXE 工作流和 `git diff --check`。包测试使用临时 `USERPROFILE`、空账号配置，只读探测当前外网；需要 Windows 和当前已联网，否则失败。不会使用真实保存密码。

工具链：Windows 内核 10.0.26200、Python 3.9.13 x64、Tk 8.6、requests 2.32.5、PyInstaller 6.22.3、hooks 2026.8。最终验证完成时间：2026-10-04 20:17:53 +08:00。

## 行为与证据

| 规格/失败模型 | 生产路径与测试 | 最终结果 |
| --- | --- | --- |
| 中国移动保存恢复、后缀和电脑前缀、旧服务 | `test_config_manager.py`、`test_app.py`、`test_router_auth.py` 的账号/服务测试 | 通过 |
| 旧自动开关 OR 迁移；新旧路由器默认值 | `test_auto_flags_migrate_and_router_preserves_old_mode`、新配置/字段优先级测试 | 通过 |
| 路由器出口身份、IP 刷新、页面注释及接入参数 | 完整 `EPortalAPI.login` + 模拟 HTTP 边界，刷新出口及配置参数测试 | 通过 |
| 身份缺失、配置缺失、身份矛盾不得提交密码 | `test_missing_identity_or_config_blocks_submission`、`test_conflicting_exit_identity_is_rejected`、前缀配置缺失测试 | 通过 |
| 在线不登录、单个探测失败、内容假阳性 | 在线短路、单地址故障和严格响应验证测试 | 通过 |
| 门户可访问不代表掉线；未知查询不认证 | `test_plan_regressions.py`、`test_arbitrary_query_error_remains_unknown` | 通过 |
| 已认证但外网故障不得重复认证 | `test_authenticated_but_no_internet_does_not_login` | 通过 |
| 明确门户重定向/未认证后恢复；接口接受后复测 | 过期会话恢复、认证接受但外网未恢复测试 | 通过 |
| 永久错误暂停、配置或手动登录恢复、日志不泄露密码 URL | 密码暂停及回显/HTTP 异常脱敏测试 | 通过 |
| 30/60/120/240/300 秒退避与恢复耗时 | 确定性 `step` 和假时钟测试 | 通过 |
| 注销前暂停、保存不解除主动暂停、手动登录恢复 | 实际 Tk 控件 + 统一监控器 + 模拟 HTTP | 通过 |
| 暂停/退出途中不得新发认证；不使用未应用配置的旧密码 | 真实线程、Event 屏障、取消及配置队列竞争测试 | 通过 |
| 重复启动/切换不增加线程；关闭资源 | duplicate start/toggles、stop during probe 测试 | 通过 |
| UI 主线程处理、销毁前取消 timers、停止后丢弃消息 | `test_app.py` 实际 Windows Tk 测试 | 通过 |
| GUI 与后台共用实例锁 | Windows 双进程 mutex；实际 EXE 拒绝第二个 `--monitor`/`--once` | 通过 |
| EXE 依赖、托盘隐藏/恢复/再次隐藏/退出/重启 | `scripts/smoke_windows.py`，含 Tcl/Tk 文件和核心模块检查 | 通过 |
| 现场当前出口身份/配置及在线状态 | 只读宿舍门户、loadConfig、chkstatus、online_list 和连通性探测 | 获取有效 IP/MAC，前缀开启，认证方式 1，在线查询 result=1，外网 online |
| 真实密码重新认证、手机共享、夜间及 24 小时观察 | 尚未执行，步骤见 README | 未验证 |

## 最终命令结果

- `build/.venv/Scripts/python.exe -m unittest discover -s tests -v`：52 项通过，无跳过。
- `build/.venv/Scripts/python.exe -m py_compile app.py drcom_api.py monitor.py config_manager.py tray_icon.py`：通过。
- `build/.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name CampusNetLogin --distpath release --workpath build/pyinstaller --icon=NONE app.py`：通过。
- `build/.venv/Scripts/python.exe -u scripts/smoke_windows.py`：所有 5 组产物工作流检查通过；实际 `--once` 返回 0，双入口锁拒绝返回 2，托盘退出返回 0，退出后可重新运行。
- `git diff --check`：通过。

上述命令由最终入口实际串行执行，日志在本地 `build/final-verification.txt`。后续只更新本记录和决策状态，没有再修改 EXE 所属源码。

交付：`release/CampusNetLogin.exe`，10,502,006 bytes，SHA256 `7658b84ae5c57903ef4fc1611e545fdc1fa653130754386df8f854029b1bfc15`。

## 未执行层与置信边界

- 全量 mutation 工具：UNAVAILABLE，项目未配置。SUBSTITUTED：以运行时临时替换账号映射、探测验证、离线判断三个方法实施负面对照，各对应测试均产生预期断言失败，恢复方法后正常测试通过。这不是持久源码变异扫描，也不证明所有分支的测试敏感度。
- 行/分支覆盖门槛、静态类型和 lint：N-A，仓库没有这些既有检查；未宣称覆盖率或类型检查通过。
- Independent verification：not performed，本地自检不是独立审计。
- 只读现场查询能证明当前接口和上下文可用，不能证明提交密码后门户一定接受，更不能证明强制会话周期或长期不掉线。
- Requests 已发出的 HTTP 请求不能撤销；退出等待其结束并停止后续请求，完成清理后才释放锁。

## 开发中发现与修复

- 原代码仅因门户可访问就判为需要登录：回归测试先失败，改为明确认证证据。
- 原认证成功测试只检查接口 result：补上外网复测的模拟响应，保留原参数断言。
- GUI 销毁后遗留 Tk timers：实际窗口测试暴露，退出时取消 timers；最终测试不再出现 Tcl 迟到回调错误。
- 新配置排队与检测之间可能使用旧密码：增加已应用配置版本检查，回归测试锁定该边界。
- 全局 Anaconda 的 pathlib 回退包阻止打包：改用构建 venv，不删除该包。初次安装构建工具更新了全局 PyInstaller/packaging/pywin32-ctypes；最终交付使用隔离环境。
- 产物检查脚本初次假设归档使用正斜杠、托盘可由 EnumWindows 查到：分别按归档路径格式及 HWND_MESSAGE 进行正确检查，最终实际 EXE 工作流通过。
