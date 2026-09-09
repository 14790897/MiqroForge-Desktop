"""#1005 节 2：docx/pptx 输入图片路径必须过边界校验（越界不读文件）。

模型可控参数（``create_docx`` 的 ``content[].path`` / ``create_pptx`` 的
``slides[].image_path``）在修复前直接喂给 ``add_picture``，可把工作区外的任意
文件读进产物（``word/media/`` / ``ppt/media/``）并随产物外发——与 #994 的 H1
同类破口。

测试口径（见 plan_1005_v3 §3 / v4 patch §1、§4）：

- 越界断言**三元组**：spy 调用列表为空 + 产物 media 为空 + 返回串含「跳过」。
  只用 media 为空会假绿——未修复代码对相对跨会话路径抛 FileNotFoundError，
  被 ``execute`` 的 except 吞掉后同样产不出 media。
- spy 必须 **call-through**（真调用原 ``Image.from_file``），否则界内分支也
  产不出 media，界外断言跟着假绿；因此每组越界用例都配**正对照**。
- 用户授权目录（``_user_roots``，#821 机制）正/反各一：``allow_user_roots=True``
  + 注入 ``_user_roots`` 才可读，缺一即跳过（fail-closed）。
"""

from __future__ import annotations

import importlib
import zipfile
from pathlib import Path

import pytest

_DOCX_IMAGE = "docx.image.image.Image"
_PPTX_IMAGE = "pptx.parts.image.Image"


# ── helpers ────────────────────────────────────────────────────────────


