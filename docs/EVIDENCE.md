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

## 2026-10-06 路由器到期认证修复验证

风险等级 Tier 3（认证与监控）。规格为 [SPEC.md 的 2026-10-06 修订](SPEC.md)，用户授权直接实施所附修复计划；spec approval: not obtained (autonomous run)。Independent verification: not performed。决策见 [路由器到期后的出口认证与结果提示](decisions/implemented/2026-10-06-router-expiry-auth.md)。本节是修正版的证据，前文保留 2026-10-04 构建历史。

源基线 `079b6831cb457a9f638b579f7d0d10c8ec56cdb7`，分支 `main`，开始时工作树干净；验证对象为未提交工作树。范围为 `drcom_api.py`、`monitor.py`、`app.py`、对应测试、README、规格/决策/证据和交付 EXE。未修改账号、密码格式、路由器、手机、远控或 Git 历史。按前文同一算法计算五个可执行源码文件的指纹：`a38f597adeac6c9d62e338140d2b4b3ecb7c98fc1c1552136fdea9b1e5306330`。

最终入口实际运行：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_windows.ps1`，退出码 0。2026-10-06 22:39:30 +08:00 汇总结果：68 项 unittest 通过、无跳过；五个模块 `py_compile` 通过；PyInstaller 构建成功；5 组真实 EXE 工作流通过；`git diff --check` 通过。日志为本地 `build/router-expiry-final-verification.txt`。工具链仍为 Windows 10.0.26200、Python 3.9.13 x64、Tk 8.6、requests 2.32.5、PyInstaller 6.22.3、hooks 2026.8，没有新增依赖。

| 修订行为 / 禁止事项 / 失败模型 | 可推翻该行为的证据 | 最终结果 |
| --- | --- | --- |
| 未认证、真实 IP、缺 MAC 时，启动监控应认证并恢复 | `test_startup_without_mac_authenticates_and_publishes_result`：真实监控线程与生产 API，HTTP 为模拟边界，提交一次 `,0,student@cmcc`、零 MAC、当前出口 IP 并验证 online | 通过 |
| MAC 例外仅用于已知协议；真实 MAC 优先，不复用旧身份或本机身份 | `test_missing_mac_uses_portal_zero_placeholder_and_restores` 先真实 MAC 登录、再模拟到期/出口变化/无 MAC；本机身份方法设为抛错；协议范围和原真实 MAC 测试 | 通过 |
| 查询成功不等于在线；只认当前出口记录 | `test_query_success_requires_matching_current_exit` 覆盖空列表、其他出口、当前出口与混合记录；`test_chkstatus_success_keeps_its_own_semantics` 保留 chkstatus 状态码语义 | 通过 |
| 查询缺失、格式异常、计数或在线信息矛盾时不提交 | `test_malformed_or_contradictory_lists_stay_unknown`，以及未知查询错误测试 | 通过 |
| 外网明确重定向到校园门户不能被查询成功覆盖 | `test_redirect_survives_online_query_success`、`test_matching_list_does_not_override_redirect` | 通过 |
| IP 缺失、身份格式异常、IP/MAC 来源冲突时不提交 | 缺 IP + 无 MAC + 重定向、异常/冲突身份、重定向与页面 IP 冲突、原配置缺失测试 | 通过 |
| 门户已接受但外网仍故障，不误报在线或反复认证 | `test_portal_acceptance_is_not_internet_success`、`test_accepted_phase_survives_verification_exception`、`test_accepted_but_no_internet_does_not_repeat_login` | 通过 |
| 错误密码暂停；自动/手动结果按阶段显示 | 无 MAC 密码错误测试；`test_login_labels_describe_submission_phase`、`test_automatic_login_result_reaches_ui` 使用真实 Windows Tk，并检查主线程处理 | 通过 |
| 保留退避、注销暂停、退出取消与资源清理 | 原 monitor/app/tray/mutex 测试在同一最终 suite 中执行 | 通过 |
| 交付可启动且拥有修复代码 | `scripts/smoke_windows.py` 检查核心模块、Tcl/Tk、空账号 `--once`、GUI/双入口锁、托盘隐藏/恢复/退出、退出后重启；另提取 EXE 的 app 与 PYZ 中四个核心模块 code object，与当前源码编译结果逐一比较 | 5 组工作流及 5 个模块比较通过 |
| 真实出口查询结构可正确解释 | 只读获取宿舍门户、a41.js、身份、配置和 `online_list`，记录含 `online_ip` 字符串；当前 `result=1,total=1` 解释为 authenticated，实际外网为 online | 通过，只证明当前已在线状态 |
| 退出→认证到期→重新打开，无浏览器操作，电脑与手机恢复 | 当前会话已由浏览器恢复，未主动注销或等待到期 | 未验证，按 README 现场验收 |

交付 EXE：`release/CampusNetLogin.exe`，10,501,355 bytes，SHA256 `7ad0bd592bf98159a9ffab492fbb24373d50343a58613e2d5482f589da8d6a27`。桌面 `CampusNetLogin.exe` 已更新为同一文件，大小和 SHA256 一致；更新前桌面哈希为旧交付版 `7658b84ae5c57903ef4fc1611e545fdc1fa653130754386df8f854029b1bfc15`。正常退出运行中的旧客户端后才进行验收和替换，没有注销网络或改写账号配置。核心源码在最终构建后没有再修改，后续仅追加本验证记录。

桌面修正版已重新启动并进入托盘，恢复原运行状态；现场日志记录 `2026-10-06 22:42:08,021` 监控器启动。只读取配置开关确认保存的服务为中国移动、路由器模式及自动监控均开启、已保存账号，未输出账号或密码。此时当前出口已经在线，仍不能据此宣称真实到期恢复通过。

开发证据：更换为现场 `list/total/online_ip` 结构的模拟响应后，原实现的 32 项认证测试出现 38 个断言失败（包括 subTest）；UI 回归也复现统一错误提示。修复后测试通过。补充异常布尔 IP 与矛盾在线标记的边界测试先观察失败，再添加拒绝逻辑。一次中间 mutex 验证被正在运行的桌面旧客户端占用，正常退出后最终验证通过；新增 UI 测试曾在监听器注册前触发自动认证，改为先注册再启动。

未执行层：全量 mutation 工具 UNAVAILABLE（项目未配置），本轮没有声称 mutation 通过；新增回归的 RED→GREEN 只能证明所覆盖边界。覆盖率门槛、lint、静态类型 N-A（仓库没有既有检查）。独立审计 not performed。真实到期认证、手机共享与 24 小时/夜间观察未验证，源码模拟、已在线只读查询和 EXE 空账号检查不能替代现场验收。

## 2026-10-06 22:56 补充：未知列表的独立内核核验

用户反馈首份修正版仍显示“未提交认证：外网探测失败，门户认证状态未知”。现场日志证实 `22:46:44` 判定 unknown，`22:48:06` 外网恢复；运行进程为前一节桌面修正版。原监控日志未记录查询结构，因此失败时的具体 `online_list` 响应未获得，不能声称已确认该原始响应的格式。再次只读查询时三个外网探测均通过，门户在线列表匹配当前出口；真实 MAC 与零 MAC 的查询都返回 `result=1,total=1`。

只读取得学校 `a41.js`，确认其按 `/drcom/chkstatus.result=0` 进入未认证流程、`result=1` 处理在线。源码原先读取该接口只补充出口身份，列表未知时没有独立核验。本轮按 [SPEC 补充修订](SPEC.md) 增加该核验；仅用于已知南京工业大学路由器协议，重新验证响应身份，内核状态未知/失败或身份冲突仍不提交。同时让离线 `NetworkStatus.debug_log` 写入监控日志，只保存安全的结构和状态摘要。

四项新增生产 API 回归先观察到预期断言失败，修复后通过：未知列表 + 内核离线 + 无 MAC 恢复；列表请求失败时恢复；内核在线但外网故障不认证；核验身份冲突不提交。原未知/异常列表测试增加独立内核也未知的条件，仍保留“不提交”的全部断言。新增监控集成测试证明摘要进入日志、认证恢复且不记录响应里的账号和密码。

最终入口 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_windows.ps1` 退出码 0；2026-10-06 22:56:39 +08:00 汇总：73 项 unittest 通过、无跳过，编译和 PyInstaller 构建通过，原 5 组真实 EXE 工作流通过，`git diff --check` 通过。日志为 `build/auth-unknown-final-verification.txt`。另提取包内 app 和四个核心模块，code object 与当前源码编译结果逐一相等。工具链和依赖未变化，独立审计 not performed，全量 mutation 仍 UNAVAILABLE。

