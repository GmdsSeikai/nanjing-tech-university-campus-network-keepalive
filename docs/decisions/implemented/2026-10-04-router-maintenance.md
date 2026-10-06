# Decision: 桌面认证出口与监控生命周期

Status: implemented

## Problem

电脑局域网身份不等于校园网识别的路由器出口。门户可访问也不代表出口需要认证。GUI 两个独立循环不能可靠保证注销和退出后停止认证。

## Decision

`drcom_api.AuthContext` 拥有当前出口身份和门户配置；路由器模式每次认证重新读取宿舍门户、`/drcom/chkstatus` 和 `/eportal/portal/page/loadConfig`。页面和在线结果矛盾、出口 IP 或配置缺失时拒绝发送密码。门户 JS `a41.js` 使用 `online_list`，`a43.js` 按 `account_prefix` 为电脑账号增加 `,0,`；当前现场配置 `login_method=1`、`account_prefix=1`、`check_online_method=1`。这些配置由运行时查询决定，不把现场 IP、MAC 或页面索引写死。

`MaintenanceMonitor` 拥有一个串行工作线程和请求队列；GUI 与后台入口共用该实现及 Windows 命名 mutex。状态由外网探测及只读门户查询决定，认证接口成功不等于外网恢复。在线时仅探测，不认证；临时失败使用 30/60/120/240/300 秒退避。

监控状态为启用、配置关闭、主动暂停、账号错误暂停、停止。配置更新解除账号错误暂停；手动登录解除主动暂停；注销首先主动暂停，保存账号或重新开启选项不能解除主动暂停。停止取消等待及后续请求，等待当前 HTTP 请求返回、关闭 session，再销毁 Tk 窗口及释放 mutex。UI 事件由队列进入主线程，退出取消 Tk timers。

ConfigManager 迁移旧自动开关的 OR 到 `auto_maintain`。旧文件缺少 `router_mode` 时为 false，新文件为 true；服务增加中国移动。保留旧自动字段，设置新开关时同步旧字段，密码继续 DPAPI。`user_index` 字段仅保留调用形状，不制造或作为会话依据。

## Alternatives considered

- 继续使用本机身份：路由器后提交错误出口。
- 门户可访问就提交认证：外网或运营商故障会导致重复提交密码。
- 保留两条循环或再启动后台进程：暂停和锁的责任分散，增加注销后重连竞争。
- 声称发送会话续期心跳：没有接口证据；只发送连通性流量并在明确到期后重新认证。

## Consequences

真实 MAC 必需的限制已由 [到期后认证决策](2026-10-06-router-expiry-auth.md) 部分替代；该记录拥有缺少 MAC 的协议例外、在线列表语义和登录阶段。请求发出后不能撤销，只能等待其有界完成。强制会话到期无法通过探测流量保证延长。永久错误分类依赖明确响应文本，未知错误保持退避。一次客户端更新原子替换 EXE；不支持旧 EXE 继续处理路由器模式。

## Verification

测试执行生产上下文与认证路径，HTTP 边界使用模拟响应。真实 Windows Tk、tray 和跨进程 mutex 已测试；现场仅执行只读上下文、在线状态和连通性查询。构建及最终验证记录见 [EVIDENCE](../../EVIDENCE.md)。真实密码认证、手机共享和长时观察尚未完成。

## Rollback

退出客户端后替换旧 EXE，配置及 DPAPI 密码留在同一 Windows 用户目录。使用旧 EXE 必须改回电脑直连方式；旧版不理解中国移动/路由器出口，不能用回退验证这两项能力。自动开关同步旧字段，无数据库或路由器变更。
