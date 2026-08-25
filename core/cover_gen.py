# -*- coding: utf-8 -*-
"""封面生成模块：从视频抽帧/原封面获取底图，PIL 合成小红书爆款封面。

支持 4 种风格：
  1. bigtext    大字报风：高饱和渐变背景 + 超大字标题 + emoji 贴纸
  2. bottombar  底部字幕条风：原画面 + 底部渐变黑底 + 白字标题
  3. topbar     顶部标题条风：顶部高亮色条 + 标题
  4. magazine   杂志拼图风：三图拼接 + 中间大标题

底图来源（优先级）：
  1. 小红书原封面 URL（如果有）
  2. 视频抽帧（视频中段、高光时刻）
  3. 纯色背景兜底

输出：3:4 竖屏（1080x1440，小红书封面推荐比例）
"""

import os
import logging
import subprocess
import random
import shutil
from typing import List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("cover_gen")

# 小红书封面推荐比例 3:4
COVER_W = 1080
COVER_H = 1440

# ---------- 字体探测 ----------

def _find_font(size: int, bold: bool = False) -> str:
    """查找系统中可用的中文字体。"""
    candidates = []
    if bold:
        candidates = [
            r"C:\Windows\Fonts\msyhbd.ttc",    # 微软雅黑 Bold
            r"C:\Windows\Fonts\simhei.ttf",     # 黑体
            r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
            r"C:\Windows\Fonts\simfang.ttf",    # 仿宋
        ]
    else:
        candidates = [
            r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
            r"C:\Windows\Fonts\simhei.ttf",     # 黑体
            r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑 Bold
            r"C:\Windows\Fonts\simfang.ttf",    # 仿宋
        ]
    # Linux/Mac 兜底
    candidates += [
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _get_emoji_stickers_dir() -> str:
    """获取 emoji 贴纸目录。"""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "stickers")


def _get_random_emojis(n: int = 3) -> List[str]:
    """随机选 n 个 emoji 贴纸路径。"""
    sticker_dir = _get_emoji_stickers_dir()
    if not os.path.isdir(sticker_dir):
        return []
    # 优先用 128 尺寸
    stickers = [f for f in os.listdir(sticker_dir)
                if f.endswith("_128.png") and not f.startswith("_")]
    if not stickers:
        stickers = [f for f in os.listdir(sticker_dir)
                    if f.endswith(".png") and not f.startswith("_")]
    if not stickers:
        return []
    selected = random.sample(stickers, min(n, len(stickers)))
    return [os.path.join(sticker_dir, s) for s in selected]


# ---------- 底图获取 ----------

def download_original_cover(cover_url: str, out_path: str, timeout: int = 15) -> Optional[str]:
    """下载小红书原封面。"""
    if not cover_url:
        return None
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.xiaohongshu.com/",
        }
        r = requests.get(cover_url, headers=headers, timeout=timeout, stream=True)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        logger.info("原封面已下载: %s", out_path)
        return out_path
    except Exception as e:
        logger.warning("下载原封面失败: %s", e)
        return None


def extract_video_frame(video_path: str, out_path: str,
                        time_ratio: float = 0.3) -> Optional[str]:
    """从视频抽帧作为封面底图。

    time_ratio: 抽帧位置（0.0=首帧, 0.5=中段, 1.0=末尾），默认 0.3（跳过黑屏片头）
    """
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning("无法打开视频抽帧: %s", video_path)
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            # 用 ffmpeg 兜底
            cap.release()
            return _extract_frame_ffmpeg(video_path, out_path, time_ratio)
        target_frame = int(total * time_ratio)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return _extract_frame_ffmpeg(video_path, out_path, time_ratio)
        # BGR -> RGB 后保存
        frame_rgb = frame[:, :, ::-1]
        from PIL import Image
        img = Image.fromarray(frame_rgb)
        img.save(out_path, quality=95)
        logger.info("视频抽帧成功: %s (帧 %d/%d)", out_path, target_frame, total)
        return out_path
    except ImportError:
        return _extract_frame_ffmpeg(video_path, out_path, time_ratio)
    except Exception as e:
        logger.warning("视频抽帧失败: %s", e)
        return _extract_frame_ffmpeg(video_path, out_path, time_ratio)


def _extract_frame_ffmpeg(video_path: str, out_path: str,
                          time_ratio: float) -> Optional[str]:
    """用 ffmpeg 抽帧（cv2 不可用时的兜底）。"""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    # 先获取时长
    try:
        import json
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            out = subprocess.check_output(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", video_path],
                stderr=subprocess.STDOUT, timeout=30,
            ).decode("utf-8", "ignore")
            duration = float(json.loads(out).get("format", {}).get("duration", 0))
        else:
            duration = 0
    except Exception:
        duration = 0

    seek_time = max(0, duration * time_ratio) if duration > 0 else 0

    try:
        cmd = [ffmpeg, "-y", "-ss", str(seek_time), "-i", video_path,
               "-frames:v", "1", "-q:v", "2", out_path]
        result = subprocess.run(cmd, capture_output=True, timeout=30,
                                encoding="utf-8", errors="ignore")
        if os.path.exists(out_path):
            logger.info("ffmpeg 抽帧成功: %s", out_path)
            return out_path
        return None
    except Exception as e:
        logger.warning("ffmpeg 抽帧失败: %s", e)
        return None


def get_cover_base_image(cover_url: str = None, video_path: str = None,
                         out_dir: str = ".",
                         cover_local: str = None) -> Tuple[Optional[str], str]:
    """获取封面底图（原封面优先，视频抽帧兜底）。

    参数:
        cover_url: 原封面 URL（小红书/抖音笔记）
        video_path: 视频文件路径（用于抽帧兜底）
        out_dir: 输出目录
        cover_local: 解析阶段已下载的原封面本地路径（优先复用，避免重复下载）
    返回: (底图路径, 来源标签 "original" / "frame" / "none")
    """
    os.makedirs(out_dir, exist_ok=True)

    # 1. 原封面优先
    #   a) 优先复用解析阶段已下载的封面文件
    if cover_local and os.path.exists(cover_local):
        # 复制到 out_dir 下统一管理（保持 cover_original.jpg 命名）
        orig_path = os.path.join(out_dir, "cover_original.jpg")
        if os.path.abspath(cover_local) != os.path.abspath(orig_path):
            try:
                shutil.copyfile(cover_local, orig_path)
                logger.info("复用已下载的原封面: %s -> %s", cover_local, orig_path)
            except Exception as e:
                logger.warning("复制原封面失败，将重新下载: %s", e)
                orig_path = None
        if orig_path and os.path.exists(orig_path):
            return orig_path, "original"
    #   b) 没有本地文件则从 URL 下载
    if cover_url:
        orig_path = os.path.join(out_dir, "cover_original.jpg")
        r = download_original_cover(cover_url, orig_path)
        if r:
            return r, "original"

    # 2. 视频抽帧兜底
    if video_path and os.path.exists(video_path):
        frame_path = os.path.join(out_dir, "cover_frame.jpg")
        r = extract_video_frame(video_path, frame_path, time_ratio=0.3)
        if r:
            return r, "frame"

    return None, "none"


