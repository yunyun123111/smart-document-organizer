"""单据判重指纹：感知哈希（dHash 64 位）+ 宽高比。

背景：同一张结算单被上传成两份副本（真实库里的 id=5 / id=17），金额会被
算两遍，下游结算变成 1,584 万。现有业务键去重（同角色 + 同合同号 + 同金额
+ 同日期）能拦住它；但只要其中一份金额或日期抽错，业务键就失效。

dHash 只看图像本身，不看抽取结果，正好补这个缺口：
- 同一张图重新编码/重存 → 距离 0~1 位、尺寸不变，判为同一张；
- 同版式不同金额的两张单 → 实测距离 2~3 位（id1/id7 差 2、id14/id15 差 3），
  阈值卡在 1 位就不会误判成同一张。

只读本地文件，纯 CPU，无外部依赖（PIL / PyMuPDF 项目已装）。
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Optional

_HASH_SIDE = 8              # 8x8 差分 → 64 位
_MAX_SAME_DIST = 1          # 判为同一张图的最大汉明距离

# 为什么阈值只有 1 位、且要求像素尺寸完全一致：
# 实测 id1 / id7 两张不同金额的增值税发票（同版式、同尺寸）dHash 只差 2 位，
# id14 / id15 两张不同货权单差 3 位。dHash 对"数字区域"的变化不敏感，
# 阈值放到 4 位就会把它们误判成同一张。真正的同图副本（id5/id17 存了两份）
# 距离是 0，尺寸也完全相同，用 1 位 + 尺寸相等既能命中，又不会误合并。


def _open_image(path: str):
    """读图；PDF 取首页按 100dpi 渲染成位图。失败返回 None。"""
    suffix = Path(path).suffix.lower()
    try:
        from PIL import Image

        if suffix == ".pdf":
            import fitz  # PyMuPDF

            with fitz.open(path) as pdf:
                if not len(pdf):
                    return None
                pix = pdf[0].get_pixmap(dpi=100)
                if pix.width == 0 or pix.height == 0:
                    return None
                return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return Image.open(path).convert("RGB")
    except Exception:  # noqa: BLE001 —— 文件可能已被移动/删除，指纹缺失不阻断对账
        return None


def _dhash(img) -> str:
    """8x8 差分哈希：每行比较相邻像素亮度，输出 16 位十六进制串。"""
    from PIL import Image

    small = img.convert("L").resize((_HASH_SIDE + 1, _HASH_SIDE), Image.LANCZOS)
    px = list(small.tobytes())  # L 模式每像素 1 字节，值即灰度
    bits = 0
    for row in range(_HASH_SIDE):
        base = row * (_HASH_SIDE + 1)
        for col in range(_HASH_SIDE):
            bits = (bits << 1) | (1 if px[base + col] < px[base + col + 1] else 0)
    return f"{bits:016x}"


@functools.lru_cache(maxsize=8192)
def _fingerprint_cached(path: str, mtime: float) -> Optional[tuple[str, int, int]]:
    del mtime  # 仅用于让缓存随文件改动失效
    img = _open_image(path)
    if img is None:
        return None
    width, height = img.size
    if width <= 0 or height <= 0:
        return None
    return _dhash(img), width, height


def fingerprint(path: str | None) -> Optional[tuple[str, int, int]]:
    """返回 (dhash, width, height)；文件读不到返回 None。

    结果按「路径 + mtime」缓存，同一进程内重复对账不再重算。
    """
    if not path:
        return None
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    return _fingerprint_cached(str(p), mtime)


def hamming(a: str, b: str) -> int:
    """两个 dHash 的汉明距离；格式非法返回 99（按"不同"处理）。"""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 99


def is_same_image(
    fp1: Optional[tuple[str, int, int]],
    fp2: Optional[tuple[str, int, int]],
) -> bool:
    """两个指纹是否指向同一张图（不含金额语义，金额冲突由调用方再判）。

    同一张图被重新编码/重存时像素尺寸不变、dHash 基本不变；而"同版式不同
    金额的两张单"尺寸虽相同，dHash 会差 2~3 位 —— 所以这里既要求尺寸完全
    相等，也把距离卡在 1 位。
    """
    if not fp1 or not fp2:
        return False
    (hash1, w1, h1), (hash2, w2, h2) = fp1, fp2
    if (w1, h1) != (w2, h2):
        return False
    return hamming(hash1, hash2) <= _MAX_SAME_DIST
