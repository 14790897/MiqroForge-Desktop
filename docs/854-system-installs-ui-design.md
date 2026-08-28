# #854 方案：allow_system_installs 开通路径（设置页开关 + 系统包安装授权确认卡）

> 状态：方案待评审（2026-08-28）
> 关联：#820（系统包安装路由已落地）、#759（重型工具链不可达）、#646（确认卡机制已存在）

## 1. 现状与问题

- **功能已存在**：`tools.sandbox.allow_system_installs`（schema.py:458，默认 False）开启后，沙箱内 `sudo apt-get install ...` 自动路由到 WSL 发行版以 root 执行、跨会话持久（#820 已落地）
- **开通路径只有一条**：手改 `~/.miqi/config.json` + 重启——普通用户不可达
- **拦截行为**：关闭状态下 AI 执行安装命令 → `_SYSTEM_INSTALL_NOT_ENABLED_MSG`（shell.py:254）直接拒绝，文案指向"配置中开启"（非 UI）
- **设置页**：Sandbox 段只有 `enabled` 总开关（SettingsPage.tsx:554-567），`allowSystemInstalls` 0 命中
- **确认卡机制已存在**（#646）：`ask_user_confirm_card` 工具 → resolver（用户输入通道）→ 桌面卡片 → 用户选择返回。resolver 是注入式（ask_user_confirm.py:144），**工具层可直接复用弹卡**

## 2. 方案设计

### A. 设置页开关（P0，必做）

Sandbox 段新增 `SettingsToggle`：

```
沙箱隔离
  开启后 AI 的文件操作和命令执行在 WSL2 bwrap 沙箱中运行...
  [沙箱 🛡️] ← 现有
  [允许系统包安装] ← 新增（默认关）
  说明：开启后 AI 执行 apt-get 等系统包安装命令时，将以 root 在 WSL
  发行版中执行并跨会话持久（仅 Windows + WSL）。仅在信任 AI 行为时开启。
```

- 组件：`SettingsToggle` + `window.miqi.config.update({tools: {sandbox: {allowSystemInstalls: next}}})` + `invalidateConfigCache()`
- **生效方式（关键决策点，见评审问题 1）**：
  - 现状：bridge 初始化时读入 SandboxManager（loop.py:1880）→ 改配置需重启
  - 方案 A1（简单）：开关保存后提示"重启后生效"（toast）
  - 方案 A2（动态）：开关切换时通过 bridge IPC 调 `sandbox_manager.allow_system_installs = next`（实例属性可直接改，manager.py:362）+ 写 config 持久化 → **立即生效无需重启**
  - 推荐 A2（用户"简单"偏好：开了就要马上能用），A1 作兜底说明

### B. 系统包安装授权确认卡（P0，核心）

拦截点（shell.py:2243 `if not allow_system_installs` 分支）**不再直接拒绝**，改为**弹确认卡**：

```
┌─ 系统包安装授权 ──────────────────────────┐
│ AI 请求执行系统包安装：                    │
│   sudo apt-get install texlive-latex-base │
│ 将作为 root 在 WSL 发行版中安装，跨会话持久 │
│ [允许本次安装] [允许并记住（开启开关）] [拒绝] │
└──────────────────────────────────────────┘
```

- **实现**：拦截点直接调用确认卡 resolver（复用 #646 机制，不经过模型——模型已发出命令，是工具层的人机握手）
  - 卡标题/文案含**具体命令**（用户知情同意）
  - 选项：允许本次 / 允许并记住 / 拒绝（超时=拒绝，安全默认）
  - "允许本次"→ 路由执行（仅本次放行，不改配置）
  - "允许并记住"→ 放行 + `sandbox_manager.allow_system_installs = True`（动态生效）+ config 写入（A2 联动）
  - "拒绝"/超时 → 返回现有拦截消息（文案更新为指向设置页）
- **resolver 注入**：ExecTool 构造需要拿到确认卡 resolver（现 ask_user_confirm_card 有；需把同一 resolver 传给 ExecTool 或走 registry 共享通道）——实现细节评审后定
- **无桌面通道时**（CLI/无 UI）：保持现有直接拒绝行为（安全兜底，不弹卡）

### C. 拦截消息文案更新（P1）

`_SYSTEM_INSTALL_NOT_ENABLED_MSG` + `command_guard.py:103`：从"请在配置中开启 tools.sandbox.allow_system_installs"改为"请在 设置 > 沙箱隔离 中开启「允许系统包安装」（或允许本次安装）"——指向 UI 而非配置文件。

### D. 范围外（不做）

- 不做"拦截消息深链跳转设置页"（备选方案 2 太绕，确认卡直接解决）
- 不改默认值（安全设计不变：默认关闭 + 显式知情同意）

## 3. 涉及文件

| 文件 | 改动 |
|---|---|
| `apps/desktop/src/renderer/features/settings/SettingsPage.tsx` | Sandbox 段加开关（+~40 行） |
| `apps/desktop/src/main/bridge.ts` + `preload/index.ts` | （A2 时）新增 `sandbox.setAllowSystemInstalls` IPC（+~20 行） |
| `miqi/agent/tools/shell.py` | 拦截点弹卡（+~40 行） |
| `miqi/agent/tools/ask_user_confirm.py` | 暴露 resolver 复用入口（+~10 行） |
| `miqi/bridge/loop.py` / `server.py` | （A2 时）注册 IPC handler |
| 测试 | SettingsPage 单测（如有）+ shell 拦截 E2E（+2~3 个） |

## 4. 验收标准

1. 设置页开关存在、默认关、切换保存、**无需重启立即生效**（A2）
2. 关闭状态下 AI 执行 `sudo apt-get install` → **出现确认卡**（含具体命令）而非直接拒绝
3. "允许本次" → 安装执行成功（WSL root 路由），开关仍关闭
4. "允许并记住" → 安装成功 + 开关自动变开 + config 已写入
5. "拒绝"/超时 → 拦截消息（文案指向设置页）
6. 无桌面通道（CLI）→ 保持直接拒绝
7. 实机 E2E：真实桌面 app + 真实模型触发安装 → 确认卡截图 → 允许 → 安装成功截图

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 工具层弹卡与模型 turn 的交互（卡等待时 turn 状态） | #646 机制已处理（turn 暂停等待）——复用同一通道 |
| 确认卡滥用（每次安装都弹卡烦） | "允许并记住"一键永久开；会话级记忆不适用（安全场景要显式） |
| 动态改实例属性与 config 不同步 | A2 实现时保证"改属性 + 写 config"原子成对 |
| ExecTool 无 resolver 通道 | 从 registry 共享（ask_user_confirm_card 的 resolver 注入点扩展） |

## 6. 评审问题

1. **生效方式**：A2（动态立即生效，需要新增 IPC）vs A1（重启生效，零后端改动）？推荐 A2——"开了就要马上能用"才符合"简单"
2. **确认卡选项**：三选项（允许本次/允许并记住/拒绝）还是两选项（允许/拒绝）？"允许并记住"直接写配置是否越权？
3. **弹卡时机**：只在 `allow_system_installs=False` 时弹卡，还是每次安装都弹（即使已开启）？——建议：已开启时不再弹（开关本身就是授权），拦截时弹
4. **无桌面通道兜底**：CLI 保持直接拒绝，是否需要 CLI 专属的交互确认（如 y/n 提示）？——建议本期不做（CLI 用户少，保持安全拒绝）
5. 设置页开关文案是否足够"知情"（用户能理解 root + 持久化的含义）？
