# -*- coding: utf-8 -*-
"""小红书爆款封面扩展模版库（全量扩展至 35+ 款高审美纯排版设计）。

理念：
  - 100% 纯代码图像几何与排版算法，零 AI 生成，画面真实干净
  - 剔除所有电商油腻营销字眼，回归小红书真实种草生活调性
  - 智能字体大小层级变换：核心关键词、数字、动作词自动放大聚焦
  - 包含丰富的设计手法：手撕纸、胶带、拍立得、日系竖排、时尚杂志、黑白堆叠、双色荧光笔刷等
"""
import os
import math
import random
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

COVER_W = 1080
COVER_H = 1440

def _get_helper_imports():
    from core.cover_gen import (
        _find_font, _fit_crop_image, _collect_4_photos, _create_4grid_canvas,
        _draw_large_card, _parse_title_for_cover, logger
    )
    return _find_font, _fit_crop_image, _collect_4_photos, _create_4grid_canvas, _draw_large_card, _parse_title_for_cover, logger


def _extract_hero_keywords(text: str) -> Tuple[str, str, str]:
    """智能拆解标题视觉层级：(前导修饰语, 核心特大关键词, 收益结果词)。
    例如："衣柜用这个收纳压缩板不仅节省空间还整齐" -> ("衣柜整理", "收纳压缩板", "省出一半空间")
    """
    import re
    clean = text.strip()
    # 如果有【】或者「」包含的内容，优先视为核心词
    bracket_match = re.search(r"[【「\[]([^】」\]]+)[】」\]]", clean)
    if bracket_match:
        hero = bracket_match.group(1).strip()
        remain = clean.replace(bracket_match.group(0), "").strip()
        parts = [p for p in re.split(r"[，, 。!！?？\s]+", remain) if p]
        prefix = parts[0] if len(parts) > 0 else "居家好物"
        suffix = parts[1] if len(parts) > 1 else "真的太好用了"
        return prefix[:8], hero[:8], suffix[:12]

    # 按标点与空格分词
    words = [w for w in re.split(r"[，, 。!！?？\s]+", clean) if w]
    if len(words) >= 3:
        return words[0][:7], words[1][:8], words[2][:12]
    elif len(words) == 2:
        return "", words[0][:8], words[1][:12]
    elif len(words) == 1:
        w = words[0]
        if len(w) > 6:
            mid = len(w) // 2
            return "", w[:mid], w[mid:]
        return "", w, "心动分享"
    return "日常", "精选好物", "实用分享"


# ==============================================================================
# 模版 14 ~ 35 全新渲染器
# ==============================================================================

