# -*- coding: utf-8 -*-
"""制作视觉扩展包（OCR + OpenCV），供设置页一键下载。

产物：
    release/extensions/vision.zip   —— 解压到 %APPDATA%\\SmartDocOrganizer\\extensions\\vision
    release/extensions/vision.json  —— 版本、大小、SHA256，供发布脚本读取

主安装包不含这些依赖；装上扩展包后重启应用即可启用 OCR 与盖章检测。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sysconfig
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sysconfig.get_paths()["purelib"])
OUT_DIR = ROOT / "release" / "extensions"

# 扩展包内容（rapidocr 的运行时依赖 + 图像处理）
PACKAGES = [
    "cv2",
    "numpy",
    "numpy.libs",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "pyclipper",
    "shapely",
    "shapely.libs",
    "six.py",
]

# 省体积：缓存、元数据、视频编解码 DLL（只做图像处理，不需要 VideoCapture）
SKIP_DIRS = {"__pycache__", "tests", "test"}
SKIP_SUFFIX = {".pyc", ".pyo"}
SKIP_FILES = {"opencv_videoio_ffmpeg500_64.dll", "opencv_videoio_ffmpeg4xx_64.dll"}


def _ignore(_dir: str, names: list[str]) -> set[str]:
    out = set()
    for n in names:
        if n in SKIP_DIRS or n.endswith(".dist-info"):
            out.add(n)
        elif Path(n).suffix in SKIP_SUFFIX:
            out.add(n)
        elif n.lower().startswith("opencv_videoio_ffmpeg"):
            out.add(n)
        elif n in SKIP_FILES:
            out.add(n)
    return out


def main() -> int:
    stage = Path(tempfile.mkdtemp(prefix="sdo_vision_"))
    try:
        for pkg in PACKAGES:
            src = SRC / pkg
            if not src.exists():
                print(f"skip missing: {pkg}")
                continue
            dst = stage / pkg
            if src.is_dir():
                shutil.copytree(src, dst, ignore=_ignore)
            else:
                shutil.copy2(src, dst)
            size = sum(f.stat().st_size for f in (dst.rglob("*") if dst.is_dir() else [dst]) if f.is_file())
            print(f"  + {pkg:24} {size / 1048576:7.1f} MB")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_zip = OUT_DIR / "vision.zip"
        out_zip.unlink(missing_ok=True)
        print("压缩中（LZMA）...")
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_LZMA, compresslevel=6) as z:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(stage).as_posix())

        sha = hashlib.sha256()
        with open(out_zip, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                sha.update(chunk)
        meta = {
            "package": "vision",
            "version": time.strftime("%Y%m%d.%H%M%S"),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "file": "vision.zip",
            "size": out_zip.stat().st_size,
            "sha256": sha.hexdigest(),
            "contains": [p for p in PACKAGES if (SRC / p).exists()],
        }
        (OUT_DIR / "vision.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"完成: {out_zip}  {meta['size'] / 1048576:.1f} MB  sha={meta['sha256'][:16]}")
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
