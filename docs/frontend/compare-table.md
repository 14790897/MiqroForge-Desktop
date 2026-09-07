# 参数/对比内容结构化展示组件（issue #878）

为科研方案中的「参数范围、多工艺路径/方案对比」提供专门的结构化展示组件：区间着色、行/列高亮、长列折叠、排序。超出普通 markdown 表格的表达能力。

## 数据约定

模型在消息中输出 ```compare 围栏代码块，内容为 JSON：

````markdown
```compare
{
  "title": "MOF 造粒工艺对比",
  "schemes": ["路径A 喷雾干燥", "路径B 挤出滚圆", "路径C 冷冻造粒"],
  "parameters": [
    {
      "name": "压力",
      "unit": "MPa",
      "range": "2–4",
      "source": "ref-1",
      "values": ["2–4", "5–8", "1–3"]
    }
  ],
  "citations": [{ "id": "ref-1", "title": "造粒工艺综述", "doi": "10.1/x", "url": "https://…" }]
}
```
````

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 否 | 对比表标题 |
| `schemes` | string[] | 否 | 方案名（列）。缺失时按 `values` 最大长度推导为「方案1…」 |
| `parameters` | object[] | 是 | 参数行 |
| `parameters[].name` | string | 是 | 参数名 |
| `parameters[].values` | string[] | 否 | 每个方案对应的取值，与 `schemes` 顺序对齐 |
| `parameters[].unit` | string | 否 | 单位 |
| `parameters[].range` | string | 否 | 参数整体区间（用于着色） |
| `parameters[].source` | string | 否 | 来源标注 id，对应 `citations[].id` |
| `citations` | object[] | 否 | 来源详情（#879 衔接）：`id`、`title`、`doi`、`url` |

解析规则（`compareData.ts` 的 `parseCompareJson`）：顶层 JSON 解析失败或根类型错误返回 `null`；字段级缺失/多余字段容忍；单个无效参数项跳过。

## 组件能力

- **区间着色**：单元格值形如「2–4」「3~5」或参数带 `range` 字段时，使用 `--accent-soft` 浅色底纹标注。
- **行/列高亮**：悬停单元格时整行 + 整列高亮。
- **长列折叠**：单元格文本超过 24 字符时截断，行首提供「展开/收起」。
- **排序**：点击方案列头按该列排序，区间值按数值下界比较；再点切换升降序。
- **来源徽标**：参数带 `source` 时显示 `citations` 命中的标题（可外链），缺失显示「未标注」。
- **复制**：整表转 TSV 复制（`compareToTsv`），可粘贴进 Excel / Google Sheets。

## 降级

模型未按约定输出、JSON 解析失败或形状错误时，```compare 块回落到普通代码块展示（与 `MarkdownContent` 的既有代码块渲染一致），不影响会话。

## 实现位置

- `apps/desktop/src/renderer/features/chat/components/compareData.ts` — 解析、排序、TSV 纯函数
- `apps/desktop/src/renderer/features/chat/components/DataTable.tsx` — 共享只读表格外壳（与 #877 复用）
- `apps/desktop/src/renderer/features/chat/components/CompareTable.tsx` — 对比表组件
- `apps/desktop/src/renderer/features/chat/components/MarkdownContent.tsx` — `pre` 组件拦截 `compare` 语言并替换渲染

## 关联

- #877：附件只读富预览。XLSX/CSV 的 `SpreadsheetPreview` 与对比表共用同一个 `DataTable` 表格外壳（边框/主题/单元格样式统一）。
- #879：引用结构化。`source` / `citations` 字段本期仅做占位徽标，深链接口待 #879 接入。
