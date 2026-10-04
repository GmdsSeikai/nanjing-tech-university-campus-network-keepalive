# Windows 桌面交付说明

交付文件为 `release/CampusNetLogin.exe`，详细操作见 [README.md](README.md)。运行前确保电脑连接校园网或宿舍路由器；首次配置服务“中国移动”、原始学号、统一身份认证密码和“通过路由器连接”。保存账号并开启“自动保活与重连”。

更新时从托盘选择“退出”，等待进程结束，再替换 EXE。账号配置继续使用同一 Windows 用户目录，密码保留 DPAPI 保护。GUI 与后台监控共用锁，二者不应同时启动。

构建：Windows 终端运行 `build.bat`。该脚本在 `build/.venv` 中隔离依赖，完成测试和 PyInstaller 单文件打包。产物为 64 位 Windows GUI 程序，也接受 `--monitor` 与 `--once`。交付无需 ConfirmServer、远控代理、手机程序或路由器固件修改；本次不创建计划任务。

日志位于 `%USERPROFILE%/.campus_net_login/logs/monitor.log`。现场验收需确认电脑与手机共享联网并连续观察至少 24 小时，包含夜间闲置。构建/测试成功不等于现场认证与长时效果通过。
