# 开发者笔记（Claude Code 会话记忆导出）

本目录是 **Claude Code 会话自动记忆**的导出——开发过程中由 AI 助手沉淀的实战经验、
踩坑记录与架构地图，按主题分类供团队成员查阅。

> ⚠️ **阅读须知**：这些笔记是**某一时间点的观察**，不是活文档。代码行为、
> 文件行号引用可能已过时，引用前请对照当前代码验证。每篇笔记记录了落笔日期。
>
> 最近一次全量同步：**2026-10-15**（共 104 篇）。

### 协作方式与约定

- [autonomous-action](autonomous-action.md) — 用户希望少问问题、自己做决定并直接执行，不要频繁抛选项征求确认
- [auto-create-pr-after-task](auto-create-pr-after-task.md) — 完成任务（代码改动+测试通过）后自动提交、推送并创建 PR，不用等用户要求
- [auto-handle-pr-reviews-and-ci](auto-handle-pr-reviews-and-ci.md) — 用户要求自动监督本会话 PR 的评审意见与 CI 并自动处理（修复→推送→回复线程→resolve→dismiss 已处理的阻塞评审），无需逐次询问
- [plain-language-communication](plain-language-communication.md) — 2026-09-29 起：对用户说话、以及 PR/issue 评论都要用平实中文，先说结论再讲原因，不堆术语/行号
- [no-alternatives-section-in-issues](no-alternatives-section-in-issues.md) — 创建 issue 时不写「备选方案」节——用户明确要求省略（2026-09-08）
- [fix-unreasonable-constraints](fix-unreasonable-constraints.md) — 遇到不合理的约束（CI 校验、模板、脚本规则）要主动改掉约束本身并提 PR，而不是改自己的产出迁就
- [invoke-github-workflow-skill](invoke-github-workflow-skill.md) — 用户说"创建pr""pr模板""创建issue""上传截图/贴图到PR"等触发词时，先调 github-workflow skill
- [pr-screenshot-mandatory](pr-screenshot-mandatory.md) — PR「截图」节强制真实图片；2026-09-20 起必须是真实运行界面/窗口的截图（HTML 重绘不算），附 App 界面与真实终端两条采集路径
- [use-luma-mcp-for-images](use-luma-mcp-for-images.md) — 读图先用 Read 工具直接读（当前模型支持），Read 失败才回退 mcp__luma-mcp__image_understand
- [task-assets-panel-design-principles](task-assets-panel-design-principles.md) — 任务资产面板的判定/展示原则（用户 2026-09-16 拍板）：agent 显式声明结果文件；交付根顶层产物必须逐张显示，批量中间目录收起
- [real-account-features-need-live-e2e](real-account-features-need-live-e2e.md) — 涉及真实账号的功能必须补真实账号 live E2E（不只 mock 契约测试）
- [verify-fix-in-real-app-not-just-mocks](verify-fix-in-real-app-not-just-mocks.md) — mock smoke 通过 ≠ 修复在真实路径生效——provider/桥相关改动要在真实应用里跑一遍，别用「commit 已覆盖」回话
- [local-e2e-before-cloud-verification](local-e2e-before-cloud-verification.md) — 为了加快速度：先在本地把 E2E 跑通，最后再推云端让 CI 做最终验证；不要靠反复推云端来迭代
- [e2e-must-have-real-ai-test](e2e-must-have-real-ai-test.md) — 用户要求（2026-09-30）e2e 技能必须规定「真实 AI 测试」为硬性规则；mock-only 覆盖不算交付
- [legacy-main-path-only](legacy-main-path-only.md) — 用户明确要求（2026-09-05 更正）：新功能只做 legacy 桌面主路径（miqi/runtime/）——KUN runtime 未接入主执行路径，不作为开发目标

### PR / Issue / 发布流程

