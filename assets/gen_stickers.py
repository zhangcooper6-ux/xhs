# -*- coding: utf-8 -*-
"""生成 emoji 贴纸素材库（透明背景 PNG）。

用 PIL 把常用 emoji 渲染到透明背景上，供二创时 overlay 到视频。
运行: python assets/gen_stickers.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

STICKER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stickers")

# 常用 emoji（小红书常见装饰元素）
EMOJIS = [
    "❤️", "🔥", "✨", "⭐", "🌟", "💕", "👍", "😍", "🎉", "🎊",
    "🎈", "🎁", "📸", "💡", "🏠", "🌿", "☕", "🌸", "💫", "💎",
    "🍀", "🥰", "😘", "😋", "🤩", "😎", "💪", "🙌", "👏", "💖",
]

# 贴纸尺寸
SIZES = [128, 96, 64]


def find_emoji_font():
    """找一个能渲染 emoji 的字体。"""
    candidates = [
        r"C:\Windows\Fonts\seguiemj.ttf",   # Segoe UI Emoji（Win 自带 emoji 字体）
        r"C:\Windows\Fonts\seguisym.ttf",   # Segoe UI Symbol
        r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑（部分 emoji）
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def render_emoji(emoji, size, font_path, out_path):
    """把单个 emoji 渲染到透明背景 PNG。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, int(size * 0.8))
    except Exception:
        font = ImageFont.load_default()

    # 居中绘制
    bbox = draw.textbbox((0, 0), emoji, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), emoji, font=font, fill=(255, 255, 255, 255))
    img.save(out_path)
    return out_path


def main():
    os.makedirs(STICKER_DIR, exist_ok=True)
    font_path = find_emoji_font()
    if not font_path:
        print("未找到 emoji 字体（seguiemj.ttf），跳过")
        return

    print(f"字体: {font_path}")
    print(f"输出目录: {STICKER_DIR}")

    count = 0
    for emoji in EMOJIS:
        for sz in SIZES:
            # 文件名用 emoji 的码点，避免文件名含 emoji 字符
            name = "_".join(f"{ord(c):x}" for c in emoji if ord(c) > 0x2000)
            if not name:
                name = "_".join(f"{ord(c):x}" for c in emoji)
            out = os.path.join(STICKER_DIR, f"{name}_{sz}.png")
            if not os.path.exists(out):
                render_emoji(emoji, sz, font_path, out)
                count += 1

    # 写一个索引文件
    index_path = os.path.join(STICKER_DIR, "_index.txt")
    with open(index_path, "w", encoding="utf-8") as f:
        for emoji in EMOJIS:
            f.write(emoji + "\n")

    print(f"生成 {count} 个贴纸（共 {len(EMOJIS)} emoji x {len(SIZES)} 尺寸）")
    print(f"索引: {index_path}")


if __name__ == "__main__":
    main()
