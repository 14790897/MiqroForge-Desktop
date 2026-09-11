# 工具系统

工具系统通过 `ToolRegistry`（`miqi/agent/tools/registry.py`）统一管理所有 Agent 可用工具，并通过 `ToolOrchestrator`（`miqi/execution/orchestrator.py`）执行四阶段安全管道。

## 工具注册架构

```
ToolRegistry
├── 内置工具 (Built-in) — 27 个工具
│   ├── filesystem:    read_file, write_file, edit_file, list_dir
│   ├── apply_patch:   apply_patch (Unified Diff)
│   ├── web:           web_search, web_fetch
│   ├── shell:         exec
│   ├── memory:        memory_read, memory_write, memory_append
│   ├── message:       message
│   ├── skill:         skill_manage
│   ├── session:       session_search
│   ├── cron:          cron_create, cron_list, cron_delete
│   ├── papers:        paper_search, paper_get, paper_download
│   ├── task_trace:    task_begin, task_end, trace_search
│   ├── spawn:         agent_spawn
│   ├── mcp:           MCP 工具代理
│   └── documents:     docx_read, create_docx, edit_docx, pptx_read, create_pptx,
│                      xlsx_read, create_xlsx, append_xlsx, pdf_read, create_pdf
└── MCP 工具 (External) — 通过 MCP Client 动态加载
    ├── raspa-mcp:     create_workspace, simulate, parse_output, ...
    ├── zeopp-backend: pore_analysis, ...
    └── ... (7 个 MCP 服务)
```

## 工具接口

```python
class Tool:
    name: str           # 工具名称 (LLM function name)
    description: str    # 工具描述
    parameters: dict    # JSON Schema 参数定义

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        """执行工具，返回结果"""
        ...

class ToolResult:
    success: bool
    content: str        # 文本结果
    metadata: dict      # 额外元数据
```

## ToolOrchestrator 执行管道

所有工具执行通过 `ToolOrchestrator` 四阶段管道：

```
审批 (ApprovalPolicy) → 沙箱选择 (SandboxPolicyEngine) → 执行 (ToolRegistry) → 重试 (指数退避)
```

### 阶段 1: 审批

`ApprovalPolicy` 根据 `ApprovalMode` 决定是否需要用户确认：

- `AUTO` — 自动批准（内置安全工具）
- `ASK` — 每次询问用户
- `DENY` — 拒绝执行

### 阶段 2: 沙箱选择

`SandboxPolicyEngine` 根据工具类型和参数选择沙箱：

- `NONE` — 无需沙箱（纯文本操作）
- `BWRAP` — bwrap 沙箱（Shell 命令执行）
- `WORKSPACE_ONLY` — 仅限工作区（文件操作）

### 阶段 3: 执行

通过 `ToolRegistry` 实际执行工具，支持串行和并行模式。

### 阶段 4: 重试

`ExecPolicy` 控制重试行为，指数退避重试瞬时错误。

## 内置工具详解

### 文件系统工具

| 工具 | 功能 | 安全限制 |
|------|------|----------|
| `read_file` | 读取文件内容 | 工作区 + `memory/`/`skills/`/`.skills/`；`tools.extra_roots` 可额外授权；config.json 仅只读；用户消息中点名的输出目录本回合内自动授权（#821） |
| `write_file` | 写入/创建文件 | 工作区 + 默认共享目录 + `tools.extra_roots`；config/session 路径不可写，自动创建快照；用户点名的输出目录自动授权 |
| `edit_file` | 精确字符串替换 | 工作区 + 默认共享目录 + `tools.extra_roots`；config/session 路径不可写；用户点名的输出目录自动授权 |
| `list_dir` | 列出目录 | 工作区 + 默认共享目录 + `tools.extra_roots`；用户点名的输出目录自动授权 |
| `apply_patch` | Unified Diff 补丁应用 | 工作区 + 默认共享目录 + `tools.extra_roots`；config/session 路径不可写，版本快照；用户点名的输出目录自动授权 |

### 办公文档工具

Office 文档工具统一位于 `miqi/documents/` 目录下，提供 Word、PowerPoint、Excel、PDF 四类文档的读写能力。所有写入操作默认限制在工作区目录内，并自动创建文件快照以支持回滚。

#### Word 文档 (DOCX)

| 工具 | 功能 | 实现 |
|------|------|------|
| `docx_read` | 读取 Word 文档文本内容 | `miqi/documents/docx_tool.py` — `DocxReadTool` |
| `create_docx` | 创建 Word 文档 | `miqi/documents/docx_tool.py` — `CreateDocxTool` |
| `docx_write` | `create_docx` 的向后兼容别名 | `miqi/documents/docx_tool.py` — `DocxWriteTool` |
| `edit_docx` | 编辑已有 Word 文档 | `miqi/documents/docx_tool.py` — `EditDocxTool` |

