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

def _find_font(size: int = 40, bold: bool = False) -> str:
    """查找系统中可用的高品质中文字体（优先厚重粗黑体/微软雅黑 Bold，打造冲击力）。"""
    candidates = []
    if bold:
        candidates = [
            r"C:\Windows\Fonts\Dengb.ttf",      # 等线 Bold（现代时尚感）
            r"C:\Windows\Fonts\msyhbd.ttc",    # 微软雅黑 Bold
            r"C:\Windows\Fonts\simhei.ttf",     # 黑体（厚重有力）
            r"C:\Windows\Fonts\NotoSansSC-VF.ttf", # 思源黑体
            r"C:\Windows\Fonts\HYZhongHeiTi-197.ttf",
            r"C:\Windows\Fonts\msyh.ttc",
        ]
    else:
        candidates = [
            r"C:\Windows\Fonts\Deng.ttf",       # 等线 Regular
            r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
            r"C:\Windows\Fonts\simhei.ttf",     # 黑体
            r"C:\Windows\Fonts\Dengb.ttf",
            r"C:\Windows\Fonts\msyhbd.ttc",
        ]
    # Linux/Mac 兜底
    candidates += [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
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
    title = title.strip().strip("'\" “”‘’")
    if len(title) > 21:
        title = title[:20].rstrip("｜·丨—→,，.!?！？ ")
    return title


def _parse_title_for_cover(title: str) -> dict:
    """智能解析小红书爆款标题层级与情绪/信任钩子（基于爆款拆解）"""
    import re
    t = title.strip().strip("'\" “”‘’")

    tag = ""
    m = re.match(r"^[【\[（\(]([^】\]）\)]+)[】\]）\)]\s*(.*)$", t)
    if m:
        tag = m.group(1).strip()
        t = m.group(2).strip()

    intent_keywords = [
        ("收纳", "📦 空间整理", "衣柜收纳日常"),
        ("衣柜", "👗 衣柜整理", "拯救衣柜空间"),
        ("避坑", "💡 真实体验", "踩坑心得分享"),
        ("改造", "🏡 居家改造", "治愈生活角落"),
        ("好物", "✨ 心动好物", "私藏居家分享"),
        ("学生", "🎒 宿舍日常", "宿舍好物分享"),
        ("桌面", "🖥️ 桌面美学", "极简桌面角落"),
        ("清洁", "🫧 解压清洁", "省心清洁记录"),
        ("幸福感", "🌿 治愈生活", "提升生活幸福感"),
        ("测评", "🔍 深度体验", "真实上手细节"),
    ]

    content_sub = ""
    for kw, default_tag, default_sub in intent_keywords:
        if kw in title or kw in t:
            if not tag:
                tag = default_tag
            content_sub = default_sub
            break

    if not tag:
        tag = "✨ 生活灵感"

    clean_t = re.sub(r"^[！!，,：: ｜·\-]+", "", t)
    parts = [p.strip() for p in re.split(r"[ ℑ·,，!！?？:：\r\n\t]+", clean_t) if p.strip()]

    if len(parts) >= 2:
        l1 = parts[0]
        l2 = parts[1]
        sub = " ".join(parts[2:]) if len(parts) > 2 else content_sub
    elif len(parts) == 1:
        text_p = parts[0]
        if len(text_p) > 7:
            mid = len(text_p) // 2
            l1 = text_p[:mid]
            l2 = text_p[mid:]
        else:
            l1 = text_p
            l2 = ""
        sub = content_sub
    else:
        l1 = "生活灵感"
        l2 = "日常分享"
        sub = content_sub

    if len(l1) > 8:
        l1 = l1[:8]
    if len(l2) > 9:
        l2 = l2[:9]
    if len(sub) > 16:
        sub = sub[:16]

    return {
        "tag": tag,
        "line1": l1,
        "line2": l2,
        "sub": sub,
        "trust_badge": sub
    }


def _fit_crop_image(img: "Image", target_w: int, target_h: int) -> "Image":
    from PIL import Image, ImageEnhance
    w, h = img.size
    scale = max(target_w / w, target_h / h)
    nw, nh = int(w * scale), int(h * scale)
    resized = img.resize((nw, nh), Image.LANCZOS)
    left = max(0, (nw - target_w) // 2)
    top = max(0, (nh - target_h) // 2)
    cropped = resized.crop((left, top, left + target_w, top + target_h))
    cropped = ImageEnhance.Color(cropped).enhance(1.08)
    cropped = ImageEnhance.Brightness(cropped).enhance(1.02)
    return cropped


def _collect_4_photos(base_path: str = None, video_path: str = None) -> List["Image.Image"]:
    from PIL import Image, ImageDraw, ImageFont
    images = []
    if video_path and os.path.exists(video_path):
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if total > 10:
                    ratios = [0.12, 0.38, 0.62, 0.86]
                    for r in ratios:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * r))
                        ok, frame = cap.read()
                        if ok and frame is not None:
                            images.append(Image.fromarray(frame[:, :, ::-1]))
            cap.release()
        except Exception as e:
            logger.debug("视频抽 4 帧失败: %s", e)

    if base_path and os.path.exists(base_path):
        try:
            base_img = Image.open(base_path).convert("RGB")
            bw, bh = base_img.size
            if not images:
                images.append(base_img)
            crops = [
                (0, 0, int(bw * 0.7), int(bh * 0.7)),
                (int(bw * 0.3), 0, bw, int(bh * 0.7)),
                (0, int(bh * 0.3), int(bw * 0.7), bh),
                (int(bw * 0.3), int(bh * 0.3), bw, bh),
            ]
            idx = 0
            while len(images) < 4:
                box = crops[idx % 4]
                images.append(base_img.crop(box))
                idx += 1
        except Exception as e:
            logger.debug("底图衍生失败: %s", e)

    colors = [(245, 240, 235), (240, 235, 230), (235, 230, 225), (230, 225, 220)]
    labels = ["产品实拍", "使用场景", "细节特写", "氛围图"]
    while len(images) < 4:
        c = colors[len(images) % 4]
        img = Image.new("RGB", (536, 716), c)
        d = ImageDraw.Draw(img)
        font = _find_font(44, bold=True)
        f_obj = ImageFont.truetype(font, 44) if font else ImageFont.load_default()
        d.text((120, 320), labels[len(images)], fill=(120, 110, 100), font=f_obj)
        images.append(img)

    return images[:4]


def _create_4grid_canvas(photos: list) -> "Image.Image":
    from PIL import Image
    canvas = Image.new("RGB", (COVER_W, COVER_H), (255, 255, 255))
    gutter = 8
    cell_w = (COVER_W - gutter) // 2  # 536
    cell_h = (COVER_H - gutter) // 2  # 716

    positions = [
        (0, 0),
        (cell_w + gutter, 0),
        (0, cell_h + gutter),
        (cell_w + gutter, cell_h + gutter)
    ]

    for i in range(4):
        p = _fit_crop_image(photos[i], cell_w, cell_h)
        canvas.paste(p, positions[i])

    return canvas


def _draw_large_card(bg_img: "Image.Image", xy: tuple, text: str, font,
                     bg_color: tuple, text_color: tuple, pad_x: int = 36, pad_y: int = 20,
                     radius: int = 22, shadow_blur: int = 16, shadow_alpha: int = 110):
    """绘制占据封面 1/3 以上面积的超大粗体字高亮标题卡片。"""
    from PIL import Image, ImageDraw, ImageFilter
    d_temp = ImageDraw.Draw(bg_img)
    bbox = d_temp.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    card_w = tw + pad_x * 2
    card_h = th + pad_y * 2
    cx, cy = xy

    if shadow_blur > 0:
        shadow = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
        s_draw = ImageDraw.Draw(shadow)
        s_draw.rounded_rectangle(
            [(cx, cy + 6), (cx + card_w, cy + card_h + 6)],
            radius=radius,
            fill=(0, 0, 0, shadow_alpha)
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        bg_img.paste(shadow, (0, 0), shadow)

    card = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(card)
    c_draw.rounded_rectangle(
        [(cx, cy), (cx + card_w, cy + card_h)],
        radius=radius,
        fill=bg_color
    )
    bg_img.paste(card, (0, 0), card)

    text_x = cx + pad_x - bbox[0]
    text_y = cy + pad_y - bbox[1]
    d = ImageDraw.Draw(bg_img)
    d.text((text_x, text_y), text, font=font, fill=text_color)
    return card_w, card_h


def _load_image(path: str, target_w: int = COVER_W, target_h: int = COVER_H, preserve_aspect: bool = True) -> "Image.Image":
    """加载图片并高质量裁切/缩放到目标尺寸（3:4 竖屏 1080x1440）。"""
    from PIL import Image, ImageDraw
    if not path or not os.path.exists(path):
        base = Image.new("RGB", (target_w, target_h), (245, 240, 235))
        d = ImageDraw.Draw(base)
        d.rectangle([(40, 40), (target_w - 40, target_h - 40)], outline=(220, 215, 210), width=4)
        return base

    img = Image.open(path).convert("RGB")
    return _fit_crop_image(img, target_w, target_h)


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
    """自适应字号：根据标题长度动态缩放字号，确保完整显示不截断。"""
    from PIL import ImageFont
    for size in range(max_size, min_size - 1, -4):
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        lines = _wrap_text(title, font, max_width, draw)
        if len(lines) <= max_lines:
            line_height = size + line_gap
            total_h = len(lines) * line_height
            return font, lines, line_height, total_h
    font = ImageFont.truetype(font_path, min_size) if font_path else ImageFont.load_default()
    lines = _wrap_text(title, font, max_width, draw)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…" if len(lines[-1]) > 1 else lines[-1]
    line_height = min_size + line_gap
    total_h = len(lines) * line_height
    return font, lines, line_height, total_h


def _draw_text_with_outline(draw, xy, text, font, fill, outline, outline_width=3):
    """绘制带描边的文字。"""
    x, y = xy
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


# ---------- 4 种拆解爆款封面模板 ----------

def render_bigtext_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 1：干货好物四宫格合集风（图 1/2/6 拆解）。

    特点：四宫格实拍图 + 占据 >1/3 面积的超大荧光黄高亮标题框 + 顶部/利益点标签。
    """
    from PIL import ImageFont

    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _create_4grid_canvas(photos)

    # 1. 顶部爆款胶囊前置标签（如：⚡ 没废话 / 💡 纯干货）
    font_path = _find_font(38, bold=True)
    tag_font = ImageFont.truetype(font_path, 38) if font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, 60), info["tag"], tag_font,
        bg_color=(255, 36, 66, 245), text_color=(255, 255, 255),
        pad_x=24, pad_y=12, radius=24, shadow_blur=10, shadow_alpha=70
    )

    # 2. 超大粗体字标题，占据封面 > 1/3 面积（字号 96px~104px）
    font_main_path = _find_font(100, bold=True)
    font_main = ImageFont.truetype(font_main_path, 100) if font_main_path else ImageFont.load_default()

    start_y = 240

    # Line 1 (荧光黄底块 + 纯黑字)
    w1, h1 = _draw_large_card(
        bg, (50, start_y), info["line1"], font_main,
        bg_color=(255, 230, 0, 255), text_color=(18, 18, 20),
        pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
    )

    # Line 2 (若有：黑曜石底块 + 纯白字)
    next_y = start_y + h1 + 18
    if info["line2"]:
        w2, h2 = _draw_large_card(
            bg, (50, next_y), info["line2"], font_main,
            bg_color=(18, 18, 24, 245), text_color=(255, 255, 255),
            pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
        )
        next_y += h2 + 18

    # 3. 信任/卖点标签（如：低至个位数 | 没废话好物 ✨）
    sub_font_path = _find_font(38, bold=True)
    sub_font = ImageFont.truetype(sub_font_path, 38) if sub_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, next_y), f"✦ {info['sub']} ✦", sub_font,
        bg_color=(0, 0, 0, 160), text_color=(255, 220, 80),
        pad_x=24, pad_y=12, radius=18, shadow_blur=8, shadow_alpha=60
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板1(干货合集): %s", out_path)
    return out_path


def render_bottombar_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 2：情绪共鸣反向种草劝退风（图 3 拆解）。

    特点：四宫格真实场景实拍 + 高亮底色强观点大字 + 避坑/劝退情绪钩子。
    """
    from PIL import ImageFont

    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _create_4grid_canvas(photos)

    # 1. 顶部警示/劝退前缀胶囊
    top_font_path = _find_font(36, bold=True)
    top_font = ImageFont.truetype(top_font_path, 36) if top_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, 70), f"💡 {info['tag']}", top_font,
        bg_color=(220, 38, 38, 245), text_color=(255, 255, 255),
        pad_x=24, pad_y=12, radius=22, shadow_blur=8, shadow_alpha=60
    )

    # 2. 强观点输出超大字（占据 1/3 以上面积）
    font_main_path = _find_font(94, bold=True)
    font_main = ImageFont.truetype(font_main_path, 94) if font_main_path else ImageFont.load_default()
    start_y = 280

    # 强观点 Line 1 (鲜艳高亮黄底块 + 纯黑字)
    w1, h1 = _draw_large_card(
        bg, (50, start_y), info["line1"], font_main,
        bg_color=(255, 230, 0, 255), text_color=(18, 18, 20),
        pad_x=42, pad_y=24, radius=24, shadow_blur=20, shadow_alpha=130
    )

    # 强观点 Line 2 (黑底红字/黄字)
    next_y = start_y + h1 + 18
    if info["line2"]:
        w2, h2 = _draw_large_card(
            bg, (50, next_y), info["line2"], font_main,
            bg_color=(20, 20, 24, 250), text_color=(255, 240, 0),
            pad_x=42, pad_y=24, radius=24, shadow_blur=20, shadow_alpha=130
        )
        next_y += h2 + 18

    # 3. 底部共鸣金句
    sub_font_path = _find_font(36, bold=True)
    sub_font = ImageFont.truetype(sub_font_path, 36) if sub_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, next_y), f"“ {info['sub']} ”" if info['sub'] else "“ 适合自己的才是最好的 ”", sub_font,
        bg_color=(0, 0, 0, 170), text_color=(255, 255, 255),
        pad_x=24, pad_y=12, radius=18, shadow_blur=8, shadow_alpha=60
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板2(反向种草劝退): %s", out_path)
    return out_path


