# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## paper_download

- Use this tool to download paper PDFs into workspace.
- It detects common paywall/login interstitial pages and returns structured errors instead of saving fake PDFs.

## Feishu Channel — Runtime Behaviors

These are enforced by the MiQroForge Feishu channel itself (not by the MCP tool):

- **Group chats require an @mention.** The bot only replies in a group when it is explicitly
  @-mentioned. In group replies, address the original sender with a real Feishu mention via
  `<at user_id="ou_xxx">name</at>` (the runtime constructs this for incoming events).
- **3-second debounce.** Multiple consecutive user messages within ~3s are coalesced into one
  agent turn. Do not send progress pings for every fragment; respond once the burst settles.
- **Non-text messages arrive as descriptive text.** Files / images / audio / video / media
  events are converted to a `[收到文件]` / `[收到图片]` / … prefix containing `message_id` and
  `file_key`. Always call `mcp_feishu-mcp_download_message_file` to fetch the actual bytes —
  never assume a local path exists.

## Feishu Message Length Limits

The Feishu API enforces a **30 720-byte hard limit** on `text` message content
(roughly 8 000 Chinese characters or 30 000 ASCII characters).
Sending a message that exceeds this limit results in a `400 Bad Request` error.

**Rules for `mcp_feishu-mcp_send_message` and `mcp_feishu-mcp_reply_message`:**

1. Keep the `text` value inside `content` under **6 000 characters** (conservative
   limit that accounts for multi-byte characters and formatting overhead).
2. If the response is longer, **split it into multiple sequential messages**:
   - Part 1: summary / key findings
   - Part 2: detailed data / tables
   - Part 3: conclusions / recommendations
3. Call `reply_message` (not `send_message`) when replying in the same thread so
   Feishu groups the messages correctly.

**Example split pattern:**
```
# Part 1 – summary (< 6000 chars)
reply_message(message_id=..., content='{"text": "📊 分析摘要\n..."}')

# Part 2 – detailed numbers (< 6000 chars)
reply_message(message_id=..., content='{"text": "📐 详细数据\n..."}')
```

> Never concatenate all analysis output into a single message when the total
> exceeds ~6000 characters.

## Feishu File Messages — Required Workflow

When a message starts with `[收到文件]`, `[收到图片]`, `[收到语音]`, or `[收到视频]`,
the user has uploaded a file attachment in the chat. **Always follow this workflow:**

1. **Download first** — call `mcp_feishu-mcp_download_message_file` with the
   `message_id` and `file_key` shown in the message. Do NOT guess or assume the
   file already exists at any local path.
2. **Use the returned local path** — the tool returns the saved file path on disk.
   Use that exact path for all subsequent processing (translation, analysis, etc.).
3. **Never skip the download step** — the file does not exist locally until you
   download it. Any tool call using a guessed path will fail.

## Feishu File Upload — Mandatory Permission Workflow

**Every time you upload a file or create a document via feishu-mcp, you MUST
grant full permissions to the current chat group immediately after the upload.**
Never skip this step — files uploaded without explicit permissions will show
"Access Denied" to group members.

### For uploaded files (`upload_file` or `upload_file_and_share`)

After obtaining `file_token`, call these two tools in order:

```
# Step 1: make the file accessible via link (anyone in the org)
mcp_feishu-mcp_set_doc_public_access(
    file_token = <file_token>,
    file_type  = "file",
    access_level = "tenant_readable"
)

# Step 2: grant the CURRENT GROUP full_access (can view, download, and manage)
mcp_feishu-mcp_set_doc_permission(
    file_token       = <file_token>,
    file_type        = "file",
    chat_ids         = [<current_chat_id>],   # the oc_xxx group this message came from
    perm_type        = "full_access"
)
```

Then send the share link to the group:

```
mcp_feishu-mcp_get_share_link(file_token=<file_token>, file_type="file")
# → send the returned URL via send_message / reply_message
```

`upload_file_and_share` already handles Step 1 internally; you still MUST call
Step 2 (`set_doc_permission`) afterwards to grant the group `full_access`.

### For created documents (`create_document`)

After creating and writing a document, use `docx` as the `file_type`:

```
mcp_feishu-mcp_set_doc_public_access(
    file_token   = <document_id>,
    file_type    = "docx",
    access_level = "tenant_readable"
)

mcp_feishu-mcp_set_doc_permission(
    file_token       = <document_id>,
    file_type        = "docx",
    chat_ids         = [<current_chat_id>],
    perm_type        = "full_access"
)
```

### Multiple files (e.g. pdf2zh mono + dual output)

When uploading multiple files at once, run both permission steps for **each**
`file_token` individually. Do not share a single token's permissions with
another file.

### Permission level reference

| `perm_type` value | Meaning |
|---|---|
| `view` | Read-only |
| `edit` | Can edit content |
| `full_access` | Full control (view, edit, share, manage) — use this |

### `set_doc_permission` error 1063003

If `set_doc_permission` returns error `1063003`, the bot is not in the target
group. Inform the user: "请将飞书机器人添加到群组：群设置 → 群机器人 → 添加机器人"。
Do NOT silently skip permissions — at minimum ensure `set_doc_public_access` is
called so the link is still accessible.
## Zeo++ MCP — Passing Structure Files

The `mcp_zeopp_*` tools run as a **local stdio process on the host** (not in
Docker). They can accept host-side file paths via `structure_path`, or inline
content via `structure_text`.

Preferred workflow when a file has been downloaded from Feishu:

```python
# 1. Download the CIF file from Feishu (returns a host-side path)
path = mcp_feishu-mcp_download_message_file(message_id=..., file_key=...)

# Option A — pass the path directly (simpler)
mcp_zeopp_pore_diameter(structure_path=path)
mcp_zeopp_surface_area(structure_path=path)
mcp_zeopp_accessible_volume(structure_path=path)
mcp_zeopp_channel_analysis(structure_path=path)
mcp_zeopp_framework_info(structure_path=path)
mcp_zeopp_open_metal_sites(structure_path=path)

# Option B — read content and pass as text (when content is already in memory)
content = read_file(path)
mcp_zeopp_pore_diameter(structure_text=content, filename="structure.cif")
```

`structure_base64` is also supported if you have Base64-encoded bytes.

> **mofchecker** also runs as a stdio process on the host and accepts
> host-side file paths normally.

## Task Trace — 让经验面板记录有意义的任务历史

每条用户消息都会自动开启一个 trace（名称从消息前几个词生成），
记录本轮所有工具调用，在本轮结束时自动关闭并写入 SQLite。

### 何时主动调用 trace 工具

仅在以下场景才需要主动干预：

1. **`task_begin`** — 执行包含 5+ 步骤的复杂工作流时，在**第一步之前**调用：
   ```
   task_begin(task_name="analyze-mof-structure", goal="分析 HKUST-1 孔径分布并生成报告")
   ```
   `task_name` 使用简短英文 slug，便于历史搜索。

2. **`task_end`** — 完成重要任务后主动提供经验总结：
   ```
   task_end(outcome="success", notes="使用 mcp_zeopp 分析了 3 个 MOF；孔径数据写入 results.csv")
   ```
   `notes` 会被向量化，用于未来相似任务的语义召回。

3. **`trace_search`** — 执行陌生复杂工作流前，先搜索历史经验：
   ```
   trace_search(query="analyze MOF pore size distribution")
   ```

### 普通对话不需要手动干预

对于简单问答（无工具调用），系统自动记录为 `outcome=partial`，无需补充。