- [pr-base-is-develop](pr-base-is-develop.md) — PRs target the develop branch; sync with develop via merge, not rebase
- [develop-to-main-sync-pr](develop-to-main-sync-pr.md) — develop↔main 双向同步 PR 的标准格式 + 每周三/周五定时发版（保留）+ develop 每次推送发 -dev.N 预发布（不写文件、不打包）
- [check-competing-pr-before-issue](check-competing-pr-before-issue.md) — 开工 issue 前先查是否已有 PR 在处理——#810 曾撞车 PR #845，白做一遍实现
- [stale-pr-branch-spec-mismatch](stale-pr-branch-spec-mismatch.md) — CI 失败先查 PR 分支是否落后 develop——workflow 来自基分支但代码来自 PR head，develop 上已修的 spec 不会生效
- [git-push-proxy-issue](git-push-proxy-issue.md) — HTTP_PROXY/HTTPS_PROXY env vars block Git push
- [miqroera-github-mirror-sync](miqroera-github-mirror-sync.md) — origin 是双 pushurl（miqroera + GitHub）顺序推两份，GitHub 腿可能静默失败；含 .github/workflows 改动时 GitHub 腿需走 SSH（gh token 缺 workflow scope）
- [github-native-stacks-conflict](github-native-stacks-conflict.md) — GitHub 原生 stacked PR 与本仓 merge 约定冲突（trunk 前移即失去线性历史、子 PR 被 trunk ruleset 评估）；解散用 POST /stacks/{n}/unstack
- [github-release-immutability-tombstone](github-release-immutability-tombstone.md) — GitHub release immutability 开启后 v0.23.0 tag 被墓碑封锁、资产上传失败的完整事故链与解法
- [github-asset-name-sanitization](github-asset-name-sanitization.md) — GitHub Release 上传资产把文件名空格替换成点；electron-updater 的 latest.yml path 必须与资产名一致，否则 404
- [repo-actions-token-permissions](repo-actions-token-permissions.md) — MiQroForge-Desktop 仓库 Actions 权限事实——GITHUB_TOKEN 不能建/批 PR 但可提交 REQUEST_CHANGES；默认权限 read，workflow 必须显式声明 permissions
- [skip-ci-suppresses-workflow-runs](skip-ci-suppresses-workflow-runs.md) — 提交信息里带 [skip ci] 会让 GitHub 对 release/tag/pull_request/push 事件都不创建 workflow run；本仓库自己写 commit/PR 标题时必须避开这个字面量
- [release-sync-workflow-never-fires](release-sync-workflow-never-fires.md) — sync-main-into-develop 的 release 事件从未触发过（仓库 release run 总数为 0），导致 develop 版本号/提交长期落后 main
- [conflict-watchdog-behavior](conflict-watchdog-behavior.md) — 冲突看门狗（conflict-watchdog.yml）的既定行为与用户定下的边界：冲突时标记 changes requested + 只取消 14790897 本人的待评审请求，不碰任何已提交评审
- [coderabbit-config-precedence](coderabbit-config-precedence.md) — CodeRabbit 配置来源与优先级（Org UI vs .coderabbit.yaml）+ inheritance 默认关闭的坑；本仓 2026-10-08 起有仓库配置文件（drafts: true）
- [pr929-review-findings](pr929-review-findings.md) — PR #929（模型选择收口）max-effort 审查结论：15 条 finding 待修复，集中在 merged-model 门控与 resolvable≠usable
- [repo-renamed-miqroforge-desktop](repo-renamed-miqroforge-desktop.md) — 桌面仓库归属变更链：14790897/MiQi → 14790897/MiqroForge-Desktop → miqro-org/MiqroForge-Desktop；gh 旧名自动重定向
- [miqroforge-desktop-rename](miqroforge-desktop-rename.md) — 规范拼写为 MiQroForge（大写 Q、大写 F）— 书面/界面/文档一律用新名，Qraft 平台名同步更名
- [miqro-org-migration-fallout](miqro-org-migration-fallout.md) — 2026-10-12 仓库迁到 miqro-org 后掉了哪些设置：ruleset bypass actor、Pages 构建源、文档自定义域名的继承关系（含恢复手法与新文档地址）

### E2E 测试

