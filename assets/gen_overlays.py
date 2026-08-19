# -*- coding: utf-8 -*-
"""生成特效素材 PNG（光斑/漏光/边框/粒子等）。

用 PIL 生成带 alpha 通道的 PNG 素材，供 style_enhance.py 的 overlay 叠加使用。
所有素材都是透明背景，可直接用 FFmpeg overlay 叠加到视频上。

运行：python assets/gen_overlays.py
"""

import os
import math
import random
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "overlays")

# 素材尺寸（覆盖 1080x1920 竖屏）
W, H = 1080, 1920


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


# ---------- 1. 光斑/漏光素材 ----------

def gen_light_leak(name, color1, color2, position="top-right"):
    """生成漏光素材（径向渐变，透明背景）。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if position == "top-right":
        cx, cy = int(W * 0.85), int(H * 0.1)
    elif position == "top-left":
        cx, cy = int(W * 0.15), int(H * 0.1)
    elif position == "bottom-right":
        cx, cy = int(W * 0.85), int(H * 0.9)
    elif position == "bottom-left":
        cx, cy = int(W * 0.15), int(H * 0.9)
    else:
        cx, cy = W // 2, H // 2

    max_r = int(max(W, H) * 0.5)

    # 用径向渐变绘制
    pixels = img.load()
    for y in range(0, H, 2):  # 步长 2 加速
        for x in range(0, W, 2):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist < max_r:
                t = 1 - dist / max_r
                t = t ** 2  # 二次衰减
                r = int(color1[0] * t + color2[0] * (1 - t))
                g = int(color1[1] * t + color2[1] * (1 - t))
                b = int(color1[2] * t + color2[2] * (1 - t))
                a = int(180 * t)
                pixels[x, y] = (r, g, b, a)
                if x + 1 < W and y + 1 < H:
                    pixels[x + 1, y] = (r, g, b, a)
                    pixels[x, y + 1] = (r, g, b, a)
                    pixels[x + 1, y + 1] = (r, g, b, a)

    # 高斯模糊柔化
    img = img.filter(ImageFilter.GaussianBlur(radius=20))
    img.save(os.path.join(OUTPUT_DIR, "light_leaks", name))
    print(f"  ✓ light_leaks/{name}")


def gen_light_spot(name, color, count=3):
    """生成光斑素材（多个圆形高光，透明背景）。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for _ in range(count):
        cx = random.randint(0, W)
        cy = random.randint(0, H)
        r = random.randint(80, 200)

        for ri in range(r, 0, -5):
            t = 1 - ri / r
            t = t ** 2
            a = int(120 * t)
            c = (*color, a)
            draw.ellipse([cx - ri, cy - ri, cx + ri, cy + ri], fill=c)

    img = img.filter(ImageFilter.GaussianBlur(radius=15))
    img.save(os.path.join(OUTPUT_DIR, "light_leaks", name))
    print(f"  ✓ light_leaks/{name}")


# ---------- 2. 边框素材 ----------