五个可执行源码文件按既有算法计算的指纹为 `f830a62f6da17d31e765542de79f9cebe8e447037ebe021006314fbd8638ca94`。最新交付 EXE 为 10,502,211 bytes，SHA256 `3814f03d137993f3c9b30a162d619e49e5a1c0d16fff79ba19a7892c8e491923`；正常退出旧进程后已同步桌面，哈希相同，并重新启动进入托盘。前一节哈希保留为历史构建。源码在本次最终构建后未再修改。

现场边界：当前会话已经恢复，真实未认证响应尚未采集；短暂主动注销复测已向用户询问，尚未得到允许，未执行注销。桌面修正版继续按保存配置监控，自然到期时会保留诊断摘要。没有宣称真实重新认证、手机共享或长时运行通过。

## 2026-10-06 用户现场确认与仓库更新

用户确认“已经实现了自动重新认证”，并要求将这一版更新到其 GitHub 仓库。该反馈是用户现场验收证据，确认最新交付版自动重新认证成功；未据此推断手机共享、完整的退出后到期重启流程或连续 24 小时运行已验收，也未主动注销会话。前文未验证状态保留为各次构建时的历史记录。

本次提交保留已验收的源码和 EXE，仅补充 README、决策与本记录中的现场结论。复用最终 73 项测试、编译和 5 组真实 EXE 工作流的有效证据；推送前核对当前源码指纹、暂存源码与包内 code object、仓库及桌面 EXE 的 SHA256，并执行差异和决策文档校验。最新 EXE 的 SHA256 为 `3814f03d137993f3c9b30a162d619e49e5a1c0d16fff79ba19a7892c8e491923`。
