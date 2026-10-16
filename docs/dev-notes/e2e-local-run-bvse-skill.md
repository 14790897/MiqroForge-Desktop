---
name: e2e-local-run-bvse-skill
description: 本机真实跑 bvse-mof skill 的完整前置（venv 位置、测试 CIF、Tsinghua 镜像装依赖、env 门控 spec、耗时约 8-10 分钟）
type: project
---


在本机真实跑 BVSE skill（bvse-mof-local-ssh）验证桌面端行为的前置（2026-09-16 实测）：

- **skill 安装位置**：`~/.miqi/skills/bvse-mof-local-ssh`（含 `.venv`，依赖已装）。依赖用 `uv pip install --python <venv>/Scripts/python.exe -r requirements.txt` + `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` 装（直连 PyPI 会卡死十几分钟无输出）。
- **测试输入**：`<本地临时目录>/bvse-run/input/1499489-acs.cgd.6b01265_1499490_freeONLY.cif`（真实 MOF，Na/Li 都能跑通；ZIF-8 用 Na 会 `No accessible grid points` 失败）。
- **跑法**：`apps/desktop/tests/e2e/bvse-skill-assets.spec.ts`（mock 驱动 exec 真跑 pipeline，`BVSE_TEST_CIF=<cif>` 环境变量门控，缺 skill/venv/CIF 自动 skip）。单次约 8-10 分钟；`--skip-zeopp`、`--low-e-max 0.5`。
- **对照物**：`~/Documents/WXWork/.../3skills(1)/3skills/BVSE_MOF_skill/标准输出/` 有一份历史产物树可做完整性对照。

**Why:** 用户要求「涉及真实运行的改动要真跑一遍技能」，这套前置一次配好可以反复用；直连 PyPI 卡死与 ZIF-8 选材的坑都浪费过时间。

**How to apply:** 需要复现/验证任务资产面板或 skill 追踪类改动时，直接按上面跑 spec；venv 缺包时先换镜像。相关：[task-assets-panel-design-principles](task-assets-panel-design-principles.md)
