# SkillHub 技能市场

SkillHub 是 MiQroForge 的公开技能注册中心，提供可搜索、可安装的 Agent 技能市场。注册中心地址：`https://skills.sixiangjia.de`。

## 架构

```mermaid
graph LR
    subgraph Frontend [SkillHubPage.tsx]
        BROWSE[浏览技能列表]
        SEARCH[关键词搜索]
        INSTALL[一键安装]
        STATUS[已安装状态]
    end

    subgraph API [Registry API]
        INDEX[index.json]
        API_SEARCH[/api/search]
        SKILLMD[SKILL.md]
    end

    subgraph Storage [Skills Storage]
        SKILLS_DIR["~/.workbuddy/skills/"]
        FILES["*.md / *.dat"]
    end

    Frontend -->|查询/安装| API
    API -->|下载技能| Storage
```

## 核心功能

### 浏览

加载完整的技能索引 (`index.json`)，以卡片网格形式展示所有可用技能。每张卡片显示：

- 技能名称和描述
- 作者信息
- 标签分类
- 安装状态（已安装 / 可安装）

### 搜索

防抖 (300ms) 关键词搜索：

```
GET https://skills.sixiangjia.de/api/search?q=<keyword>
```

支持按名称、描述、标签匹配。

### 一键安装

1. 用户点击"安装"
2. 从注册中心获取 `SKILL.md`
3. 通过 `skills:upload` IPC 写入本地技能目录
4. Agent 下次对话即可使用

### 加密技能

支持 AES-256-GCM 加密的技能文件（`.dat` 格式）：

- **明文元数据**：名称、描述、作者等可公开信息
- **加密 body**：SKILL.md 的实际内容加密存储
- **使用时解密**：Agent 调用时自动解密

## 安装目录

| 级别 | 路径 | 作用域 |
|------|------|--------|
| 用户级 | `~/.workbuddy/skills/` | 所有项目可用 |
| 项目级 | `{workspace}/.workbuddy/skills/` | 仅当前项目 |

## CSP 配置

由于 SkillHub 注册中心的跨域请求，Electron 的 CSP 需要配置：

```html
<!-- apps/desktop/src/renderer/index.html -->
<meta http-equiv="Content-Security-Policy"
  content="connect-src 'self' https://skills.sixiangjia.de;">
```

## 已安装状态管理

`SkillHubPage` 会对比注册中心索引与已安装技能，自动展示"已安装"标识。安装状态缓存在本地，避免重复加载。
