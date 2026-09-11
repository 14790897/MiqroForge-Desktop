# 桌面打包

## 快速打包（一键）

```bash
cd apps/desktop
npm run build:all
```

该命令依次执行：Python 后端打包 → 前端编译 → Electron 安装包打包。

也可单独执行各步骤：

| 命令 | 作用 |
|------|------|
| `npm run build:bridge` | 仅构建 Python 后端（`uv run pyinstaller miqi.spec`） |
| `npm run build` | 仅编译前端（`electron-vite build`） |
| `npx electron-builder --win --publish never` | 仅打包安装包 |

## 构建前置环境要求（Windows 必读）

> ⚠️ 这一节只影响**打包机（build machine）**，不影响最终用户。
> 最终 exe 已自带全套运行时 DLL，用户双击即用、无需安装任何东西。

**onnxruntime 1.26 需要 `msvcp140.dll` >= 14.40。** PyInstaller 的分析阶段会真实
`import onnxruntime`（以探测它要打进 exe 的 DLL），如果打包机的 VC++ 运行库太旧或
版本错配，这一步会报 `initialization routine failed` / `DLL load failed`，构建直接失败。

**新机器首次打包前，先跑环境检查：**

```bash
uv run python scripts/check-build-env.py
```

- 输出 `Result: PASS` → 可以打包
- 输出 `Result: FAIL` → 按提示安装最新的 **Visual C++ 2015-2022 Redistributable (x64)**：
  https://aka.ms/vs/17/release/vc_redist.x64.exe ，装完重跑检查

检查脚本的判据是 `import onnxruntime` 是否成功（Python 会优先从自身 prefix 目录解析
DLL，所以只要 import 成功即代表实际生效的 DLL 版本正确），失败时才会列出各位置
`msvcp140.dll` / `vcruntime140.dll` 的版本帮助定位。**不要手动往 System32 里复制 DLL**，
统一用官方 Redistributable 安装包解决。

## 打包流程详解

### 1. Python 后端打包

使用 PyInstaller 将 Bridge Server 打包为独立可执行文件：

```bash
uv run pyinstaller miqi.spec --noconfirm
# 输出: dist/miqi-bridge.exe (Windows) 或 dist/miqi-bridge (Linux/macOS)
```

`miqi.spec` 关键配置：

```python
a = Analysis(
    ['miqi/bridge/server.py'],
    pathex=[],
    binaries=[],
    datas=[('miqi/templates', 'miqi/templates'),
           ('miqi/skills', 'miqi/skills')],
    hiddenimports=[
        'miqi.agent', 'miqi.agent.tools', 'miqi.agent.memory',
        'miqi.providers', 'miqi.providers.openai_provider',
        'miqi.providers.anthropic_provider', 'miqi.config',
        'miqi.session', 'miqi.cron', 'miqi.channels',
        'miqi.bus', 'miqi.utils'
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)

pyz = PYZ(a.pure)
exe = EXE(pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='miqi-bridge',
    console=False,       # GUI 模式 (无控制台窗口)
    icon=None
)
```

### 2. 前端打包

使用 electron-builder：

```bash
cd apps/desktop
npm run build              # electron-vite 构建
npx electron-builder       # 打包安装包
```

`electron-builder.yml` 配置：

```yaml
appId: com.miqi.desktop
productName: MiQroForge Desktop
directories:
  output: ../../dist-new

win:
  target:
    - portable   # 便携版 .exe
    - nsis       # NSIS 安装器
    - msi        # MSI 安装器
  arch: [x64]
  icon: src/renderer/assets/icon.ico  # ICO 文件需包含多种尺寸

extraResources:
  - from: ../../dist/miqi-bridge.exe
    to: miqi-bridge.exe

publish:
  provider: generic
  url: https://releases.example.com
```

## 构建产物

| 文件 | 描述 | 平台 |
|------|------|------|
| `dist-new/MiQroForge Desktop 0.1.0.exe` | 便携版 | Windows |
| `dist-new/MiQroForge Desktop Setup 0.1.0.exe` | NSIS 安装器 | Windows |
| `dist/miqi-bridge.exe` | Python 后端（自包含） | Windows |
| `dist/miqi-0.1.4.tar.gz` | Python 源码包 | 通用 |
| `dist/miqi-0.1.4-py3-none-any.whl` | Python Wheel | 通用 |

## miqi-bridge.exe 自检模式

打包后的 `miqi-bridge.exe` 支持 `--check` 参数，用于环境验证（被 Setup Wizard 的 PYTHON_CHECK 调用）：

```bash
miqi-bridge.exe --check
# 输出: {"ok": true, "python_version": "3.12.10", "issues": []}
```

返回 JSON 格式：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | bool | 环境是否就绪 |
| `python_version` | string | Python 版本号 |
| `issues` | string[] | 问题列表（空=无问题） |

**实现位置**：`miqi/bridge/server.py` 文件顶部，在标准库 import 之后、项目 import 之前处理，确保即使项目模块加载失败也能正确报告环境状态。

**注意**：PyInstaller 打包的 exe **不支持 `-c` 参数**（传参会变成 `sys.argv`，而非 Python 解释器选项），因此必须使用 `--check` 而非 `-c "code"`。