- [e2e-ci-needs-miqi-config](e2e-ci-needs-miqi-config.md) — E2E/CI 跑真实应用前必须存在 ~/.miqi/config.json，否则应用停在首启动向导、chat-input 永不挂载（2026-09-22 云端登录流水线首跑 5/5 失败根因）
- [e2e-config-patch-clobbered-by-helper](e2e-config-patch-clobbered-by-helper.md) — launchElectronApp 的 patchConfig 改 channels 会被 helper 整体覆盖——反馈通道 E2E 要用 config.update + 轮询，并串行跑
- [e2e-exec-slow-spawn-timeout](e2e-exec-slow-spawn-timeout.md) — E2E 环境（Electron 桥接）下每次 Git Bash 子进程 spawn 要 25-30 秒，exec 相关 E2E 必须把 tools.exec.timeout 配大并避免拿 UI 静止当同步点
- [e2e-inject-chat-events](e2e-inject-chat-events.md) — E2E 测试前端聊天渲染契约可用主进程 webContents.send('chat:progress') 注入事件，需挂起 mock 保持回合 in-flight
- [e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md) — E2E 的 --user-data-dir 被 main 的 app.setPath 覆盖，localStorage 在同 checkout 的所有运行间共享
- [e2e-local-run-bvse-skill](e2e-local-run-bvse-skill.md) — 本机真实跑 bvse-mof skill 的完整前置（venv 位置、测试 CIF、Tsinghua 镜像装依赖、env 门控 spec、耗时约 8-10 分钟）
- [e2e-probe-false-positive-trap](e2e-probe-false-positive-trap.md) — E2E 中用 locator('main').getByText(<提示词里的 marker>) 断言会命中用户自己的消息 → 假阳性；探针门禁曾有此坑（issue #1120 / PR #1122）
- [e2e-real-llm-hard-fail-rerun](e2e-real-llm-hard-fail-rerun.md) — electron-e2e 的 real-LLM 用例偶发「全重试失败」阻塞 PR（回合超时/模型行为抖动）——处理=重跑失败 job，跟踪 issue #1193
- [e2e-window-offscreen-default](e2e-window-offscreen-default.md) — E2E 应用窗口默认停在屏幕外（MIQI_E2E_OFFSCREEN），以及为什么不能用真·最小化（fullPage 截图超时）
- [e2e-direct-screen-recording](e2e-direct-screen-recording.md) — E2E 录屏用 Electron desktopCapturer+MediaRecorder 录窗口流（真·窗口录制，全自动）；备选 ffmpeg gdigrab 桌面区域裁剪；不要截图拼帧/抽帧
- [local-sandbox-e2e-unavailable](local-sandbox-e2e-unavailable.md) — E2E 沙箱不初始化的原因是 dev config sandbox.enabled=false，不是机器问题——spec 里 patch enabled:true 后秒级就绪
- [local-sandbox-enabled-breaks-host-path-specs](local-sandbox-enabled-breaks-host-path-specs.md) — 本机 tools.sandbox.enabled=true 会被 E2E 复制进临时 home，工具走 WSL 沙箱而 /mnt/c 只读，写宿主路径的 spec 全挂；对照法判断是否本机环境问题
- [macos-ci-local-mock-server](macos-ci-local-mock-server.md) — macOS GitHub runner 上 E2E 无法使用本地 mock 服务器（undici fetch 连不上 127.0.0.1 监听、spawn 的 python stdout 管道不投递），需跳过或改用其他验证方式
- [playwright-beforeall-per-worker-e2e](playwright-beforeall-per-worker-e2e.md) — Electron E2E 的 beforeAll 每 worker 一次，worker 重启会重新 launchElectronApp（全新 MIQI_HOME），依赖上一条用例会话的 spec 会挂
- [playwright-describe-skip-kills-timeout-option](playwright-describe-skip-kills-timeout-option.md) — describe 层的 test.skip() 会让单测级 { timeout } 选项失效（info.timeout 仍是项目默认 600s）——长用例必须 test.setTimeout() 兜底
- [sandbox-and-workspace-switch-e2e-facts](sandbox-and-workspace-switch-e2e-facts.md) — 工作目录切换会新建会话、sandbox 在创建时读「已落盘」workspace；ubuntu runner 无法 unshare-user 故 sandbox 用例须确定性 skip
- [session-scoped-bridge-reads-null](session-scoped-bridge-reads-null.md) — E2E/桥接下裸名+session_key 的 documents.parse/files.read 返回 null，必须用 sessions/<safeKey>/files/<name> 全路径且不带 session key
- [smoke-privacy-gate-and-cwd-sensitive-tests](smoke-privacy-gate-and-cwd-sensitive-tests.md) — smoke 跑前必须重建 out/renderer + mock 需预置隐私同意与 config.onUpdated；exec-timeout 测试对 cwd 敏感（node_modules junction）
- [ci-only-zero-width-layout-collapse](ci-only-zero-width-layout-collapse.md) — 只在 CI（Linux 宽字体）复现的 0 宽布局塌陷——toBeVisible 报 "resolved but hidden" 的排查与守卫写法
- [live-login-e2e-diagnosis](live-login-e2e-diagnosis.md) — 真实登录 live E2E 挂在 #login_phone 时的排查路径——本地真浏览器打开平台登录页验证，bundle grep 不可信
- [local-real-config-no-provider](local-real-config-no-provider.md) — 本机 e2e 真实 LLM 用例会被「尚未配置模型服务」发送门禁拦截，与代码无关

