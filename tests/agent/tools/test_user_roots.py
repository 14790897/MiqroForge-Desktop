"""Unit tests for user-mentioned output directory extraction (issue #821).

The KUN agent loop auto-senses directories the user names in their message
(e.g. "输出到 C:\\Users\\x\\Desktop\\test_result") so file tools can
read/write them without static ``tools.extra_roots`` config.  These tests
cover extraction, root classification, and the guard rails (home root,
system dirs, protected config/session paths, workspace dedupe, caps).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from miqi.agent.tools.filesystem import _effective_shared_roots
from miqi.agent.tools.user_roots import (
    DEFAULT_MAX_USER_ROOTS,
    _is_protected_extra_root,
    _raw_mentions,
    extract_user_mentioned_roots,
)
from miqi.paths import get_config_path

_IS_WINDOWS = sys.platform == "win32"


# ── _raw_mentions ────────────────────────────────────────────────────────


class TestRawMentions:
    def test_windows_paths(self) -> None:
        raw = _raw_mentions(
            r"输出到 C:\Users\wangsaibo\Desktop\test_result 和 D:\data\mof_price相关"
        )
        assert r"C:\Users\wangsaibo\Desktop\test_result" in raw
        assert r"D:\data\mof_price相关" in raw

    def test_forward_slash_and_mnt_forms(self) -> None:
        raw = _raw_mentions("放 C:/Users/x/out/ 与 /mnt/d/share/ 里")
        assert "C:/Users/x/out" in raw
        assert "/mnt/d/share" in raw

    def test_posix_and_home_forms(self) -> None:
        raw = _raw_mentions("写到 ~/Desktop/work 和 /tmp/out_dir 里")
        assert "~/Desktop/work" in raw
        assert "/tmp/out_dir" in raw

    def test_trailing_punctuation_stripped(self) -> None:
        raw = _raw_mentions(r"输出到 C:\Users\x\Desktop\test_result，然后分析。")
        assert raw == [r"C:\Users\x\Desktop\test_result"]

    def test_path_glued_to_cjk_prose_not_extracted(self, tmp_path: Path) -> None:
        # "输出到<路径>" with no separator — the boundary requirement keeps
        # prose-glued CJK out to avoid false positives.
        assert extract_user_mentioned_roots([f"输出到{tmp_path}"] ) == []

    def test_drive_root_skipped(self) -> None:
        assert _raw_mentions(r"看下 C:\ 的内容") == []

    def test_plain_text_returns_empty(self) -> None:
        assert _raw_mentions("帮我写一个报告，关于 MOF 材料的价格分析") == []

    def test_urls_not_matched(self) -> None:
        assert _raw_mentions("参考 https://example.com/a/b 和 http://x.y/z") == []


# ── extract_user_mentioned_roots ─────────────────────────────────────────


class TestExtractUserMentionedRoots:
    def test_empty_input(self) -> None:
        assert extract_user_mentioned_roots([]) == []
        assert extract_user_mentioned_roots(["没有路径"]) == []

    def test_existing_dir_roots_itself(self, tmp_path: Path) -> None:
        out = tmp_path / "test_result"
        out.mkdir()
        roots = extract_user_mentioned_roots([f"结果输出到 {out}，谢谢"])
        assert roots == [out.resolve()]

    def test_existing_file_uses_parent(self, tmp_path: Path) -> None:
        f = tmp_path / "report.json"
        f.write_text("{}", encoding="utf-8")
        roots = extract_user_mentioned_roots([f"输出到 {f}"])
        assert roots == [tmp_path.resolve()]

    def test_nonexistent_output_dir_rooted_itself(self, tmp_path: Path) -> None:
        # "输出到 尚不存在的目录" — the mention itself becomes the root so
        # the first write_file can create it.
        out = tmp_path / "not_yet_created" / "esm2"
        roots = extract_user_mentioned_roots([f"结果输出到 {out}"])
        assert roots == [out.resolve()]

    def test_dedupe_and_cap(self, tmp_path: Path) -> None:
        dirs = [tmp_path / f"d{i}" for i in range(DEFAULT_MAX_USER_ROOTS + 4)]
        for d in dirs:
            d.mkdir()
        text = "输出到 " + " ".join(str(d) for d in dirs)
        # same text twice → dedupe
        roots = extract_user_mentioned_roots([text, text])
        assert len(roots) == DEFAULT_MAX_USER_ROOTS

    def test_workspace_mentions_skipped(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "ws" / "sub"
        out.mkdir()
        roots = extract_user_mentioned_roots([f"输出到 {out}"], workspace=ws)
        assert roots == []

    def test_home_root_skipped(self) -> None:
        roots = extract_user_mentioned_roots([f"把文件放到 {Path.home()}"])
        assert roots == []

    @pytest.mark.skipif(not _IS_WINDOWS, reason="drive-root/system-dir guards are Windows-only")
    def test_top_level_system_dirs_skipped(self) -> None:
        roots = extract_user_mentioned_roots([
            r"放 C:\Windows 和 C:\Users 以及 C:\Program Files",
        ])
        assert roots == []

    def test_posix_top_level_system_dirs_skipped(self) -> None:
        """POSIX 顶层系统目录（/etc、/usr、/var）同样永不授权（CodeRabbit #851）。"""
        roots = extract_user_mentioned_roots(["看 /etc 和 /usr 还有 /var 目录"])
        assert roots == []

    @pytest.mark.skipif(not _IS_WINDOWS, reason="drive-letter mentions Windows-only")
    def test_windows_desktop_mention_allowed(self) -> None:
        # C:\Users\<user>\Desktop is depth-3 — allowed (the issue's scenario).
        desktop = Path.home() / "Desktop"
        roots = extract_user_mentioned_roots([f"结果输出到 {desktop}"])
        assert desktop.resolve() in roots

    def test_protected_config_root_skipped(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        config_dir = Path(get_config_path()).parent
        roots = extract_user_mentioned_roots([f"看下 {config_dir}"], workspace=ws)
        assert roots == []

    def test_protected_sessions_root_skipped(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        sessions = ws / "sessions"
        sessions.mkdir()
        roots = extract_user_mentioned_roots([f"输出到 {sessions}"], workspace=ws)
        assert roots == []

    def test_workspace_none_skips_protected_check(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        assert extract_user_mentioned_roots([str(out)], workspace=None) == [out.resolve()]


# ── _is_protected_extra_root ─────────────────────────────────────────────


class TestIsProtectedExtraRoot:
    def test_config_path_covered(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        root = Path(get_config_path()).parent
        assert _is_protected_extra_root(root, ws) is True

    def test_sessions_dir_covered(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        assert _is_protected_extra_root(ws / "sessions", ws) is True

    def test_ordinary_dir_not_protected(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        assert _is_protected_extra_root(out, ws) is False


# ── _effective_shared_roots ──────────────────────────────────────────────


class TestEffectiveSharedRoots:
    def test_merge_user_roots(self, tmp_path: Path) -> None:
        base = [tmp_path / "ws"]
        out = tmp_path / "out"
        merged = _effective_shared_roots(base, [str(out)], allow_user_roots=True)
        assert merged == [*base, out]

    def test_disabled_flag_ignores_user_roots(self, tmp_path: Path) -> None:
        base = [tmp_path / "ws"]
        merged = _effective_shared_roots(base, [str(tmp_path / "out")], allow_user_roots=False)
        assert merged == base

    def test_none_user_roots_returns_base(self, tmp_path: Path) -> None:
        base = [tmp_path / "ws"]
        assert _effective_shared_roots(base, None, True) == base

    def test_duplicates_skipped(self, tmp_path: Path) -> None:
        base = [tmp_path / "ws"]
        merged = _effective_shared_roots(base, [str(base[0]), str(base[0])], True)
        assert merged == base

    def test_invalid_entries_ignored(self, tmp_path: Path) -> None:
        base = [tmp_path / "ws"]
        merged = _effective_shared_roots(base, [None, 123, b"bytes"], True)
        assert merged == base