**创建文档支持**：
- 结构化内容块：`paragraph`、`heading`、`table`、`image`
- Markdown 风格文本输入（自动解析 `# heading`）
- 内置中文排版预设：`chinese_document`、`chinese_essay`
  - 标题：黑体三号加粗居中（16pt）
  - 正文：宋体小四（12pt）、1.5 倍行距
- 自然语言格式指令：如 `"正文宋体小四，段落1.5行距，标题黑体加粗三号字居中"`
- 字号支持：中文号数（初号~小五）和 pt 值
- 对齐方式：左对齐、居中、右对齐、两端对齐（含中文词）

**编辑文档支持**：
- 全文文本替换（段落 + 表格单元格）
- 追加段落
- 应用中文格式样式

**底层依赖**：`python-docx`，通过 `w:eastAsia` XML 属性注册东亚字体。

#### PowerPoint 演示文稿 (PPTX)

| 工具 | 功能 | 实现 |
|------|------|------|
| `pptx_read` | 读取 PowerPoint 文本内容 | `miqi/documents/pptx_tool.py` — `PptxReadTool` |
| `create_pptx` | 创建 PowerPoint 演示文稿 | `miqi/documents/pptx_tool.py` — `CreatePptxTool` |
| `pptx_write` | `create_pptx` 的向后兼容别名 | `miqi/documents/pptx_tool.py` — `PptxWriteTool` |

**创建演示文稿支持**：
- 多张幻灯片，每张包含：`title`、`subtitle`、`content`（字符串或字符串数组）、`bullets`、`image_path`
- 图片定位：可指定 left、top、width（英寸单位）
- 使用默认幻灯片母版布局

**底层依赖**：`python-pptx`

#### Excel 电子表格 (XLSX)

| 工具 | 功能 | 实现 |
|------|------|------|
| `xlsx_read` | 按工作表名称读取数据 | `miqi/documents/xlsx_tool.py` — `XlsxReadTool` |
| `create_xlsx` | 创建多工作表工作簿 | `miqi/documents/xlsx_tool.py` — `CreateXlsxTool` |
| `xlsx_write` | `create_xlsx` 的向后兼容别名 | `miqi/documents/xlsx_tool.py` — `XlsxWriteTool` |
| `append_xlsx` | 向已有工作表追加行 | `miqi/documents/xlsx_tool.py` — `AppendXlsxTool` |

**创建工作簿支持**：
- 多工作表：支持 `{sheet_name: [[rows]]}` 字典格式和 `[{name, rows, charts}]` 数组格式
- 公式：以 `=` 开头的字符串自动保留为公式（不转换为文本）
- 内嵌图表：条形图 (`bar`)、折线图 (`line`)、饼图 (`pie`)，通过 `openpyxl.chart` 实现
  - 支持 `data_range` 直接引用 和 `series[{name, values}]` 命名系列定位
  - 支持 `category_range` 和 `categories` 数组

**追加数据支持**：
- 按工作表名称追加行
- 支持 `create_sheet` 标志在目标工作表不存在时自动创建

**底层依赖**：`openpyxl`

#### PDF 文档

| 工具 | 功能 | 实现 |
|------|------|------|
| `pdf_read` | 提取 PDF 文本，支持 OCR | `miqi/documents/pdf_read_tool.py` — `PdfReadTool` |
| `create_pdf` | 创建 PDF 文档 | `miqi/documents/pdf_create_tool.py` — `CreatePdfTool` |
| `pdf_write` | `create_pdf` 的向后兼容别名 | `miqi/documents/pdf_create_tool.py` — `PdfWriteTool` |

**PDF 读取**：
- 三层提取管道：pypdfium2（最快 ~1ms/页）→ pypdf（备用）→ Tesseract OCR（扫描件，支持 `chi_sim+eng` 语言）
- 通过 pdfplumber 提取结构化表格和图表数据
- 返回元数据：页数、是否使用 OCR、文件大小、解析耗时
- 支持 `force_ocr` 参数跳过直接提取强制走 OCR

**PDF 创建**：
- 使用 reportlab 构建，支持完整的中文 CJK 字体自动发现
  - 搜索路径覆盖：Linux/WSL 系统字体（`/usr/share/fonts/`）、Windows 字体（`C:/Windows/Fonts/`）、用户字体（`~/AppData/Local/Microsoft/Windows/Fonts/`）
  - 回退机制：`fc-list :lang=zh` 命令扫描