### 架构 / 运行时

- [confirm-card-two-runtime-map](confirm-card-two-runtime-map.md) — ask_user_confirm_card 确认卡功能的双路径实现地图——KUN 与 legacy 各有哪些组件、真实流程的 thread_id≠session_key 等关键事实
- [confirm-card-issue-714-fix](confirm-card-issue-714-fix.md) — issue #714 修复要点 — 最终方案为同回合并发确认卡排队串行（PR #718）；#716 的拒绝方案被用户否定并替换
- [revert-1071-646v2-merged](revert-1071-646v2-merged.md) — #1071（646-v2 计划/确认卡）已于 2026-09-22 经 PR #1205 回退并合入 develop；确认卡/PlanCard 类旧记忆只代表回退前状态；相关 issue/PR 的后续处理清单
- [1094-truncated-tool-args-refusal](1094-truncated-tool-args-refusal.md) — #1094 输出上限截断的工具参数——provider 标记 ToolCallRequest.truncated + legacy 拒执与回显防孤儿 + 纯文本截断留痕
- [issue981-thread-tab-wiring-gap](issue981-thread-tab-wiring-gap.md) — 子线程 tab 原本无生产者（agent:spawned 无人发送）；已在 PR #1218 接线（d7701d04），合并前 develop 仍走不到
- [mcp-desktop-wiring](mcp-desktop-wiring.md) — MCP 桌面主路径接线（RuntimeSession 连接 config MCP 服务器）+ anyio cancel-scope 跨 task 坑 + E2E 测试基础设施
- [desktop-mcp-via-miqrogate](desktop-mcp-via-miqrogate.md) — 桌面端 MCP 改经 MiQroGate 接入的需求（miqro-gate#1）——两边现状、真正卡点、范围边界
- [sandbox-write-boundary-984](sandbox-write-boundary-984.md) — #984 沙箱写边界（层 1+2+3）—— /mnt 只读 + per-call rw bind + 静态词表，边界与残余缺口清单
- [watchdog-false-timeout-after-interrupt](watchdog-false-timeout-after-interrupt.md) — 中断+切会话+恢复后出现"后端 60s 无响应"是前端看门狗僵尸误报，后端其实在正常流式输出
- [dual-instance-localstorage-degradation](dual-instance-localstorage-degradation.md) — 双开时第二个 Electron 实例的 Chromium 存储退化成内存——localStorage 读不到也写不进；长期状态别只放 localStorage
- [svg-embed-sanitizer-dompurify-traps](svg-embed-sanitizer-dompurify-traps.md) — SvgEmbed 的 DOMPurify 消毒边界——ALLOWED_URI_REGEXP 全局语义、use 需 ADD_TAGS、预解析 fail-open 三个坑（PR #843 修复）
- [tailwind-v4-translate-transform-stacking](tailwind-v4-translate-transform-stacking.md) — Tailwind v4 的 translate-x 工具类落到独立 translate 属性，与 keyframes 的 transform 叠加——浮动居中元素做 transform 动画时会双重位移
- [ddgs-backend-invalid-names](ddgs-backend-invalid-names.md) — ddgs 9.14.4 中 backend="html"/"lite" 是无效名会静默回落 auto——web.py/search_orchestrator 存量值是 no-op，PR #1023 因此返工
- [qraft-netfetch-manual-redirect](qraft-netfetch-manual-redirect.md) — Electron net.fetch 对 redirect:manual 的 302 直接 reject（"Redirect was cancelled"），授权码流程须回退 undici fetch
- [gateway-creds-handshake-map](gateway-creds-handshake-map.md) — 网关凭据「握手文件」不一致的根因与修复（#1258/PR #1261）：前端看内存 store、后端读 <workspace>/.qraft/token.json，失败全静默；Windows junction 会让 syncTokenFile 拒写
- [gateway-model-auto-readiness](gateway-model-auto-readiness.md) — 登录后默认模型自动就绪（#1172/PR #1173）：判空逻辑为何失效、active_model_resolvable 判据、smoke mock 默认登录态带网关的坑
- [relogin-notification-feature-map](relogin-notification-feature-map.md) — 平台登录失效全局告知的实现地图——横幅/顶栏 chip/发送拦截三件套、状态机语义、E2E mock 技术
- [login-gate-mandatory-login-1095](login-gate-mandatory-login-1095.md) — #1095 登录收口（未登录强制退出）的实现地图——门控位置、首帧同步登录态、MIQI_LOGIN_BYPASS 绕过链、E2E 位置
- [login-entry-visibility-issue-1000](login-entry-visibility-issue-1000.md) — #1000 登录入口显性化的实现地图——共享组件、五处入口、E2E 覆盖与文案约定
- [pr-613-skill-discovery](pr-613-skill-discovery.md) — PR #625 已拆分为 #642（基础技能索引）+ #643（触发词增强），父子关系

