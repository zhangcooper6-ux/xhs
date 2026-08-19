# -*- coding: utf-8 -*-
"""图片去重模块（基于 Pillow）。

去重手段：
  - 水平镜像
  - 轻微旋转/裁剪
  - 色彩微调（亮度/对比度/饱和度/锐度）
  - 添加轻微噪点
  - 去除 EXIF 元数据
  - 强制重编码（破坏原始哈希）
"""
import os
import random
import logging
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import config

logger = logging.getLogger("dedup_image")


def _add_noise(img, amount=4):
    """给图片添加轻微高斯噪点。amount: 0-50。"""
    if amount <= 0:
        return img
    # 用噪点滤镜模拟，强度映射到 radius
    radius = max(0.05, amount / 100.0 * 0.6)
    noise_layer = img.convert("L").filter(ImageFilter.GaussianBlur(radius=radius))
    # 将噪点叠加回原图（轻微）
    return Image.blend(img, Image.merge("RGB", (noise_layer, noise_layer, noise_layer)), 0.08)


def dedup_image(src_path, dst_path, params=None):
    """对单张图片执行去重处理。

    参数:
        src_path: 源图片路径
        dst_path: 输出路径
        params: 去重参数（默认取 config.IMAGE_DEDUP）
    返回:
        dst_path 或 None
    """
    if params is None:
        params = config.IMAGE_DEDUP

    if not os.path.exists(src_path):
        logger.error("源图片不存在: %s", src_path)
        return None

    try:
        img = Image.open(src_path)
    except Exception as e:
        logger.error("打开图片失败 %s: %s", src_path, e)
        return None

    # 处理 RGBA/P 等模式 -> RGB
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size

    # 1. 旋转
    rotate = params.get("rotate", 0)
    if rotate and rotate != 0:
        img = img.rotate(-rotate, expand=True, fillcolor=(255, 255, 255))
        w, h = img.size

    # 2. 裁剪四边
    crop_ratio = params.get("crop_ratio", 0.0)
    if crop_ratio and crop_ratio > 0:
        cx = int(w * crop_ratio)
        cy = int(h * crop_ratio)
        img = img.crop((cx, cy, w - cx, h - cy))
        nw, nh = img.size
        # 放大回原尺寸
        if params.get("resize_back", True):
            img = img.resize((w, h), Image.LANCZOS)
        else:
            w, h = nw, nh

    # 3. 水平镜像
    if params.get("mirror", True):
        img = ImageOps.mirror(img)

    # 4. 色彩微调
    if params.get("brightness", 1.0) != 1.0:
        img = ImageEnhance.Brightness(img).enhance(params["brightness"])
    if params.get("contrast", 1.0) != 1.0:
        img = ImageEnhance.Contrast(img).enhance(params["contrast"])
    if params.get("saturation", 1.0) != 1.0:
        img = ImageEnhance.Color(img).enhance(params["saturation"])
    if params.get("sharpness", 1.0) != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(params["sharpness"])

    # 5. 轻微噪点
    if params.get("noise_amount", 0) > 0:
        img = _add_noise(img, params["noise_amount"])

    # 6. 去除 EXIF 并保存（强制重编码）
    fmt = params.get("format", "JPEG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    quality = params.get("quality", 98)
    strip_exif = params.get("strip_exif", True)

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    save_kwargs = {"quality": quality, "optimize": True}
    if fmt == "JPEG":
        save_kwargs["progressive"] = True
        subsampling = params.get("subsampling", 0)  # 0 = 4:4:4 全色度无损采样（无字幕/轮廓边缘模糊）
        save_kwargs["subsampling"] = subsampling

    # 根据目标扩展名决定格式
    ext = os.path.splitext(dst_path)[1].lower()
    if ext in (".png",):
        fmt = "PNG"
        save_kwargs = {"optimize": True}
    elif ext in (".webp",):
        fmt = "WEBP"
        save_kwargs = {"quality": quality, "method": 6}

    # 保留 EXIF（--no-strip-exif 时）：强制重编码仍会破坏像素哈希，但保留元数据
    if not strip_exif:
        exif_data = img.info.get("exif")
        if exif_data:
            save_kwargs["exif"] = exif_data

    try:
        img.save(dst_path, format=fmt, **save_kwargs)
        logger.info("图片去重完成: %s -> %s", os.path.basename(src_path), dst_path)
        return dst_path
    except Exception as e:
        logger.error("保存图片失败 %s: %s", dst_path, e)
        return None
