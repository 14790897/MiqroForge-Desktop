---
name: repo-renamed-miqroforge-desktop
description: 桌面仓库归属变更链：14790897/MiQi → 14790897/MiqroForge-Desktop → miqro-org/MiqroForge-Desktop；gh 旧名自动重定向
type: project
---


桌面端仓库的标识变更链：

1. `14790897/MiQi` → `14790897/MiqroForge-Desktop`（2026-08-27，随产品更名 #780）
2. → **`miqro-org/MiqroForge-Desktop`**（2026-10-12 观察：`gh repo view 14790897/MiqroForge-Desktop` 返回 `nameWithOwner = miqro-org/MiqroForge-Desktop`，说明已转入 miqro-org 组织，不只是重定向）

现在 `miqro-org` 下同时有 **MiqroForge-Desktop**（桌面端）和 **miqro-gate**（MiQroGate 网关），见 [miqro-gate-repo-map](miqro-gate-repo-map.md)。

**Why**：仓库从个人账号转入组织，链接与 `--repo` 参数的规范名随之改变。

**How to apply**：
- 旧名（`14790897/MiQi`、`14790897/MiqroForge-Desktop`）`gh` 仍能自动重定向，不用改历史脚本；但**新写的**链接/`--repo` 用 `miqro-org/MiqroForge-Desktop`。
- `gh pr create` 返回的 URL 会是 miqro-org 形式。
- **注意 git remote 还没改**：本地 `origin` 的 fetch 仍指向 `github.com/14790897/MiqroForge-Desktop.git`（靠重定向可用），push 是双 pushurl（GitHub + miqroera），见 [miqroera-github-mirror-sync](miqroera-github-mirror-sync.md)。
- 相关：[develop-to-main-sync-pr](develop-to-main-sync-pr.md)、[miqroforge-desktop-rename](miqroforge-desktop-rename.md)