### 计费与平台集成

- [slurm-mcp-billing-map](slurm-mcp-billing-map.md) — Slurm MCP 作业计费架构地图——Python 握手桥 + Desktop 扣费发起方、10 分/次、charge_id 去重、扣费历史、capabilities 白名单坑
- [billing-history-missing-diagnosis](billing-history-missing-diagnosis.md) — 排查「扣费历史/登录态消失」类反馈的路径——dev userData 按 checkout 隔离（ws-hash=sha256(<checkout>/apps)）、主进程日志里的扣费/登出行为
- [qraft-refresh-rotation-and-invalidation](qraft-refresh-rotation-and-invalidation.md) — Qraft/MiQroForge 平台 refresh_token 真实语义（2026-09-15 实测更正）：不轮换、可复用、30 天 TTL，但平台会成批作废——「登录每天失效」根因
- [gateway-403-content-moderation](gateway-403-content-moderation.md) — MiQroForge 平台 AI 网关内容安全拦截返回 403 sensitive_word_detected；表现为「模型服务认证失败」误报
- [gateway-model-canonical-name](gateway-model-canonical-name.md) — 平台聚合网关的规范模型名是 deepseek-flash（deepseek-v4-flash 只是别名）——CI/终端命令按规范名写
- [platform-error-envelope-original-message](platform-error-envelope-original-message.md) — MiQroForge 平台业务错误信封——真实异常只在 data.originalMessage，顶层 msg 恒为「未知错误」；积分接口 2026-10-08 服务端 SQL 异常
- [platform-domain-migration](platform-domain-migration.md) — 2026-09-17 生产平台/OAuth 域名已迁移至 www.miqroforge.com（PR #1119，只改生产）；浏览器登录入口的平台侧遗留问题
- [ai-gateway-https-policy](ai-gateway-https-policy.md) — #922 AI 网关地址现状与 https 强制策略——默认 http 仅测试环境,显式 QRAFT_GATEWAY_BASE 强制 https
- [feedback-platform-channel-map](feedback-platform-channel-map.md) — 应用内反馈双通道地图——平台 POST /oauth2/feedback（登录加写）+ 飞书兜底，标题字段已移除，Bitable 标题由正文首行派生
- [legal-documents-feature-map](legal-documents-feature-map.md) — 法律文件体系（律师定稿 5 份，v2.0 中文唯一权威）落点地图、文案不可被格式化器改动的约束、待决的搜索引擎可达性
- [miqro-gate-repo-map](miqro-gate-repo-map.md) — miqro-gate（MiQroGate 网关仓库）协作事实——分支、权限、标签、本地克隆位置、模板已对齐桌面端
- [cloud-agent-issue-1271](cloud-agent-issue-1271.md) — 全天候云端 Agent 立项（对标 Grok Bot）issue #1271，前置阻塞是「本地优先/数据不上云」的产品定位决策
- [quantum-api-feature-requirement](quantum-api-feature-requirement.md) — 例会提出 desktop 需要量子计算 API 调用功能（2026-09-28）
- [new-sixiangjia-de-is-network-path](new-sixiangjia-de-is-network-path.md) — new.sixiangjia.de 慢是本地出国线路问题，不是服务端；服务端 TLS/h2/h3 已配好，别去调它

### 构建 / 打包 / 环境 / 工具

