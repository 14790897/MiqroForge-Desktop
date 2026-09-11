# MiQroForge Desktop Internal Alpha Smoke Checklist

Run this checklist before sharing an internal alpha build.

## Environment

- Windows 10/11 x64
- Python 3.11 or 3.12
- Node 20 for local development builds
- Valid provider API key or local provider configuration

## Build gates

```powershell
uv run pytest tests/bridge -q -W error
uv run pytest tests/runtime tests/bridge -q
Set-Location apps/desktop
npm run typecheck
npm run test
npm run build
Set-Location ..\..
uv run pyinstaller miqi.spec
Test-Path dist\miqi-bridge.exe
Set-Location apps/desktop
npx electron-builder --win --publish never
Set-Location ..\..
```

## Manual smoke

1. Launch Desktop in dev or packaged build.
2. If setup appears, complete provider/model/workspace setup.
3. Confirm bottom status bar reaches `运行中`.
4. Open Settings → 运行日志 and confirm bridge initialized.
5. Create a new session from the sidebar.
6. Send: `你好，简单介绍一下你能做什么。`
7. Confirm an assistant response appears.
8. Send: `列出当前工作区的几个重要文件，不要修改。`
9. Confirm progress/tool hints appear.
10. Open 文件 page.
11. Open a text file.
12. Edit the file and save.
13. Return to chat.
14. Ask the agent to modify a small file in the workspace.
15. Confirm touched file appears in the tracked file panel.
16. Open Diff for that file.
17. Use Revert or Accept and confirm no crash.
18. Open 会话 page.
19. Reopen the active session.
20. Stop and restart Desktop.
21. Confirm the session remains visible.
22. Open Settings → 运行日志.
23. Click 复制日志 and paste into a text editor.
24. Close Desktop.

## Release blockers

- Bridge fails to initialize.
- First chat request returns `NOT_INITIALIZED`.
- AppServer responses time out because Desktop cannot match `request_id`.
- Chat final response never appears.
- Session list is empty after a successful chat.
- Files page cannot load the workspace tree.
- Logs cannot be exported or copied.
- Packaged build cannot find `miqi-bridge.exe`.