def _remove_text_from_image(image_path: str, out_path: str,
                            padding: int = 6,
                            inpaint_radius: int = 4,
                            min_text_score: float = 0.5) -> Optional[str]:
    """去除图片上的文字（PaddleOCR 检测 + cv2.inpaint 修复）。

    用于封面底图预处理：小红书原封面常带标题/作者名/水印文字，
    生成 bigtext/bottombar/topbar/magazine 等新封面时若不去除，
    原图文字会与新叠加的标题重叠冲突，视觉杂乱。

    参数:
        image_path: 输入图片路径
        out_path: 输出图片路径
        padding: 文字框外扩像素（确保覆盖文字描边/阴影）
        inpaint_radius: cv2.inpaint 修复半径
        min_text_score: OCR 置信度阈值，低于此值的识别结果忽略
    返回:
        处理后图片路径；OCR/cv2 不可用或未检测到文字时返回 None
        （调用方应回退到原图）
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.debug("cv2/numpy 不可用，跳过封面底图文字去除")
        return None

    try:
        from . import watermark_remove as wm_mod
    except ImportError:
        logger.debug("watermark_remove 模块不可用，跳过封面底图文字去除")
        return None

    ocr = wm_mod._get_ocr()
    if ocr is None:
        logger.info("PaddleOCR 不可用，跳过封面底图文字去除"
                    "（如需启用: pip install paddleocr paddlepaddle）")
        return None

    img = cv2.imread(image_path)
    if img is None:
        logger.warning("封面底图文字去除: 无法读取图片 %s", image_path)
        return None

    h, w = img.shape[:2]

    # OCR 检测所有文字区域（_run_ocr_on_frame 内部已做大图缩放保护）
    ocr_results = wm_mod._run_ocr_on_frame(img, ocr, max_size=1280)
    # 过滤低置信度
    ocr_results = [(b, t, s) for (b, t, s) in ocr_results if s >= min_text_score]
    if not ocr_results:
        logger.info("封面底图未检测到文字，无需去除")
        return None

    logger.info("封面底图检测到 %d 处文字，开始 inpaint 去除", len(ocr_results))

    # 构建 inpaint mask
    mask = np.zeros((h, w), dtype=np.uint8)
    for box, text, score in ocr_results:
        x, y, rw, rh = wm_mod._box_to_rect(box)
        # 外扩 padding 确保覆盖文字描边/阴影
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(w, x + rw + padding)
        y2 = min(h, y + rh + padding)
        mask[y1:y2, x1:x2] = 255
        logger.debug("  去除文字: %s (score=%.2f, box=[%d,%d,%d,%d])",
                     text, score, x1, y1, x2, y2)

    # inpaint 修复（TELEA 算法速度快，适合大面积文字区域）
    inpainted = cv2.inpaint(img, mask, inpaint_radius, cv2.INPAINT_TELEA)

    cv2.imwrite(out_path, inpainted, [cv2.IMWRITE_JPEG_QUALITY, 95])
    logger.info("封面底图文字已去除: %s", out_path)
    return out_path


# ---------- PIL 绘图工具 ----------

def _clean_title_length(title: str, max_len: int = 20) -> str:
    """清洗标题并严格限制在 20 字以内（硬限制不超过 21 字）。"""
    if not title:
        return ""
    title = title.strip().strip("\"'\"\"''")
    if len(title) > 21:
        title = title[:20].rstrip("｜·丨—→,，.!?！？ ")
    return title


def _load_image(path: str, target_w: int, target_h: int, preserve_aspect: bool = True):
    """加载图片并高质量裁切/缩放到目标尺寸（3:4 竖屏 1080x1440）。

    优化小红书视觉审美：
    - 垂直竖屏（9:16 等）：聚焦上中段核心视觉区域（40% 黄金机位），全画幅无黑边超清呈现。
    - 横屏视频（16:9 等）：居中高画质等比裁切填充，保持画面饱满。
    - 画面微调：提升饱和度与清晰度，让封面色彩鲜活通透。
    """
    from PIL import Image, ImageEnhance
    img = Image.open(path).convert("RGB")
    w, h = img.size

    # 计算目标缩放比例（等比缩放确保填满 1080x1440 全画幅）
    scale = max(target_w / w, target_h / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # 裁剪：针对人像/物体的上中位置（top 偏上 40% 处，避免切掉人物头部与核心主体）
    left = max(0, (new_w - target_w) // 2)
    top_offset_ratio = 0.38 if new_h > target_h else 0.5
    top = max(0, int((new_h - target_h) * top_offset_ratio))
    if top + target_h > new_h:
        top = new_h - target_h

    cropped = img.crop((left, top, left + target_w, top + target_h))

    # 色彩与清晰度质感增强（小红书通透画质）
    cropped = ImageEnhance.Color(cropped).enhance(1.08)
    cropped = ImageEnhance.Brightness(cropped).enhance(1.02)
    cropped = ImageEnhance.Sharpness(cropped).enhance(1.15)
    return cropped


def _draw_text_drop_shadow(draw_target_img, xy, text, font, fill=(255, 255, 255),
                           shadow_color=(0, 0, 0, 160), shadow_offset=(3, 5), shadow_blur=10):
    """绘制带柔和高斯模糊投影的文字，避免生硬的像素描边。"""
    from PIL import Image, ImageDraw, ImageFilter
    x, y = xy
    # 在独立透明图层渲染阴影并高斯模糊
    shadow_img = Image.new("RGBA", draw_target_img.size, (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_img)
    s_draw.text((x + shadow_offset[0], y + shadow_offset[1]), text, font=font, fill=shadow_color)
    if shadow_blur > 0:
        shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=shadow_blur))

    # 贴合阴影
    draw_target_img.paste(shadow_img, (0, 0), shadow_img)
    # 绘制前景文字
    draw = ImageDraw.Draw(draw_target_img)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_sticker_card(bg_img, xy, text, font, bg_color, text_color,
                       pad_x=28, pad_y=16, radius=18, shadow_blur=14, shadow_alpha=90):
    """绘制小红书爆款贴纸字条（带圆角和自然悬浮投影）。"""
    from PIL import Image, ImageDraw, ImageFilter
    draw = ImageDraw.Draw(bg_img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    card_w = tw + pad_x * 2
    card_h = th + pad_y * 2
    cx, cy = xy

    # 1. 柔和投影
    if shadow_blur > 0:
        shadow_layer = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
        s_draw = ImageDraw.Draw(shadow_layer)
        s_draw.rounded_rectangle(
            [(cx + 2, cy + 6), (cx + card_w + 2, cy + card_h + 6)],
            radius=radius,
            fill=(0, 0, 0, shadow_alpha)
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        bg_img.paste(shadow_layer, (0, 0), shadow_layer)

    # 2. 贴纸卡片主体
    card_layer = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(card_layer)
    c_draw.rounded_rectangle(
        [(cx, cy), (cx + card_w, cy + card_h)],
        radius=radius,
        fill=bg_color
    )
    bg_img.paste(card_layer, (0, 0), card_layer)

    # 3. 内部文字
    text_x = cx + pad_x - bbox[0]
    text_y = cy + pad_y - bbox[1]
    d = ImageDraw.Draw(bg_img)
    d.text((text_x, text_y), text, font=font, fill=text_color)
    return card_w, card_h


def _split_title_smartly(title: str, max_chars: int = 10) -> List[str]:
    """智能将小红书标题切分为 1-2 段核心视觉短句（符合贴纸排版美感）。"""
    import re
    title = title.strip().strip("\"'\"\"''")
    if not title:
        return ["爆款内容推荐"]

    # 若包含分隔符（空格、｜、·、、！、？），优先按标点切分
    parts = [p.strip() for p in re.split(r"[ ｜·,，!！?？\n]+", title) if p.strip()]
    if len(parts) >= 2 and len(parts[0]) <= max_chars and len(parts[1]) <= max_chars:
        return parts[:2]

    # 无自然分隔符：按长度均匀切成 2 行
    if len(title) > max_chars:
        mid = len(title) // 2
        return [title[:mid], title[mid:mid + max_chars]]
    return [title]


def _create_gradient_bg(w: int, h: int, color1: Tuple, color2: Tuple,
                        direction: str = "vertical") -> "Image":
    """创建渐变背景。"""
    from PIL import Image
    bg = Image.new("RGB", (w, h), color1)
    # 逐行/逐列填充渐变
    if direction == "vertical":
        for y in range(h):
            t = y / max(1, h - 1)
            r = int(color1[0] + (color2[0] - color1[0]) * t)
            g = int(color1[1] + (color2[1] - color1[1]) * t)
            b = int(color1[2] + (color2[2] - color1[2]) * t)
            for x in range(w):
                bg.putpixel((x, y), (r, g, b))
    else:
        for x in range(w):
            t = x / max(1, w - 1)
            r = int(color1[0] + (color2[0] - color1[0]) * t)
            g = int(color1[1] + (color2[1] - color1[1]) * t)
            b = int(color1[2] + (color2[2] - color1[2]) * t)
            for y in range(h):
                bg.putpixel((x, y), (r, g, b))
    return bg


def _create_gradient_bg_fast(w: int, h: int, color1: Tuple, color2: Tuple,
                              direction: str = "vertical") -> "Image":
    """快速渐变背景（用 numpy 加速）。"""
    try:
        import numpy as np
        from PIL import Image
        if direction == "vertical":
            t = np.linspace(0, 1, h).reshape(-1, 1)
            r = (color1[0] + (color2[0] - color1[0]) * t).astype(np.uint8)
            g = (color1[1] + (color2[1] - color1[1]) * t).astype(np.uint8)
            b = (color1[2] + (color2[2] - color1[2]) * t).astype(np.uint8)
            arr = np.broadcast_to(
                np.stack([r, g, b], axis=-1).reshape(h, 1, 3),
                (h, w, 3)
            ).copy()
        else:
            t = np.linspace(0, 1, w).reshape(1, -1)
            r = (color1[0] + (color2[0] - color1[0]) * t).astype(np.uint8)
            g = (color1[1] + (color2[1] - color1[1]) * t).astype(np.uint8)
            b = (color1[2] + (color2[2] - color1[2]) * t).astype(np.uint8)
            arr = np.broadcast_to(
                np.stack([r, g, b], axis=-1).reshape(1, w, 3),
                (h, w, 3)
            ).copy()
        return Image.fromarray(arr)
    except ImportError:
        return _create_gradient_bg(w, h, color1, color2, direction)


def _wrap_text(text: str, font, max_width: int, draw) -> List[str]:
    """文字自动换行（按像素宽度）。"""
    lines = []
    current = ""
    for char in text:
        test = current + char
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = char
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _fit_font_size(title: str, max_width: int, max_lines: int,
                    max_size: int, min_size: int, font_path: str,
                    draw, line_gap: int = 20) -> tuple:
    """自适应字号：根据标题长度动态缩放字号，确保完整显示不截断。

    参数:
        title: 标题文本（不截断）
        max_width: 每行最大像素宽度
        max_lines: 最大行数
        max_size: 最大字号
        min_size: 最小字号（再小就不好看了）
        font_path: 字体路径
        draw: ImageDraw 对象
        line_gap: 行间距
    返回:
        (font, lines, line_height, total_height)
        font: 最终字体对象
        lines: 换行后的文本列表
        line_height: 单行高度
        total_height: 总高度
    """
    from PIL import ImageFont
    # 从最大字号开始，逐步缩小直到能容纳
    for size in range(max_size, min_size - 1, -4):
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        lines = _wrap_text(title, font, max_width, draw)
        # 检查是否在最大行数内
        if len(lines) <= max_lines:
            line_height = size + line_gap
            total_h = len(lines) * line_height
            return font, lines, line_height, total_h
    # 用最小字号 + 强制截断到 max_lines 行（兜底）
    font = ImageFont.truetype(font_path, min_size) if font_path else ImageFont.load_default()
    lines = _wrap_text(title, font, max_width, draw)
    if len(lines) > max_lines:
        # 最后一行加省略号
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…" if len(lines[-1]) > 1 else lines[-1]
    line_height = min_size + line_gap
    total_h = len(lines) * line_height
    return font, lines, line_height, total_h


def _draw_text_with_outline(draw, xy, text, font, fill, outline, outline_width=3):
    """绘制带描边的文字。"""
    x, y = xy
    # 描边（8 方向偏移）
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    # 主文字
    draw.text((x, y), text, font=font, fill=fill)


# ---------- 4 种封面风格 ----------

def render_bigtext_style(base_path: str, title: str, out_path: str,
                         emoji_count: int = 2) -> str:
    """风格 1：小红书爆款贴纸大字报风（全画幅超清底图 + 双色圆角贴纸字条 + 爆款钩子标签）。

    特点：100% 保留视频高光人物/画面，贴纸字条层次分明吸睛，杜绝背景模糊遮挡。
    """
    from PIL import Image, ImageDraw, ImageFont

    title = _clean_title_length(title, 20)

    # 1. 基础超清画面底图
    if not base_path or not os.path.exists(base_path):
        bg = Image.new("RGB", (COVER_W, COVER_H), (30, 30, 35))
    else:
        bg = _load_image(base_path, COVER_W, COVER_H)

    # 2. 顶部爆款胶囊 Hook 标签（如：🔥 建议收藏 / ⭐ 爆款干货）
    hook_tags = ["🔥 建议收藏", "💡 纯干货分享", "⭐ 保姆级教程", "📌 避坑指南", "✨ 爆款好物推荐"]
    hook_text = random.choice(hook_tags)
    hook_font_path = _find_font(42, bold=True)
    hook_font = ImageFont.truetype(hook_font_path, 42) if hook_font_path else ImageFont.load_default()

    # 顶部左上角贴上小红书红胶囊标签
    _draw_sticker_card(
        bg, (60, 80), hook_text, hook_font,
        bg_color=(255, 36, 66, 240), text_color=(255, 255, 255),
        pad_x=24, pad_y=12, radius=26, shadow_blur=10, shadow_alpha=70
    )

    # 3. 智能拆分 1-2 行核心贴纸标题
    lines = _split_title_smartly(title, max_chars=9)

    # 自适应字体大小（70~96px）
    font_path = _find_font(90, bold=True)
    font_size = 90 if max(len(l) for l in lines) <= 7 else 76
    font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()

    # 贴纸排版色彩方案（经典小红书配色：第一行荧光黄黑字，第二行纯黑白字/珊瑚红）
    color_schemes = [
        ((255, 230, 0, 245), (20, 20, 20), (20, 20, 24, 235), (255, 255, 255)),
        ((255, 255, 255, 245), (255, 36, 66), (20, 20, 24, 235), (255, 240, 0)),
        ((255, 36, 66, 245), (255, 255, 255), (255, 255, 255, 245), (20, 20, 20)),
    ]
    c_scheme = random.choice(color_schemes)

    # 贴纸垂直起始位置（居中偏下或黄金分割点）
    start_y = COVER_H - 420 - (len(lines) * 110)

    for i, line in enumerate(lines):
        d = ImageDraw.Draw(bg)
        bbox = d.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        card_w = tw + 28 * 2
        card_x = (COVER_W - card_w) // 2
        card_y = start_y + i * 115

        bg_col = c_scheme[0] if i == 0 else c_scheme[2]
        txt_col = c_scheme[1] if i == 0 else c_scheme[3]

        _draw_sticker_card(
            bg, (card_x, card_y), line, font,
            bg_color=bg_col, text_color=txt_col,
            pad_x=28, pad_y=16, radius=18, shadow_blur=16, shadow_alpha=110
        )

    # 4. 右下角点缀精致角标
    sub_font_path = _find_font(30, bold=True)
    sub_font = ImageFont.truetype(sub_font_path, 30) if sub_font_path else ImageFont.load_default()
    _draw_sticker_card(
        bg, (COVER_W - 240, COVER_H - 80), "HD 1080P", sub_font,
        bg_color=(0, 0, 0, 150), text_color=(255, 255, 255, 220),
        pad_x=16, pad_y=8, radius=12, shadow_blur=6, shadow_alpha=50
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("大字报风封面: %s", out_path)
    return out_path


def render_bottombar_style(base_path: str, title: str, out_path: str) -> str:
    """风格 2：底部电影感字幕条风（全画幅超清原图 + 顺滑暗调渐变 + 高质感白字）。

    特点：极具质感与沉浸感，适合 VLOG/剧情/美食/生活/旅行。
    """
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    title = _clean_title_length(title, 20)

    if not base_path or not os.path.exists(base_path):
        bg = Image.new("RGB", (COVER_W, COVER_H), (20, 20, 24))
    else:
        bg = _load_image(base_path, COVER_W, COVER_H)

    # 1. 平滑顺滑的电影级底部暗角渐变（非突兀黑块，使用平滑平缓的三次方曲线）
    bar_height = 560
    t = np.linspace(0, 1, bar_height)
    alpha = (t ** 1.8 * 230).astype(np.uint8)
    overlay = np.zeros((bar_height, COVER_W, 4), dtype=np.uint8)
    overlay[:, :, 3] = alpha[:, None]
    overlay_img = Image.fromarray(overlay, "RGBA")
    bg.paste(overlay_img, (0, COVER_H - bar_height), overlay_img)

    # 2. 绘制黄金钩子小标（金黄或珊瑚红）
    hook_font_path = _find_font(38, bold=True)
    hook_font = ImageFont.truetype(hook_font_path, 38) if hook_font_path else ImageFont.load_default()
    hook_text = "✦ 真实测评 · 完整记录"
    _draw_text_drop_shadow(
        bg, (70, COVER_H - bar_height + 100), hook_text, hook_font,
        fill=(255, 210, 80), shadow_color=(0, 0, 0, 180), shadow_offset=(2, 3), shadow_blur=8
    )

    # 3. 绘制主标题（大字号，自适应换行，柔和阴影）
    draw = ImageDraw.Draw(bg)
    font_path = _find_font(84, bold=True)
    font, lines, line_height, total_h = _fit_font_size(
        title, max_width=COVER_W - 140, max_lines=2,
        max_size=84, min_size=50, font_path=font_path,
        draw=draw, line_gap=16
    )

    start_y = COVER_H - 120 - total_h
    for i, line in enumerate(lines):
        _draw_text_drop_shadow(
            bg, (70, start_y + i * line_height), line, font,
            fill=(255, 255, 255), shadow_color=(0, 0, 0, 200), shadow_offset=(2, 4), shadow_blur=10
        )

    # 4. 底部微型引导条
    tip_font_path = _find_font(32, bold=False)
    tip_font = ImageFont.truetype(tip_font_path, 32) if tip_font_path else ImageFont.load_default()
    tip_draw = ImageDraw.Draw(bg)
    tip_draw.text((70, COVER_H - 60), "高清完整版 | 建议点赞收藏 ✨", font=tip_font, fill=(200, 200, 200))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("底部字幕条风封面: %s", out_path)
    return out_path


def render_topbar_style(base_path: str, title: str, out_path: str) -> str:
    """风格 3：顶部精致干货卡片风（顶部圆角高定卡片 + 纯正小红书教程排版）。

    特点：专业干货/攻略/清单感极强，上方卡片规整，下方主体清晰展示。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import numpy as np

    title = _clean_title_length(title, 20)

    if not base_path or not os.path.exists(base_path):
        bg = Image.new("RGB", (COVER_W, COVER_H), (240, 240, 245))
    else:
        bg = _load_image(base_path, COVER_W, COVER_H)

    # 1. 顶部高质感卡片（高度 320，带底部柔和圆角与阴影）
    card_h = 320
    card_bg_colors = [
        ((255, 36, 66), (255, 80, 100)),     # 小红书正红渐变
        ((24, 24, 28), (40, 40, 48)),        # 高级黑曜石渐变
        ((255, 140, 0), (255, 180, 50)),     # 活力橙黄渐变
    ]
    c1, c2 = random.choice(card_bg_colors)

    # 生成渐变卡片
    card_layer = Image.new("RGBA", (COVER_W, card_h), (0, 0, 0, 0))
    t = np.linspace(0, 1, card_h).reshape(-1, 1)
    r = (c1[0] + (c2[0] - c1[0]) * t).astype(np.uint8)
    g = (c1[1] + (c2[1] - c1[1]) * t).astype(np.uint8)
    b = (c1[2] + (c2[2] - c1[2]) * t).astype(np.uint8)
    arr = np.broadcast_to(np.stack([r, g, b], axis=-1).reshape(card_h, 1, 3), (card_h, COVER_W, 3)).copy()
    grad_img = Image.fromarray(arr).convert("RGBA")

    # 创建带圆角遮罩
    mask = Image.new("L", (COVER_W, card_h), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle([(0, -30), (COVER_W, card_h)], radius=28, fill=255)
    card_layer.paste(grad_img, (0, 0), mask)

    # 卡片柔和下边缘投影
    shadow_layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)
    s_draw.rectangle([(0, card_h - 10), (COVER_W, card_h + 15)], fill=(0, 0, 0, 80))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=12))
    bg.paste(shadow_layer, (0, 0), shadow_layer)

    # 贴合卡片
    bg.paste(card_layer, (0, 0), card_layer)

    # 2. 顶部系列 Tag（如 📌 干货合集 / ⚡ 新手必看）
    tag_font_path = _find_font(34, bold=True)
    tag_font = ImageFont.truetype(tag_font_path, 34) if tag_font_path else ImageFont.load_default()
    tags = ["📌 干货合集 · 建议收藏", "⚡ 新手避坑 · 保姆级指南", "💡 爆款教程 · 零基础必看"]
    tag_text = random.choice(tags)
    _draw_text_drop_shadow(
        bg, (60, 42), tag_text, tag_font,
        fill=(255, 235, 120), shadow_color=(0, 0, 0, 100), shadow_offset=(1, 2), shadow_blur=4
    )

    # 3. 卡片内大标题
    draw = ImageDraw.Draw(bg)
    font_path = _find_font(84, bold=True)
    font, lines, line_height, total_h = _fit_font_size(
        title, max_width=COVER_W - 120, max_lines=2,
        max_size=84, min_size=46, font_path=font_path,
        draw=draw, line_gap=12
    )

    title_start_y = 105 + (190 - total_h) // 2
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lx = (COVER_W - lw) // 2
        _draw_text_drop_shadow(
            bg, (lx, title_start_y + i * line_height), line, font,
            fill=(255, 255, 255), shadow_color=(0, 0, 0, 130), shadow_offset=(2, 3), shadow_blur=6
        )

    # 4. 底部微型卡片
    tip_font_path = _find_font(32, bold=False)
    tip_font = ImageFont.truetype(tip_font_path, 32) if tip_font_path else ImageFont.load_default()
    _draw_sticker_card(
        bg, (COVER_W // 2 - 160, COVER_H - 75), "👇 完整视频点进来看", tip_font,
        bg_color=(0, 0, 0, 150), text_color=(255, 255, 255, 230),
        pad_x=20, pad_y=8, radius=16, shadow_blur=8, shadow_alpha=60
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("顶部标题条风封面: %s", out_path)
    return out_path


def render_magazine_style(base_path: str, title: str, video_path: str = None,
                           out_path: str = None) -> str:
    """风格 4：时尚画报杂志风（三图黄金拼接 + 中央浮雕卡片 + 杂志刊头排版）。

    特点：极具画册时尚感，适合产品/好物/步骤/穿搭/对比。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    title = _clean_title_length(title, 20)

    images = []
    if base_path and os.path.exists(base_path):
        images.append(base_path)

    if video_path and os.path.exists(video_path) and len(images) < 3:
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            ratios = [0.25, 0.6, 0.85]
            for r in ratios:
                if len(images) >= 3:
                    break
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * r))
                ok, frame = cap.read()
                if ok and frame is not None:
                    tmp_path = os.path.join(os.path.dirname(out_path), f"_frame_{len(images)}.jpg")
                    frame_rgb = frame[:, :, ::-1]
                    Image.fromarray(frame_rgb).save(tmp_path, quality=90)
                    if tmp_path != base_path:
                        images.append(tmp_path)
            cap.release()
        except Exception as e:
            logger.debug("抽帧补充失败: %s", e)

    while len(images) < 3:
        if images:
            images.append(images[0])
        else:
            tmp_path = os.path.join(os.path.dirname(out_path), "_solid_bg.jpg")
            Image.new("RGB", (COVER_W, COVER_H), (220, 220, 225)).save(tmp_path)
            images.append(tmp_path)

    # 1. 干净三图画报排版（左侧 58% 大主图，右侧上下两张细节图，中间 6px 白色高级分割线）
    gutter = 6
    left_w = int(COVER_W * 0.58) - gutter // 2
    right_w = COVER_W - left_w - gutter
    right_h = (COVER_H - gutter) // 2

    bg = Image.new("RGB", (COVER_W, COVER_H), (255, 255, 255))
    left_img = _load_image(images[0], left_w, COVER_H)
    bg.paste(left_img, (0, 0))

    right_top_img = _load_image(images[1], right_w, right_h)
    bg.paste(right_top_img, (left_w + gutter, 0))

    right_bot_img = _load_image(images[2], right_w, COVER_H - right_h - gutter)
    bg.paste(right_bot_img, (left_w + gutter, right_h + gutter))

    # 2. 中央现代悬浮杂志卡片
    card_w = COVER_W - 120
    card_h = 280
    card_x = 60
    card_y = (COVER_H - card_h) // 2

    # 柔和阴影
    shadow_layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)
    s_draw.rounded_rectangle(
        [(card_x, card_y + 4), (card_x + card_w, card_y + card_h + 4)],
        radius=20, fill=(0, 0, 0, 90)
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=16))
    bg.paste(shadow_layer, (0, 0), shadow_layer)

    # 纯白卡片 + 细边框
    card_layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(card_layer)
    c_draw.rounded_rectangle(
        [(card_x, card_y), (card_x + card_w, card_y + card_h)],
        radius=20, fill=(255, 255, 255, 245),
        outline=(230, 230, 235), width=2
    )
    bg.paste(card_layer, (0, 0), card_layer)

    # 3. 杂志刊头 Tag（如 — ISSUE · 好物志 —）
    issue_font_path = _find_font(30, bold=True)
    issue_font = ImageFont.truetype(issue_font_path, 30) if issue_font_path else ImageFont.load_default()
    i_draw = ImageDraw.Draw(bg)
    issue_text = "— ISSUE · 2025 SPECIAL REPORT —"
    bbox = i_draw.textbbox((0, 0), issue_text, font=issue_font)
    iw = bbox[2] - bbox[0]
    i_draw.text(((COVER_W - iw) // 2, card_y + 28), issue_text, font=issue_font, fill=(255, 36, 66))

    # 4. 杂志主标题（优雅深色 sans-serif）
    font_path = _find_font(78, bold=True)
    font, lines, line_height, total_h = _fit_font_size(
        title, max_width=card_w - 60, max_lines=2,
        max_size=78, min_size=44, font_path=font_path,
        draw=i_draw, line_gap=12
    )

    t_start_y = card_y + 75 + (180 - total_h) // 2
    for i, line in enumerate(lines):
        bbox = i_draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lx = (COVER_W - lw) // 2
        i_draw.text((lx, t_start_y + i * line_height), line, font=font, fill=(24, 24, 28))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("杂志拼图风封面: %s", out_path)

    # 清理临时抽帧
    for i in range(1, 3):
        tmp = os.path.join(os.path.dirname(out_path), f"_frame_{i}.jpg")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

    return out_path

    # 清理临时帧
    for i in range(1, 3):
        tmp = os.path.join(os.path.dirname(out_path), f"_frame_{i}.jpg")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

    return out_path


# ---------- 主入口：生成所有风格的封面 ----------

def generate_covers(title: str, cover_url: str = None, video_path: str = None,
                     out_dir: str = ".", styles: List[str] = None,
                     use_llm: bool = False, llm_only: bool = False,
                     original_title: str = None, original_desc: str = None,
                     tags: List[str] = None, subtitle_text: str = None,
                     image_params: dict = None,
                     erase_base_text: bool = False,
                     cover_local: str = None) -> dict:
    """生成封面（保留原封面 + 原封面去重版 + 多种风格新封面）。

    参数:
        title: 封面标题文案
        cover_url: 原封面 URL（小红书笔记）
        video_path: 视频文件路径（用于抽帧）
        out_dir: 输出目录
        styles: 要生成的风格列表，默认全部 4 种
        use_llm: 是否用 LLM 生成封面标题（更贴切，结合字幕内容）
        llm_only: 强制用 LLM，失败不回退
        original_title: 原标题（LLM 生成时需要）
        original_desc: 原正文（LLM 生成时需要）
        tags: 标签列表（LLM 生成时需要）
        subtitle_text: whisper 识别的字幕文本（LLM 生成封面标题时参考）
        image_params: 原封面去重参数（镜像/裁剪/调色/噪点/去EXIF/重编码），
                      None=用 config.IMAGE_DEDUP 默认值。传 {} 可跳过去重
        erase_base_text: 是否去除底图上原有的文字（标题/水印/作者名等）。
                      True=用 PaddleOCR 检测文字 + cv2.inpaint 修复后再合成新封面；
                      False=直接用原底图（默认，保持向后兼容）。
                      原封面副本(cover_原封面.jpg)始终保留原始内容不受影响。
        cover_local: 解析阶段已下载的原封面本地路径（如抖音/小红书下载目录下的
                      cover_original.jpg）。优先复用，避免重复下载。None=从 cover_url 下载。
    返回:
        {
            "original": str,          # 原封面路径（None=无）
            "original_dedup": str,    # 原封面去重版路径（None=未生成）
            "covers": {               # 各风格封面路径
                "bigtext": str,
                "bottombar": str,
                "topbar": str,
                "magazine": str,
            },
            "title_source": str,      # 标题来源 "user"/"llm"/"original"
            "base_text_erased": bool, # 底图文字是否已去除
        }
    """
    from PIL import Image  # 确保 PIL 可用

    os.makedirs(out_dir, exist_ok=True)

    if styles is None:
        styles = ["bigtext", "bottombar", "topbar", "magazine"]

    # === LLM 生成封面标题（如启用） ===
    title_source = "user"  # 用户指定或二创 Top1
    if use_llm and original_title:
        try:
            from core import llm as llm_mod
            from core import copywrite as cw_mod
            if llm_mod.is_available():
                content_type = cw_mod.detect_content_type(original_title, original_desc or "", tags or [])
                logger.info("用 LLM 生成封面标题（结合字幕内容）...")
                llm_title = llm_mod.llm_generate_cover_title(
                    original_title, original_desc or "",
                    subtitle_text=subtitle_text, content_type=content_type)
                if llm_title:
                    title = llm_title
                    title_source = "llm"
                    logger.info("LLM 封面标题: %s", title)
                elif llm_only:
                    logger.warning("LLM 封面标题生成失败且指定了 --llm-only")
            elif llm_only:
                logger.warning("LLM 不可用且指定了 --llm-only，请检查 API Key 配置")
        except Exception as e:
            logger.warning("LLM 封面标题生成异常: %s", e)
            if llm_only:
                pass  # 不回退

    # 获取底图（优先复用解析阶段已下载的原封面）
    base_path, source = get_cover_base_image(cover_url, video_path, out_dir,
                                              cover_local=cover_local)
    logger.info("封面底图来源: %s", source)

    # 保存原封面副本（如果有）
    original_path = None
    original_dedup_path = None
    if base_path and os.path.exists(base_path):
        original_path = os.path.join(out_dir, "cover_原封面.jpg")
        if base_path != original_path:
            import shutil
            shutil.copy2(base_path, original_path)
        logger.info("原封面已保留: %s", original_path)

        # 对原封面应用图片去重流程（镜像/裁剪/调色/噪点/去EXIF/重编码）
        # 保留原封面视觉内容，但破坏哈希查重
        if image_params is None:
            import config
            image_params = config.IMAGE_DEDUP
        if image_params:  # None 或 {} 跳过
            try:
                from core import dedup_image as dedup_img_mod
                original_dedup_path = os.path.join(out_dir, "cover_原封面_去重.jpg")
                r = dedup_img_mod.dedup_image(original_path, original_dedup_path, image_params)
                if r:
                    logger.info("原封面去重版: %s", original_dedup_path)
                else:
                    original_dedup_path = None
            except Exception as e:
                logger.warning("原封面去重失败: %s", e)
                original_dedup_path = None

    covers = {}

    # === 文字去除预处理（开关：erase_base_text）===
    # 去除底图上原有的标题/水印/作者名等文字，避免与新标题叠加冲突
    # 原封面副本(cover_原封面.jpg)始终保留原始内容，仅生成新封面时用去文字版底图
    base_for_render = base_path
    base_text_erased = False
    if erase_base_text and base_path and os.path.exists(base_path):
        notext_path = os.path.join(out_dir, "cover_base_notext.jpg")
        try:
            r = _remove_text_from_image(base_path, notext_path)
            if r:
                base_for_render = r
                base_text_erased = True
        except Exception as e:
            logger.warning("封面底图文字去除失败，回退到原图: %s", e)

    for style in styles:
        out_path = os.path.join(out_dir, f"cover_{style}.jpg")
        try:
            if style == "bigtext":
                render_bigtext_style(base_for_render, title, out_path)
            elif style == "bottombar":
                render_bottombar_style(base_for_render, title, out_path)
            elif style == "topbar":
                render_topbar_style(base_for_render, title, out_path)
            elif style == "magazine":
                render_magazine_style(base_for_render, title, video_path, out_path)
            else:
                logger.warning("未知封面风格: %s", style)
                continue
            covers[style] = out_path
        except Exception as e:
            logger.error("生成 %s 风格封面失败: %s", style, e)

    return {
        "original": original_path,
        "original_dedup": original_dedup_path,
        "source": source,
        "covers": covers,
        "title_source": title_source,
        "title": title,
        "base_text_erased": base_text_erased,
    }


# ---------- AI 生图封面（调用文生图模型 + PIL 叠加文字）----------

def _download_ai_image(url: str, out_path: str, timeout: int = 60) -> Optional[str]:
    """下载 AI 生图返回的图片 URL。"""
    try:
        import requests
        r = requests.get(url, timeout=timeout, stream=True)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        logger.info("AI 生图已下载: %s", out_path)
        return out_path
    except Exception as e:
        logger.warning("下载 AI 生图失败: %s", e)
        return None


def _build_fallback_prompt(style: str, title: str, desc: str) -> str:
    """LLM 不可用时，用模板兜底生成生图 prompt（提取标题关键词作为 subject）。"""
    # 从标题提取核心词作为 subject（去掉标点和常见钩子词）
    import re
    subject = re.sub(r"[#@\d%\.\!\?,，。！？、（）\(\)]", " ", title)
    for w in ("绝了", "天花板", "相见恨晚", "被问爆", "保姆级", "神仙", "宝藏",
              "治愈", "真香", "避坑", "实测", "推荐", "分享", "教程"):
        subject = subject.replace(w, "")
    subject = " ".join(subject.split())[:40] or "product"

    from . import llm as llm_mod
    style_cfg = llm_mod.AI_COVER_STYLES.get(style, {})
    tmpl = style_cfg.get("prompt_tmpl", "{subject}, 3:4 vertical composition")
    try:
        return tmpl.format(subject=subject)
    except Exception:
        return tmpl.replace("{subject}", subject)


def _render_ai_product_hero(ai_image_path: str, title: str, hook: str,
                             out_path: str) -> str:
    """AI 封面风格 1：杂志大片感产品主角 + 爽点文案 + 信任标签（权威性心理）。"""
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
    import numpy as np
    title = _clean_title_length(title, 20)
    bg = _load_image(ai_image_path, COVER_W, COVER_H, preserve_aspect=True)
    bg = ImageEnhance.Color(bg).enhance(1.12)
    bg = ImageEnhance.Brightness(bg).enhance(1.05)
    bg = ImageEnhance.Sharpness(bg).enhance(1.08)

    # 底部渐变蒙版（深色→透明，营造杂志大片底部文字区）
    bar_h = 420
    try:
        alpha = np.linspace(0, 235, bar_h).astype(np.uint8)
        overlay = np.zeros((bar_h, COVER_W, 4), dtype=np.uint8)
        overlay[:, :, 3] = alpha[:, None]
        ov = Image.fromarray(overlay, "RGBA")
        bg.paste(ov, (0, COVER_H - bar_h), ov)
    except ImportError:
        pass
    draw = ImageDraw.Draw(bg, "RGBA")

    # 顶部金色细线装饰（杂志感）
    draw.line([(80, 60), (COVER_W - 80, 60)], fill=(255, 215, 130, 160), width=2)

    # 标题（大字，居中，杂志感）
    font_path = _find_font(96, bold=True)
    font, lines, lh, th = _fit_font_size(
        title, max_width=COVER_W - 120, max_lines=2,
        max_size=98, min_size=54, font_path=font_path, draw=draw, line_gap=14)
    sy = COVER_H - bar_h + 50
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        # 柔和投影
        for dx, dy, a in [(3, 3, 80), (5, 5, 40)]:
            draw.text((x + dx, sy + i * lh + dy), line, font=font, fill=(0, 0, 0, a))
        _draw_text_with_outline(draw, (x, sy + i * lh), line, font,
                                fill=(255, 255, 255), outline=(0, 0, 0), outline_width=4)

    # 爽点钩子（金色胶囊标签）
    hook_font_path = _find_font(50, bold=True)
    hook_font = ImageFont.truetype(hook_font_path, 50) if hook_font_path else ImageFont.load_default()
    bbox = draw.textbbox((0, 0), hook, font=hook_font)
    hw = bbox[2] - bbox[0]
    hh = bbox[3] - bbox[1]
    hx = (COVER_W - hw) // 2
    hy = sy + th + 30
    # 金色渐变胶囊背景
    pad_x, pad_y = 30, 12
    capsule = Image.new("RGBA", (hw + pad_x * 2, hh + pad_y * 2), (0, 0, 0, 0))
    cd = ImageDraw.Draw(capsule)
    cd.rounded_rectangle([(0, 0), (hw + pad_x * 2, hh + pad_y * 2)],
                         radius=(hh + pad_y * 2) // 2, fill=(255, 195, 100, 235))
    bg.paste(capsule, (hx - pad_x, hy - pad_y), capsule)
    draw.text((hx, hy), hook, font=hook_font, fill=(80, 50, 20))

    bg.save(out_path, quality=95)
    return out_path


def _render_ai_before_after(ai_image_path: str, title: str, hook: str,
                              out_path: str) -> str:
    """AI 封面风格 2：前后对比 + 真实感标签 + 顶部钩子 + 底部天数徽章（损失厌恶心理）。"""
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    import numpy as np
    bg = _load_image(ai_image_path, COVER_W, COVER_H)
    bg = ImageEnhance.Color(bg).enhance(1.12)
    bg = ImageEnhance.Contrast(bg).enhance(1.05)
    draw = ImageDraw.Draw(bg, "RGBA")

    # 中间分割线（渐变细线，更精致）
    mid_x = COVER_W // 2
    for y in range(0, COVER_H, 2):
        ratio = 1 - abs(y - COVER_H / 2) / (COVER_H / 2)
        alpha = int(60 + 180 * ratio)
        draw.rectangle([(mid_x - 2, y), (mid_x + 2, y + 1)], fill=(255, 255, 255, alpha))

    # 顶部钩子（渐变白条 + 大字）
    hook_font_path = _find_font(72, bold=True)
    hook_font, hlines, hlh, hth = _fit_font_size(
        hook, max_width=COVER_W - 80, max_lines=2,
        max_size=74, min_size=42, font_path=hook_font_path, draw=draw, line_gap=12)
    try:
        alpha = np.linspace(190, 0, 220).astype(np.uint8)
        overlay = np.zeros((220, COVER_W, 4), dtype=np.uint8)
        overlay[:, :, 3] = alpha[:, None]
        ov = Image.fromarray(overlay, "RGBA")
        bg.paste(ov, (0, 0), ov)
    except ImportError:
        pass
    draw = ImageDraw.Draw(bg, "RGBA")
    sy = 165
    for i, line in enumerate(hlines):
        bbox = draw.textbbox((0, 0), line, font=hook_font)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        for dx, dy, a in [(2, 2, 100), (4, 4, 50)]:
            draw.text((x + dx, sy + i * hlh + dy), line, font=hook_font, fill=(0, 0, 0, a))
        _draw_text_with_outline(draw, (x, sy + i * hlh), line, hook_font,
                                fill=(255, 255, 255), outline=(0, 0, 0), outline_width=4)

    # 底部渐变蒙版
    try:
        alpha = np.linspace(0, 200, 300).astype(np.uint8)
        overlay = np.zeros((300, COVER_W, 4), dtype=np.uint8)
        overlay[:, :, 3] = alpha[:, None]
        ov = Image.fromarray(overlay, "RGBA")
        bg.paste(ov, (0, COVER_H - 300), ov)
    except ImportError:
        pass
    draw = ImageDraw.Draw(bg, "RGBA")

    # 底部标题
    title_font_path = _find_font(64, bold=True)
    tfont, tlines, tlh, tth = _fit_font_size(
        title, max_width=COVER_W - 120, max_lines=2,
        max_size=66, min_size=40, font_path=title_font_path, draw=draw, line_gap=12)
    tsy = COVER_H - 160 - tth
    for i, line in enumerate(tlines):
        bbox = draw.textbbox((0, 0), line, font=tfont)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        _draw_text_with_outline(draw, (x, tsy + i * tlh), line, tfont,
                                fill=(255, 255, 255), outline=(0, 0, 0), outline_width=3)

    # 底部真实感徽章（损失厌恶：强调真实数据增强可信度）
    badge_font_path = _find_font(28, bold=True)
    badge_font = ImageFont.truetype(badge_font_path, 28) if badge_font_path else ImageFont.load_default()
    badge_text = ""
    bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    bx = (COVER_W - (bbox[2] - bbox[0])) // 2
    by = COVER_H - 55
    draw.text((bx, by), badge_text, font=badge_font, fill=(255, 230, 80, 200))

    bg.save(out_path, quality=95)
    return out_path


def _render_ai_hook_closeup(ai_image_path: str, title: str, hook: str,
                              out_path: str) -> str:
    """AI 封面风格 3：戏剧性特写 + 大字钩子 + 悬念氛围 + 紧迫感标签（好奇心+FOMO心理）。"""
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
    import numpy as np
    bg = _load_image(ai_image_path, COVER_W, COVER_H)
    bg = ImageEnhance.Color(bg).enhance(1.18)
    bg = ImageEnhance.Contrast(bg).enhance(1.12)
    bg = ImageEnhance.Sharpness(bg).enhance(1.1)

    # 暗角效果（增强戏剧感）
    try:
        bg_rgba = bg.convert("RGBA")
        vignette = np.zeros((COVER_H, COVER_W, 4), dtype=np.uint8)
        cy, cx = COVER_H / 2, COVER_W / 2
        max_d = (cx ** 2 + cy ** 2) ** 0.5
        for y in range(0, COVER_H, 4):
            for x in range(0, COVER_W, 4):
                d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                a = int(120 * (d / max_d) ** 2)
                vignette[y:y+4, x:x+4, 3] = a
        ov = Image.fromarray(vignette, "RGBA")
        bg = Image.alpha_composite(bg_rgba, ov).convert("RGB")
    except Exception:
        pass

    draw = ImageDraw.Draw(bg, "RGBA")

    # 中央渐变黑条（放钩子文案，中间深两端淡）
    bar_h = 380
    bar_y = (COVER_H - bar_h) // 2 - 40
    try:
        alpha = np.zeros(bar_h, dtype=np.uint8)
        for i in range(bar_h):
            t = abs(i - bar_h / 2) / (bar_h / 2)
            alpha[i] = int(220 * (1 - t ** 0.5))
        overlay = np.zeros((bar_h, COVER_W, 4), dtype=np.uint8)
        overlay[:, :, 3] = alpha[:, None]
        ov = Image.fromarray(overlay, "RGBA")
        bg.paste(ov, (0, bar_y), ov)
    except ImportError:
        draw.rectangle([(0, bar_y), (COVER_W, bar_y + bar_h)], fill=(0, 0, 0, 170))
    draw = ImageDraw.Draw(bg, "RGBA")

    # 超大字钩子（金黄色 + 强描边 + 发光感）
    hook_font_path = _find_font(120, bold=True)
    hfont, hlines, hlh, hth = _fit_font_size(
        hook, max_width=COVER_W - 80, max_lines=2,
        max_size=128, min_size=64, font_path=hook_font_path, draw=draw, line_gap=18)
    hsy = bar_y + (bar_h - hth) // 2
    for i, line in enumerate(hlines):
        bbox = draw.textbbox((0, 0), line, font=hfont)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        # 多层发光效果
        for dx, dy, a in [(0, 0, 60), (3, 3, 40), (6, 6, 20)]:
            draw.text((x + dx, hsy + i * hlh + dy), line, font=hfont, fill=(255, 200, 50, a))
        _draw_text_with_outline(draw, (x, hsy + i * hlh), line, hfont,
                                fill=(255, 235, 100), outline=(0, 0, 0), outline_width=7)

    # 钩子下方装饰线（悬念感）
    line_y = hsy + hth + 20
    line_w = 80
    line_x1 = (COVER_W - line_w) // 2
    draw.line([(line_x1, line_y), (line_x1 + line_w, line_y)],
              fill=(255, 235, 100, 200), width=3)

    # 底部渐变蒙版
    try:
        alpha = np.linspace(0, 200, 280).astype(np.uint8)
        overlay = np.zeros((280, COVER_W, 4), dtype=np.uint8)
        overlay[:, :, 3] = alpha[:, None]
        ov = Image.fromarray(overlay, "RGBA")
        bg.paste(ov, (0, COVER_H - 280), ov)
    except ImportError:
        pass
    draw = ImageDraw.Draw(bg, "RGBA")

    # 底部标题
    title_font_path = _find_font(56, bold=True)
    tfont, tlines, tlh, tth = _fit_font_size(
        title, max_width=COVER_W - 120, max_lines=2,
        max_size=58, min_size=36, font_path=title_font_path, draw=draw, line_gap=12)
    tsy = COVER_H - 130 - tth
    for i, line in enumerate(tlines):
        bbox = draw.textbbox((0, 0), line, font=tfont)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        for dx, dy, a in [(2, 2, 80)]:
            draw.text((x + dx, tsy + i * tlh + dy), line, font=tfont, fill=(0, 0, 0, a))
        _draw_text_with_outline(draw, (x, tsy + i * tlh), line, tfont,
                                fill=(255, 255, 255), outline=(0, 0, 0), outline_width=3)

    bg.save(out_path, quality=95)
    return out_path


def _render_ai_scene_lifestyle(ai_image_path: str, title: str, hook: str,
                                 out_path: str) -> str:
    """AI 封面风格 4：ins 风氛围感场景 + 暖光仪式感 + 品牌标签（理想自我投射心理）。"""
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
    import numpy as np
    bg = _load_image(ai_image_path, COVER_W, COVER_H)
    bg = ImageEnhance.Color(bg).enhance(1.1)
    bg = ImageEnhance.Brightness(bg).enhance(1.06)
    # 暖色调滤镜（golden hour 氛围）
    bg_rgba = bg.convert("RGBA")
    warm_overlay = Image.new("RGBA", (COVER_W, COVER_H), (255, 200, 130, 25))
    bg = Image.alpha_composite(bg_rgba, warm_overlay).convert("RGB")
    # 轻微柔焦（梦幻感）
    bg = bg.filter(ImageFilter.GaussianBlur(radius=1))

    # 底部渐变蒙版（深色→透明）
    bar_h = 420
    try:
        alpha = np.linspace(0, 200, bar_h).astype(np.uint8)
        overlay = np.zeros((bar_h, COVER_W, 4), dtype=np.uint8)
        overlay[:, :, 3] = alpha[:, None]
        ov = Image.fromarray(overlay, "RGBA")
        bg.paste(ov, (0, COVER_H - bar_h), ov)
    except ImportError:
        pass
    draw = ImageDraw.Draw(bg, "RGBA")

    # 顶部小标签（仪式感：日常碎片）
    top_tag_font_path = _find_font(30, bold=False)
    top_tag_font = ImageFont.truetype(top_tag_font_path, 30) if top_tag_font_path else ImageFont.load_default()
    top_tag = ""
    draw.text((80, 70), top_tag, font=top_tag_font, fill=(255, 255, 255, 200))

    # 标题（大字，居中）
    font_path = _find_font(80, bold=True)
    font, lines, lh, th = _fit_font_size(
        title, max_width=COVER_W - 120, max_lines=2,
        max_size=84, min_size=48, font_path=font_path, draw=draw, line_gap=14)
    sy = COVER_H - bar_h + 60
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        # 柔和投影
        for dx, dy, a in [(2, 2, 70), (4, 4, 35)]:
            draw.text((x + dx, sy + i * lh + dy), line, font=font, fill=(0, 0, 0, a))
        _draw_text_with_outline(draw, (x, sy + i * lh), line, font,
                                fill=(255, 255, 255), outline=(0, 0, 0), outline_width=3)

    # 副标题（钩子，暖金色）
    sub_font_path = _find_font(44, bold=False)
    sub_font = ImageFont.truetype(sub_font_path, 44) if sub_font_path else ImageFont.load_default()
    bbox = draw.textbbox((0, 0), hook, font=sub_font)
    sx = (COVER_W - (bbox[2] - bbox[0])) // 2
    draw.text((sx + 1, sy + th + 24 + 1), hook, font=sub_font, fill=(0, 0, 0, 50))
    draw.text((sx, sy + th + 24), hook, font=sub_font, fill=(255, 220, 130))

    # 装饰小线条（标题上方）
    deco_y = sy - 25
    deco_w = 50
    deco_x = (COVER_W - deco_w) // 2
    draw.line([(deco_x, deco_y), (deco_x + deco_w, deco_y)],
              fill=(255, 255, 255, 160), width=2)

    # 左下角圆角标签（理想自我：生活美学）
    tag = ""
    if tag:
        tag_font_path = _find_font(36, bold=True)
        tag_font = ImageFont.truetype(tag_font_path, 36) if tag_font_path else ImageFont.load_default()
        bbox = draw.textbbox((0, 0), tag, font=tag_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pad = 14
        draw.rounded_rectangle([(50, COVER_H - 80), (50 + tw + pad * 2, COVER_H - 80 + th + pad * 2)],
                               radius=(th + pad * 2) // 2, fill=(255, 130, 160, 230))
        draw.text((50 + pad, COVER_H - 80 + pad), tag, font=tag_font, fill=(255, 255, 255))

    # 右下角小心心（情感共鸣）
    heart_font_path = _find_font(36, bold=False)
    heart_font = ImageFont.truetype(heart_font_path, 36) if heart_font_path else ImageFont.load_default()
    heart_text = ""
    bbox = draw.textbbox((0, 0), heart_text, font=heart_font)
    hw = bbox[2] - bbox[0]
    draw.text((COVER_W - 50 - hw, COVER_H - 65), heart_text, font=heart_font, fill=(255, 255, 255, 200))

    bg.save(out_path, quality=95)
    return out_path


def _render_ai_list_infographic(ai_image_path: str, title: str, hook: str,
                                  out_path: str) -> str:
    """AI 封面风格 5：莫兰迪配色信息图 + 数字层次 + 收藏引导标签（认知放松+信息价值心理）。"""
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    import numpy as np
    bg = _load_image(ai_image_path, COVER_W, COVER_H)
    bg = ImageEnhance.Color(bg).enhance(1.05)

    # 顶部莫兰迪渐变标题条（鼠尾草绿→浅灰绿）
    bar_h = 340
    bar_color = (143, 175, 158)  # 鼠尾草绿
    try:
        c2 = (190, 210, 200)  # 浅灰绿
        t = np.linspace(0, 1, bar_h).reshape(-1, 1)
        r = (bar_color[0] + (c2[0] - bar_color[0]) * t).astype(np.uint8)
        g = (bar_color[1] + (c2[1] - bar_color[1]) * t).astype(np.uint8)
        b = (bar_color[2] + (c2[2] - bar_color[2]) * t).astype(np.uint8)
        arr = np.broadcast_to(np.stack([r, g, b], -1).reshape(bar_h, 1, 3),
                              (bar_h, COVER_W, 3)).copy()
        bg.paste(Image.fromarray(arr), (0, 0))
    except ImportError:
        draw.rectangle([(0, 0), (COVER_W, bar_h)], fill=bar_color)
    draw = ImageDraw.Draw(bg, "RGBA")

    # 顶部装饰小标签（信息价值：TUTORIAL/干货）
    top_tag_font_path = _find_font(28, bold=True)
    top_tag_font = ImageFont.truetype(top_tag_font_path, 28) if top_tag_font_path else ImageFont.load_default()
    top_tag = ""
    bbox = draw.textbbox((0, 0), top_tag, font=top_tag_font)
    tx = (COVER_W - (bbox[2] - bbox[0])) // 2
    draw.text((tx, 50), top_tag, font=top_tag_font, fill=(255, 255, 255, 220))

    # 标题（居中，白色 + 柔和投影）
    font_path = _find_font(84, bold=True)
    font, lines, lh, th = _fit_font_size(
        title, max_width=COVER_W - 100, max_lines=2,
        max_size=86, min_size=48, font_path=font_path, draw=draw, line_gap=14)
    sy = 100 + (bar_h - 100 - th) // 2
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        for dx, dy, a in [(2, 2, 60), (4, 4, 30)]:
            draw.text((x + dx, sy + i * lh + dy), line, font=font, fill=(0, 0, 0, a))
        _draw_text_with_outline(draw, (x, sy + i * lh), line, font,
                                fill=(255, 255, 255), outline=(120, 140, 125), outline_width=3)

    # 标题下方分隔线
    sep_y = sy + th + 15
    sep_w = 100
    sep_x = (COVER_W - sep_w) // 2
    draw.line([(sep_x, sep_y), (sep_x + sep_w, sep_y)],
              fill=(255, 255, 255, 180), width=2)

    # 底部渐变蒙版
    try:
        alpha = np.linspace(0, 180, 280).astype(np.uint8)
        overlay = np.zeros((280, COVER_W, 4), dtype=np.uint8)
        overlay[:, :, 3] = alpha[:, None]
        ov = Image.fromarray(overlay, "RGBA")
        bg.paste(ov, (0, COVER_H - 280), ov)
    except ImportError:
        pass
    draw = ImageDraw.Draw(bg, "RGBA")

    # 底部钩子（莫兰迪粉胶囊 + 收藏引导）
    hook_font_path = _find_font(48, bold=True)
    hook_font = ImageFont.truetype(hook_font_path, 48) if hook_font_path else ImageFont.load_default()
    bbox = draw.textbbox((0, 0), hook, font=hook_font)
    hw = bbox[2] - bbox[0]
    hh = bbox[3] - bbox[1]
    cx = COVER_W // 2
    pad_x, pad_y = 35, 14
    # 莫兰迪粉胶囊背景
    draw.rounded_rectangle(
        [(cx - hw // 2 - pad_x, COVER_H - 120),
         (cx + hw // 2 + pad_x, COVER_H - 120 + hh + pad_y * 2)],
        radius=(hh + pad_y * 2) // 2, fill=(200, 150, 155, 235))
    draw.text((cx - hw // 2, COVER_H - 120 + pad_y), hook, font=hook_font, fill=(255, 255, 255))

    bg.save(out_path, quality=95)
    return out_path


def _render_ai_emotion_card(ai_image_path: str, title: str, hook: str,
                              out_path: str) -> str:
    """AI 封面风格 6：梦幻情绪卡片 + 精致排版（小红书氛围感审美）。"""
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
    import numpy as np

    bg = _load_image(ai_image_path, COVER_W, COVER_H)
    # 轻微模糊（梦幻感）+ 提亮 + 增加饱和度
    bg = bg.filter(ImageFilter.GaussianBlur(radius=6))
    bg = ImageEnhance.Brightness(bg).enhance(1.08)
    bg = ImageEnhance.Color(bg).enhance(1.12)

    bg_rgba = bg.convert("RGBA")

    # === 精致渐变蒙版（上浅下深，营造文字可读性 + 氛围感）===
    try:
        t = np.linspace(0, 1, COVER_H).reshape(-1, 1, 1)
        # 上方 30% 区域：白色半透明（文字区）
        # 中间 40% 区域：透明（展示背景）
        # 下方 30% 区域：白色半透明（标题区）
        alpha_arr = np.zeros((COVER_H, 1), dtype=np.uint8)
        for y in range(COVER_H):
            ratio = y / COVER_H
            if ratio < 0.35:
                # 上方渐变：从 140 → 40
                alpha_arr[y] = int(140 * (1 - ratio / 0.35) + 40)
            elif ratio > 0.65:
                # 下方渐变：从 40 → 160
                alpha_arr[y] = int(40 + 160 * ((ratio - 0.65) / 0.35))
            else:
                alpha_arr[y] = 30
        alpha_full = np.broadcast_to(alpha_arr.reshape(COVER_H, 1, 1),
                                     (COVER_H, COVER_W, 1)).astype(np.uint8)
        overlay_arr = np.concatenate([
            np.full((COVER_H, COVER_W, 3), 255, dtype=np.uint8),
            alpha_full
        ], axis=2)
        overlay = Image.fromarray(overlay_arr, "RGBA")
        bg_rgba = Image.alpha_composite(bg_rgba, overlay)
    except Exception:
        # 回退：简单白色蒙版
        from PIL import Image as PILImage
        overlay = PILImage.new("RGBA", (COVER_W, COVER_H), (255, 255, 255, 70))
        bg_rgba = Image.alpha_composite(bg_rgba, overlay)

    bg = bg_rgba.convert("RGB")
    draw = ImageDraw.Draw(bg, "RGBA")

    # === 超大字情绪文案（垂直居中偏上，暖色调）===
    hook_font_path = _find_font(140, bold=True)
    hfont, hlines, hlh, hth = _fit_font_size(
        hook, max_width=COVER_W - 120, max_lines=3,
        max_size=150, min_size=75, font_path=hook_font_path, draw=draw, line_gap=22)
    hsy = (COVER_H - hth) // 2 - 60

    # 情绪文案：深棕色 + 白色描边（比纯灰更有质感）
    for i, line in enumerate(hlines):
        bbox = draw.textbbox((0, 0), line, font=hfont)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        # 文字阴影（柔和投影）
        for dx, dy, a in [(2, 2, 40), (4, 4, 20)]:
            draw.text((x + dx, hsy + i * hlh + dy), line, font=hfont,
                      fill=(0, 0, 0, a))
        # 主体文字 + 白色描边
        _draw_text_with_outline(draw, (x, hsy + i * hlh), line, hfont,
                                fill=(75, 55, 50), outline=(255, 255, 255),
                                outline_width=4)

    # === 装饰元素：文案上方小圆点 ===
    dot_y = hsy - 50
    if dot_y > 100:
        dot_size = 8
        spacing = 24
        total_w = dot_size * 3 + spacing * 2
        dot_start_x = (COVER_W - total_w) // 2
        for di in range(3):
            cx = dot_start_x + di * (dot_size + spacing) + dot_size // 2
            draw.ellipse([cx - dot_size // 2, dot_y - dot_size // 2,
                          cx + dot_size // 2, dot_y + dot_size // 2],
                         fill=(255, 255, 255, 200))

    # === 底部标题（小字，居中，暖灰色）===
    title_font_path = _find_font(48, bold=False)
    tfont, tlines, tlh, tth = _fit_font_size(
        title, max_width=COVER_W - 200, max_lines=2,
        max_size=52, min_size=34, font_path=title_font_path, draw=draw, line_gap=10)
    tsy = COVER_H - 140 - tth

    # 底部分隔线（细线）
    line_y = tsy - 25
    line_w = 60
    line_x1 = (COVER_W - line_w) // 2
    draw.line([(line_x1, line_y), (line_x1 + line_w, line_y)],
              fill=(255, 255, 255, 180), width=2)

    for i, line in enumerate(tlines):
        bbox = draw.textbbox((0, 0), line, font=tfont)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        # 标题阴影
        draw.text((x + 1, tsy + i * tlh + 1), line, font=tfont, fill=(0, 0, 0, 50))
        draw.text((x, tsy + i * tlh), line, font=tfont, fill=(90, 75, 70))

    # === 底部小标签（笔记来源感）===
    tag_font_path = _find_font(28, bold=False)
    try:
        tfont2 = ImageFont.truetype(tag_font_path, 28)
    except Exception:
        tfont2 = ImageFont.load_default()
    tag_text = ""
    bbox = draw.textbbox((0, 0), tag_text, font=tfont2)
    tag_x = (COVER_W - (bbox[2] - bbox[0])) // 2
    tag_y = COVER_H - 60
    draw.text((tag_x, tag_y), tag_text, font=tfont2, fill=(255, 255, 255, 160))

    bg.save(out_path, quality=95)
    return out_path


def _render_ai_pain_scenario(ai_image_path: str, title: str, hook: str,
                              out_path: str) -> str:
    """AI 封面风格 7：场景痛点 + 爽文反转（痛点放大→爽点反转的强对比排版）。

    设计逻辑（心理学：痛点放大→爽点反转）：
    - 中部：超大字爽文（白色描边，反转爽感）
    - 底部：标题文案（暖色，希望感）
    - 整体：上半部分冷色调（痛点）→ 下半部分暖色调（爽点）
    """
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
    import numpy as np

    bg = _load_image(ai_image_path, COVER_W, COVER_H)
    # 略微降低饱和度 + 降低亮度（痛点场景氛围）
    bg = ImageEnhance.Color(bg).enhance(0.82)
    bg = ImageEnhance.Brightness(bg).enhance(0.92)

    bg_rgba = bg.convert("RGBA")

    # === 双色渐变蒙版：上半冷色（痛点）→ 下半暖色（爽点反转）===
    try:
        # 上半部分：冷色调（蓝灰）半透明，强化痛点氛围
        # 下半部分：暖色调（橙红）渐变，强化爽点反转
        arr = np.zeros((COVER_H, COVER_W, 4), dtype=np.uint8)
        for y in range(COVER_H):
            ratio = y / COVER_H
            if ratio < 0.45:
                # 上半：冷色调（痛点区），alpha 从 80→40
                alpha = int(80 * (1 - ratio / 0.45) + 40)
                arr[y, :, 0] = 40   # R
                arr[y, :, 1] = 50   # G
                arr[y, :, 2] = 70   # B（冷蓝灰）
                arr[y, :, 3] = alpha
            else:
                # 下半：暖色调（爽点区），alpha 从 60→180
                alpha = int(60 + 180 * ((ratio - 0.45) / 0.55))
                arr[y, :, 0] = 25   # R（深暖色）
                arr[y, :, 1] = 15
                arr[y, :, 2] = 10
                arr[y, :, 3] = alpha
        overlay = Image.fromarray(arr, "RGBA")
        bg_rgba = Image.alpha_composite(bg_rgba, overlay)
    except Exception:
        # 回退：简单深色蒙版
        overlay = Image.new("RGBA", (COVER_W, COVER_H), (20, 20, 30, 100))
        bg_rgba = Image.alpha_composite(bg_rgba, overlay)

    bg = bg_rgba.convert("RGB")
    draw = ImageDraw.Draw(bg, "RGBA")

    # === 中部：超大字爽文（核心视觉，爽点反转）===
    # 爽文用大字 + 黄色填充 + 白色粗描边（最吸睛）
    hook_font_path = _find_font(130, bold=True)
    hfont, hlines, hlh, hth = _fit_font_size(
        hook, max_width=COVER_W - 100, max_lines=3,
        max_size=135, min_size=72, font_path=hook_font_path,
        draw=draw, line_gap=20)
    hsy = (COVER_H - hth) // 2 - 20

    for i, line in enumerate(hlines):
        bbox = draw.textbbox((0, 0), line, font=hfont)
        x = (COVER_W - (bbox[2] - bbox[0])) // 2
        y = hsy + i * hlh
        # 厚重阴影（增强冲击力）
        for dx, dy, a in [(3, 3, 100), (5, 5, 50)]:
            draw.text((x + dx, y + dy), line, font=hfont, fill=(0, 0, 0, a))
        # 主体：亮黄色 + 白色粗描边
        _draw_text_with_outline(draw, (x, y), line, hfont,
                                fill=(255, 220, 60), outline=(255, 255, 255),
                                outline_width=5)

    # === 底部：标题文案（暖色希望感，小字）===
    # 分隔线（上细下粗，视觉引导）
    sep_y = COVER_H - 230
    draw.line([(80, sep_y), (COVER_W - 80, sep_y)],
              fill=(255, 200, 100, 180), width=3)

    # 标题文案
    title_font_path = _find_font(52, bold=True)
    tfont, tlines, tlh, tth = _fit_font_size(
        title, max_width=COVER_W - 160, max_lines=2,
        max_size=56, min_size=36, font_path=title_font_path,
        draw=draw, line_gap=10)
    tsy = sep_y + 30
    for i, line in enumerate(tlines):
        # 暖色文字 + 阴影
        draw.text((81, tsy + i * tlh + 1), line, font=tfont, fill=(0, 0, 0, 80))
        draw.text((80, tsy + i * tlh), line, font=tfont, fill=(255, 240, 200))

    bg.save(out_path, quality=95)
    return out_path


# 风格名 → 渲染函数映射
_AI_RENDER_FUNCS = {
    "product_hero": _render_ai_product_hero,
    "before_after": _render_ai_before_after,
    "hook_closeup": _render_ai_hook_closeup,
    "scene_lifestyle": _render_ai_scene_lifestyle,
    "list_infographic": _render_ai_list_infographic,
    "emotion_card": _render_ai_emotion_card,
    "pain_scenario": _render_ai_pain_scenario,
}


def generate_ai_covers(title: str, original_title: str = None,
                        original_desc: str = None, tags: List[str] = None,
                        content_type: str = None, out_dir: str = ".",
                        styles: List[str] = None,
                        image_size: str = "1024x1024",
                        image_model: str = None,
                        image_mode: str = "t2i",
                        reference_image_url: str = None) -> dict:
    """用 AI 生图模型生成多种风格的小红书封面。

    参数:
        title: 封面主标题
        original_title/desc/tags: 笔记原内容（用于生成生图 prompt 和钩子文案）
        content_type: 内容类型
        out_dir: 输出目录
        styles: AI 封面风格列表（默认全部 6 种）
        image_size: 生图尺寸
        image_model: 指定文生图模型（如 GrsAI 的 sora-image / gpt-image-1.5 / nano-banana 等，
                     None=用服务商默认）
        image_mode: 图片生成模式
            "t2i" = 文生图（默认，从零生成）
            "i2i" = 图生图（基于原封面重新生成，需提供 reference_image_url）
            "t2i+i2i" = 混合模式（以图生图为主，原产品保真；prompt 辅助场景美化）
        reference_image_url: 图生图参考图 URL（公网可访问），image_mode="i2i" 或 "t2i+i2i" 时生效
    返回:
        {
            "covers": {style: path},
            "failed": [style, ...],
            "image_source": "ai" / "fallback",
        }
    """
    from . import llm as llm_mod

    os.makedirs(out_dir, exist_ok=True)

    if styles is None:
        styles = list(llm_mod.AI_COVER_STYLES.keys())

    # 检查生图能力
    if not llm_mod.is_image_generation_available():
        logger.error("AI 生图封面: 当前 LLM 服务商不支持文生图/图生图，跳过")
        logger.error("支持文生图的服务商: 豆包(doubao-seedream) / OpenAI(dall-e-3) / GrsAI(sora-image 等)")
        logger.error("请用 --llm-provider doubao / openai / grsai 配合 --ai-cover 使用")
        return {"covers": {}, "failed": list(styles), "image_source": "unavailable"}

    # 图生图模式校验
    if image_mode == "i2i":
        if not reference_image_url:
            logger.warning("图生图模式未提供参考图 URL，自动回退到文生图模式")
            image_mode = "t2i"
        else:
            logger.info("图生图模式: 参考图=%s...", reference_image_url[:80])

    original_title = original_title or title
    original_desc = original_desc or ""
    tags = tags or []

    covers = {}
    failed = []

    # ---- 单个风格的完整生成流程（供线程池调用）----
    def _gen_one(style: str) -> Tuple[str, Optional[str], Optional[str]]:
        """返回 (style, out_path, hook)；失败时 out_path=None。"""
        style_cfg = llm_mod.AI_COVER_STYLES.get(style)
        if not style_cfg:
            logger.warning("未知 AI 封面风格: %s", style)
            return (style, None, None)

        logger.info("=" * 50)
        logger.info("生成 AI 封面 [%s]: %s (模式: %s)",
                    style, style_cfg["name_cn"],
                    {"i2i": "图生图", "t2i+i2i": "混合"}.get(image_mode, "文生图"))

        # 1. 生成生图 prompt（LLM 优先，模板兜底）
        prompt = None
        if llm_mod.is_available():
            prompt = llm_mod.llm_generate_image_prompt(
                style, original_title, original_desc, tags, content_type,
                image_mode=image_mode)
        if not prompt:
            prompt = _build_fallback_prompt(style, original_title, original_desc)
            logger.info("[%s] 使用模板兜底 prompt: %s...", style, prompt[:60])

        # 2. 调用文生图 / 图生图 / 混合模式
        if image_mode == "t2i+i2i" and reference_image_url:
            logger.info("[%s] 混合模式: 图生图为主（原产品保真）+ prompt 辅助场景美化", style)
            i2i_prompt = (
                f"Based on the reference product image, keep the EXACT same product "
                f"(same shape, color, brand, packaging) as the main subject. "
                f"Enhance the scene with: {prompt}. "
                f"Only upgrade background, lighting and composition, "
                f"do NOT change the product itself. "
                f"3:4 vertical, high quality, natural seamless composition.")
            image_url = llm_mod.llm_generate_image_edit(
                i2i_prompt, reference_image_url, size=image_size, model=image_model)
            if image_url:
                logger.info("[%s] 混合模式成功: 原产品保真 + 场景美化", style)
            else:
                logger.warning("[%s] 混合模式图生图失败，尝试用基础 prompt 重试", style)
                image_url = llm_mod.llm_generate_image_edit(
                    prompt, reference_image_url, size=image_size, model=image_model)
        elif image_mode == "i2i" and reference_image_url:
            image_url = llm_mod.llm_generate_image_edit(
                prompt, reference_image_url, size=image_size, model=image_model)
        else:
            image_url = llm_mod.llm_generate_image(prompt, size=image_size, model=image_model)
        if not image_url:
            logger.warning("AI 封面 [%s] 生图失败，跳过", style)
            return (style, None, None)

        # 3. 下载图片
        ai_img_path = os.path.join(out_dir, f"_ai_raw_{style}.jpg")
        if not _download_ai_image(image_url, ai_img_path):
            return (style, None, None)

        # 4. 生成钩子文案（LLM 优先，模板兜底）
        hook = None
        if llm_mod.is_available():
            hook = llm_mod.llm_generate_cover_hook(
                style, original_title, original_desc, tags, content_type)
        if not hook:
            hook = random.choice(style_cfg.get("hook_examples", ["真的绝了"]))

        # 5. PIL 后处理（叠加文字）
        out_path = os.path.join(out_dir, f"cover_ai_{style}.jpg")
        render_func = _AI_RENDER_FUNCS.get(style)
        try:
            render_func(ai_img_path, title, hook, out_path)
            logger.info("AI 封面 [%s] 完成: %s (钩子: %s)", style, out_path, hook)
        except Exception as e:
            logger.error("AI 封面 [%s] PIL 后处理失败: %s", style, e)
            out_path = None

        # 清理临时生图
        try:
            os.remove(ai_img_path)
        except Exception:
            pass

        return (style, out_path, hook)

    # ---- 并发生成所有风格 ----
    # 生图是网络 IO 密集型（每次 10-30s），6 个并发可将总时长从 ~90s 降到 ~30s
    max_workers = min(8, max(2, len(styles)))
    logger.info("并发生成 %d 个 AI 封面 (max_workers=%d)...", len(styles), max_workers)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ai-cover") as executor:
        futures = {executor.submit(_gen_one, style): style for style in styles}
        for fut in as_completed(futures):
            style = futures[fut]
            try:
                _, out_path, hook = fut.result()
                if out_path:
                    covers[style] = out_path
                else:
                    failed.append(style)
            except Exception as e:
                logger.error("AI 封面 [%s] 线程异常: %s", style, e)
                failed.append(style)

    return {
        "covers": covers,
        "failed": failed,
        "image_source": "ai",
    }
