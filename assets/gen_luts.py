# -*- coding: utf-8 -*-
"""生成 LUT (.cube) 文件。

生成符合 Adobe Cube 格式规范的 3D LUT 文件，每个 LUT 有独特的色调映射。
这些 LUT 用于 core/filters.py 中的滤镜预设。

生成的 LUT 列表（15 个）：
  - teal_orange.cube      青橙色调
  - cinematic.cube         电影感
  - moody_green.cube       墨绿
  - dark_cinema.cube       暗调电影
  - orange_cinema.cube     橘色电影
  - hollywood.cube         好莱坞
  - suspense.cube          悬疑
  - scifi.cube             科幻
  - war.cube               战争
  - kodak_portra.cube      Kodak Portra 胶片
  - fuji_400h.cube         Fuji 400H 胶片
  - film_warm.cube         暖色胶片
  - film_cool.cube         冷色胶片
  - vintage_faded.cube     褪色复古
  - pastel.cube            柔色

运行：python assets/gen_luts.py
"""

import os
import math

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "luts")

# LUT 尺寸（33x33x33 是行业标准，体积/精度平衡）
LUT_SIZE = 33


def write_cube(filename, transform_func, title=""):
    """生成一个 .cube 文件。

    参数:
        filename: 输出文件名
        transform_func: 颜色变换函数 (r, g, b) -> (r', g', b')
                        r, g, b 范围 0.0-1.0
        title: LUT 标题
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)

    lines = []
    if title:
        lines.append(f'TITLE "{title}"')
    lines.append(f"LUT_3D_SIZE {LUT_SIZE}")
    lines.append("DOMAIN_MIN 0.0 0.0 0.0")
    lines.append("DOMAIN_MAX 1.0 1.0 1.0")
    lines.append("")

    # 3D LUT 遍历顺序：B 外层，G 中层，R 内层
    for b_idx in range(LUT_SIZE):
        for g_idx in range(LUT_SIZE):
            for r_idx in range(LUT_SIZE):
                r = r_idx / (LUT_SIZE - 1)
                g = g_idx / (LUT_SIZE - 1)
                b = b_idx / (LUT_SIZE - 1)

                r_out, g_out, b_out = transform_func(r, g, b)

                # 钳制到 0-1
                r_out = max(0.0, min(1.0, r_out))
                g_out = max(0.0, min(1.0, g_out))
                b_out = max(0.0, min(1.0, b_out))

                lines.append(f"{r_out:.6f} {g_out:.6f} {b_out:.6f}")

    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines))

    size_kb = os.path.getsize(path) / 1024
    print(f"  ✓ {filename:<25} ({size_kb:.0f}KB)")
    return path


# ---------- 颜色变换函数 ----------

def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def lift_gamma_gain(r, g, b, lift=(0, 0, 0), gamma=(1, 1, 1), gain=(1, 1, 1)):
    """Lift/Gamma/Gain 调色（阴影/中间调/高光）。"""
    # Lift（阴影偏移）
    r = r * (1 - lift[0]) + lift[0]
    g = g * (1 - lift[1]) + lift[1]
    b = b * (1 - lift[2]) + lift[2]
    # Gain（高光增益）
    r = r * gain[0]
    g = g * gain[1]
    b = b * gain[2]
    # Gamma（中间调）
    if gamma[0] != 1 and r > 0:
        r = r ** (1 / gamma[0])
    if gamma[1] != 1 and g > 0:
        g = g ** (1 / gamma[1])
    if gamma[2] != 1 and b > 0:
        b = b ** (1 / gamma[2])
    return r, g, b


def s_curve(x, strength=0.15):
    """S 曲线增对比。"""
    return clamp(0.5 + (x - 0.5) * (1 + strength * math.sin(x * math.pi)))


# ---------- 15 个 LUT 变换 ----------

def teal_orange(r, g, b):
    """青橙色调：阴影偏青，高光偏橙。"""
    # 高光偏橙
    highlight = max(0, (r + g) / 2 - b)
    r = clamp(r + highlight * 0.15)
    g = clamp(g + highlight * 0.08)
    b = clamp(b - highlight * 0.1)
    # 阴影偏青
    shadow = max(0, b - (r + g) / 2)
    b = clamp(b + shadow * 0.12)
    g = clamp(g + shadow * 0.06)
    r = clamp(r - shadow * 0.08)
    # S 曲线
    r, g, b = s_curve(r, 0.12), s_curve(g, 0.1), s_curve(b, 0.1)
    return r, g, b


def cinematic(r, g, b):
    """电影感：降饱和+对比+微青。"""
    # S 曲线
    r, g, b = s_curve(r, 0.18), s_curve(g, 0.15), s_curve(b, 0.15)
    # 微青调
    b = clamp(b + 0.02)
    g = clamp(g + 0.01)
    r = clamp(r - 0.02)
    # 略降饱和
    avg = (r + g + b) / 3
    r = clamp(avg + (r - avg) * 0.92)
    g = clamp(avg + (g - avg) * 0.92)
    b = clamp(avg + (b - avg) * 0.92)
    return r, g, b


def moody_green(r, g, b):
    """墨绿：暗调+偏绿。"""
    # 偏绿
    g = clamp(g + 0.05)
    b = clamp(b + 0.02)
    r = clamp(r - 0.05)
    # 压暗
    r, g, b = r * 0.88, g * 0.9, b * 0.85
    # S 曲线
    r, g, b = s_curve(r, 0.15), s_curve(g, 0.12), s_curve(b, 0.12)
    return r, g, b


def dark_cinema(r, g, b):
    """暗调电影：大幅压暗+降饱和+偏冷。"""
    # 压暗
    r, g, b = r * 0.82, g * 0.82, b * 0.85
    # 偏冷
    b = clamp(b + 0.03)
    r = clamp(r - 0.03)
    # 降饱和
    avg = (r + g + b) / 3
    r = clamp(avg + (r - avg) * 0.88)
    g = clamp(avg + (g - avg) * 0.88)
    b = clamp(avg + (b - avg) * 0.88)
    # S 曲线
    r, g, b = s_curve(r, 0.2), s_curve(g, 0.18), s_curve(b, 0.18)
    return r, g, b


def orange_cinema(r, g, b):
    """橘色电影：暖色调+对比。"""
    # 暖调
    r = clamp(r + 0.04)
    g = clamp(g + 0.02)
    b = clamp(b - 0.05)
    # S 曲线
    r, g, b = s_curve(r, 0.15), s_curve(g, 0.12), s_curve(b, 0.1)
    return r, g, b


def hollywood(r, g, b):
    """好莱坞：高对比+微青橙。"""
    # S 曲线强对比
    r, g, b = s_curve(r, 0.22), s_curve(g, 0.2), s_curve(b, 0.2)
    # 高光偏橙
    if r > 0.5:
        r = clamp(r + 0.03)
        g = clamp(g + 0.015)
    # 阴影偏青
    if b < 0.5:
        b = clamp(b + 0.03)
    return r, g, b


def suspense(r, g, b):
    """悬疑：冷暗+降饱和。"""
    # 压暗
    r, g, b = r * 0.8, g * 0.8, b * 0.83
    # 偏冷
    b = clamp(b + 0.04)
    r = clamp(r - 0.04)
    # 大幅降饱和
    avg = (r + g + b) / 3
    r = clamp(avg + (r - avg) * 0.82)
    g = clamp(avg + (g - avg) * 0.82)
    b = clamp(avg + (b - avg) * 0.82)
    # S 曲线
    r, g, b = s_curve(r, 0.18), s_curve(g, 0.15), s_curve(b, 0.15)
    return r, g, b


def scifi(r, g, b):
    """科幻：蓝调+高饱和。"""
    # 偏蓝
    b = clamp(b + 0.08)
    g = clamp(g + 0.03)
    r = clamp(r - 0.05)
    # 提饱和
    avg = (r + g + b) / 3
    r = clamp(avg + (r - avg) * 1.15)
    g = clamp(avg + (g - avg) * 1.15)
    b = clamp(avg + (b - avg) * 1.15)
    # S 曲线
    r, g, b = s_curve(r, 0.15), s_curve(g, 0.15), s_curve(b, 0.15)
    return r, g, b


def war(r, g, b):
    """战争：黄沙感+降饱和。"""
    # 暖黄
    r = clamp(r + 0.06)
    g = clamp(g + 0.04)
    b = clamp(b - 0.08)
    # 降饱和
    avg = (r + g + b) / 3
    r = clamp(avg + (r - avg) * 0.85)
    g = clamp(avg + (g - avg) * 0.85)
    b = clamp(avg + (b - avg) * 0.85)
    # 压暗
    r, g, b = r * 0.9, g * 0.88, b * 0.85
    # S 曲线
    r, g, b = s_curve(r, 0.18), s_curve(g, 0.15), s_curve(b, 0.12)
    return r, g, b


def kodak_portra(r, g, b):
    """Kodak Portra：暖肤色+柔和对比。"""
    # 暖调
    r = clamp(r + 0.03)
    g = clamp(g + 0.015)
    b = clamp(b - 0.03)
    # 柔和 S 曲线
    r, g, b = s_curve(r, 0.08), s_curve(g, 0.07), s_curve(b, 0.07)
    # 微提亮
    r, g, b = r * 1.03, g * 1.03, b * 1.02
    return r, g, b


def fuji_400h(r, g, b):
    """Fuji 400H：偏绿+柔和。"""
    # 微偏绿
    g = clamp(g + 0.02)
    b = clamp(b + 0.01)
    r = clamp(r - 0.02)
    # 柔和对比
    r, g, b = s_curve(r, 0.06), s_curve(g, 0.06), s_curve(b, 0.06)
    # 提亮
    r, g, b = r * 1.05, g * 1.05, b * 1.05
    return r, g, b


def film_warm(r, g, b):
    """暖色胶片：强暖调+颗粒感。"""
    r = clamp(r + 0.05)
    g = clamp(g + 0.025)
    b = clamp(b - 0.06)
    # S 曲线
    r, g, b = s_curve(r, 0.1), s_curve(g, 0.08), s_curve(b, 0.08)
    return r, g, b


def film_cool(r, g, b):
    """冷色胶片：偏冷+柔和。"""
    b = clamp(b + 0.04)
    g = clamp(g + 0.01)
    r = clamp(r - 0.04)
    # S 曲线
    r, g, b = s_curve(r, 0.08), s_curve(g, 0.08), s_curve(b, 0.1)
    return r, g, b


def vintage_faded(r, g, b):
    """褪色复古：低对比+偏暖+褪色。"""
    # 褪色（提亮阴影）
    r = r * 0.85 + 0.12
    g = g * 0.85 + 0.1
    b = b * 0.85 + 0.08
    # 偏暖
    r = clamp(r + 0.03)
    b = clamp(b - 0.03)
    # 降饱和
    avg = (r + g + b) / 3
    r = clamp(avg + (r - avg) * 0.88)
    g = clamp(avg + (g - avg) * 0.88)
    b = clamp(avg + (b - avg) * 0.88)
    return r, g, b


def pastel(r, g, b):
    """柔色：低饱和+提亮+柔和。"""
    # 提亮
    r, g, b = r * 1.1 + 0.03, g * 1.1 + 0.03, b * 1.1 + 0.03
    # 降饱和
    avg = (r + g + b) / 3
    r = clamp(avg + (r - avg) * 0.82)
    g = clamp(avg + (g - avg) * 0.82)
    b = clamp(avg + (b - avg) * 0.82)
    # 柔和对比
    r, g, b = s_curve(r, 0.05), s_curve(g, 0.05), s_curve(b, 0.05)
    return r, g, b


# ---------- 主函数 ----------

def main():
    print(f"生成 LUT 文件到: {OUTPUT_DIR}")
    print(f"LUT 尺寸: {LUT_SIZE}x{LUT_SIZE}x{LUT_SIZE}")
    print()

    luts = [
        ("teal_orange.cube", teal_orange, "Teal & Orange"),
        ("cinematic.cube", cinematic, "Cinematic"),
        ("moody_green.cube", moody_green, "Moody Green"),
        ("dark_cinema.cube", dark_cinema, "Dark Cinema"),
        ("orange_cinema.cube", orange_cinema, "Orange Cinema"),
        ("hollywood.cube", hollywood, "Hollywood"),
        ("suspense.cube", suspense, "Suspense"),
        ("scifi.cube", scifi, "Sci-Fi"),
        ("war.cube", war, "War"),
        ("kodak_portra.cube", kodak_portra, "Kodak Portra"),
        ("fuji_400h.cube", fuji_400h, "Fuji 400H"),
        ("film_warm.cube", film_warm, "Film Warm"),
        ("film_cool.cube", film_cool, "Film Cool"),
        ("vintage_faded.cube", vintage_faded, "Vintage Faded"),
        ("pastel.cube", pastel, "Pastel"),
    ]

    for filename, func, title in luts:
        write_cube(filename, func, title)

    print(f"\n完成！共生成 {len(luts)} 个 LUT 文件")
    print(f"目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
