# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（--onedir）。

在项目根执行：
    python -m PyInstaller packaging/smartdoc.spec --noconfirm --clean
产物：dist/SmartDocOrganizer/SmartDocOrganizer.exe（旁边 _internal/ 为资源目录）
"""
import os

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = os.getcwd()
APP_NAME = "SmartDocOrganizer"

# 业务代码按包整包收集；测试不打进安装包
hiddenimports = [
    m for m in collect_submodules("backend") if not m.startswith("backend.tests")
]
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    # 自动更新模块在项目根，backend 通过 import desktop_update 调用
    "desktop_update",
]
# pywebview 的 Windows 后端依赖 pythonnet / clr
hiddenimports += collect_submodules("webview")

datas = [
    # 前端构建产物：config.BASE_DIR 在打包后等于 _internal，故落在 frontend/dist
    (os.path.join(ROOT, "frontend", "dist"), os.path.join("frontend", "dist")),
]
datas += collect_data_files("pythonnet")
datas += collect_data_files("webview")
binaries = collect_dynamic_libs("pythonnet")

# 当前构建号，供应用自更新时与远端 desktop.json 的 build 比较
_build_ver = os.path.join(ROOT, "packaging", "build_version.txt")
if os.path.exists(_build_ver):
    datas.append((_build_ver, "."))

a = Analysis(
    [os.path.join(ROOT, "desktop_app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tkinter",
        "matplotlib",
        "pandas",
        "IPython",
        "notebook",
        "backend.tests",
        # 视觉能力（OCR + 图像处理）拆为可选扩展包，主包不携带
        "cv2",
        "rapidocr_onnxruntime",
        "onnxruntime",
        "numpy",
        "pyclipper",
        "shapely",
    ],
    noarchive=False,
)

# 视觉依赖（OCR / OpenCV / numpy）不进主包，改由设置页下载扩展包提供
_VISION_MARKS = ("cv2", "rapidocr", "onnxruntime", "numpy", "pyclipper", "shapely", "opencv_videoio_ffmpeg")


def _is_vision(p: str) -> bool:
    low = p.replace("\\", "/").lower()
    return any(m in low for m in _VISION_MARKS)


a.binaries = [b for b in a.binaries if not _is_vision(b[0])]
a.datas = [d for d in a.datas if not _is_vision(d[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 桌面形态：不弹黑框
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "packaging", "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