def _write_png(path: Path) -> Path:
    """Write a real (readable by python-docx/pptx) PNG and return it."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (200, 30, 30)).save(str(path))
    return path


def _session_files_dir(tmp_path: Path, key: str = "desktop_A") -> Path:
    """``<base>/sessions/<key>/files`` — the session files root office tools get."""
    files_dir = tmp_path / "workspace" / "sessions" / key / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def _media_entries(artifact: Path, prefix: str) -> list[str]:
    """Names of embedded media parts inside a .docx/.pptx zip container."""
    with zipfile.ZipFile(str(artifact)) as zf:
        return [n for n in zf.namelist() if n.startswith(prefix)]


def _spy_image_from_file(monkeypatch, dotted: str) -> list:
    """Patch ``<Image>.from_file`` with a call-through spy; return its call list.

    Call-through is mandatory: a bare MagicMock would make *in-bounds* images
    produce no media either, so the out-of-bounds ``media == []`` assertion
    would pass vacuously.
    """
    mod_name, cls_name = dotted.rsplit(".", 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)
    real = cls.from_file
    calls: list = []

    def spy(image_descriptor):
        calls.append(image_descriptor)
        return real(image_descriptor)

    monkeypatch.setattr(cls, "from_file", spy)
    return calls


async def _run_docx(files_dir: Path, image_path, **kwargs) -> str:
    from miqi.documents.docx_tool import CreateDocxTool

    tool = CreateDocxTool(
        workspace=files_dir,
        allowed_dir=files_dir,
        allow_user_roots=kwargs.pop("allow_user_roots", False),
    )
    return await tool.execute(
        filename="out.docx",
        content=[{"type": "image", "path": str(image_path)}],
        **kwargs,
    )


async def _run_pptx(files_dir: Path, image_path, **kwargs) -> str:
    from miqi.documents.pptx_tool import CreatePptxTool

    tool = CreatePptxTool(
        workspace=files_dir,
        allowed_dir=files_dir,
        allow_user_roots=kwargs.pop("allow_user_roots", False),
    )
    return await tool.execute(
        filename="out.pptx",
        slides=[{"title": "t", "image_path": str(image_path)}],
        **kwargs,
    )


# ── docx ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_docx_out_of_bounds_image_is_not_read(tmp_path, monkeypatch):
    """工作区外的绝对路径图片：不读文件、产物无 media、返回串含「跳过」。"""
    files_dir = _session_files_dir(tmp_path)
    secret = _write_png(tmp_path / "outside" / "secret.png")
    calls = _spy_image_from_file(monkeypatch, _DOCX_IMAGE)

    result = await _run_docx(files_dir, secret)

    assert calls == []
    assert _media_entries(files_dir / "out.docx", "word/media/") == []
    assert "跳过" in result


@pytest.mark.asyncio
async def test_docx_in_bounds_image_is_embedded(tmp_path, monkeypatch):
    """正对照：界内图片真嵌入（spy 恰好 1 次、media 非空）。"""
    files_dir = _session_files_dir(tmp_path)
    image = _write_png(files_dir / "assets" / "chart.png")
    calls = _spy_image_from_file(monkeypatch, _DOCX_IMAGE)

    result = await _run_docx(files_dir, image)

    assert len(calls) == 1
    assert _media_entries(files_dir / "out.docx", "word/media/") != []
    assert "Created:" in result
    assert "跳过" not in result


@pytest.mark.asyncio
async def test_docx_cross_session_image_is_not_read(tmp_path, monkeypatch):
    """跨会话图片（绝对路径指向另一会话 files 根）：三元组断言。"""
    files_dir = _session_files_dir(tmp_path, key="desktop_A")
    other = _write_png(
        _session_files_dir(tmp_path, key="desktop_B") / "secret.png"
    )
    calls = _spy_image_from_file(monkeypatch, _DOCX_IMAGE)

    result = await _run_docx(files_dir, other)

    assert calls == []
    assert _media_entries(files_dir / "out.docx", "word/media/") == []
    assert "跳过" in result


@pytest.mark.asyncio
async def test_docx_missing_and_non_image_blocks_do_not_fail_document(
    tmp_path, monkeypatch,
):
    """界内但不存在 / 非图片：跳过该图片，文档照常产出（不整档失败）。"""
    from docx import Document

    files_dir = _session_files_dir(tmp_path)
    not_an_image = files_dir / "fake.png"
    not_an_image.write_text("this is not a png", encoding="utf-8")
    _spy_image_from_file(monkeypatch, _DOCX_IMAGE)

    result = await _run_docx(files_dir, files_dir / "missing.png")
    assert "跳过" in result
    result2 = await _run_docx(files_dir, not_an_image)
    assert "跳过" in result2

    # 这两种失败发生在 Image.from_file 内部（spy 会被调到），所以只断言
    # 「没产出 media + 文档照常建成」，不断言 spy 为空。
    assert _media_entries(files_dir / "out.docx", "word/media/") == []
    # 文档本身仍然可读（不是 Error 串）
    assert "Created:" in result2
    assert Document(str(files_dir / "out.docx")) is not None


@pytest.mark.asyncio
async def test_docx_user_roots_positive(tmp_path, monkeypatch):
    """allow_user_roots=True + 注入 _user_roots：授权目录内图片真嵌入。"""
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    image = _write_png(authorized / "photo.png")
    calls = _spy_image_from_file(monkeypatch, _DOCX_IMAGE)

    result = await _run_docx(
        files_dir,
        image,
        allow_user_roots=True,
        _user_roots=[str(authorized)],
    )

    assert len(calls) == 1
    assert _media_entries(files_dir / "out.docx", "word/media/") != []
    assert "Created:" in result


@pytest.mark.asyncio
async def test_docx_user_roots_not_injected_is_skipped(tmp_path, monkeypatch):
    """反例：allow_user_roots=True 但没注入 _user_roots → 跳过。"""
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    image = _write_png(authorized / "photo.png")
    calls = _spy_image_from_file(monkeypatch, _DOCX_IMAGE)

    result = await _run_docx(files_dir, image, allow_user_roots=True)

    assert calls == []
    assert _media_entries(files_dir / "out.docx", "word/media/") == []
    assert "跳过" in result


@pytest.mark.asyncio
async def test_docx_user_roots_disabled_flag_is_skipped(tmp_path, monkeypatch):
    """反例：注入了 _user_roots 但 allow_user_roots=False → 仍跳过（fail-closed）。"""
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    image = _write_png(authorized / "photo.png")
    calls = _spy_image_from_file(monkeypatch, _DOCX_IMAGE)

    result = await _run_docx(
        files_dir,
        image,
        allow_user_roots=False,
        _user_roots=[str(authorized)],
    )

    assert calls == []
    assert _media_entries(files_dir / "out.docx", "word/media/") == []
    assert "跳过" in result


# ── pptx ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pptx_out_of_bounds_image_is_not_read(tmp_path, monkeypatch):
    files_dir = _session_files_dir(tmp_path)
    secret = _write_png(tmp_path / "outside" / "secret.png")
    calls = _spy_image_from_file(monkeypatch, _PPTX_IMAGE)

    result = await _run_pptx(files_dir, secret)

    assert calls == []
    assert _media_entries(files_dir / "out.pptx", "ppt/media/") == []
    assert "跳过" in result


@pytest.mark.asyncio
async def test_pptx_in_bounds_image_is_embedded(tmp_path, monkeypatch):
    files_dir = _session_files_dir(tmp_path)
    image = _write_png(files_dir / "assets" / "chart.png")
    calls = _spy_image_from_file(monkeypatch, _PPTX_IMAGE)

    result = await _run_pptx(files_dir, image)

    assert len(calls) == 1
    assert _media_entries(files_dir / "out.pptx", "ppt/media/") != []
    assert "Created:" in result
    assert "跳过" not in result


@pytest.mark.asyncio
async def test_pptx_cross_session_image_is_not_read(tmp_path, monkeypatch):
    files_dir = _session_files_dir(tmp_path, key="desktop_A")
    other = _write_png(
        _session_files_dir(tmp_path, key="desktop_B") / "secret.png"
    )
    calls = _spy_image_from_file(monkeypatch, _PPTX_IMAGE)

    result = await _run_pptx(files_dir, other)

    assert calls == []
    assert _media_entries(files_dir / "out.pptx", "ppt/media/") == []
    assert "跳过" in result


@pytest.mark.asyncio
async def test_pptx_user_roots_positive(tmp_path, monkeypatch):
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    image = _write_png(authorized / "photo.png")
    calls = _spy_image_from_file(monkeypatch, _PPTX_IMAGE)

    result = await _run_pptx(
        files_dir,
        image,
        allow_user_roots=True,
        _user_roots=[str(authorized)],
    )

    assert len(calls) == 1
    assert _media_entries(files_dir / "out.pptx", "ppt/media/") != []
    assert "Created:" in result


@pytest.mark.asyncio
async def test_pptx_user_roots_not_injected_is_skipped(tmp_path, monkeypatch):
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    image = _write_png(authorized / "photo.png")
    calls = _spy_image_from_file(monkeypatch, _PPTX_IMAGE)

    result = await _run_pptx(files_dir, image, allow_user_roots=True)

    assert calls == []
    assert _media_entries(files_dir / "out.pptx", "ppt/media/") == []
    assert "跳过" in result


# ── 接线级：工厂必须把 tools.auto_user_dirs 传进 4 个工具 ─────────────


def _registry(fake_config, session_id: str = "desktop:1005"):
    from pathlib import Path as _Path

    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    return create_runtime_tool_registry(
        config=fake_config,
        workspace=_Path(fake_config.agents.defaults.workspace),
        session_id=session_id,
    )


@pytest.mark.parametrize(
    "tool_name",
    ["create_docx", "docx_write", "create_pptx", "pptx_write"],
)
def test_registry_wires_auto_user_dirs_true(fake_config, tool_name):
    """正向接线：默认 auto_user_dirs=True → 4 个别名工具都得 True。

    只断言 create_docx 会漏掉 docx_write / create_pptx / pptx_write 的静默
    丢图（别名工具继承 __init__，漏传即静默 allow_user_roots=False）。
    """
    registry = _registry(fake_config)

    assert registry.get(tool_name)._allow_user_roots is True


@pytest.mark.parametrize(
    "tool_name",
    ["create_docx", "docx_write", "create_pptx", "pptx_write"],
)
def test_registry_wires_auto_user_dirs_false(fake_config, tool_name):
    """负向接线：auto_user_dirs=False → False（挡「工厂里硬编码 True」）。"""
    fake_config.tools.auto_user_dirs = False
    registry = _registry(fake_config)

    assert registry.get(tool_name)._allow_user_roots is False