def render_topbar_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 3：垂直人群精准 / 信任背书风（图 4/5 拆解）。

    特点：人群精准前置标 + 无广无废物信任徽章 + 黑底白字超大标题卡片。
    """
    from PIL import ImageFont

    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _create_4grid_canvas(photos)

    # 1. 顶部左侧人群精准胶囊
    top_font_path = _find_font(36, bold=True)
    top_font = ImageFont.truetype(top_font_path, 36) if top_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, 60), f"🎒 {info['tag']}", top_font,
        bg_color=(20, 20, 24, 240), text_color=(255, 230, 0),
        pad_x=22, pad_y=12, radius=20, shadow_blur=8, shadow_alpha=60
    )

    # 2. 顶部右侧【无广！无废物！】信任勋章
    trust_font_path = _find_font(34, bold=True)
    trust_font = ImageFont.truetype(trust_font_path, 34) if trust_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (COVER_W - 350, 60), f"✦ {info['tag']}", trust_font,
        bg_color=(255, 36, 66, 245), text_color=(255, 255, 255),
        pad_x=20, pad_y=12, radius=20, shadow_blur=8, shadow_alpha=60
    )

    # 3. 中下部超大标题卡片（占据 1/3 面积）
    font_main_path = _find_font(94, bold=True)
    font_main = ImageFont.truetype(font_main_path, 94) if font_main_path else ImageFont.load_default()
    start_y = 280

    w1, h1 = _draw_large_card(
        bg, (50, start_y), info["line1"], font_main,
        bg_color=(18, 18, 22, 245), text_color=(255, 255, 255),
        pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
    )

    next_y = start_y + h1 + 18
    if info["line2"]:
        w2, h2 = _draw_large_card(
            bg, (50, next_y), info["line2"], font_main,
            bg_color=(255, 230, 0, 255), text_color=(18, 18, 20),
            pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
        )
        next_y += h2 + 18

    # 4. 底部引流卖点
    sub_font_path = _find_font(36, bold=False)
    sub_font = ImageFont.truetype(sub_font_path, 36) if sub_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, next_y), f"{info['sub']}" if info['sub'] else "日常好物真实记录", sub_font,
        bg_color=(0, 0, 0, 150), text_color=(240, 240, 245),
        pad_x=22, pad_y=10, radius=18, shadow_blur=6, shadow_alpha=50
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板3(垂直人群信任): %s", out_path)
    return out_path


def render_magazine_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 4：大画幅单图/主次平铺海报风（兜底单图/少图大字爆款）。

    特点：全画幅超清实拍 + 占据 >1/3 面积的双色高亮贴纸大字。
    """
    from PIL import Image, ImageFont

    info = _parse_title_for_cover(title)

    # 加载 1 张全画幅超清底图
    if base_path and os.path.exists(base_path):
        bg = _fit_crop_image(Image.open(base_path).convert("RGB"), COVER_W, COVER_H)
    else:
        photos = _collect_4_photos(base_path, video_path)
        bg = _create_4grid_canvas(photos)

    # 1. 顶部爆款 Tag
    tag_font_path = _find_font(38, bold=True)
    tag_font = ImageFont.truetype(tag_font_path, 38) if tag_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, 70), f"🔥 {info['tag']}", tag_font,
        bg_color=(255, 36, 66, 245), text_color=(255, 255, 255),
        pad_x=24, pad_y=12, radius=22, shadow_blur=10, shadow_alpha=70
    )

    # 2. 占据 >1/3 面积的超大两行贴纸卡片大字
    font_main_path = _find_font(98, bold=True)
    font_main = ImageFont.truetype(font_main_path, 98) if font_main_path else ImageFont.load_default()
    start_y = 260

    w1, h1 = _draw_large_card(
        bg, (50, start_y), info["line1"], font_main,
        bg_color=(255, 255, 255, 250), text_color=(255, 36, 66),
        pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
    )

    next_y = start_y + h1 + 18
    if info["line2"]:
        w2, h2 = _draw_large_card(
            bg, (50, next_y), info["line2"], font_main,
            bg_color=(255, 36, 66, 245), text_color=(255, 255, 255),
            pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
        )
        next_y += h2 + 18

    # 3. 底部提示
    sub_font_path = _find_font(36, bold=False)
    sub_font = ImageFont.truetype(sub_font_path, 36) if sub_font_path else ImageFont.load_default()
    _draw_large_card(
        bg, (50, next_y), f"✦ {info['sub']} ✦", sub_font,
        bg_color=(0, 0, 0, 160), text_color=(255, 220, 80),
        pad_x=22, pad_y=10, radius=18, shadow_blur=8, shadow_alpha=60
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板4(单图大字爆款): %s", out_path)
    return out_path




def render_vlog_card_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 5：VLOG生活记录微透卡片风（极具治感/日常小确幸/人像/日常）。

    特点：全画幅超清原图 + 居中极简微透奶油风圆角卡片 + 双色柔和标题。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    info = _parse_title_for_cover(title)
    if base_path and os.path.exists(base_path):
        bg = _fit_crop_image(Image.open(base_path).convert("RGB"), COVER_W, COVER_H)
    else:
        photos = _collect_4_photos(base_path, video_path)
        bg = _create_4grid_canvas(photos)

    # 居中悬浮极简奶油微透卡片
    card_w = COVER_W - 140
    card_h = 320
    card_x = 70
    card_y = (COVER_H - card_h) // 2

    # 1. 柔和下沉高斯阴影
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    s_draw.rounded_rectangle(
        [(card_x, card_y + 8), (card_x + card_w, card_y + card_h + 8)],
        radius=26, fill=(0, 0, 0, 75)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=16))
    bg.paste(shadow, (0, 0), shadow)

    # 2. 奶油微透卡片 (带 2px 细边框)
    card = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(card)
    c_draw.rounded_rectangle(
        [(card_x, card_y), (card_x + card_w, card_y + card_h)],
        radius=26, fill=(255, 255, 255, 242),
        outline=(240, 240, 245), width=2
    )
    bg.paste(card, (0, 0), card)

    # 3. 顶部微型 Tag
    font_path = _find_font(30, bold=True)
    tag_font = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
    d = ImageDraw.Draw(bg)
    tag_text = f"✨ MY VLOG DIARY · {info['tag']}"
    bbox = d.textbbox((0, 0), tag_text, font=tag_font)
    tw = bbox[2] - bbox[0]
    d.text(((COVER_W - tw) // 2, card_y + 32), tag_text, font=tag_font, fill=(255, 36, 66))

    # 4. 主标题
    font_main_path = _find_font(78, bold=True)
    font_main = ImageFont.truetype(font_main_path, 78) if font_main_path else ImageFont.load_default()
    full_title = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_title, font=font_main)
    mw = bbox[2] - bbox[0]
    d.text(((COVER_W - mw) // 2, card_y + 95), full_title, font=font_main, fill=(24, 24, 28))

    # 5. 底部一句话
    sub_font_path = _find_font(32, bold=False)
    sub_font = ImageFont.truetype(sub_font_path, 32) if sub_font_path else ImageFont.load_default()
    sub_text = f"“ {info['sub']} ”"
    bbox = d.textbbox((0, 0), sub_text, font=sub_font)
    sw = bbox[2] - bbox[0]
    d.text(((COVER_W - sw) // 2, card_y + 215), sub_text, font=sub_font, fill=(120, 120, 130))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板5(VLOG微透卡片): %s", out_path)
    return out_path


def render_list_banner_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 6：清单干货三图横幅风（干货清单/推荐榜单/对比榜）。

    特点：3 张实拍图片垂直三分切片 + 顶部黑金双色超大标题横幅。
    """
    from PIL import Image, ImageDraw, ImageFont

    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    
    # 3 张图片垂直三分切片
    gutter = 6
    cell_h = (COVER_H - gutter * 2) // 3  # ~474
    bg = Image.new("RGB", (COVER_W, COVER_H), (255, 255, 255))

    for i in range(3):
        p = _fit_crop_image(photos[i], COVER_W, cell_h)
        bg.paste(p, (0, i * (cell_h + gutter)))

    # 顶部黑金双色超大标题横幅 (Y: 100 ~ 440)
    banner_w = COVER_W - 100
    banner_h = 320
    bx = 50
    by = 100

    _draw_large_card(
        bg, (bx, by), f"📌 {info['tag']}", 
        ImageFont.truetype(_find_font(36, bold=True), 36),
        bg_color=(255, 230, 0, 255), text_color=(18, 18, 20),
        pad_x=24, pad_y=12, radius=18, shadow_blur=12, shadow_alpha=90
    )

    font_main_path = _find_font(88, bold=True)
    font_main = ImageFont.truetype(font_main_path, 88) if font_main_path else ImageFont.load_default()

    w1, h1 = _draw_large_card(
        bg, (bx, by + 75), info["line1"], font_main,
        bg_color=(18, 18, 24, 250), text_color=(255, 255, 255),
        pad_x=36, pad_y=20, radius=22, shadow_blur=16, shadow_alpha=110
    )

    if info["line2"]:
        _draw_large_card(
            bg, (bx, by + 75 + h1 + 14), info["line2"], font_main,
            bg_color=(255, 36, 66, 245), text_color=(255, 255, 255),
            pad_x=36, pad_y=20, radius=22, shadow_blur=16, shadow_alpha=110
        )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板6(清单三图横幅): %s", out_path)
    return out_path




def render_split_comparison_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 7：50:50 左右双图 PK 强对比风（前后对比/避坑vs推荐/真假对比）。

    特点：左右 50:50 分屏切片 + 中央悬浮 VS 勋章 + 上下超大高亮卡片标题。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    bg = Image.new("RGB", (COVER_W, COVER_H), (255, 255, 255))
    half_w = (COVER_W - 6) // 2

    # 左右两图拼贴
    img_left = _fit_crop_image(photos[0], half_w, COVER_H)
    img_right = _fit_crop_image(photos[1], half_w, COVER_H)

    bg.paste(img_left, (0, 0))
    bg.paste(img_right, (half_w + 6, 0))

    # 1. 顶端对比标志/警告胶囊
    top_font = ImageFont.truetype(_find_font(36, bold=True), 36)
    _draw_large_card(
        bg, (50, 60), f"⚠️ {info['tag']} | 真实对比", top_font,
        bg_color=(220, 38, 38, 245), text_color=(255, 255, 255),
        pad_x=24, pad_y=12, radius=20, shadow_blur=10, shadow_alpha=70
    )

    # 2. 中间大字强观点标题（占据 >1/3 面积）
    font_main = ImageFont.truetype(_find_font(94, bold=True), 94)
    start_y = 250

    w1, h1 = _draw_large_card(
        bg, (50, start_y), info["line1"], font_main,
        bg_color=(255, 230, 0, 255), text_color=(18, 18, 20),
        pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
    )

    next_y = start_y + h1 + 18
    if info["line2"]:
        _draw_large_card(
            bg, (50, next_y), info["line2"], font_main,
            bg_color=(18, 18, 24, 250), text_color=(255, 255, 255),
            pad_x=40, pad_y=24, radius=24, shadow_blur=18, shadow_alpha=120
        )

    # 3. 画面中央红黑悬浮 VS 勋章
    vs_size = 130
    vs_x = (COVER_W - vs_size) // 2
    vs_y = (COVER_H - vs_size) // 2 + 180

    vs_img = Image.new("RGBA", (vs_size, vs_size), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vs_img)
    vd.ellipse([(0, 0), (vs_size, vs_size)], fill=(255, 36, 66, 250), outline=(255, 255, 255), width=4)
    vs_font = ImageFont.truetype(_find_font(52, bold=True), 52)
    bbox = vd.textbbox((0, 0), "VS", font=vs_font)
    vw = bbox[2] - bbox[0]
    vh = bbox[3] - bbox[1]
    vd.text(((vs_size - vw) // 2, (vs_size - vh) // 2 - bbox[1]), "VS", font=vs_font, fill=(255, 255, 255))

    bg.paste(vs_img, (vs_x, vs_y), vs_img)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板7(50:50对比): %s", out_path)
    return out_path


def render_frosted_glass_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 8：高级感 iOS 悬浮毛玻璃卡片风（高级感/美妆/数码/好物/简约）。

    特点：全画幅超清图 + 中央真实高斯模糊 iOS 毛玻璃微透浮雕卡片 + 优雅精致排版。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

    info = _parse_title_for_cover(title)
    if base_path and os.path.exists(base_path):
        bg = _fit_crop_image(Image.open(base_path).convert("RGB"), COVER_W, COVER_H)
    else:
        photos = _collect_4_photos(base_path, video_path)
        bg = _create_4grid_canvas(photos)

    # 卡片尺寸与位置
    card_w = COVER_W - 140  # 940
    card_h = 360
    card_x = 70
    card_y = (COVER_H - card_h) // 2 - 20

    # 1. 截取卡片对应区域底图并做高斯模糊（模拟真实毛玻璃效果）
    card_crop = bg.crop((card_x, card_y, card_x + card_w, card_y + card_h))
    card_blur = card_crop.filter(ImageFilter.GaussianBlur(radius=16))
    card_blur = ImageEnhance.Brightness(card_blur).enhance(1.18)

    # 2. 混合半透明白色蒙版（RGBA）
    white_overlay = Image.new("RGBA", card_blur.size, (255, 255, 255, 180))
    frosted = Image.alpha_composite(card_blur.convert("RGBA"), white_overlay)

    # 3. 绘制圆角与 2px 极细半透明白色边框
    mask = Image.new("L", (card_w, card_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([(0, 0), (card_w, card_h)], radius=28, fill=255)

    f_draw = ImageDraw.Draw(frosted)
    f_draw.rounded_rectangle([(1, 1), (card_w - 2, card_h - 2)], radius=28, outline=(255, 255, 255, 220), width=2)

    # 4. 阴影贴图
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    s_draw.rounded_rectangle([(card_x, card_y + 8), (card_x + card_w, card_y + card_h + 8)], radius=28, fill=(0, 0, 0, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))
    bg.paste(shadow, (0, 0), shadow)

    # 贴回毛玻璃卡片
    bg.paste(frosted.convert("RGB"), (card_x, card_y), mask)

    # 5. 精致排版文字
    d = ImageDraw.Draw(bg)
    tag_font = ImageFont.truetype(_find_font(32, bold=True), 32)
    tag_text = f"✦ D A I L Y · {info['tag']} ✦"
    bbox = d.textbbox((0, 0), tag_text, font=tag_font)
    d.text(((COVER_W - (bbox[2] - bbox[0])) // 2, card_y + 36), tag_text, font=tag_font, fill=(210, 40, 60))

    font_main = ImageFont.truetype(_find_font(76, bold=True), 76)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    d.text(((COVER_W - (bbox[2] - bbox[0])) // 2, card_y + 105), full_t, font=font_main, fill=(20, 20, 24))

    sub_font = ImageFont.truetype(_find_font(34, bold=False), 34)
    sub_t = f"— {info['sub']} —"
    bbox = d.textbbox((0, 0), sub_t, font=sub_font)
    d.text(((COVER_W - (bbox[2] - bbox[0])) // 2, card_y + 240), sub_t, font=sub_font, fill=(100, 100, 110))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板8(毛玻璃): %s", out_path)
    return out_path


def render_grid_mosaic_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 9：3×3 极致九宫格大容量合集风（九宫格平铺/海量合集/强迫症必看）。

    特点：3×3 九宫格平铺 + 5号核心中位格替换为黑金/红黄高亮卡片。
    """
    from PIL import Image, ImageDraw, ImageFont

    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    # 衍生 9 张图片切片
    nine_photos = []
    while len(nine_photos) < 9:
        nine_photos.extend(photos)
    nine_photos = nine_photos[:9]

    gutter = 6
    cell_w = (COVER_W - gutter * 2) // 3  # 354
    cell_h = (COVER_H - gutter * 2) // 3  # 474

    bg = Image.new("RGB", (COVER_W, COVER_H), (255, 255, 255))

    for idx in range(9):
        row = idx // 3
        col = idx % 3
        cx = col * (cell_w + gutter)
        cy = row * (cell_h + gutter)

        if idx == 4:
            # 5号中央主格：替换为纯黑高亮底块
            card_img = Image.new("RGB", (cell_w, cell_h), (18, 18, 22))
            cd = ImageDraw.Draw(card_img)
            # 边框装饰
            cd.rectangle([(8, 8), (cell_w - 8, cell_h - 8)], outline=(255, 230, 0), width=3)

            font_tag = ImageFont.truetype(_find_font(32, bold=True), 32)
            font_title = ImageFont.truetype(_find_font(60, bold=True), 60)

            cd.text((24, 40), f"📌 {info['tag']}", font=font_tag, fill=(255, 230, 0))

            # 主文字居中
            bbox1 = cd.textbbox((0, 0), info["line1"], font=font_title)
            cd.text(((cell_w - (bbox1[2] - bbox1[0])) // 2, 130), info["line1"], font=font_title, fill=(255, 255, 255))

            if info["line2"]:
                bbox2 = cd.textbbox((0, 0), info["line2"], font=font_title)
                cd.text(((cell_w - (bbox2[2] - bbox2[0])) // 2, 220), info["line2"], font=font_title, fill=(255, 230, 0))

            font_sub = ImageFont.truetype(_find_font(26, bold=False), 26)
            cd.text((24, cell_h - 60), f"✦ {info['tag']} ✦", font=font_sub, fill=(200, 200, 210))

            bg.paste(card_img, (cx, cy))
        else:
            p = _fit_crop_image(nine_photos[idx], cell_w, cell_h)
            bg.paste(p, (cx, cy))

    # 顶部分线标
    top_font = ImageFont.truetype(_find_font(36, bold=True), 36)
    _draw_large_card(
        bg, (40, 40), f"✦ {info['tag']} ✦", top_font,
        bg_color=(255, 36, 66, 245), text_color=(255, 255, 255),
        pad_x=22, pad_y=10, radius=18, shadow_blur=10, shadow_alpha=70
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板9(九宫格): %s", out_path)
    return out_path


def render_polaroid_stack_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 10：复古拍立得胶片手写风（拍立得相框/随手拍/博主自用/复古）。

    特点：燕麦质感背景 + 拍立得相框 + 底部留白手写体 + 顶部标签。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    # 燕麦色暖质感背景
    bg = Image.new("RGB", (COVER_W, COVER_H), (244, 240, 234))

    # 拍立得白框尺寸
    frame_w = 900
    frame_h = 1060
    fx = (COVER_W - frame_w) // 2
    fy = 220

    # 1. 拍立得卡片高斯阴影
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([(fx, fy + 8), (fx + frame_w, fy + frame_h + 8)], radius=18, fill=(0, 0, 0, 60))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=16))
    bg.paste(shadow, (0, 0), shadow)

    # 2. 拍立得白框主体
    polaroid = Image.new("RGB", (frame_w, frame_h), (255, 255, 255))
    pd = ImageDraw.Draw(polaroid)

    # 拍立得内嵌照片 (上方)
    photo_w = 830
    photo_h = 750
    photo_x = (frame_w - photo_w) // 2
    photo_y = 35

    photo_img = _fit_crop_image(photos[0], photo_w, photo_h)
    polaroid.paste(photo_img, (photo_x, photo_y))

    # 拍立得下方留白文字区域
    font_main = ImageFont.truetype(_find_font(68, bold=True), 68)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = pd.textbbox((0, 0), full_t, font=font_main)
    pd.text(((frame_w - (bbox[2] - bbox[0])) // 2, photo_y + photo_h + 40), full_t, font=font_main, fill=(28, 28, 32))

    font_sub = ImageFont.truetype(_find_font(32, bold=False), 32)
    sub_t = f"DATE: 2025.08 · {info['sub']}"
    pd.text((photo_x, frame_h - 70), sub_t, font=font_sub, fill=(130, 130, 140))

    bg.paste(polaroid, (fx, fy))

    # 3. 顶部爆款标签
    top_font = ImageFont.truetype(_find_font(36, bold=True), 36)
    _draw_large_card(
        bg, (50, 70), f"🔥 自用实拍 | {info['tag']}", top_font,
        bg_color=(255, 36, 66, 245), text_color=(255, 255, 255),
        pad_x=24, pad_y=12, radius=20, shadow_blur=10, shadow_alpha=70
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板10(拍立得): %s", out_path)
    return out_path


def render_minimal_banner_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 11：电影级上下遮幅黑金大片风（极简/大气/电影感/零遮挡）。

    特点：上下 18% 电影黑色遮幅 + 烫金细线 + 纯白大字标题。
    """
    from PIL import Image, ImageDraw, ImageFont

    info = _parse_title_for_cover(title)
    if base_path and os.path.exists(base_path):
        bg = _fit_crop_image(Image.open(base_path).convert("RGB"), COVER_W, COVER_H)
    else:
        photos = _collect_4_photos(base_path, video_path)
        bg = _create_4grid_canvas(photos)

    d = ImageDraw.Draw(bg, "RGBA")
    bar_h = 240

    # 1. 顶部遮幅
    d.rectangle([(0, 0), (COVER_W, bar_h)], fill=(15, 15, 18, 245))
    # 2. 底部遮幅
    d.rectangle([(0, COVER_H - bar_h), (COVER_W, COVER_H)], fill=(15, 15, 18, 245))

    # 3. 顶部烫金装饰线与标签
    d.line([(80, 50), (COVER_W - 80, 50)], fill=(255, 215, 130, 180), width=2)
    top_font = ImageFont.truetype(_find_font(32, bold=True), 32)
    top_t = f"— {info['tag']} · 真实记录 —"
    bbox = d.textbbox((0, 0), top_t, font=top_font)
    d.text(((COVER_W - (bbox[2] - bbox[0])) // 2, 80), top_t, font=top_font, fill=(255, 215, 130))

    # 4. 底部超大醒目标题
    font_main = ImageFont.truetype(_find_font(84, bold=True), 84)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    d.text(((COVER_W - (bbox[2] - bbox[0])) // 2, COVER_H - bar_h + 35), full_t, font=font_main, fill=(255, 255, 255))

    sub_font = ImageFont.truetype(_find_font(32, bold=False), 32)
    sub_t = f"✦ {info['sub']} ✦"
    bbox = d.textbbox((0, 0), sub_t, font=sub_font)
    d.text(((COVER_W - (bbox[2] - bbox[0])) // 2, COVER_H - 75), sub_t, font=sub_font, fill=(200, 200, 210))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板11(电影遮幅): %s", out_path)
    return out_path


def render_bold_highlight_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 12：荧光高亮马克笔划线强调风（重点强调/干货/划线笔刷/学生党）。

    特点：全画幅超清实拍 + 荧光笔刷高亮划线底层 + 左侧黑圈角标。
    """
    from PIL import Image, ImageDraw, ImageFont

    info = _parse_title_for_cover(title)
    if base_path and os.path.exists(base_path):
        bg = _fit_crop_image(Image.open(base_path).convert("RGB"), COVER_W, COVER_H)
    else:
        photos = _collect_4_photos(base_path, video_path)
        bg = _create_4grid_canvas(photos)

    # 1. 顶部 Tag
    tag_font = ImageFont.truetype(_find_font(36, bold=True), 36)
    _draw_large_card(
        bg, (50, 70), f"✦ {info['tag']}", tag_font,
        bg_color=(20, 20, 24, 240), text_color=(255, 230, 0),
        pad_x=24, pad_y=12, radius=20, shadow_blur=10, shadow_alpha=70
    )

    # 2. 荧光笔高亮划线大字 (Y=260 起)
    font_main = ImageFont.truetype(_find_font(96, bold=True), 96)
    d_temp = ImageDraw.Draw(bg)

    start_y = 260
    # Line 1 (荧光笔粉色 stroke)
    bbox1 = d_temp.textbbox((0, 0), info["line1"], font=font_main)
    w1 = bbox1[2] - bbox1[0]
    h1 = bbox1[3] - bbox1[1]

    # 绘制高亮划线块
    hl1 = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    hd1 = ImageDraw.Draw(hl1)
    hd1.rounded_rectangle([(45, start_y), (45 + w1 + 60, start_y + h1 + 36)], radius=16, fill=(255, 51, 102, 240))
    bg.paste(hl1, (0, 0), hl1)

    d = ImageDraw.Draw(bg)
    d.text((75 - bbox1[0], start_y + 18 - bbox1[1]), info["line1"], font=font_main, fill=(255, 255, 255))

    next_y = start_y + h1 + 54
    if info["line2"]:
        bbox2 = d_temp.textbbox((0, 0), info["line2"], font=font_main)
        w2 = bbox2[2] - bbox2[0]
        h2 = bbox2[3] - bbox2[1]

        hl2 = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        hd2 = ImageDraw.Draw(hl2)
        hd2.rounded_rectangle([(45, next_y), (45 + w2 + 60, next_y + h2 + 36)], radius=16, fill=(255, 230, 0, 245))
        bg.paste(hl2, (0, 0), hl2)

        d = ImageDraw.Draw(bg)
        d.text((75 - bbox2[0], next_y + 18 - bbox2[1]), info["line2"], font=font_main, fill=(18, 18, 20))
        next_y += h2 + 54

    # 3. 底部金句
    sub_font = ImageFont.truetype(_find_font(36, bold=True), 36)
    _draw_large_card(
        bg, (50, next_y), f"✦ {info['sub']} ✦", sub_font,
        bg_color=(0, 0, 0, 170), text_color=(255, 255, 255),
        pad_x=22, pad_y=10, radius=18, shadow_blur=8, shadow_alpha=60
    )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板12(马克笔高亮): %s", out_path)
    return out_path




def render_sticker_painpoint_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 13：居家收纳爆款痛点贴纸风（高质感黄色爆炸贴纸 + 渐变暗角 + 3D立体描边发光大字 + 信任红黄角标）。

    对标小红书数万点赞爆款收纳笔记封面排版拆解：
      - 场景主体：100% 居中展示真实手持实拍，背景微调对比度+底部添加电影级柔和暗角（确保底部文字极度清晰）
      - 顶部痛点：亮黄色不规则放射爆炸对话框贴纸（小角度倾斜 -2° 营造动感，双重投影 + 粗黑外边框），醒目制造痛点
      - 底部卖点：双行超大白色粗黑体，采用 3D 悬浮投影 + 8px 饱满黑边 + 关键动作/数字高亮（亮黄/西瓜红）
      - 信任角标：双色信赖标签（如“🔥 告别拥挤 · 一扣省一半” + “✔ 真实测评 · 不占空间”）
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
    import math

    info = _parse_title_for_cover(title)
    if base_path and os.path.exists(base_path):
        bg = _fit_crop_image(Image.open(base_path).convert("RGB"), COVER_W, COVER_H)
    else:
        photos = _collect_4_photos(base_path, video_path)
        bg = _create_4grid_canvas(photos)

    # 1. 底图画质精修：微调明暗对比，增强衣服和收纳神器的质感
    bg = ImageEnhance.Contrast(bg).enhance(1.10)
    bg = ImageEnhance.Sharpness(bg).enhance(1.15)

    # 2. 底部电影级微渐变暗角（Vignette Overlay），防止背景衣服太杂导致看不清文字
    vignette = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vignette)
    dark_h = 420
    for y in range(dark_h):
        alpha = int(170 * (y / dark_h) ** 1.6)
        vdraw.line([(0, COVER_H - dark_h + y), (COVER_W, COVER_H - dark_h + y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), vignette).convert("RGB")

    # ===== 3. 顶部：高质感倾斜黄色爆炸对话框贴纸 =====
    top_w = COVER_W - 140  # 940
    top_h = 220
    top_x = 70
    top_y = 60

    sticker_layer = Image.new("RGBA", (top_w + 80, top_h + 80), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(sticker_layer)

    scx = (top_w + 80) / 2
    scy = (top_h + 80) / 2
    srx = top_w / 2
    sry = top_h / 2

    # 生成 18 角动态爆炸顶点
    n_spikes = 18
    pts = []
    for i in range(n_spikes * 2):
        angle = i * (math.pi / n_spikes)
        # 增加轻微交错随机感，避免过于死板的对称
        wave = 1.0 if (i % 2 == 0) else (0.84 + (i % 4) * 0.02)
        px = scx + srx * wave * math.cos(angle)
        py = scy + sry * wave * math.sin(angle)
        pts.append((px, py))

    # 绘制深层柔和弥散阴影
    shadow_layer = Image.new("RGBA", sticker_layer.size, (0, 0, 0, 0))
    sh_draw = ImageDraw.Draw(shadow_layer)
    sh_draw.polygon([(x + 6, y + 14) for x, y in pts], fill=(0, 0, 0, 110))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=14))
    sticker_layer = Image.alpha_composite(shadow_layer, sticker_layer)
    sdraw = ImageDraw.Draw(sticker_layer)

    # 绘制高饱和度亮黄爆炸背景 + 5px 粗黑复古边框
    sdraw.polygon(pts, fill=(255, 230, 0, 255), outline=(20, 20, 24, 255), width=6)

    # 在贴纸内部写痛点提问文案（大字黑体）
    font_top = ImageFont.truetype(_find_font(66, bold=True), 66)
    # 智能提炼痛点文案：如“衣柜塞爆了？别慌！”
    line1 = info['line1']
    if not any(p in line1 for p in ("？", "!", "！", "?")):
        top_text = f"{line1}？别慌！"
    else:
        top_text = line1

    bbox_top = sdraw.textbbox((0, 0), top_text, font=font_top)
    tw = bbox_top[2] - bbox_top[0]
    th = bbox_top[3] - bbox_top[1]

    # 若字数过多自适应缩放
    if tw > top_w - 90:
        font_top = ImageFont.truetype(_find_font(54, bold=True), 54)
        bbox_top = sdraw.textbbox((0, 0), top_text, font=font_top)
        tw = bbox_top[2] - bbox_top[0]
        th = bbox_top[3] - bbox_top[1]

    sdraw.text((scx - tw // 2, scy - th // 2 - bbox_top[1] - 4), top_text, font=font_top, fill=(18, 18, 20))

    # 轻微旋转 -2.5 度营造小红书手工贴纸呼吸感
    sticker_rotated = sticker_layer.rotate(-2.5, resample=Image.BICUBIC, expand=True)
    bg.paste(sticker_rotated, (top_x - 30, top_y - 30), sticker_rotated)

    # ===== 4. 底部：超大 3D 立体饱满白黑粗体字 =====
    d = ImageDraw.Draw(bg)

    # 辅助函数：绘制高质感圆形描边 + 3D 立体黑影
    def _draw_deluxe_text(draw, x, y, text, font, fill_color, stroke_color=(12, 12, 16), stroke_w=7, shadow_offset=(0, 7)):
        # 1. 下沉立体暗影
        sx, sy = shadow_offset
        for dx in range(-stroke_w, stroke_w + 1):
            for dy in range(-stroke_w, stroke_w + 1):
                if dx * dx + dy * dy <= stroke_w * stroke_w:
                    draw.text((x + sx + dx, y + sy + dy), text, font=font, fill=(0, 0, 0, 180))
        # 2. 饱满高对比黑描边
        for dx in range(-stroke_w, stroke_w + 1):
            for dy in range(-stroke_w, stroke_w + 1):
                if dx * dx + dy * dy <= stroke_w * stroke_w:
                    draw.text((x + dx, y + dy), text, font=font, fill=stroke_color)
        # 3. 前景字
        draw.text((x, y), text, font=font, fill=fill_color)

    font_main = ImageFont.truetype(_find_font(90, bold=True), 90)
    main_text = info['line2'] if info['line2'] else "告别拥挤 一扣省一半！"

    bbox_m = d.textbbox((0, 0), main_text, font=font_main)
    mw = bbox_m[2] - bbox_m[0]
    mh = bbox_m[3] - bbox_m[1]

    # 自适应文字宽度，防止顶格或出界
    if mw > COVER_W - 100:
        font_main = ImageFont.truetype(_find_font(74, bold=True), 74)
        bbox_m = d.textbbox((0, 0), main_text, font=font_main)
        mw = bbox_m[2] - bbox_m[0]
        mh = bbox_m[3] - bbox_m[1]

    main_y = COVER_H - 260
    main_x = (COVER_W - mw) // 2

    _draw_deluxe_text(d, main_x, main_y, main_text, font_main,
                      fill_color=(255, 255, 255), stroke_color=(12, 12, 16), stroke_w=8)

    # ===== 5. 底部小字辅助（如果有用户原本文案就显示，没有就干净留白，绝不加多余营销词） =====
    if info.get('sub') and len(info['sub'].strip()) > 0:
        sub_font = ImageFont.truetype(_find_font(34, bold=True), 34)
        _draw_large_card(
            bg, ((COVER_W - 460) // 2, COVER_H - 110), info['sub'], sub_font,
            bg_color=(255, 230, 0, 240), text_color=(18, 18, 20),
            pad_x=24, pad_y=10, radius=18, shadow_blur=10, shadow_alpha=70
        )

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板13(精致升级-收纳痛点贴纸): %s", out_path)
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
        styles = ["bigtext", "bottombar", "topbar", "magazine", "vlog_card", "list_banner", "split_comparison", "frosted_glass", "grid_mosaic", "polaroid_stack", "minimal_banner", "bold_highlight", "sticker_painpoint"]

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
                render_bigtext_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "bottombar":
                render_bottombar_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "topbar":
                render_topbar_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "magazine":
                render_magazine_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "vlog_card":
                render_vlog_card_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "list_banner":
                render_list_banner_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "split_comparison":
                render_split_comparison_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "frosted_glass":
                render_frosted_glass_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "grid_mosaic":
                render_grid_mosaic_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "polaroid_stack":
                render_polaroid_stack_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "minimal_banner":
                render_minimal_banner_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "bold_highlight":
                render_bold_highlight_style(base_for_render, title, out_path, video_path=video_path)
            elif style == "sticker_painpoint":
                render_sticker_painpoint_style(base_for_render, title, out_path, video_path=video_path)
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
