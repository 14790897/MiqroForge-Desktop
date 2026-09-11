# Contributing

Thanks for contributing to MiQroForge Desktop.

## Naming Convention

书面表达（文档、界面文案、Issue/PR/commit 描述）一律使用产品名 **MiQroForge Desktop**（注意大写 Q 与大写 F：`MiQroForge`，不要写作 `MiQroForge` / `MicroForge` / `Qraft` / `microforge`）。外部平台名与产品同名，亦为 **MiQroForge**（原 Qraft，issue #786）；平台域名 `forge.miqroera.com`、OAuth `client_id` 等基础设施标识保持不变。旧名 "MiQi Desktop" 与 "Qraft" 仅允许出现在内部标识符（npm 包名 `miqi-desktop`、appId `com.miqi.desktop`、bridge 产物 `miqi-bridge`、Python 包 `miqi`、仓库 URL、`qraft` 模块/文件名、`qraft.agent-session` workflow_ref）与历史变更记录中。

## Before You Start

1. Fork the repository and create a branch.
2. Install the local development environment: `uv sync --extra dev` (or `pip install -e '.[dev]'`)
3. Read these docs before making changes:
   - `README.md`
   - `docs/DEVELOPER_GUIDE.md`
   - `docs/ARCHITECTURE.md`

## Development Principles

- Make minimal and verifiable changes.
- Preserve backward compatibility (especially public CLI behavior).
- Use `loguru.logger` for logging and avoid adding built-in `print()`.
- Do not refactor unrelated areas.

## Testing Requirements

Run at least the tests relevant to your changes before submitting:

```bash
python -m pytest tests/test_commands.py tests/test_cron_commands.py -q
```

If your change touches cron or agent core behavior, also run:

```bash
python -m pytest tests/test_agent_loop_core.py tests/test_cron_service_core.py -q
```

## Pull Request Guidelines

Your PR description should include:
- Background and goal
- Key changes
- Risk and compatibility notes
- Test commands and results

## Documentation

If your changes affect behavior or interfaces, update these files as needed:
- `docs/API.md`
- `docs/DEVELOPER_GUIDE.md`
- `docs/ARCHITECTURE.md`
- `CHANGELOG.md`
