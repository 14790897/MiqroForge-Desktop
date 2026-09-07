---
name: e2e-exec-slow-spawn-timeout
description: E2E 环境（Electron 桥接）下每次 Git Bash 子进程 spawn 要 25-30 秒，exec 相关 E2E 必须把 tools.exec.timeout 配大并避免拿 UI 静止当同步点
type: project
---

E2E（`launchElectronApp` 起的 Electron 桥接 Python）里，**每一次** exec 子进程（Git Bash，`create_subprocess_exec(bash, "-c", ...)`）实际耗时约 25-30 秒——本机直接跑同样命令只要 0.1s，慢在 Electron 桥接环境下（疑似本机安全软件扫描新父进程链）。而 SandboxSelection 超时默认硬编码 30s（`SandboxPolicyEngine.default_timeout_ms`），exec 会在边界上随机超时（"命令在 30 秒后超时"）。

**Why:** issue #811 E2E 调试时，session 内 `rm -rf` 用例反复在 30s 超时与 29.9s 完成的边界上抖动，mock 驱动的多 exec 序列（mkdir → rm → ls）每步都要等 25-30s，整个 spec 单用例约 1.5 分钟。

**How to apply:**
1. 写 exec 相关的 E2E spec 时，在 `patchConfig` 里把 `config.tools.exec.timeout` 调到 120（已从 factory 接入 selection 超时，见 #850）。
2. **禁止拿 main 文本静止当同步点**：工具执行期间 UI 静止是正常状态，`waitForResponseComplete` 会在第一轮 exec 就过早返回——必须用 `expect(locator.getByText(marker)).toBeVisible({timeout: 180_000})` 轮询最终标记。
3. mock 状态机驱动 exec 时，把工具结果折算进最终回复文本（如 `REPRO_A_DONE OK/BLOCKED`），断言最终标记而不是直接找工具输出——UI 中 exec 输出不一定渲染在 main.textContent。
4. 单测（pytest 直接调 ExecTool）不受影响，只有 Electron E2E 环境有这个慢速。