- 内置样式预设：`chinese_document`、`chinese_essay`、`report`
- 内容块类型：`paragraph`、`heading`（支持 1-6 级）、`table`（含表头）、`list`、`spacer`、`page_break`
- 页面大小：A4（默认）、Letter、A3，边距按中文公文规范（上下 2.54cm、左右 3.17cm）
- 自然语言格式指令（与 DOCX 共用相同的解析逻辑）
- 30 秒内相同路径自动去重，防止重复生成

**底层依赖**：`reportlab`、`pypdfium2`（推荐）/ `pypdf`（备用）、`pdfplumber`（表格提取）、`tesseract` + `pdftoppm`（OCR）

#### 统一文档解析服务

`miqi/documents/document_parser.py` 提供跨所有格式的统一解析入口，服务于文件预览和 LLM 上下文注入：

```python
parse_document(path) → {"text": "...", "page_count": N, "ocr_used": bool, ...}
is_supported_document(path) → bool
get_document_category(path) → "pdf" | "word" | "ppt" | "excel" | ...
```

**支持 23 种文件格式**：
- Office 文档：PDF、DOCX/DOC/ODT、PPTX/PPT/ODP、XLSX/XLS/ODS
- 标记/代码：Markdown（提取 Mermaid 图、表格、代码块、标题大纲）、HTML（lxml 解析，stdlib 回退）
- 数据/配置：CSV、JSON、XML、YAML/YML、ENV、LOG、SQL、INI、TOML、HTACCESS
- 脚本/文本：SH/BASH、TXT、RTF（剥离 RTF 标记、解码 Unicode/hex 转义）

安全限制：
- 预览最大 50,000 字符，LLM 上下文最大 200,000 字符
- 按需加载，不阻塞 UI

### 网络工具

| 工具 | 功能 | 后端 |
|------|------|------|
| `web_search` | 搜索互联网 | Brave / SearXNG / Hybrid |
| `web_fetch` | 获取网页内容 | readability-lxml 解析 |

### 执行工具

| 工具 | 功能 | 安全机制 |
|------|------|----------|
| `exec` | Shell 命令执行 | bwrap 沙箱 + LANDLOCK 规则 |

> 命令执行通过 `command/exec` 和 `process/*` AppServer 协议方法支持流式 I/O、PTY 调整和进程快照。

## MCP 工具特性

外部 MCP 工具通过 Model Context Protocol 集成：

- **心跳进度**：长时任务每 15 秒报告进度
- **超时控制**：每个 MCP 工具可独立设置超时（如 RASPA GCMC 6 小时）
- **延迟加载**：按需启动 MCP 服务器，节省资源
- **连接复用**：MCP 进程保持存活，避免重复启动

## 安全机制

1. **工作区隔离**：文件操作受 `workspace` 目录约束（由 `restrict_to_workspace` 控制），WSL 沙箱下默认放行 `memory/`、`skills/`、`.skills/` 与配置文件；`tools.extra_roots` 可显式授权 workspace 外目录
2. **用户输出目录自动授权（#821）**：KUN 运行时从用户本条消息中自动提取点名的目录（如「结果输出到 `C:\Users\x\Desktop\test_result`」），该回合内授权文件工具（read/write/edit/list/apply_patch/graph_render）读写；仅扫描用户消息、不扫描模型输出或文档内容，且配置目录、会话文件目录、用户主目录、盘符根与系统顶层目录（Windows/Program Files 等）永不授权；可用 `tools.auto_user_dirs: false` 关闭
3. **危险命令审批**：39 种危险命令模式需用户确认（`miqi/agent/command_approval.py`）
4. **bwrap 沙箱**：LANDLOCK 文件系统规则，FIFO 驱逐（最多 10 个）
5. **快照保护**：文件写入前自动创建原始内容快照，支持回滚
6. **超时终止**：工具执行超时自动中断
7. **默认拒绝**：PermissionEngine 采用 deny-by-default 策略

## Hook 系统

`HookRuntime` 支持在工具执行生命周期中注入自定义行为：

| Hook 点 | 触发时机 |
|----------|----------|
| `pre_tool` | 工具执行前 |
| `post_tool` | 工具执行后 |
| `on_error` | 工具执行出错时 |

## 相关文档

- [Runtime 引擎](agent.md) — RuntimeSession / TaskRunner / TurnRunner
- [Bridge 通信](bridge.md) — Bridge Server 工具调用处理