def render_keyword_hero_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 14：核心关键词特大号聚焦风（极强视觉反差：主关键词 120px 巨幅放大）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 底部微暗角保证文字清晰
    vignette = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vignette)
    for y in range(400):
        alpha = int(180 * (y / 400) ** 1.5)
        vdraw.line([(0, COVER_H - 400 + y), (COVER_W, COVER_H - 400 + y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), vignette).convert("RGB")

    prefix, hero, suffix = _extract_hero_keywords(title)
    if not hero:
        hero = info["line1"]
        suffix = info["line2"]

    # 1. 顶部极简标签
    top_font = ImageFont.truetype(_find_font(34, bold=True), 34)
    _draw_large_card(bg, (50, 60), info["tag"], top_font, bg_color=(255, 255, 255, 240), text_color=(20, 20, 25), pad_x=22, pad_y=10, radius=16, shadow_blur=8, shadow_alpha=50)

    # 2. 底部文字层级：小前缀 + 巨无霸核心词 + 结果词
    d = ImageDraw.Draw(bg)
    cur_y = COVER_H - 330
    if prefix:
        pre_font = ImageFont.truetype(_find_font(42, bold=True), 42)
        d.text((60, cur_y), prefix, font=pre_font, fill=(255, 235, 100))
        cur_y += 56

    # 核心词巨型放大 (110px 鲜亮高光)
    hero_font = ImageFont.truetype(_find_font(110, bold=True), 110)
    for dx in (-4, -3, 3, 4):
        for dy in (-4, -3, 3, 4):
            d.text((60 + dx, cur_y + dy), hero, font=hero_font, fill=(15, 15, 20))
    d.text((60, cur_y), hero, font=hero_font, fill=(255, 255, 255))
    cur_y += 125

    if suffix:
        suf_font = ImageFont.truetype(_find_font(52, bold=True), 52)
        d.text((60, cur_y), suffix, font=suf_font, fill=(255, 230, 0))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板14(关键词巨无霸): %s", out_path)
    return out_path


def render_torn_paper_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 15：真实感手撕纸边缘风（不规则纤维撕边 + 质朴手账感）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 在下方贴一张手撕白纸
    paper_h = 320
    paper_y = COVER_H - paper_h - 90
    paper_layer = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(paper_layer)

    # 撕纸锯齿随机边缘
    random.seed(42)
    pts = [(0, paper_y + paper_h), (0, paper_y)]
    step = 25
    for x in range(0, COVER_W + step, step):
        jitter = random.randint(-6, 6)
        pts.append((min(COVER_W, x), paper_y + jitter))
    pts.append((COVER_W, paper_y + paper_h))

    # 投影
    shadow_pts = [(x, y + 8) for x, y in pts]
    pdraw.polygon(shadow_pts, fill=(0, 0, 0, 70))
    paper_layer = paper_layer.filter(ImageFilter.GaussianBlur(radius=10))
    pdraw = ImageDraw.Draw(paper_layer)
    # 纯白纸面
    pdraw.polygon(pts, fill=(250, 248, 245, 255))
    bg = Image.alpha_composite(bg.convert("RGBA"), paper_layer).convert("RGB")

    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(78, bold=True), 78)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    d.text(((COVER_W - tw) // 2, paper_y + 70), full_t, font=font_main, fill=(35, 35, 40))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(36, bold=False), 36)
        sbox = d.textbbox((0, 0), info["sub"], font=sub_font)
        d.text(((COVER_W - (sbox[2] - sbox[0])) // 2, paper_y + 180), info["sub"], font=sub_font, fill=(110, 110, 120))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板15(手撕纸边缘): %s", out_path)
    return out_path


def render_post_it_note_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 16：便签胶带风（鹅黄色便利贴 + 半透明和纸胶带固定）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 绘制一张便利贴
    note_w, note_h = 860, 360
    note_img = Image.new("RGBA", (note_w + 40, note_h + 40), (0, 0, 0, 0))
    ndraw = ImageDraw.Draw(note_img)

    # 便利贴投影与黄色底
    ndraw.rectangle([(10, 15), (note_w + 10, note_h + 15)], fill=(0, 0, 0, 50))
    note_img = note_img.filter(ImageFilter.GaussianBlur(radius=8))
    ndraw = ImageDraw.Draw(note_img)
    ndraw.rectangle([(10, 10), (note_w + 10, note_h + 10)], fill=(255, 246, 175, 255))

    # 顶部和纸胶带 (半透明带斜撕角)
    tape_w, tape_h = 240, 55
    tape_x = (note_w - tape_w) // 2 + 10
    ndraw.rectangle([(tape_x, 0), (tape_x + tape_w, tape_h)], fill=(225, 220, 200, 200))

    # 便签文字
    font_main = ImageFont.truetype(_find_font(68, bold=True), 68)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = ndraw.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    ndraw.text(((note_w - tw) // 2 + 10, 110), full_t, font=font_main, fill=(40, 35, 30))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(34, bold=False), 34)
        sbox = ndraw.textbbox((0, 0), info["sub"], font=sub_font)
        ndraw.text(((note_w - (sbox[2] - sbox[0])) // 2 + 10, 220), info["sub"], font=sub_font, fill=(120, 110, 95))

    # 轻微旋转 -2 度贴上
    note_rot = note_img.rotate(-2, resample=Image.BICUBIC, expand=True)
    bg.paste(note_rot, ((COVER_W - note_rot.size[0]) // 2, COVER_H - 460), note_rot)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板16(便签胶带): %s", out_path)
    return out_path


def render_magazine_vogue_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 17：时尚画报排版风（细网格线 + 首字沉降 + 顶级留白呼吸感）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 细线画框
    d = ImageDraw.Draw(bg)
    margin = 45
    d.rectangle([(margin, margin), (COVER_W - margin, COVER_H - margin)], outline=(255, 255, 255, 180), width=2)

    # 顶部细线杂志表头
    d.line([(margin + 30, margin + 80), (COVER_W - margin - 30, margin + 80)], fill=(255, 255, 255, 150), width=1)
    tag_font = ImageFont.truetype(_find_font(32, bold=True), 32)
    d.text((margin + 35, margin + 35), info["tag"], font=tag_font, fill=(255, 255, 255))

    # 底部卡片放置大字标题
    card_y = COVER_H - 320
    card_w = COVER_W - (margin * 2) - 60
    card = Image.new("RGBA", (card_w, 200), (15, 15, 20, 210))
    cdraw = ImageDraw.Draw(card)
    font_main = ImageFont.truetype(_find_font(72, bold=True), 72)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = cdraw.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    cdraw.text(((card_w - tw) // 2, 45), full_t, font=font_main, fill=(255, 255, 255))
    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(30, bold=False), 30)
        sbox = cdraw.textbbox((0, 0), info["sub"], font=sub_font)
        cdraw.text(((card_w - (sbox[2] - sbox[0])) // 2, 135), info["sub"], font=sub_font, fill=(200, 200, 210))

    bg.paste(card, (margin + 30, card_y), card)
    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板17(时尚画报): %s", out_path)
    return out_path


def render_film_strip_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 18：35mm 胶片底片风（电影打孔齿孔黑边 + 真实生活纪实颗粒）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    bg = Image.new("RGB", (COVER_W, COVER_H), (16, 16, 18))
    # 中央嵌入实拍胶片画面
    inner_w, inner_h = 920, 1180
    inner_x = (COVER_W - inner_w) // 2
    inner_y = 100
    p = _fit_crop_image(photos[0], inner_w, inner_h)
    bg.paste(p, (inner_x, inner_y))

    # 两侧胶卷齿孔
    d = ImageDraw.Draw(bg)
    hole_w, hole_h = 36, 50
    step = 75
    for y in range(40, COVER_H - 40, step):
        # 左侧齿孔
        d.rounded_rectangle([(18, y), (18 + hole_w, y + hole_h)], radius=6, fill=(255, 255, 255))
        # 右侧齿孔
        d.rounded_rectangle([(COVER_W - 18 - hole_w, y), (COVER_W - 18, y + hole_h)], radius=6, fill=(255, 255, 255))

    # 底部大字标题悬浮横幅
    font_main = ImageFont.truetype(_find_font(76, bold=True), 76)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    _draw_large_card(bg, (inner_x + 30, inner_y + inner_h - 130), full_t, font_main,
                     bg_color=(255, 230, 0, 255), text_color=(18, 18, 20), pad_x=34, pad_y=16, radius=20, shadow_blur=12, shadow_alpha=90)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板18(胶片底片): %s", out_path)
    return out_path


def render_two_thirds_split_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 19：上2/3大图 + 下1/3暖白纯色卡（日系生活志极致留白，主体零遮挡）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    bg = Image.new("RGB", (COVER_W, COVER_H), (250, 248, 244))
    # 上部 68% 纯实拍大图
    top_h = int(COVER_H * 0.68)
    p = _fit_crop_image(photos[0], COVER_W, top_h)
    bg.paste(p, (0, 0))

    # 下部 32% 排版
    d = ImageDraw.Draw(bg)
    tag_font = ImageFont.truetype(_find_font(34, bold=True), 34)
    d.text((60, top_h + 40), f"✦ {info['tag']}", font=tag_font, fill=(180, 50, 60))

    font_main = ImageFont.truetype(_find_font(80, bold=True), 80)
    d.text((60, top_h + 100), info["line1"], font=font_main, fill=(24, 24, 28))

    if info["line2"]:
        d.text((60, top_h + 200), info["line2"], font=font_main, fill=(180, 50, 60))
    elif info["sub"]:
        sub_font = ImageFont.truetype(_find_font(42, bold=False), 42)
        d.text((60, top_h + 210), info["sub"], font=sub_font, fill=(110, 110, 120))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板19(2/3分屏): %s", out_path)
    return out_path


def render_char_boxes_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 20：单字方块印章风（核心词一字一框，秩序感与吸睛度兼备）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 顶部标签
    top_font = ImageFont.truetype(_find_font(34, bold=True), 34)
    _draw_large_card(bg, (50, 60), info["tag"], top_font, bg_color=(20, 20, 24, 230), text_color=(255, 230, 0), pad_x=22, pad_y=10, radius=16)

    # 核心词拆解为单个方块（如【整】【理】【神】【器】）
    chars = list(info["line1"][:6])
    box_size = 140
    gap = 14
    total_w = len(chars) * box_size + (len(chars) - 1) * gap
    start_x = (COVER_W - total_w) // 2
    box_y = COVER_H - 340

    d = ImageDraw.Draw(bg)
    char_font = ImageFont.truetype(_find_font(84, bold=True), 84)

    for i, ch in enumerate(chars):
        bx = start_x + i * (box_size + gap)
        # 黑黄相间方块
        fill_bg = (255, 230, 0) if (i % 2 == 0) else (20, 20, 25)
        text_col = (20, 20, 25) if (i % 2 == 0) else (255, 255, 255)
        d.rounded_rectangle([(bx, box_y), (bx + box_size, box_y + box_size)], radius=18, fill=fill_bg)
        bbox = d.textbbox((0, 0), ch, font=char_font)
        cw, ch_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((bx + (box_size - cw) // 2, box_y + (box_size - ch_h) // 2 - bbox[1]), ch, font=char_font, fill=text_col)

    if info["line2"]:
        sub_font = ImageFont.truetype(_find_font(56, bold=True), 56)
        _draw_large_card(bg, ((COVER_W - 500) // 2, box_y + box_size + 24), info["line2"], sub_font,
                         bg_color=(255, 255, 255, 245), text_color=(20, 20, 25), pad_x=30, pad_y=12, radius=18)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板20(单字方块): %s", out_path)
    return out_path


def render_highlight_pen_dual_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 21：双色马卡龙荧光笔刷风（双层划线，薄荷绿+鹅黄马卡龙色）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    font_main = ImageFont.truetype(_find_font(90, bold=True), 90)
    start_y = COVER_H - 350

    overlay = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    # Line 1 薄荷绿荧光划线
    t1 = info["line1"]
    bbox1 = odraw.textbbox((0, 0), t1, font=font_main)
    w1, h1 = bbox1[2] - bbox1[0], bbox1[3] - bbox1[1]
    odraw.rounded_rectangle([(50, start_y), (50 + w1 + 50, start_y + h1 + 24)], radius=12, fill=(130, 240, 180, 240))

    # Line 2 鹅黄色荧光划线
    t2 = info["line2"] if info["line2"] else "实用日常分享"
    next_y = start_y + h1 + 45
    bbox2 = odraw.textbbox((0, 0), t2, font=font_main)
    w2, h2 = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
    odraw.rounded_rectangle([(50, next_y), (50 + w2 + 50, next_y + h2 + 24)], radius=12, fill=(255, 235, 100, 245))

    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(bg)
    d.text((75 - bbox1[0], start_y + 12 - bbox1[1]), t1, font=font_main, fill=(20, 25, 20))
    d.text((75 - bbox2[0], next_y + 12 - bbox2[1]), t2, font=font_main, fill=(25, 20, 15))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板21(双色荧光笔): %s", out_path)
    return out_path


def render_minimal_border_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 22：画廊极简细线框风（四周白色卡纸留白 + 极简黑色纤细字标）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    bg = Image.new("RGB", (COVER_W, COVER_H), (255, 255, 255))
    # 内嵌留白 60px
    pad = 60
    inner_w = COVER_W - pad * 2
    inner_h = COVER_H - pad * 2 - 120
    p = _fit_crop_image(photos[0], inner_w, inner_h)
    bg.paste(p, (pad, pad))

    # 底部卡纸极简字
    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(64, bold=True), 64)
    full_t = f"{info['line1']} · {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    d.text(((COVER_W - tw) // 2, COVER_H - pad - 90), full_t, font=font_main, fill=(30, 30, 35))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板22(画廊细线框): %s", out_path)
    return out_path


def render_center_circle_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 23：圆形景窗聚焦风（全景模糊底图 + 中央正圆特写展示局部细节）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    # 背景毛玻璃虚化
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H).filter(ImageFilter.GaussianBlur(radius=25))
    bg = ImageEnhance.Brightness(bg).enhance(0.85)

    # 中央正圆景窗
    circle_size = 780
    cx = (COVER_W - circle_size) // 2
    cy = 200

    circle_img = _fit_crop_image(photos[0], circle_size, circle_size)
    mask = Image.new("L", (circle_size, circle_size), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.ellipse([(0, 0), (circle_size, circle_size)], fill=255)

    bg.paste(circle_img, (cx, cy), mask)

    # 圆形白边
    d = ImageDraw.Draw(bg)
    d.ellipse([(cx, cy), (cx + circle_size, cy + circle_size)], outline=(255, 255, 255), width=6)

    # 底部大字卡片
    font_main = ImageFont.truetype(_find_font(78, bold=True), 78)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    _draw_large_card(bg, (70, COVER_H - 320), full_t, font_main,
                     bg_color=(255, 230, 0, 255), text_color=(20, 20, 25), pad_x=36, pad_y=18, radius=22, shadow_blur=14, shadow_alpha=80)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板23(圆形景窗): %s", out_path)
    return out_path


def render_vertical_text_japan_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 24：日系竖排极简风（右侧竖排文字，大气质朴生活美学）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 右侧半透明白底文字长条
    strip_w = 170
    strip_h = COVER_H - 180
    sx = COVER_W - strip_w - 60
    sy = 90

    strip = Image.new("RGBA", (strip_w, strip_h), (255, 255, 255, 230))
    bg.paste(strip, (sx, sy), strip)

    # 竖排写入字符
    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(62, bold=True), 62)
    chars = list(info["line1"][:7])
    char_y = sy + 50
    for ch in chars:
        d.text((sx + (strip_w - 62) // 2, char_y), ch, font=font_main, fill=(30, 30, 35))
        char_y += 75

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板24(日系竖排): %s", out_path)
    return out_path


def render_asymmetric_3grid_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 25：左一大图 + 右上下两图非对称三拼（小红书最经典测评三拼图）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)

    bg = Image.new("RGB", (COVER_W, COVER_H), (255, 255, 255))
    gutter = 6
    left_w = int(COVER_W * 0.58)
    right_w = COVER_W - left_w - gutter
    right_h = (COVER_H - gutter) // 2

    # 左一大图
    img_left = _fit_crop_image(photos[0], left_w, COVER_H)
    bg.paste(img_left, (0, 0))

    # 右上
    img_rt = _fit_crop_image(photos[1], right_w, right_h)
    bg.paste(img_rt, (left_w + gutter, 0))

    # 右下
    img_rb = _fit_crop_image(photos[2] if len(photos) > 2 else photos[0], right_w, right_h)
    bg.paste(img_rb, (left_w + gutter, right_h + gutter))

    # 底部横跨标题大卡片
    font_main = ImageFont.truetype(_find_font(78, bold=True), 78)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    _draw_large_card(bg, (50, COVER_H - 240), full_t, font_main,
                     bg_color=(20, 20, 25, 245), text_color=(255, 255, 255), pad_x=34, pad_y=16, radius=20, shadow_blur=14, shadow_alpha=90)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板25(非对称三拼): %s", out_path)
    return out_path


def render_gradient_capsule_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 26：落日微光渐变胶囊风（暖橙至蜜桃粉微透渐变大胶囊）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    cap_w, cap_h = COVER_W - 120, 240
    cap_x, cap_y = 60, COVER_H - 330

    # 绘制暖橙渐变
    cap_img = Image.new("RGBA", (cap_w, cap_h), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(cap_img)
    for x in range(cap_w):
        t = x / cap_w
        r = int(255)
        g = int(140 + 70 * t)
        b = int(70 + 80 * t)
        cdraw.line([(x, 0), (x, cap_h)], fill=(r, g, b, 235))

    # 裁剪圆角胶囊蒙版
    mask = Image.new("L", (cap_w, cap_h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([(0, 0), (cap_w, cap_h)], radius=32, fill=255)

    bg.paste(cap_img, (cap_x, cap_y), mask)

    # 胶囊内大字
    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(78, bold=True), 78)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    d.text(((COVER_W - tw) // 2, cap_y + 55), full_t, font=font_main, fill=(25, 25, 30))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(34, bold=True), 34)
        sbox = d.textbbox((0, 0), info["sub"], font=sub_font)
        d.text(((COVER_W - (sbox[2] - sbox[0])) // 2, cap_y + 155), info["sub"], font=sub_font, fill=(60, 40, 30))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板26(渐变胶囊): %s", out_path)
    return out_path


def render_contrast_duotone_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 27：黑白高反差积木咬合风（第一行黑底白字 + 第二行白底黑字，高对比）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    font_main = ImageFont.truetype(_find_font(88, bold=True), 88)
    start_y = COVER_H - 360

    # Line 1 纯黑底白字卡片
    w1, h1 = _draw_large_card(bg, (60, start_y), info["line1"], font_main,
                              bg_color=(18, 18, 22, 250), text_color=(255, 255, 255), pad_x=36, pad_y=18, radius=20)

    # Line 2 纯白底黑字卡片咬合
    line2_t = info["line2"] if info["line2"] else "实用日常清单"
    _draw_large_card(bg, (60, start_y + h1 + 14), line2_t, font_main,
                     bg_color=(255, 255, 255, 250), text_color=(18, 18, 22), pad_x=36, pad_y=18, radius=20)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板27(黑白咬合): %s", out_path)
    return out_path


def render_quote_clean_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 28：治愈系大双引号语录风（温柔大双引号装饰，慢生活文艺感）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 底部微暗遮罩
    overlay = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for y in range(400):
        odraw.line([(0, COVER_H - 400 + y), (COVER_W, COVER_H - 400 + y)], fill=(0, 0, 0, int(190 * (y / 400))))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    d = ImageDraw.Draw(bg)
    # 巨大浅色英文双引号
    quote_font = ImageFont.truetype(_find_font(160, bold=True), 160)
    d.text((60, COVER_H - 420), "“", font=quote_font, fill=(255, 255, 255, 120))

    font_main = ImageFont.truetype(_find_font(76, bold=True), 76)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    d.text((150, COVER_H - 290), full_t, font=font_main, fill=(255, 255, 255))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(36, bold=False), 36)
        d.text((150, COVER_H - 180), f"— {info['sub']}", font=sub_font, fill=(220, 220, 230))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板28(治愈双引号): %s", out_path)
    return out_path


def render_washi_tape_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 29：双胶带手账白卡风（两角贴半透明斜切胶带，自然温馨）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    card_w, card_h = COVER_W - 140, 260
    cx = 70
    cy = COVER_H - 360

    # 白卡
    card_img = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 245))
    cdraw = ImageDraw.Draw(card_img)

    # 两角半透明胶带
    cdraw.polygon([(0, 40), (70, 0), (130, 0), (0, 70)], fill=(220, 215, 195, 210))
    cdraw.polygon([(card_w - 130, card_h), (card_w, card_h - 70), (card_w, card_h - 40), (card_w - 70, card_h)], fill=(220, 215, 195, 210))

    font_main = ImageFont.truetype(_find_font(76, bold=True), 76)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = cdraw.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    cdraw.text(((card_w - tw) // 2, 60), full_t, font=font_main, fill=(25, 25, 30))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(34, bold=False), 34)
        sbox = cdraw.textbbox((0, 0), info["sub"], font=sub_font)
        cdraw.text(((card_w - (sbox[2] - sbox[0])) // 2, 160), info["sub"], font=sub_font, fill=(110, 110, 120))

    bg.paste(card_img, (cx, cy), card_img)
    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板29(双胶带手账): %s", out_path)
    return out_path


def render_editorial_bold_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 30：排版大师层次风（顶部极简细字背景叙述，底部巨无霸大字直击重心）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 顶部浅色细字
    top_font = ImageFont.truetype(_find_font(36, bold=True), 36)
    _draw_large_card(bg, (50, 60), f"✦ {info['tag']} ✦", top_font, bg_color=(255, 255, 255, 240), text_color=(30, 30, 35), pad_x=24, pad_y=10, radius=18)

    # 底部超大字阶梯
    font_main = ImageFont.truetype(_find_font(96, bold=True), 96)
    _draw_large_card(bg, (50, COVER_H - 280), info["line1"], font_main,
                     bg_color=(255, 230, 0, 255), text_color=(18, 18, 20), pad_x=40, pad_y=20, radius=22, shadow_blur=16, shadow_alpha=110)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板30(排版大师): %s", out_path)
    return out_path


def render_spotlight_dark_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 31：电影聚光灯风（全画幅深邃暗角，居中发光大字）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 聚光灯暗角
    overlay = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    cx, cy = COVER_W // 2, COVER_H // 2
    for r in range(400, 900, 30):
        alpha = int(140 * ((r - 400) / 500))
        odraw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline=(0, 0, 0, alpha), width=30)
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(84, bold=True), 84)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    d.text(((COVER_W - tw) // 2, COVER_H - 260), full_t, font=font_main, fill=(255, 255, 255))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板31(电影聚光灯): %s", out_path)
    return out_path


def render_retro_stamp_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 32：复古手作红印章风（大字标题旁盖一枚质朴印章“心选/好物”）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    font_main = ImageFont.truetype(_find_font(82, bold=True), 82)
    w, h = _draw_large_card(bg, (50, COVER_H - 280), info["line1"], font_main,
                            bg_color=(255, 255, 255, 245), text_color=(25, 25, 30), pad_x=36, pad_y=18, radius=20)

    # 盖红印章
    stamp_size = 90
    sx = 50 + w + 15
    sy = COVER_H - 280 + (h - stamp_size) // 2
    if sx + stamp_size < COVER_W - 20:
        d = ImageDraw.Draw(bg)
        d.rounded_rectangle([(sx, sy), (sx + stamp_size, sy + stamp_size)], radius=16, outline=(220, 50, 50), width=4)
        s_font = ImageFont.truetype(_find_font(38, bold=True), 38)
        d.text((sx + 8, sy + 6), "生活", font=s_font, fill=(220, 50, 50))
        d.text((sx + 8, sy + 44), "精选", font=s_font, fill=(220, 50, 50))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板32(手作印章): %s", out_path)
    return out_path


def render_bubble_talk_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 33：亲切对话气泡风（带萌系小三角形尾巴的圆角对话气泡）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    bw, bh = COVER_W - 140, 240
    bx, by = 70, COVER_H - 340

    bubble = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bubble)
    # 气泡主体
    bdraw.rounded_rectangle([(bx, by), (bx + bw, by + bh)], radius=28, fill=(255, 255, 255, 245))
    # 气泡小三角
    bdraw.polygon([(bx + 80, by + bh), (bx + 110, by + bh), (bx + 75, by + bh + 30)], fill=(255, 255, 255, 245))

    bg = Image.alpha_composite(bg.convert("RGBA"), bubble).convert("RGB")
    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(74, bold=True), 74)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    d.text(((COVER_W - tw) // 2, by + 50), full_t, font=font_main, fill=(25, 25, 30))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(34, bold=False), 34)
        sbox = d.textbbox((0, 0), info["sub"], font=sub_font)
        d.text(((COVER_W - (sbox[2] - sbox[0])) // 2, by + 145), info["sub"], font=sub_font, fill=(120, 120, 130))

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板33(对话气泡): %s", out_path)
    return out_path


def render_top_tag_bottom_hero_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 34：顶标下沉稳重台阶风（视觉重心稳稳下沉，极耐看）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 顶部分类
    top_font = ImageFont.truetype(_find_font(34, bold=True), 34)
    _draw_large_card(bg, (50, 60), info["tag"], top_font, bg_color=(255, 255, 255, 230), text_color=(30, 30, 35), pad_x=22, pad_y=10, radius=16)

    # 底部台阶大底座
    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(86, bold=True), 86)
    _draw_large_card(bg, (50, COVER_H - 330), info["line1"], font_main,
                     bg_color=(20, 20, 24, 250), text_color=(255, 230, 0), pad_x=36, pad_y=18, radius=20)

    if info["line2"]:
        _draw_large_card(bg, (50, COVER_H - 190), info["line2"], font_main,
                         bg_color=(255, 255, 255, 250), text_color=(20, 20, 24), pad_x=36, pad_y=18, radius=20)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板34(稳重台阶): %s", out_path)
    return out_path


def render_fresh_clean_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 35：性冷淡极简透气风（大面积呼吸留白，高级感小方糖徽章）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 悬浮极轻薄方糖卡片
    card_w, card_h = COVER_W - 160, 200
    cx = 80
    cy = COVER_H - 280

    card = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 230))
    cdraw = ImageDraw.Draw(card)
    cdraw.rectangle([(0, 0), (card_w, card_h)], outline=(255, 255, 255, 255), width=2)

    font_main = ImageFont.truetype(_find_font(70, bold=True), 70)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = cdraw.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    cdraw.text(((card_w - tw) // 2, 40), full_t, font=font_main, fill=(35, 35, 40))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(32, bold=False), 32)
        sbox = cdraw.textbbox((0, 0), info["sub"], font=sub_font)
        cdraw.text(((card_w - (sbox[2] - sbox[0])) // 2, 130), info["sub"], font=sub_font, fill=(120, 120, 130))

    bg.paste(card, (cx, cy), card)
    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板35(性冷淡极简): %s", out_path)
    return out_path


# 额外扩展模版注册表字典


def render_checklist_tick_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 36：清单实测打勾风（带 3 个小圆点对勾项，信息量与干货感极强，诱发高点击与高收藏）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 底部微暗遮罩
    overlay = Image.new("RGBA", (COVER_W, COVER_H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for y in range(480):
        odraw.line([(0, COVER_H - 480 + y), (COVER_W, COVER_H - 480 + y)], fill=(0, 0, 0, int(210 * (y / 480))))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    # 1. 顶部生活灵感胶囊
    top_font = ImageFont.truetype(_find_font(34, bold=True), 34)
    _draw_large_card(bg, (50, 60), info["tag"], top_font, bg_color=(255, 230, 0, 245), text_color=(20, 20, 25), pad_x=22, pad_y=10, radius=18)

    # 2. 底部大标题
    d = ImageDraw.Draw(bg)
    font_main = ImageFont.truetype(_find_font(80, bold=True), 80)
    full_t = f"{info['line1']} {info['line2']}" if info['line2'] else info['line1']
    bbox = d.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    d.text((60, COVER_H - 400), full_t, font=font_main, fill=(255, 255, 255))

    # 3. 仿小红书笔记 3 个实测对勾清单
    check_font = ImageFont.truetype(_find_font(34, bold=True), 34)
    bullets = ["省出50%空间", "不压衣物不变形", "换季拿取超顺手"]
    cy = COVER_H - 280
    for b in bullets:
        # 绿色/黄色小圆点对勾
        d.ellipse([(60, cy + 4), (60 + 28, cy + 32)], fill=(255, 230, 0))
        d.text((66, cy + 2), "✔", font=check_font, fill=(20, 20, 25))
        d.text((105, cy), b, font=check_font, fill=(235, 235, 240))
        cy += 50

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板36(清单打勾): %s", out_path)
    return out_path


def render_question_curiosity_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 37：痛点大问号悬念风（巨大亮黄立体问号？+ 悬念式短句，好奇心点击率极高）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 顶部巨大立体问号（180px 巨型荧光黄）
    d = ImageDraw.Draw(bg)
    q_font = ImageFont.truetype(_find_font(180, bold=True), 180)
    # 问号立体阴影
    d.text((70, 70), "？", font=q_font, fill=(0, 0, 0, 160))
    d.text((64, 64), "？", font=q_font, fill=(255, 230, 0))

    # 问号右侧紧跟场景标签
    top_font = ImageFont.truetype(_find_font(38, bold=True), 38)
    _draw_large_card(bg, (230, 100), info["tag"], top_font, bg_color=(20, 20, 25, 240), text_color=(255, 255, 255), pad_x=24, pad_y=12, radius=18)

    # 底部下沉悬念式双色积木字
    font_main = ImageFont.truetype(_find_font(86, bold=True), 86)
    w1, h1 = _draw_large_card(bg, (60, COVER_H - 330), info["line1"], font_main,
                              bg_color=(255, 230, 0, 255), text_color=(20, 20, 25), pad_x=36, pad_y=18, radius=22, shadow_blur=14, shadow_alpha=100)

    if info["line2"]:
        _draw_large_card(bg, (60, COVER_H - 330 + h1 + 16), info["line2"], font_main,
                         bg_color=(20, 20, 25, 245), text_color=(255, 255, 255), pad_x=36, pad_y=18, radius=22, shadow_blur=14, shadow_alpha=100)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板37(悬念大问号): %s", out_path)
    return out_path


def render_diagonal_slash_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 38：斜切几何色块撞击风（-8°斜角交错双色底块，打破死板横平竖直，视觉动感强）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 制作两块倾斜的色块条
    font_main = ImageFont.truetype(_find_font(84, bold=True), 84)

    # 建立倾斜画板
    slash_layer = Image.new("RGBA", (COVER_W + 200, 400), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(slash_layer)

    # 第一行色块 (荧光黄)
    t1 = info["line1"]
    bbox1 = sdraw.textbbox((0, 0), t1, font=font_main)
    w1, h1 = bbox1[2] - bbox1[0], bbox1[3] - bbox1[1]
    sdraw.rounded_rectangle([(60, 40), (60 + w1 + 60, 40 + h1 + 28)], radius=18, fill=(255, 230, 0, 255))
    sdraw.text((90 - bbox1[0], 54 - bbox1[1]), t1, font=font_main, fill=(20, 20, 25))

    # 第二行色块 (纯黑底白字)
    t2 = info["line2"] if info["line2"] else "实用日常清单"
    bbox2 = sdraw.textbbox((0, 0), t2, font=font_main)
    w2, h2 = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
    sdraw.rounded_rectangle([(100, 40 + h1 + 45), (100 + w2 + 60, 40 + h1 + 45 + h2 + 28)], radius=18, fill=(20, 20, 25, 250))
    sdraw.text((130 - bbox2[0], 40 + h1 + 59 - bbox2[1]), t2, font=font_main, fill=(255, 255, 255))

    # 整体逆时针旋转 -4.5 度营造破格张力
    slash_rot = slash_layer.rotate(-4.5, resample=Image.BICUBIC, expand=True)
    bg.paste(slash_rot, (-40, COVER_H - 450), slash_rot)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板38(斜切几何色块): %s", out_path)
    return out_path


def render_circular_stamp_badge_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 39：复古双环圆标火漆勋章风（右上角悬浮红/黄双环火漆印章，提升美学格调）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _draw_large_card, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 1. 右上角精美双圈圆环印章 (180x180)
    stamp_size = 170
    stamp_img = Image.new("RGBA", (stamp_size, stamp_size), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stamp_img)
    # 外圈
    sdraw.ellipse([(2, 2), (stamp_size - 2, stamp_size - 2)], outline=(255, 230, 0, 250), width=4)
    # 内圈
    sdraw.ellipse([(14, 14), (stamp_size - 14, stamp_size - 14)], outline=(255, 230, 0, 200), width=2)
    # 填充半透明深黑
    sdraw.ellipse([(16, 16), (stamp_size - 16, stamp_size - 16)], fill=(20, 20, 25, 220))
    # 印章文字
    s_font = ImageFont.truetype(_find_font(34, bold=True), 34)
    sdraw.text((45, 42), "生活", font=s_font, fill=(255, 230, 0))
    sdraw.text((45, 85), "精选", font=s_font, fill=(255, 230, 0))

    # 印章倾斜 8 度并贴上
    stamp_rot = stamp_img.rotate(8, resample=Image.BICUBIC, expand=True)
    bg.paste(stamp_rot, (COVER_W - stamp_rot.size[0] - 60, 60), stamp_rot)

    # 2. 底部优雅大字
    font_main = ImageFont.truetype(_find_font(84, bold=True), 84)
    _draw_large_card(bg, (60, COVER_H - 300), info["line1"], font_main,
                     bg_color=(255, 255, 255, 250), text_color=(25, 25, 30), pad_x=38, pad_y=20, radius=22, shadow_blur=16, shadow_alpha=90)
    if info["line2"]:
        _draw_large_card(bg, (60, COVER_H - 160), info["line2"], font_main,
                         bg_color=(255, 230, 0, 255), text_color=(20, 20, 25), pad_x=38, pad_y=20, radius=22, shadow_blur=16, shadow_alpha=90)

    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板39(复古双环圆标): %s", out_path)
    return out_path


def render_handdrawn_border_style(base_path: str, title: str, out_path: str, video_path: str = None) -> str:
    """模板 40：手绘星芒随笔风（手绘质感双层框 + 标题两侧伴有 ✨ 闪光手绘星芒）。"""
    _find_font, _fit_crop_image, _collect_4_photos, _, _, _parse_title_for_cover, logger = _get_helper_imports()
    info = _parse_title_for_cover(title)
    photos = _collect_4_photos(base_path, video_path)
    bg = _fit_crop_image(photos[0], COVER_W, COVER_H)

    # 绘制手绘感外边框
    d = ImageDraw.Draw(bg)
    margin = 50
    d.rectangle([(margin, margin), (COVER_W - margin, COVER_H - margin)], outline=(255, 255, 255, 220), width=3)
    d.rectangle([(margin + 8, margin + 8), (COVER_W - margin - 8, COVER_H - margin - 8)], outline=(255, 255, 255, 140), width=1)

    # 底部手绘卡片
    card_w, card_h = COVER_W - margin * 2 - 40, 220
    cx = margin + 20
    cy = COVER_H - margin - card_h - 40

    card = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 235))
    cdraw = ImageDraw.Draw(card)
    cdraw.rectangle([(2, 2), (card_w - 2, card_h - 2)], outline=(30, 30, 35), width=2)

    font_main = ImageFont.truetype(_find_font(74, bold=True), 74)
    full_t = f"✨ {info['line1']} {info['line2']} ✨" if info['line2'] else f"✨ {info['line1']} ✨"
    bbox = cdraw.textbbox((0, 0), full_t, font=font_main)
    tw = bbox[2] - bbox[0]
    cdraw.text(((card_w - tw) // 2, 45), full_t, font=font_main, fill=(25, 25, 30))

    if info["sub"]:
        sub_font = ImageFont.truetype(_find_font(34, bold=False), 34)
        sbox = cdraw.textbbox((0, 0), info["sub"], font=sub_font)
        cdraw.text(((card_w - (sbox[2] - sbox[0])) // 2, 140), info["sub"], font=sub_font, fill=(110, 110, 120))

    bg.paste(card, (cx, cy), card)
    bg.save(out_path, quality=98, subsampling=0)
    logger.info("模板40(手绘星芒): %s", out_path)
    return out_path


EXTRA_COVER_RENDERERS = {
    "keyword_hero": render_keyword_hero_style,
    "torn_paper": render_torn_paper_style,
    "post_it_note": render_post_it_note_style,
    "magazine_vogue": render_magazine_vogue_style,
    "film_strip": render_film_strip_style,
    "two_thirds_split": render_two_thirds_split_style,
    "char_boxes": render_char_boxes_style,
    "highlight_pen_dual": render_highlight_pen_dual_style,
    "minimal_border": render_minimal_border_style,
    "center_circle": render_center_circle_style,
    "vertical_text_japan": render_vertical_text_japan_style,
    "asymmetric_3grid": render_asymmetric_3grid_style,
    "gradient_capsule": render_gradient_capsule_style,
    "contrast_duotone": render_contrast_duotone_style,
    "quote_clean": render_quote_clean_style,
    "washi_tape": render_washi_tape_style,
    "editorial_bold": render_editorial_bold_style,
    "spotlight_dark": render_spotlight_dark_style,
    "retro_stamp": render_retro_stamp_style,
    "bubble_talk": render_bubble_talk_style,
    "top_tag_bottom_hero": render_top_tag_bottom_hero_style,
    "fresh_clean": render_fresh_clean_style,
    "checklist_tick": render_checklist_tick_style,
    "question_curiosity": render_question_curiosity_style,
    "diagonal_slash": render_diagonal_slash_style,
    "circular_stamp_badge": render_circular_stamp_badge_style,
    "handdrawn_border": render_handdrawn_border_style,
}
