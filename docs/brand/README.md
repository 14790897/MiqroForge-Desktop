# MiQroForge 品牌视觉规范（桌面端）

> 来源：《MiQroForge平台配色.pptx》+ 官方 Logo 素材（标志1.png / 标志2.png / 源文件 .ai）。
> 本文件是桌面端主题的唯一事实来源，改动前先读这里，防止品牌色漂移（#828）。

## 1. 品牌色

| 名称 | 色值 | 用途 |
|---|---|---|
| 主品牌色 | `#EA653D` | `--accent`、主按钮、强调元素（浅色/暗色主题统一） |
| 辅助品牌色 | `#0B7F91` | 渐变/点缀（如模式选择器渐变、手动档色点） |

## 2. 功能色（两套主题共用同一语义色）

| 语义 | 色值（暗色） | 色值（浅色文本档） | CSS 变量 |
|---|---|---|---|
| 成功 | `#10B981` | `#087A4F` | `--success` / `--success-text` |
| 提示 | `#F59E0B` | `#B45309` | `--warning` / `--bypass` |
| 错误 | `#FF6161` | `#C04040` | `--danger` |
| 执行 | `#3B82F6` | `#1D6FD8` | `--info` |
| 失效 | `#999999` | 禁用态（`disabled:opacity-40/50` 表达） | 无独立变量 |

> 色相两套主题一致（同一种状态同一种颜色认知）;规范原值为暗底设计,
> 浅色 12px 文本用保持色相的深档变体保证 WCAG ≥4.5:1（参照 --accent-hover 模式）。

## 3. 文字色

| 层级 | 色值 | 浅色主题 | 暗色主题 |
|---|---|---|---|
| 强调/标题 | `#F4F4F6`（暗）/ `#121212`（浅） | `--text` | `--text` |
| 次强调/文本描述 | `#D0D6E0`（暗）/ `#555555`（浅） | `--text-muted` | `--text-muted` |
| 辅助文本 | `#8A8F98` | `--text-faint` / `--placeholder` | `--text-faint` / `--placeholder` |
| 弱文本 | `#62666D`（暗）/ `#8A8F98`（浅） | `--placeholder` | `--placeholder` |
| 纯白 | `#FFFFFF` | `--accent-text` | `--accent-text` |

## 4. 背景色

| 层级 | 色值 | 浅色主题 | 暗色主题 |
|---|---|---|---|
| 主背景 | `#FFFFFF`（浅）/ `#08090A`（暗） | `--background` | `--background` |
| 一级层次 | `#FFFFFF`（浅）/ `#0F1011`（暗） | `--surface` / `--topbar-bg` / `--panel-bg` | 同左 |
| 侧栏 | `#F6F5F2`（浅米灰，WorkBuddy 式）/ `#0F1011`（暗） | `--sidebar-bg` | 同左 |
| 二级层次 | `#F2F2F0`（浅）/ `#161718`（暗） | `--surface-muted` / `--surface-3` | 同左 |

## 5. Logo 使用规范

LOGO 元素 = **量子、卡片、节点、连接**（`MiQroForge元素组成.txt`）。

| 场景 | 使用素材 | 说明 |
|---|---|---|
| 应用内图标（侧栏/顶栏等 28px 位） | `apps/desktop/src/renderer/assets/brand/logo-icon-light.png`（彩色）/ `logo-icon-dark.png`（彩色提亮版） | `MiQroForgeLogo` 组件按 `.dark` 类切换；深色版为提亮增强（深蓝→亮蓝，红橙+25%），避免深色背景上细节丢失 |
| 窗口/打包图标 | `apps/desktop/src/renderer/assets/icon.ico` / `icon.icns` | 基于标志2 生成（多尺寸 16–1024），electron-builder 引用 |
| 官方素材原件 | `docs/brand/标志1.png`（白色版）/ `标志2.png`（彩色版）/ `MiQroForge标志源文件.ai` | 设计/文档用，勿直接引用运行时 |

**规则**：
1. 不要直接在组件里写死旧 Logo（红金波浪 `MiQiLogo` 已废弃删除）。
2. 新增 Logo 展示位用 `MiQroForgeLogo` 组件，不要自造 SVG。
3. 深色场景禁止用原版深蓝节点色 `#232D4B`（会融入背景），用提亮版或提亮逻辑。
4. 任何色值改动需同步更新本文件与 `globals.css` 的对照注释。

## 6. 主题对照速查（关键 token）

| 变量 | 浅色 | 暗色 |
|---|---|---|
| `--accent` | `#EA653D` | `#EA653D` |
| `--accent-hover` | `#D4572E` | `#F07A52` |
| `--background` | `#FFFFFF` | `#08090A` |
| `--sidebar-bg` | `#F6F5F2`（浅米灰） | `#0F1011` |
| `--panel-bg` | `#FAFAF9` | `#0F1011` |
| `--surface` | `#FFFFFF` | `#0F1011` |
| `--surface-muted` | `#F2F2F0` | `#161718` |
| `--text` | `#121212` | `#F4F4F6` |
| `--text-muted` | `#555555` | `#D0D6E0` |
| `--text-faint` | `#8A8F98` | `#8A8F98` |
| `--border` | `#D0D0D0` | `#3A3C40` |
| `--border-subtle` | `#E4E4E0` | `#202124` |
| `--success` | `#087A4F` | `#10B981` |
| `--warning` | `#B45309` | `#F59E0B` |
| `--danger` | `#C04040` | `#FF6161` |
| `--info` | `#1D6FD8` | `#3B82F6` |