def gen_vignette_mask(name, strength=0.6, feather=300):
    """生成暗角蒙版（中心透明，边缘半透明黑色）。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pixels = img.load()

    cx, cy = W // 2, H // 2
    max_dist = math.sqrt(cx ** 2 + cy ** 2)

    for y in range(0, H, 2):
        for x in range(0, W, 2):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist > max_dist - feather:
                t = (dist - (max_dist - feather)) / feather
                t = min(1, t)
                a = int(255 * strength * t)
                pixels[x, y] = (0, 0, 0, a)
                if x + 1 < W:
                    pixels[x + 1, y] = (0, 0, 0, a)
                if y + 1 < H:
                    pixels[x, y + 1] = (0, 0, 0, a)
                    if x + 1 < W:
                        pixels[x + 1, y + 1] = (0, 0, 0, a)

    img = img.filter(ImageFilter.GaussianBlur(radius=10))
    img.save(os.path.join(OUTPUT_DIR, "vignettes", name))
    print(f"  ✓ vignettes/{name}")


def gen_frame_border(name, color=(255, 255, 255), width=20, style="solid"):
    """生成边框素材。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if style == "solid":
        # 实线边框
        draw.rectangle([0, 0, W - 1, H - 1], outline=(*color, 200), width=width)
    elif style == "polaroid":
        # 宝丽来边框（底部更宽）
        bw = width
        bh = int(width * 3)
        draw.rectangle([0, 0, W - 1, H - 1], outline=(*color, 220), width=bw)
        # 底部加宽
        draw.rectangle([0, H - bh, W - 1, H - 1], fill=(*color, 220))
    elif style == "film":
        # 胶片孔边框
        draw.rectangle([0, 0, W - 1, H - 1], outline=(*color, 180), width=width)
        # 顶部和底部的胶片孔
        hole_w = 40
        hole_h = 25
        spacing = 80
        for x in range(spacing, W - spacing, spacing):
            draw.ellipse([x, 5, x + hole_w, 5 + hole_h], fill=(0, 0, 0, 0))
            draw.ellipse([x, H - 5 - hole_h, x + hole_w, H - 5], fill=(0, 0, 0, 0))

    img.save(os.path.join(OUTPUT_DIR, "frames", name))
    print(f"  ✓ frames/{name}")


# ---------- 3. 粒子/星光素材 ----------

def gen_particles(name, color=(255, 255, 255), count=50, size_range=(2, 8)):
    """生成粒子/星光素材。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for _ in range(count):
        x = random.randint(0, W)
        y = random.randint(0, H)
        s = random.randint(*size_range)
        a = random.randint(80, 200)

        # 中心亮点
        draw.ellipse([x - s, y - s, x + s, y + s], fill=(*color, a))

        # 十字光芒
        beam_len = s * 3
        beam_a = a // 2
        draw.line([x - beam_len, y, x + beam_len, y], fill=(*color, beam_a), width=1)
        draw.line([x, y - beam_len, x, y + beam_len], fill=(*color, beam_a), width=1)

    img = img.filter(ImageFilter.GaussianBlur(radius=1))
    img.save(os.path.join(OUTPUT_DIR, "particles", name))
    print(f"  ✓ particles/{name}")


def gen_dust(name, count=200):
    """生成灰尘/胶片颗粒素材。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for _ in range(count):
        x = random.randint(0, W)
        y = random.randint(0, H)
        s = random.randint(1, 3)
        a = random.randint(30, 100)
        gray = random.randint(150, 255)
        draw.ellipse([x - s, y - s, x + s, y + s], fill=(gray, gray, gray, a))

    img.save(os.path.join(OUTPUT_DIR, "particles", name))
    print(f"  ✓ particles/{name}")


# ---------- 4. 扫光素材 ----------