## 环境检查逻辑

Setup Wizard 的 PYTHON_CHECK IPC handler（`apps/desktop/src/main/ipc/index.ts`）按以下优先级检测环境：

```
1. 检测 process.resourcesPath/miqi-bridge.exe 是否存在
   ├─ 存在 → 打包环境：认为环境就绪（exe 包含 Python + 全部依赖）
   │         可选执行 miqi-bridge.exe --check 获取详细版本信息（best-effort，失败不影响结果）
   └─ 不存在 → 开发环境：走系统 Python 检查流程
       按 MIQI_PYTHON_PATH → uv → .venv → python3 → python 顺序查找
```

**设计原因**：bundled exe 存在即认为环境就绪，而非强制执行 `--check`，是因为：
- PyInstaller onefile 模式首次启动有解压延迟，`--check` 可能耗时较长
- 旧版 exe 不支持 `--check` 参数时会走 `main()` 启动 bridge 并阻塞在 stdin，导致白屏
- 打包构建时已保证依赖完整性，无需重复验证

## Python Wheel 打包

使用 Hatchling 构建：

```bash
uv build
# 输出:
# dist/miqi-0.1.4.tar.gz
# dist/miqi-0.1.4-py3-none-any.whl
```

Wheel 包含：

- `miqi/` 所有 Python 模块
- `miqi/templates/` 模板文件
- `miqi/skills/` 内置技能
- CLI 入口 `miqi = miqi.cli.commands:app`

## 版本管理

版本号定义在 `pyproject.toml`：

```toml
[project]
version = "0.1.4.post1"
```

构建时自动同步到 `electron-builder.yml` 的 `version` 字段。

## 常见问题与解决方案

### 1. MSI 构建失败：图标找不到

错误信息：
```
error LGHT0094 : The identifier 'Icon:MiQiDesktopIcon.exe' could not be found.
```

问题原因：
WiX Toolset（用于构建 MSI）需要应用程序图标包含多种尺寸（16x16、32x32、48x48、64x64、128x128、256x256）。单一尺寸的图标会导致构建失败。

解决方案：
创建包含多种尺寸的 ICO 文件。项目中提供了生成脚本：

```bash
cd apps/desktop
node scripts/generate-icon.js
```

脚本会生成 `src/renderer/assets/icon.ico`，包含所有必需的尺寸。

### 2. 图标尺寸不足

错误信息：
```
image C:\...\icon.ico must be at least 256x256
```

问题原因：
electron-builder 要求主图标至少为 256x256 像素。

解决方案：
确保 ICO 文件包含 256x256 尺寸，同时包含其他常用尺寸以兼容不同系统和显示环境。

### 3. 打包后环境检查报"Python not found"

问题现象：
在干净机器上运行打包后的 MiQroForge Desktop，Setup Wizard 显示 Python 缺失和依赖缺失。

问题原因：
PYTHON_CHECK handler 只查找系统 Python，没有检测打包的 `miqi-bridge.exe`。已在新版本中修复（优先检测 bundled exe）。

解决方案：
确保使用最新代码打包（PYTHON_CHECK 已增加 bundled exe 检测逻辑）。

### 4. 打包后白屏

问题现象：
启动 Setup Wizard 后界面白屏无响应。

问题原因：
旧版 `miqi-bridge.exe` 不支持 `--check` 参数，`spawnSync` 调用时 exe 走 `main()` 启动 bridge 并阻塞在 stdin 等待，导致 IPC handler 超时卡死。

解决方案：
确保使用最新代码打包。新版本的 PYTHON_CHECK 逻辑为：bundled exe 存在即认为环境就绪，`--check` 仅作为可选验证。

### 5. PyInstaller exe 不支持 `-c` 参数

问题原因：
PyInstaller 打包的 exe 不是 Python 解释器，传给 exe 的参数会变成 `sys.argv`，而非 Python 解释器选项。`miqi-bridge.exe -c "code"` 不会执行代码。

解决方案：
使用 `miqi-bridge.exe --check` 替代。`--check` 在 server.py 顶部处理，输出 JSON 格式的环境检查结果。

### 6. 构建报 onnxruntime DLL 初始化失败

错误信息：
```
OSError: [WinError 1114] A dynamic link library (DLL) initialization routine failed
ImportError: DLL load failed while importing onnxruntime
```

问题原因：
打包机的 VC++ 运行库 `msvcp140.dll` 版本太旧（< 14.40），或与 Python 自带的
`vcruntime140.dll` 版本错配。onnxruntime 1.26 需要 14.40+，而 PyInstaller 分析阶段
必须真实加载 onnxruntime，于是构建失败。

解决方案：
```bash
uv run python scripts/check-build-env.py   # 确认根因
```
然后安装最新版 Visual C++ 2015-2022 Redistributable (x64)：
https://aka.ms/vs/17/release/vc_redist.x64.exe

装完重跑检查脚本，`Result: PASS` 后再打包。最终 exe 不受此影响——它已内置正确的
`msvcp140.dll`，最终用户无需安装任何东西。