- [buildresources-license-files-dmg-gotcha](buildresources-license-files-dmg-gotcha.md) — build/ 下的 license_*.txt 会同时被 NSIS 和 DMG 打包拾取，中文文本会让 macOS 打包失败
- [electron-updater-integration](electron-updater-integration.md) — 桌面端自动更新接入要点（#1124）：CJS getter 导出坑、latest.yml/dev.yml channel 规则、本地假 feed 验证法、产物命名一致性
- [nsis-installer-ui-automation](nsis-installer-ui-automation.md) — 无人值守驱动 NSIS 安装器（点同意/下一步）与截被遮挡窗口的方法；AppActivate/SetForegroundWindow 会被前台锁拒绝
- [macos-packaging-release-only](macos-packaging-release-only.md) — macOS 打包只在发版跑（PR #1077 从 PR CI 移除 macos-build）；pull_request_target 用基分支 workflow 定义，改动下个 PR 才生效
- [wfp-blocks-ipv6-loopback](wfp-blocks-ipv6-loopback.md) — 本机 WFP 网络过滤组件拦截回环（IPv6 ::1 + 按二进制身份拦未签名进程的 IPv4 回环），dev 连不上 Vite、打包版卡「启动中」，已有 #895 + #898 双层修复
- [wsl-wizard-live-test-findings](wsl-wizard-live-test-findings.md) — WSL2 一键安装向导真机实测发现：ExitCode 怪癖、docker-desktop 阻断、卸载不持久，PR #972 已修
- [worktree-node-modules-junction](worktree-node-modules-junction.md) — MiQi worktree 里跑测试/构建前要 junction 主仓库的 node_modules 和 .venv，并装 Playwright 浏览器
- [uv-run-rewrites-uv-lock](uv-run-rewrites-uv-lock.md) — 本仓库跑 uv run/uv sync 会重写 uv.lock（版本号漂移），提交前要 checkout 还原
- [loguru-stream-sink-encoding](loguru-stream-sink-encoding.md) — loguru add() 对 stream sink 拒绝 encoding 参数（只有路径 sink 接受），正确做法是 reconfigure + PYTHONIOENCODING
- [dirty-api-key-httpx-crash](dirty-api-key-httpx-crash.md) — 用户曾在 API Key 字段粘贴 key 后输入中文备注「用这个」→ httpx 构建 Authorization 头报 UnicodeEncodeError
- [heredoc-escaping-pitfall](heredoc-escaping-pitfall.md) — 用 heredoc 喂 Python 脚本做文件补丁会踩反斜杠转义坑，改用 Edit/Write 工具
- [docs-site-publish-pipeline](docs-site-publish-pipeline.md) — 文档站发布管道：Actions 按仓库 docs/ 构建（gh-pages 已废弃）；页面必须进 docs/ + mkdocs nav 才会发布
- [docs-url-single-source-mkdocs](docs-url-single-source-mkdocs.md) — 文档站/仓库地址唯一来源是仓库根 mkdocs.yml，构建期注入渲染层；src/shared 不能引 node:fs（typecheck:web 无 node types）
- [verify-workflow-shell-locally](verify-workflow-shell-locally.md) — 本地复跑 CI workflow 里 shell 逻辑的方法（抽取 run 块 + 下载 jq），以及 Windows 版 jq stdout 加 CRLF 的伪影
- [skill-copies-and-sync](skill-copies-and-sync.md) — e2e/github 技能存在三处副本（仓库 skills/、桌面 app 的 .claude/skills/*.md+zip、用户 ~/.claude/skills/），仓库为准；只改个人副本会丢
- [extract-pasted-screenshots-from-jsonl](extract-pasted-screenshots-from-jsonl.md) — 用户粘贴到对话里的截图不落盘，可从会话 JSONL 提取 base64 再上传到 _gh-imgup（issue/PR 配图用）

## 说明

- **来源**：Claude Code 会话自动记忆（`~/.claude/projects/<项目>/memory/`）。导出时对集群
  SSH 地址与密钥路径、平台内部网关地址、个人本机路径等做了占位符替换；测试账号
  （18500000000 / 1q2w3e4R）是公开测试凭据，原样保留。
- **未导出**：含真实凭据的笔记（测试账号手机号/密码、MCP 服务端密钥、SSH 私钥路径）、
  已失效的分支指针记录，以及记忆导出流程本身。
- **维护**：本目录是记忆的镜像，最近一次全量同步见上方日期。新增经验除了更新记忆，
  也建议沉淀到对应主题的正式文档（[architecture](../architecture.md)、
  [contributing](../contributing.md) 等）。
