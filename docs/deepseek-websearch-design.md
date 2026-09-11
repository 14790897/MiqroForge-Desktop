# web_search 零配置方案：复用 DeepSeek 官方联网搜索（#804 后续）

> 状态：方案待评审（2026-08-25）
> 背景：issue #804 修复后，用户仍需要配置 Tavily/Brave key 才能获得高质量搜索；诉求——**用 DeepSeek 自带的联网搜索，用户零配置**（LLM key 已配，搜索直接复用）。

## 1. 研究结论（2026-08-25 实测，用户真实 key）

| 端点 | 是否支持联网搜索 | 实测证据 |
|---|---|---|
| `POST /chat/completions`（OpenAI 兼容，现用主链路） | ❌ 不支持 | `tools:[{type:"web_search"}]` → 400 `unknown variant 'web_search', expected 'function'`；顶层 `web_search:true` / `enable_search:true` 被静默忽略（模型答"无法实时获取"） |
| `POST /responses`（OpenAI Responses API 风格） | ✅ **完整支持** | 实测成功：模型自动拆 3 查询 → 搜索 → **open_page 打开新华社/凤凰网原文核实** → 综合回答。返回"2025年AI核心产业规模超1.2万亿，企业超6200家（工信部部长李乐成，2026-03-05 部长通道）" |
| `POST /anthropic/v1/messages` | ✅ 支持 | 错误信息揭示工具类型：`web_search_20250305` / `web_search_20260209`（Anthropic 版式，未实测成功调用） |
| 阿里云百炼（Model Studio） | ✅ 支持 | 文档：Responses API 支持 deepseek-v4-flash/pro 的 web_search tool（但需换平台/换 key） |

**关键事实**：
- DeepSeek 官网聊天/App 的联网搜索是消费端功能；API 侧**只有 Responses 端点和 Anthropic 端点**提供 hosted web search
- Responses 端点响应结构：`output` 数组 = `reasoning` → `web_search_call`（自动多查询，含 `open_page` 开原文）→ `message`（最终文本，已核实）
- 成本实测：一次搜索请求 ≈ 输入 6.6K tokens（缓存命中 4K）/ 输出 711 tokens，deepseek 计价极低
- 耗时实测（2026-08-25 复测，用户真实 key）：
  - `context_size=high`：~35s（多查询 + 开 2 个原文页核实，最深档）
  - **`context_size=medium`：~8s**（与 DeepSeek 消费端"几秒回答"体验一致）
  - `context_size=low`：~9s（与 medium 相当，档位差异主要在查询深度）
  - 结论：**默认 medium 档，8 秒级完成**，fast 模式 30s 预算完全容纳
- 鉴权：与现有 LLM 完全相同的 `Authorization: Bearer <deepseek api key>`

## 2. 方案对比

### 方案 A（推荐）：web_search 工具新增 `DeepSeekProvider`，复用 LLM key，插入 auto 链首位

- **实现**：`miqi/agent/tools/web.py` 新增 `DeepSeekSearchProvider(SearchProvider)`——httpx POST `https://api.deepseek.com/responses`，`tools:[{type:"web_search"}]`，从 `output` 提取 `message.content[].output_text` 作为搜索结果字符串返回
- **接入**：`SearchProviderManager` 的 auto 链 = **DeepSeek（有 key）→ Tavily（有 key）→ Brave（有 key）→ DDGS**；`tool_registry_factory` 构造 WebSearchTool 时传入 `providers.deepseek.api_key`
- **零配置**：用户已配 deepseek LLM key，搜索自动生效，无需任何设置
- **fast 扇出**：DeepSeek 成功时直接返回（服务端已自动多查询+开页核实，fanout 无需再拆）
- **失败分类**：401→AUTH_ERROR（key 无效透出）、429→RATE_LIMIT、超时/网络→NETWORK、5xx→SERVER_ERROR，走现有 fallback 链
- **改动面**：web.py（+~80 行）、tool_registry_factory（+1 行传参）、schema（provider 枚举加 `deepseek`）、测试（+5~8 个）

### 方案 B：主 LLM 链路整体切换 Responses API

- 让对话主链路（stream_chat）走 /responses + web_search tool，模型"自搜自答"
- ❌ 拒绝：改 provider 层大动干戈，streaming/工具循环/预算逻辑全要适配；Responses 端点生态成熟度待观察；风险不可控

### 方案 C：引导用户换阿里云百炼

- ❌ 拒绝：换平台 = 用户要重新申请百炼 key、迁移配置，违背"零配置"诉求

### 方案 D：不做（保持现状：Tavily/Brave 配置链）

- 现状已修复 #804，但用户仍需配第三方 key 才有高质量搜索；不满足诉求

## 3. 推荐方案 A 详细设计

### 3.1 DeepSeekSearchProvider