def gen_sweep_light(name, color=(255, 255, 255), angle=0):
    """生成扫光条素材（斜向高光条）。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 斜向高光条
    bar_width = 200
    bar_length = int(math.sqrt(W ** 2 + H ** 2))

    # 创建一个大画布绘制斜条
    margin = bar_length
    big = Image.new("RGBA", (W + 2 * margin, H + 2 * margin), (0, 0, 0, 0))
    big_draw = ImageDraw.Draw(big)

    # 渐变条
    for i in range(bar_width):
        t = 1 - abs(i - bar_width // 2) / (bar_width // 2)
        t = t ** 2
        a = int(150 * t)
        big_draw.line([0, i + margin, W + 2 * margin, i + margin],
                      fill=(*color, a), width=1)

    # 旋转
    big = big.rotate(angle, expand=False)

    # 裁剪回原尺寸
    img = big.crop((margin, margin, margin + W, margin + H))
    img = img.filter(ImageFilter.GaussianBlur(radius=10))
    img.save(os.path.join(OUTPUT_DIR, "sweeps", name))
    print(f"  ✓ sweeps/{name}")


# ---------- 主函数 ----------

def main():
    print(f"生成特效素材到: {OUTPUT_DIR}")
    print(f"素材尺寸: {W}x{H}")
    print()

    random.seed(42)  # 固定种子保证可复现

    # 1. 漏光素材
    ensure_dir(os.path.join(OUTPUT_DIR, "light_leaks"))
    gen_light_leak("leak_warm_tr.png", (255, 200, 100), (255, 100, 50), "top-right")
    gen_light_leak("leak_warm_tl.png", (255, 180, 80), (255, 120, 60), "top-left")
    gen_light_leak("leak_cool_tr.png", (100, 200, 255), (50, 100, 255), "top-right")
    gen_light_leak("leak_pink_tr.png", (255, 150, 200), (255, 100, 150), "top-right")
    gen_light_leak("leak_sunset.png", (255, 150, 50), (255, 50, 100), "bottom-right")
    gen_light_spot("spot_warm_1.png", (255, 200, 100), count=4)
    gen_light_spot("spot_cool_1.png", (150, 200, 255), count=3)
    gen_light_spot("spot_white_1.png", (255, 255, 255), count=5)

    # 2. 暗角蒙版
    ensure_dir(os.path.join(OUTPUT_DIR, "vignettes"))
    gen_vignette_mask("vignette_light.png", strength=0.4, feather=400)
    gen_vignette_mask("vignette_medium.png", strength=0.6, feather=350)
    gen_vignette_mask("vignette_strong.png", strength=0.8, feather=300)
    gen_vignette_mask("vignette_extreme.png", strength=0.95, feather=250)

    # 3. 边框素材
    ensure_dir(os.path.join(OUTPUT_DIR, "frames"))
    gen_frame_border("frame_white_thin.png", (255, 255, 255), width=8, style="solid")
    gen_frame_border("frame_white_thick.png", (255, 255, 255), width=20, style="solid")
    gen_frame_border("frame_black_thin.png", (40, 40, 40), width=8, style="solid")
    gen_frame_border("frame_polaroid.png", (250, 245, 235), width=30, style="polaroid")
    gen_frame_border("frame_film.png", (20, 20, 20), width=40, style="film")

    # 4. 粒子素材
    ensure_dir(os.path.join(OUTPUT_DIR, "particles"))
    gen_particles("stars_white.png", (255, 255, 255), count=40, size_range=(3, 8))
    gen_particles("stars_gold.png", (255, 215, 0), count=35, size_range=(2, 6))
    gen_particles("dust_1.png", count=200)
    gen_particles("dust_2.png", count=150)
    gen_particles("bokeh_warm.png", (255, 200, 150), count=20, size_range=(10, 30))
    gen_particles("bokeh_cool.png", (150, 200, 255), count=20, size_range=(10, 30))

    # 5. 扫光素材
    ensure_dir(os.path.join(OUTPUT_DIR, "sweeps"))
    gen_sweep_light("sweep_white.png", (255, 255, 255), angle=30)
    gen_sweep_light("sweep_warm.png", (255, 220, 150), angle=30)
    gen_sweep_light("sweep_cool.png", (180, 220, 255), angle=30)

    print()
    print("完成！素材目录结构：")
    print(f"  {OUTPUT_DIR}/")
    print(f"  ├── light_leaks/   漏光/光斑 ({len(os.listdir(os.path.join(OUTPUT_DIR, 'light_leaks')))} 个)")
    print(f"  ├── vignettes/     暗角蒙版 ({len(os.listdir(os.path.join(OUTPUT_DIR, 'vignettes')))} 个)")
    print(f"  ├── frames/        边框 ({len(os.listdir(os.path.join(OUTPUT_DIR, 'frames')))} 个)")
    print(f"  ├── particles/     粒子/星光 ({len(os.listdir(os.path.join(OUTPUT_DIR, 'particles')))} 个)")
    print(f"  └── sweeps/        扫光 ({len(os.listdir(os.path.join(OUTPUT_DIR, 'sweeps')))} 个)")


if __name__ == "__main__":
    main()
