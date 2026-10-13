
import os
import rapidocr_onnxruntime as _roc

# rapidocr ONNX models live under site-packages/rapidocr_onnxruntime/models
# (ch_PP-OCRv4_det/rec + cls, ~15MB). PyInstaller does NOT auto-collect them —
# without this the packaged miqi-bridge.exe silently loses image OCR (#704).
_roc_models = os.path.join(os.path.dirname(_roc.__file__), "models")

a = Analysis(
    ['miqi/bridge/server.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('miqi/templates', 'miqi/templates'),
        ('miqi/skills', 'miqi/skills'),
        # #1267：内置插件（system_plugins_dir = miqi/plugins）须随桥打包，
        # 否则 PyInstaller onefile 下插件目录不存在，内置插件永不被发现。
        ('miqi/plugins', 'miqi/plugins'),
        (_roc_models, 'rapidocr_onnxruntime/models'),
    ],
    hiddenimports=[
        # MiQi internal modules
        'miqi.agent',
        'miqi.agent.tools',
        'miqi.agent.memory',
        'miqi.providers',
        'miqi.channels',
        'miqi.config',
        'miqi.session',
        'miqi.cron',
        'miqi.bus',
        'miqi.utils',
        # Third-party deps checked dynamically via importlib.import_module()
        # (PyInstaller can't detect these — server.py handle_python_check, line 1957)
        'pydantic',
        'pydantic.deprecated.decorator',
        'pydantic._internal._config',
        'httpx',
        'httpcore',
        'loguru',
        # OCR: rapidocr imports onnxruntime dynamically (#704)
        'rapidocr_onnxruntime',
        'rapidocr_onnxruntime.onnxruntime_engine',
        'onnxruntime',
        'onnxruntime.capi._pybind_state',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='miqi-bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 改为 True 调试时可以看到输出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