```python
class DeepSeekSearchProvider(SearchProvider):
    """DeepSeek 官方联网搜索（Responses API，复用 LLM key，零配置）。"""
    name = "deepseek"

    def __init__(self, api_key: str, api_base: str = "https://api.deepseek.com",
                 timeout: float = 60.0):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    async def search(self, query: str, count: int) -> SearchResult:
        if not self.api_key:
            return SearchResult(False, error_type="NO_KEY", provider="deepseek")
        # 模型已自动多查询+开页核实，count 只影响 context_size 档位
        context = "high" if count >= 8 else "medium"
        POST {api_base}/responses
          {"model": "deepseek-v4-flash",
           "input": query,
           "tools": [{"type": "web_search", "web_search": {"context_size": context}}]}
        # 提取 output[] 中 type=="message" 的 content[].type=="output_text"
        # 无 output_text（如被拒/异常）→ error
        # 分类：401/403→AUTH_ERROR；429→RATE_LIMIT；5xx→SERVER_ERROR；
        #       httpx.Timeout/网络→NETWORK
```

要点：
- **model 选择**：用 `deepseek-v4-flash`（快+便宜）还是跟随用户主模型？——用用户 LLM 配置里的实际模型名（`agents.defaults.model` 解析出的 deepseek 模型，如 `deepseek/deepseek-chat` 或 `deepseek-v4-flash`），保持与用户主链路一致
- **context_size 默认 medium**（实测 ~8s，与消费端体验一致；high 35s 太慢不用）
- **返回格式**：`_format_results` 直接包 search text 为单条结果（`title: "DeepSeek 联网搜索结果", snippet: <output_text>`），或新增 `_format_deepseek` 原样返回——评审定
- **超时**：30s（medium 实测 8s，30s 余量充足且与 fast 预算对齐），超时 → NETWORK → 回退链
- **stream**：非流式（工具调用语义是等完整结果）

### 3.2 auto 链与显式选择

- `SearchProviderManager._chain()`：`provider=="auto"` 时 = `[DeepSeek(有key)] + [Tavily(有key)] + [Brave(有key)] + [DDGS]`
- provider 枚举：`auto | deepseek | tavily | brave | ddgs`（显式 deepseek = 只用 DeepSeek；无 key 时报 NO_KEY 透出"未配置 DeepSeek API key"——实际不可能，因为 LLM 必须有 key）
- **键来源**：`tool_registry_factory` 构造 WebSearchTool 时 `deepseek_api_key=getattr(providers_cfg.deepseek, "api_key", "")` + env `DEEPSEEK_API_KEY` 兜底

### 3.3 错误透出（延续 #804）

- NO_KEY → "未配置 DeepSeek API key（与对话模型共用，正常不应出现）"
- AUTH_ERROR → "DeepSeek API key 无效（401/403），请在设置中检查模型服务配置"
- RATE_LIMIT/NETWORK/SERVER_ERROR → 沿用现有文案

### 3.4 测试

- provider 级：mock httpx 返回 Responses 结构 → 提取 output_text；401/429/超时分类；无 key
- manager 级：auto 链 DeepSeek 优先、失败回落 Tavily/DDGS
- 集成：真实 key 一次（可选，慢）
- 现有 17 个 test_web.py 测试回归

## 4. 成本与风险

| 项 | 评估 |
|---|---|
| 成本 | 一次搜索 ~7K tokens（缓存命中后增量 ~3K），deepseek 计价 <0.1 元级；远低于 Tavily 免费额度外的付费 |
| 速度 | medium 档 ~8s（与消费端几秒体验一致）；high 档 35s 不用；fast 模式 30s 预算容纳 |
| 稳定性 | Responses 端点较新（V4 时代），需在 PR 后持续观察；失败自动回退链兜底 |
| 数据合规 | 搜索走 DeepSeek 官方服务（与对话同通道），无第三方 key 泄露面 |

## 5. 验收标准

1. 用户零配置：删除 config 里 tavily/brave key 后，web_search 仍能返回真实搜索结果（实测"中国AI产业规模"类查询）
2. auto 链：DeepSeek 不可用（mock 失败）→ 自动回退 Tavily/DDGS
3. 失败透出：key 无效/超时给可操作中文提示
4. 实机 E2E：真实桌面链路（同 #804 验证方式）跑通，回答含搜索来源
5. 全量测试通过 + CI 全绿

## 6. 评审问题

1. DeepSeek 放 auto 链首位是否合适？（质量最高、零配置、8s 可接受；是否仍让 Tavily 优先？）
2. ~~fast 模式 30s 预算与搜索耗时的冲突~~（已消解：medium 档 ~8s，预算容纳；是否仍需要评审 fast 扇出与 DeepSeek 自动多查询的叠加问题——DeepSeek 场景 fanout 应跳过，避免重复搜索）
3. 返回格式：原样透传 DeepSeek 总结文本 vs 包装成标准结果列表？
4. model 选择：跟随用户主模型 vs 固定 deepseek-v4-flash？
5. 是否需要设置 UI 显示"搜索引擎：DeepSeek（无需配置）"状态？
