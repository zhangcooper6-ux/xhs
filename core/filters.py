# -*- coding: utf-8 -*-
"""滤镜与特效系统：50+ 预设 + LUT 加载 + 原生滤镜组合 + 特效生成。

架构：
  FilterPreset（预设）
    ├── color: 原生调色参数（eq/curves/colorbalance/hue/vibrance/vignette）
    ├── lut: LUT 文件配置（可选，加载 .cube 文件）
    ├── effects: 特效列表（光斑/暗角/胶片颗粒/漏光/眩光/边框等）
    └── meta: 预设元信息（名称/分类/描述）

预设分类（50+ 个）：
  - 基础调色（10）：暖阳/冷调/清新/浓郁/淡雅/对比/柔和/鲜艳/复古暖/复古冷
  - 电影感（10）：青橙/电影感/墨绿/暗调电影/橘色电影/好莱坞/悬疑/科幻/战争/黑色电影
  - 胶片质感（10）：Kodak Portra/Fuji 400H/胶片颗粒/漏光胶片/褪色胶片/宝丽来/胶片暗角/反转片/负片/老照片
  - 风格化（10）：日系/韩系/ins风/港风/欧美/赛博朋克/故障/蒸汽波/宝丽来/拍立得
  - 黑白（5）：经典黑白/高对比黑白/柔焦黑白/胶片黑白/暗调黑白
  - 美食/风景（5）：美食鲜艳/美食暖调/风景明亮/风景饱和/日落暖光
  - 人像（5）：柔肤美白/粉嫩/冷白皮/暖黄皮/自然人像

LUT 加载：
  - 从 assets/luts/ 目录加载 .cube 文件
  - 支持 strength 参数（0-1，控制 LUT 强度）
  - 通过 lut3d 滤镜应用

特效生成（纯代码，无素材依赖）：
  - 暗角（vignette）
  - 胶片颗粒（noise + blur 模拟）
  - 漏光（geq 径向渐变）
  - 眩光（geq + blend=screen）
  - 光斑（geq 径向渐变 + 移动动画）
  - 边框（drawbox）
"""

import os
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("filters")

# ---------- 资源目录 ----------

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
LUTS_DIR = os.path.join(ASSETS_DIR, "luts")
OVERLAYS_DIR = os.path.join(ASSETS_DIR, "overlays")


# ---------- 50+ 滤镜预设定义 ----------

# 每个预设包含：
#   color: 原生调色参数（传给 FFmpeg eq/curves/colorbalance 等）
#   lut: LUT 配置（可选）{"file": "xxx.cube", "strength": 0.8}
#   effects: 特效列表 ["vignette", "grain", "light_leak", ...]
#   meta: {"name_cn": "中文名", "category": "分类", "desc": "描述"}

FILTER_PRESETS = {
    # ========== 1. 基础调色（10个） ==========
    "warm_sunny": {
        "color": {"warm": 0.15, "saturation": 1.18, "contrast": 1.08, "brightness": 0.05, "vibrance": 0.15},
        "effects": [],
        "meta": {"name_cn": "暖阳", "category": "基础", "desc": "温暖阳光感，适合生活日常"},
    },
    "cool_tone": {
        "color": {"warm": -0.1, "saturation": 1.05, "contrast": 1.1, "brightness": 0.03, "vibrance": 0.1},
        "effects": [],
        "meta": {"name_cn": "冷调", "category": "基础", "desc": "清冷色调，适合科技/极简"},
    },
    "fresh": {
        "color": {"warm": 0.03, "saturation": 1.2, "contrast": 1.06, "brightness": 0.08, "vibrance": 0.2},
        "effects": [],
        "meta": {"name_cn": "清新", "category": "基础", "desc": "明亮清新，适合春夏内容"},
    },
    "rich": {
        "color": {"warm": 0.08, "saturation": 1.3, "contrast": 1.15, "brightness": 0.0, "vibrance": 0.25},
        "effects": [],
        "meta": {"name_cn": "浓郁", "category": "基础", "desc": "高饱和浓郁，适合美食/风景"},
    },
    "elegance": {
        "color": {"warm": 0.05, "saturation": 0.95, "contrast": 1.1, "brightness": 0.05, "vibrance": 0.0},
        "effects": [],
        "meta": {"name_cn": "淡雅", "category": "基础", "desc": "低饱和淡雅，适合家居/文具"},
    },
    "high_contrast": {
        "color": {"warm": 0.0, "saturation": 1.1, "contrast": 1.3, "brightness": 0.0, "vibrance": 0.1},
        "effects": [],
        "meta": {"name_cn": "高对比", "category": "基础", "desc": "强对比度，适合建筑/街拍"},
    },
    "soft": {
        "color": {"warm": 0.08, "saturation": 1.05, "contrast": 0.95, "brightness": 0.08, "vibrance": 0.05},
        "effects": ["soft_glow"],
        "meta": {"name_cn": "柔和", "category": "基础", "desc": "柔焦感，适合人像/花朵"},
    },
    "vivid": {
        "color": {"warm": 0.05, "saturation": 1.35, "contrast": 1.12, "brightness": 0.03, "vibrance": 0.3},
        "effects": [],
        "meta": {"name_cn": "鲜艳", "category": "基础", "desc": "超鲜艳色彩，适合旅行"},
    },
    "vintage_warm": {
        "color": {"warm": 0.2, "saturation": 0.9, "contrast": 1.05, "brightness": 0.03, "vibrance": -0.1},
        "effects": ["vignette", "grain"],
        "meta": {"name_cn": "复古暖", "category": "基础", "desc": "暖色复古感"},
    },
    "vintage_cool": {
        "color": {"warm": -0.15, "saturation": 0.85, "contrast": 1.08, "brightness": 0.02, "vibrance": -0.1},
        "effects": ["vignette", "grain"],
        "meta": {"name_cn": "复古冷", "category": "基础", "desc": "冷色复古感"},
    },

    # ========== 2. 电影感（10个） ==========
    "teal_orange": {
        "color": {"warm": 0.08, "saturation": 1.15, "contrast": 1.18, "brightness": -0.02, "vibrance": 0.2,
                  "colorbalance": {"rs": 0.08, "bs": -0.1, "rm": 0.05, "bm": -0.08}},
        "lut": {"file": "teal_orange.cube", "strength": 0.7},
        "effects": ["vignette", "grain_light"],
        "meta": {"name_cn": "青橙色调", "category": "电影感", "desc": "经典电影青橙调色"},
    },
    "cinematic": {
        "color": {"warm": 0.05, "saturation": 1.08, "contrast": 1.2, "brightness": -0.03, "vibrance": 0.1},
        "lut": {"file": "cinematic.cube", "strength": 0.7},
        "effects": ["vignette", "grain_light"],
        "meta": {"name_cn": "电影感", "category": "电影感", "desc": "通用电影调色"},
    },
    "moody_green": {
        "color": {"warm": -0.08, "saturation": 1.0, "contrast": 1.15, "brightness": -0.05, "vibrance": 0.0,
                  "colorbalance": {"gs": 0.08, "bs": 0.05}},
        "lut": {"file": "moody_green.cube", "strength": 0.6},
        "effects": ["vignette"],
        "meta": {"name_cn": "墨绿", "category": "电影感", "desc": "暗调墨绿色调"},
    },
    "dark_cinema": {
        "color": {"warm": -0.05, "saturation": 0.95, "contrast": 1.25, "brightness": -0.08, "vibrance": -0.05},
        "lut": {"file": "dark_cinema.cube", "strength": 0.7},
        "effects": ["vignette_strong", "grain"],
        "meta": {"name_cn": "暗调电影", "category": "电影感", "desc": "暗调悬疑感"},
    },
    "orange_cinema": {
        "color": {"warm": 0.18, "saturation": 1.1, "contrast": 1.12, "brightness": 0.02, "vibrance": 0.15},
        "lut": {"file": "orange_cinema.cube", "strength": 0.65},
        "effects": ["vignette", "grain_light"],
        "meta": {"name_cn": "橘色电影", "category": "电影感", "desc": "暖橘色电影调"},
    },
    "hollywood": {
        "color": {"warm": 0.06, "saturation": 1.12, "contrast": 1.22, "brightness": -0.02, "vibrance": 0.18},
        "lut": {"file": "hollywood.cube", "strength": 0.75},
        "effects": ["vignette", "grain_light"],
        "meta": {"name_cn": "好莱坞", "category": "电影感", "desc": "好莱坞大片调色"},
    },
    "suspense": {
        "color": {"warm": -0.12, "saturation": 0.85, "contrast": 1.2, "brightness": -0.08, "vibrance": -0.1},
        "lut": {"file": "suspense.cube", "strength": 0.7},
        "effects": ["vignette_strong", "grain"],
        "meta": {"name_cn": "悬疑", "category": "电影感", "desc": "冷暗悬疑感"},
    },
    "scifi": {
        "color": {"warm": -0.15, "saturation": 1.0, "contrast": 1.18, "brightness": 0.0, "vibrance": 0.1,
                  "colorbalance": {"bs": 0.12, "gs": 0.05}},
        "lut": {"file": "scifi.cube", "strength": 0.6},
        "effects": ["grain_light"],
        "meta": {"name_cn": "科幻", "category": "电影感", "desc": "蓝调科技感"},
    },
    "war": {
        "color": {"warm": 0.12, "saturation": 0.8, "contrast": 1.2, "brightness": -0.05, "vibrance": -0.15},
        "lut": {"file": "war.cube", "strength": 0.7},
        "effects": ["vignette_strong", "grain"],
        "meta": {"name_cn": "战争", "category": "电影感", "desc": "沙漠黄沙感"},
    },
    "noir": {
        "color": {"saturation": 0.0, "contrast": 1.35, "brightness": -0.03, "vibrance": 0.0},
        "effects": ["vignette_strong", "grain"],
        "meta": {"name_cn": "黑色电影", "category": "电影感", "desc": "黑白高对比"},
    },

    # ========== 3. 胶片质感（10个） ==========
    "kodak_portra": {
        "color": {"warm": 0.1, "saturation": 1.0, "contrast": 1.05, "brightness": 0.03, "vibrance": 0.05},
        "lut": {"file": "kodak_portra.cube", "strength": 0.8},
        "effects": ["grain_light", "vignette"],
        "meta": {"name_cn": "Kodak Portra", "category": "胶片", "desc": "经典人像胶片"},
    },
    "fuji_400h": {
        "color": {"warm": -0.05, "saturation": 0.95, "contrast": 1.0, "brightness": 0.05, "vibrance": 0.0,
                  "colorbalance": {"gs": 0.05}},
        "lut": {"file": "fuji_400h.cube", "strength": 0.8},
        "effects": ["grain_light"],
        "meta": {"name_cn": "Fuji 400H", "category": "胶片", "desc": "日系胶片质感"},
    },
    "film_grain": {
        "color": {"warm": 0.08, "saturation": 1.05, "contrast": 1.08, "brightness": 0.02, "vibrance": 0.05},
        "effects": ["grain", "vignette"],
        "meta": {"name_cn": "胶片颗粒", "category": "胶片", "desc": "颗粒感胶片"},
    },
    "light_leak_film": {
        "color": {"warm": 0.12, "saturation": 1.1, "contrast": 1.05, "brightness": 0.05, "vibrance": 0.1},
        "effects": ["grain_light", "light_leak", "vignette"],
        "meta": {"name_cn": "漏光胶片", "category": "胶片", "desc": "漏光复古胶片"},
    },
    "faded_film": {
        "color": {"warm": 0.08, "saturation": 0.75, "contrast": 0.95, "brightness": 0.08, "vibrance": -0.1},
        "effects": ["grain_light", "vignette"],
        "meta": {"name_cn": "褪色胶片", "category": "胶片", "desc": "褪色怀旧感"},
    },
    "polaroid": {
        "color": {"warm": 0.15, "saturation": 0.9, "contrast": 0.98, "brightness": 0.06, "vibrance": -0.05},
        "effects": ["grain_light", "vignette_strong", "light_leak"],
        "meta": {"name_cn": "宝丽来", "category": "胶片", "desc": "即影即有感"},
    },
    "film_vignette": {
        "color": {"warm": 0.06, "saturation": 1.05, "contrast": 1.1, "brightness": 0.0, "vibrance": 0.05},
        "effects": ["vignette_strong", "grain_light"],
        "meta": {"name_cn": "胶片暗角", "category": "胶片", "desc": "强暗角胶片感"},
    },
    "slide_film": {
        "color": {"warm": 0.05, "saturation": 1.25, "contrast": 1.15, "brightness": 0.0, "vibrance": 0.2},
        "effects": ["grain_light"],
        "meta": {"name_cn": "反转片", "category": "胶片", "desc": "高饱和反转片"},
    },
    "negative_film": {
        "color": {"warm": -0.05, "saturation": 1.1, "contrast": 1.08, "brightness": 0.03, "vibrance": 0.1,
                  "colorbalance": {"rs": -0.05, "bs": 0.08}},
        "effects": ["grain_light"],
        "meta": {"name_cn": "负片", "category": "胶片", "desc": "负片冲印感"},
    },
    "old_photo": {
        "color": {"warm": 0.25, "saturation": 0.3, "contrast": 1.1, "brightness": 0.05, "vibrance": -0.3},
        "effects": ["grain", "vignette_strong"],
        "meta": {"name_cn": "老照片", "category": "胶片", "desc": "泛黄老照片"},
    },

    # ========== 4. 风格化（10个） ==========
    "japanese": {
        "color": {"warm": -0.05, "saturation": 0.9, "contrast": 0.98, "brightness": 0.08, "vibrance": -0.05},
        "effects": ["soft_glow"],
        "meta": {"name_cn": "日系", "category": "风格化", "desc": "低饱和明亮日系"},
    },
    "korean": {
        "color": {"warm": 0.05, "saturation": 1.0, "contrast": 1.02, "brightness": 0.06, "vibrance": 0.05},
        "effects": ["soft_glow"],
        "meta": {"name_cn": "韩系", "category": "风格化", "desc": "清透韩系"},
    },
    "ins_style": {
        "color": {"warm": 0.08, "saturation": 1.1, "contrast": 1.08, "brightness": 0.05, "vibrance": 0.15},
        "effects": ["vignette_light"],
        "meta": {"name_cn": "ins风", "category": "风格化", "desc": "Instagram 风格"},
    },
    "hongkong": {
        "color": {"warm": 0.18, "saturation": 1.05, "contrast": 1.15, "brightness": -0.03, "vibrance": 0.05},
        "effects": ["grain_light", "vignette"],
        "meta": {"name_cn": "港风", "category": "风格化", "desc": "王家卫式港风"},
    },
    "europe": {
        "color": {"warm": -0.08, "saturation": 1.05, "contrast": 1.12, "brightness": 0.0, "vibrance": 0.1},
        "effects": ["vignette_light"],
        "meta": {"name_cn": "欧美", "category": "风格化", "desc": "冷调欧美风"},
    },
    "cyberpunk": {
        "color": {"warm": -0.2, "saturation": 1.3, "contrast": 1.25, "brightness": -0.05, "vibrance": 0.3,
                  "colorbalance": {"rs": 0.1, "bs": 0.15, "rm": 0.08, "bm": 0.1}},
        "effects": ["grain_light"],
        "meta": {"name_cn": "赛博朋克", "category": "风格化", "desc": "霓虹赛博朋克"},
    },
    "glitch": {
        "color": {"saturation": 1.2, "contrast": 1.3, "brightness": 0.0, "vibrance": 0.2,
                  "colorbalance": {"rs": 0.1, "bs": 0.1}},
        "effects": ["glitch", "grain_light"],
        "meta": {"name_cn": "故障", "category": "风格化", "desc": "故障艺术风"},
    },
    "vaporwave": {
        "color": {"warm": -0.1, "saturation": 1.25, "contrast": 1.1, "brightness": 0.05, "vibrance": 0.25,
                  "colorbalance": {"rs": 0.12, "bs": 0.1}},
        "effects": ["grain_light"],
        "meta": {"name_cn": "蒸汽波", "category": "风格化", "desc": "粉紫蒸汽波"},
    },
    "pastel": {
        "color": {"warm": 0.08, "saturation": 0.85, "contrast": 0.95, "brightness": 0.1, "vibrance": -0.05},
        "effects": ["soft_glow"],
        "meta": {"name_cn": "柔色", "category": "风格化", "desc": "马卡龙柔色"},
    },
    "instant": {
        "color": {"warm": 0.12, "saturation": 1.0, "contrast": 1.05, "brightness": 0.05, "vibrance": 0.05},
        "effects": ["grain_light", "vignette", "light_leak"],
        "meta": {"name_cn": "拍立得", "category": "风格化", "desc": "拍立得相纸感"},
    },

    # ========== 5. 黑白（5个） ==========
    "bw_classic": {
        "color": {"saturation": 0.0, "contrast": 1.15, "brightness": 0.02, "vibrance": 0.0},
        "effects": ["grain_light"],
        "meta": {"name_cn": "经典黑白", "category": "黑白", "desc": "经典黑白照"},
    },
    "bw_high_contrast": {
        "color": {"saturation": 0.0, "contrast": 1.4, "brightness": -0.02, "vibrance": 0.0},
        "effects": ["vignette"],
        "meta": {"name_cn": "高对比黑白", "category": "黑白", "desc": "强对比黑白"},
    },
    "bw_soft": {
        "color": {"saturation": 0.0, "contrast": 1.0, "brightness": 0.08, "vibrance": 0.0},
        "effects": ["soft_glow", "grain_light"],
        "meta": {"name_cn": "柔焦黑白", "category": "黑白", "desc": "柔和黑白"},
    },
    "bw_film": {
        "color": {"saturation": 0.0, "contrast": 1.2, "brightness": 0.0, "vibrance": 0.0},
        "effects": ["grain", "vignette"],
        "meta": {"name_cn": "胶片黑白", "category": "黑白", "desc": "胶片颗粒黑白"},
    },
    "bw_dark": {
        "color": {"saturation": 0.0, "contrast": 1.25, "brightness": -0.08, "vibrance": 0.0},
        "effects": ["vignette_strong", "grain"],
        "meta": {"name_cn": "暗调黑白", "category": "黑白", "desc": "暗调黑白"},
    },

    # ========== 6. 美食/风景（5个） ==========
    "food_vivid": {
        "color": {"warm": 0.1, "saturation": 1.35, "contrast": 1.12, "brightness": 0.05, "vibrance": 0.3},
        "effects": [],
        "meta": {"name_cn": "美食鲜艳", "category": "美食风景", "desc": "鲜艳美食"},
    },
    "food_warm": {
        "color": {"warm": 0.2, "saturation": 1.2, "contrast": 1.08, "brightness": 0.06, "vibrance": 0.15},
        "effects": [],
        "meta": {"name_cn": "美食暖调", "category": "美食风景", "desc": "暖调美食"},
    },
    "scenery_bright": {
        "color": {"warm": 0.05, "saturation": 1.2, "contrast": 1.05, "brightness": 0.08, "vibrance": 0.2},
        "effects": [],
        "meta": {"name_cn": "风景明亮", "category": "美食风景", "desc": "明亮风景"},
    },
    "scenery_vivid": {
        "color": {"warm": 0.03, "saturation": 1.3, "contrast": 1.1, "brightness": 0.03, "vibrance": 0.25},
        "effects": ["vignette_light"],
        "meta": {"name_cn": "风景饱和", "category": "美食风景", "desc": "高饱和风景"},
    },
    "sunset_warm": {
        "color": {"warm": 0.25, "saturation": 1.2, "contrast": 1.08, "brightness": 0.03, "vibrance": 0.15},
        "effects": ["light_leak", "vignette_light"],
        "meta": {"name_cn": "日落暖光", "category": "美食风景", "desc": "日落暖光感"},
    },

    # ========== 7. 人像（5个） ==========
    "portrait_soft": {
        "color": {"warm": 0.08, "saturation": 1.05, "contrast": 1.02, "brightness": 0.06, "vibrance": 0.05,
                  "denoise": 5},
        "effects": ["soft_glow"],
        "meta": {"name_cn": "柔肤美白", "category": "人像", "desc": "柔肤美白"},
    },
    "pink_girl": {
        "color": {"warm": 0.12, "saturation": 1.1, "contrast": 1.03, "brightness": 0.06, "vibrance": 0.1,
                  "colorbalance": {"rs": 0.08, "rm": 0.05}},
        "effects": ["soft_glow"],
        "meta": {"name_cn": "粉嫩", "category": "人像", "desc": "粉嫩少女感"},
    },
    "cool_white": {
        "color": {"warm": -0.12, "saturation": 1.0, "contrast": 1.05, "brightness": 0.08, "vibrance": 0.0},
        "effects": ["soft_glow"],
        "meta": {"name_cn": "冷白皮", "category": "人像", "desc": "冷白皮调"},
    },
    "warm_yellow": {
        "color": {"warm": 0.22, "saturation": 1.1, "contrast": 1.05, "brightness": 0.05, "vibrance": 0.1},
        "effects": [],
        "meta": {"name_cn": "暖黄皮", "category": "人像", "desc": "暖黄健康肤色"},
    },
    "portrait_natural": {
        "color": {"warm": 0.05, "saturation": 1.05, "contrast": 1.05, "brightness": 0.03, "vibrance": 0.05,
                  "denoise": 3},
        "effects": [],
        "meta": {"name_cn": "自然人像", "category": "人像", "desc": "自然人像"},
    },
}


# ---------- LUT 文件检查 ----------

def get_lut_path(lut_file: str) -> Optional[str]:
    """获取 LUT 文件路径，检查是否存在。"""
    if not lut_file:
        return None
    path = os.path.join(LUTS_DIR, lut_file)
    if os.path.exists(path):
        return path
    logger.debug("LUT 文件不存在: %s", path)
    return None


def list_available_luts() -> List[str]:
    """列出 assets/luts/ 目录下所有可用的 .cube 文件。"""
    if not os.path.isdir(LUTS_DIR):
        return []
    return [f for f in os.listdir(LUTS_DIR) if f.endswith(".cube")]


def list_available_overlays() -> List[str]:
    """列出 assets/overlays/ 目录下所有可用的素材。"""
    if not os.path.isdir(OVERLAYS_DIR):
        return []
    result = []
    for root, dirs, files in os.walk(OVERLAYS_DIR):
        for f in files:
            if f.lower().endswith((".png", ".jpg", ".webp")):
                rel = os.path.relpath(os.path.join(root, f), OVERLAYS_DIR)
                result.append(rel)
    return result


# ---------- 构建调色滤镜链 ----------

def build_color_filter(color_params: Dict, w: int = 0, h: int = 0) -> str:
    """根据调色参数构建 FFmpeg 调色滤镜链片段。

    参数:
        color_params: 调色参数 dict，支持：
            warm: 暖色调整 (-0.3 ~ 0.3)
            saturation: 饱和度 (0.0 ~ 2.0)
            contrast: 对比度 (0.5 ~ 2.0)
            brightness: 亮度 (-0.5 ~ 0.5)
            gamma: 伽马 (0.1 ~ 10.0)
            vibrance: 自然饱和度 (-1.0 ~ 1.0)
            denoise: 磨皮强度 (0 ~ 10)
            colorbalance: {"rs":, "gs":, "bs":, "rm":, "gm":, "bm":} 色彩平衡
    返回:
        FFmpeg 滤镜链字符串（逗号分隔），如 "eq=brightness=0.05:contrast=1.1,colorbalance=..."
    """
    parts = []

    # eq 滤镜（亮度/对比度/饱和度/伽马）
    eq_parts = []
    brightness = color_params.get("brightness", 0)
    contrast = color_params.get("contrast", 1.0)
    saturation = color_params.get("saturation", 1.0)
    gamma = color_params.get("gamma", 1.0)

    if brightness != 0:
        eq_parts.append(f"brightness={brightness:.3f}")
    if contrast != 1.0:
        eq_parts.append(f"contrast={contrast:.3f}")
    if saturation != 1.0:
        eq_parts.append(f"saturation={saturation:.3f}")
    if gamma != 1.0:
        eq_parts.append(f"gamma={gamma:.3f}")
    if eq_parts:
        parts.append("eq=" + ":".join(eq_parts))

    # 暖色调（colorbalance）
    warm = color_params.get("warm", 0)
    cb = color_params.get("colorbalance", {})
    if warm != 0 or cb:
        wv = warm
        cb_parts = []
        # 暖色：提红压蓝
        rs = cb.get("rs", wv * 0.5)
        gs = cb.get("gs", wv * 0.15)
        bs = cb.get("bs", -wv * 0.5)
        rm = cb.get("rm", wv * 0.3)
        gm = cb.get("gm", wv * 0.1)
        bm = cb.get("bm", -wv * 0.3)
        if rs or gs or bs:
            cb_parts.append(f"rs={rs:.3f}:gs={gs:.3f}:bs={bs:.3f}")
        if rm or gm or bm:
            cb_parts.append(f"rm={rm:.3f}:gm={gm:.3f}:bm={bm:.3f}")
        if cb_parts:
            parts.append("colorbalance=" + ":".join(cb_parts))

    # 自然饱和度（vibrance）
    vibrance = color_params.get("vibrance", 0)
    if vibrance != 0:
        parts.append(f"vibrance=intensity={vibrance:.2f}")

    # 磨皮（hqdn3d）
    denoise = color_params.get("denoise", 0)
    if denoise > 0:
        parts.append(f"hqdn3d={denoise}:{denoise * 0.7}:{denoise * 2}:{denoise * 1.5}")

    # 锐化（unsharp）
    if color_params.get("sharpen", False):
        parts.append("unsharp=5:5:0.6:5:5:0.0")

    return ",".join(parts) if parts else None


def build_lut_filter(lut_config: Dict) -> Optional[str]:
    """构建 LUT 滤镜片段。

    参数:
        lut_config: {"file": "xxx.cube", "strength": 0.8}
    返回:
        FFmpeg 滤镜字符串，如 "lut3d=file='...':interp=trilinear"
        如果 LUT 文件不存在返回 None
    """
    if not lut_config:
        return None
    lut_file = lut_config.get("file")
    if not lut_file:
        return None
    lut_path = get_lut_path(lut_file)
    if not lut_path:
        logger.debug("LUT 不存在，跳过: %s", lut_file)
        return None

    # FFmpeg 路径转义（反斜杠→正斜杠，冒号→\:）
    lut_esc = lut_path.replace("\\", "/").replace(":", "\\:")
    strength = lut_config.get("strength", 1.0)

    if strength >= 1.0:
        return f"lut3d=file='{lut_esc}':interp=trilinear"
    else:
        # strength < 1.0 时，用 blend 混合原画面和 LUT 画面
        # 需要在 filter_complex 中用 split + blend 实现，这里返回基础片段
        # 调用方需处理混合逻辑
        return f"lut3d=file='{lut_esc}':interp=trilinear"


def build_lut_filter_with_strength(lut_config: Dict, cur_label: str,
                                     input_idx: int = 0,
                                     intensity: float = 1.0) -> Tuple[Optional[str], Optional[str], str]:
    """构建带强度控制的 LUT 滤镜（用 split + blend 实现）。

    参数:
        lut_config: {"file": "xxx.cube", "strength": 0.8}
        cur_label: 当前流标签
        input_idx: 用于生成新标签的序号
        intensity: 全局强度倍数 (0-2)，乘到预设 strength 上
    返回:
        (filter_fragment, new_label, blend_label)
        filter_fragment: 要追加到 filter_complex 的片段
        new_label: LUT 处理后的流标签
        blend_label: 混合后的流标签（如果 strength < 1.0）
        如果 LUT 文件不存在返回 (None, cur_label, cur_label)
    """
    if not lut_config:
        return None, cur_label, cur_label
    lut_file = lut_config.get("file")
    if not lut_file:
        return None, cur_label, cur_label
    lut_path = get_lut_path(lut_file)
    if not lut_path:
        return None, cur_label, cur_label

    lut_esc = lut_path.replace("\\", "/").replace(":", "\\:")
    # 全局 intensity 倍数作用到预设 strength 上，clamp 到 [0, 1]
    base_strength = lut_config.get("strength", 1.0)
    strength = max(0.0, min(1.0, base_strength * intensity))
    lut_label = f"lut{input_idx}"
    orig_label = f"lutorig{input_idx}"
    blend_label = f"lutblend{input_idx}"

    if strength >= 1.0:
        # 直接应用 LUT
        fragment = f"[{cur_label}]lut3d=file='{lut_esc}':interp=trilinear[{lut_label}]"
        return fragment, lut_label, lut_label
    else:
        # split → 一路 LUT，一路原样 → blend 混合
        fragment = (
            f"[{cur_label}]split=2[{orig_label}][{lut_label}_raw];"
            f"[{lut_label}_raw]lut3d=file='{lut_esc}':interp=trilinear[{lut_label}];"
            f"[{orig_label}][{lut_label}]blend=all_mode=overlay:all_opacity={strength:.2f}[{blend_label}]"
        )
        return fragment, lut_label, blend_label


# ---------- 构建特效滤镜 ----------

def build_effect_filter(effect_name: str, cur_label: str, w: int, h: int,
                         duration: float, input_idx: int = 0,
                         intensity: float = 1.0) -> Tuple[Optional[str], str]:
    """构建单个特效的滤镜片段（用于滤镜预设内置特效）。

    参数:
        effect_name: 特效名称
        cur_label: 当前流标签
        w, h: 视频宽高
        duration: 视频时长
        input_idx: 用于生成新标签的序号
        intensity: 全局强度倍数 (0-2)，作用于 opacity/混合强度
    返回:
        (filter_fragment, new_label)
        filter_fragment: 要追加到 filter_complex 的片段
        new_label: 处理后的新流标签
        如果特效不支持返回 (None, cur_label)
    """
    new_label = f"eff{input_idx}_{effect_name}"
    # clamp intensity 到 [0, 2]
    inten = max(0.0, min(2.0, float(intensity)))

    if effect_name == "vignette":
        # 暗角（中等强度）
        fragment = f"[{cur_label}]vignette=PI/{5.5}:mode=forward[{new_label}]"
        return fragment, new_label

    elif effect_name == "vignette_light":
        # 轻微暗角
        fragment = f"[{cur_label}]vignette=PI/{6.5}:mode=forward[{new_label}]"
        return fragment, new_label

    elif effect_name == "vignette_strong":
        # 强暗角
        fragment = f"[{cur_label}]vignette=PI/{4.0}:mode=forward[{new_label}]"
        return fragment, new_label

    elif effect_name == "grain":
        # 胶片颗粒（强），强度受 intensity 影响
        alls = max(1, int(12 * inten))
        fragment = f"[{cur_label}]noise=alls={alls}:allf=t[{new_label}]"
        return fragment, new_label

    elif effect_name == "grain_light":
        # 胶片颗粒（轻）
        alls = max(1, int(5 * inten))
        fragment = f"[{cur_label}]noise=alls={alls}:allf=t[{new_label}]"
        return fragment, new_label

    elif effect_name == "soft_glow":
        # 柔焦光晕（复制→模糊→叠加）
        glow_label = f"glow{input_idx}"
        opacity = max(0.0, min(1.0, 0.25 * inten))
        fragment = (
            f"[{cur_label}]split=2[{cur_label}_a][{glow_label}_raw];"
            f"[{glow_label}_raw]gblur=sigma=15[{glow_label}];"
            f"[{cur_label}_a][{glow_label}]blend=all_mode=screen:all_opacity={opacity:.2f}[{new_label}]"
        )
        return fragment, new_label

    elif effect_name == "light_leak":
        # 漏光效果（用 geq 生成渐变 + blend=screen）
        leak_label = f"leak{input_idx}"
        # 生成一个暖色渐变（右上角偏亮）
        cx = int(w * 0.85)
        cy = int(h * 0.15)
        r = int(max(w, h) * 0.4)
        opacity = max(0.0, min(1.0, 0.4 * inten))
        fragment = (
            f"color=c=black:s={w}x{h}:d={duration},"
            f"geq=r='min(255,255*exp(-((X-{cx})^2+(Y-{cy})^2)/(2*{r}^2))*1.2)':"
            f"g='min(255,200*exp(-((X-{cx})^2+(Y-{cy})^2)/(2*{r}^2))*1.0)':"
            f"b='min(255,100*exp(-((X-{cx})^2+(Y-{cy})^2)/(2*{r}^2))*0.8)',"
            f"format=rgba[{leak_label}];"
            f"[{cur_label}][{leak_label}]blend=all_mode=screen:all_opacity={opacity:.2f}[{new_label}]"
        )
        return fragment, new_label

    elif effect_name == "glitch":
        # 故障效果（随机扫描线偏移）
        glitch_label = f"glitch{input_idx}"
        fragment = (
            f"[{cur_label}]scroll=horizontal=0.005:vertical=0.002[{new_label}]"
        )
        return fragment, new_label

    else:
        logger.warning("未知特效: %s", effect_name)
        return None, cur_label


# ---------- 构建完整滤镜链 ----------

def build_filter_chain(preset_name: str, cur_label: str, w: int, h: int,
                       duration: float,
                       intensity: float = 1.0) -> Tuple[str, str]:
    """根据预设名构建完整的滤镜链。

    参数:
        preset_name: 预设名（如 "teal_orange"）
        cur_label: 输入流标签
        w, h: 视频宽高
        duration: 视频时长
        intensity: 全局强度倍数 (0-2)，作用于 LUT strength 和特效 opacity
    返回:
        (filter_fragment, new_label)
        filter_fragment: 追加到 filter_complex 的完整片段
        new_label: 最终输出的流标签
    """
    preset = FILTER_PRESETS.get(preset_name)
    if not preset:
        logger.warning("未知滤镜预设: %s", preset_name)
        return "", cur_label

    fragments = []
    current = cur_label
    idx = 0

    # 1. 原生调色
    color_params = preset.get("color", {})
    color_filter = build_color_filter(color_params, w, h)
    if color_filter:
        new_label = f"color{idx}"
        fragments.append(f"[{current}]{color_filter}[{new_label}]")
        current = new_label
        idx += 1

    # 2. LUT（带强度控制，受 intensity 影响）
    lut_config = preset.get("lut")
    if lut_config:
        lut_frag, lut_label, blend_label = build_lut_filter_with_strength(
            lut_config, current, idx, intensity=intensity)
        if lut_frag:
            fragments.append(lut_frag)
            current = blend_label
            idx += 1

    # 3. 特效（预设内置特效，受 intensity 影响）
    effects = preset.get("effects", [])
    for effect_name in effects:
        eff_frag, eff_label = build_effect_filter(
            effect_name, current, w, h, duration, idx, intensity=intensity)
        if eff_frag:
            fragments.append(eff_frag)
            current = eff_label
            idx += 1

    return ";".join(fragments), current


# ---------- 预设查询 ----------

def get_preset(name: str) -> Optional[Dict]:
    """获取预设配置。"""
    return FILTER_PRESETS.get(name)


def list_presets(category: str = None) -> List[Dict]:
    """列出所有预设（可按分类过滤）。

    返回: [{"name": "teal_orange", "name_cn": "青橙色调", "category": "电影感", "desc": "...", "has_lut": True}, ...]
    """
    result = []
    for name, preset in FILTER_PRESETS.items():
        meta = preset.get("meta", {})
        if category and meta.get("category") != category:
            continue
        result.append({
            "name": name,
            "name_cn": meta.get("name_cn", name),
            "category": meta.get("category", ""),
            "desc": meta.get("desc", ""),
            "has_lut": bool(preset.get("lut")),
            "lut_available": bool(get_lut_path(preset.get("lut", {}).get("file", ""))) if preset.get("lut") else False,
            "effects": preset.get("effects", []),
        })
    return result


def list_categories() -> List[str]:
    """列出所有预设分类。"""
    cats = set()
    for preset in FILTER_PRESETS.values():
        meta = preset.get("meta", {})
        cat = meta.get("category")
        if cat:
            cats.add(cat)
    return sorted(cats)


def print_presets(category: str = None):
    """打印预设列表（用于 --list-filters CLI）。"""
    presets = list_presets(category)
    if not presets:
        print("无可用预设")
        return

    # 按分类分组
    by_cat = {}
    for p in presets:
        by_cat.setdefault(p["category"], []).append(p)

    for cat in sorted(by_cat.keys()):
        print(f"\n【{cat}】")
        for p in by_cat[cat]:
            lut_tag = ""
            if p["has_lut"]:
                lut_tag = " [LUT]" if p["lut_available"] else " [LUT未下载]"
            eff_tag = f" +{','.join(p['effects'])}" if p["effects"] else ""
            print(f"  {p['name']:<20} {p['name_cn']:<10}{lut_tag}{eff_tag}")
            if p["desc"]:
                print(f"{'':>24}{p['desc']}")

    total_luts = sum(1 for p in presets if p["has_lut"])
    available_luts = sum(1 for p in presets if p["lut_available"])
    print(f"\n共 {len(presets)} 个预设（{total_luts} 个需要 LUT，{available_luts} 个 LUT 已就绪）")
    if available_luts < total_luts:
        print(f"提示: {total_luts - available_luts} 个 LUT 文件缺失，预设仍可用但会跳过 LUT 调色")
        print(f"      LUT 目录: {LUTS_DIR}")


# ============================================================
# 独立特效系统（类似剪映特效，用 --effect 参数叠加）
# ============================================================
#
# 与滤镜预设的区别：
#   - 滤镜预设（FILTER_PRESETS）：调色 + LUT + 内置特效的套餐，一次只能选一个
#   - 独立特效（EFFECT_PRESETS）：单独的视觉特效，可多次 --effect 叠加
#
# 特效分类（44 个）：
#   1. 画面特效（10）：模糊/锐化/像素化/马赛克/RGB分离/故障/抖动/缩放呼吸/镜像/水波纹
#   2. 光影特效（10）：暖色漏光/冷色漏光/日落漏光/粉色漏光/暖色扫光/冷色扫光/白色扫光/镜头光晕/丁达尔光柱/辉光
#   3. 氛围粒子（10）：雪花/雨滴/樱花飘落/白色星空/金色星空/暖色光斑/冷色光斑/灰尘/气泡/烟花
#   4. 边框装饰（9）：胶片框/拍立得框/白色粗框/白色细框/黑色细框/轻暗角/中暗角/强暗角/电影遮幅
#   5. 复古胶片（5）：VHS/老电影/胶片划痕/闪烁/高光泛光

EFFECT_PRESETS = {
    # ========== 1. 画面特效（10个） - FFmpeg 原生 ==========
    "blur": {
        "type": "ffmpeg",
        "filter": "gblur=sigma=5",
        "meta": {"name_cn": "高斯模糊", "category": "画面", "desc": "整体模糊，梦幻感"},
    },
    "sharpen": {
        "type": "ffmpeg",
        "filter": "unsharp=5:5:1.0:5:5:0.0",
        "meta": {"name_cn": "锐化", "category": "画面", "desc": "增强边缘清晰度"},
    },
    "pixelate": {
        "type": "ffmpeg",
        "filter": "scale={w}/20:{h}/20,scale={w}:{h}:flags=neighbor",
        "meta": {"name_cn": "像素化", "category": "画面", "desc": "马赛克像素效果"},
    },
    "mosaic": {
        "type": "ffmpeg",
        "filter": "scale={w}/40:{h}/40,scale={w}:{h}:flags=neighbor",
        "meta": {"name_cn": "马赛克", "category": "画面", "desc": "大块马赛克遮挡"},
    },
    "rgb_split": {
        "type": "ffmpeg",
        "filter": "chromashift=cbh=4:cbv=2:crh=-4:crv=-2",
        "meta": {"name_cn": "RGB分离", "category": "画面", "desc": "色彩通道偏移，迷幻感"},
    },
    "glitch": {
        "type": "ffmpeg",
        "filter": "scroll=horizontal=0.005:vertical=0.002,noise=alls=8:allf=t",
        "meta": {"name_cn": "故障艺术", "category": "画面", "desc": "扫描线偏移 + 噪点"},
    },
    "shake": {
        "type": "ffmpeg",
        "filter": "scroll=horizontal=0.02:vertical=0.01",
        "meta": {"name_cn": "画面抖动", "category": "画面", "desc": "轻微抖动，动感"},
    },
    "zoom_pulse": {
        "type": "ffmpeg",
        "filter": "zoompan=z='min(zoom+0.0015,1.5)':d=1:s={w}x{h}:fps=30",
        "meta": {"name_cn": "缩放呼吸", "category": "画面", "desc": "缓慢放大，呼吸感"},
    },
    "mirror": {
        "type": "ffmpeg",
        "filter": "crop=iw/2:ih:0:0,split=2[m_a][m_b];[m_b]hflip[m_b2];[m_a][m_b2]hstack",
        "meta": {"name_cn": "镜像", "category": "画面", "desc": "左半屏镜像到右半屏"},
    },
    "wave": {
        "type": "ffmpeg",
        "filter": "geq=lum='lum(X,sin(T/2+X/30)*5+Y)':cb='cb(X,sin(T/2+X/30)*5+Y)':cr='cr(X,sin(T/2+X/30)*5+Y)'",
        "meta": {"name_cn": "水波纹", "category": "画面", "desc": "水波荡漾效果"},
    },

    # ========== 2. 光影特效（10个） - PNG overlay + geq ==========
    "light_leak_warm": {
        "type": "png_overlay",
        "png": "light_leaks/leak_warm_tr.png",
        "mode": "screen",
        "opacity": 0.6,
        "meta": {"name_cn": "暖色漏光", "category": "光影", "desc": "右上角暖色漏光，胶片感"},
    },
    "light_leak_cool": {
        "type": "png_overlay",
        "png": "light_leaks/leak_cool_tr.png",
        "mode": "screen",
        "opacity": 0.6,
        "meta": {"name_cn": "冷色漏光", "category": "光影", "desc": "冷色漏光，清冷感"},
    },
    "light_leak_sunset": {
        "type": "png_overlay",
        "png": "light_leaks/leak_sunset.png",
        "mode": "screen",
        "opacity": 0.65,
        "meta": {"name_cn": "日落漏光", "category": "光影", "desc": "日落暖橙漏光"},
    },
    "light_leak_pink": {
        "type": "png_overlay",
        "png": "light_leaks/leak_pink_tr.png",
        "mode": "screen",
        "opacity": 0.55,
        "meta": {"name_cn": "粉色漏光", "category": "光影", "desc": "粉色漏光，少女感"},
    },
    "sweep_warm": {
        "type": "png_overlay_anim",
        "png": "sweeps/sweep_warm.png",
        "mode": "screen",
        "opacity": 0.5,
        "anim": "sweep_lr",
        "meta": {"name_cn": "暖色扫光", "category": "光影", "desc": "暖色光条从左到右扫过"},
    },
    "sweep_cool": {
        "type": "png_overlay_anim",
        "png": "sweeps/sweep_cool.png",
        "mode": "screen",
        "opacity": 0.5,
        "anim": "sweep_lr",
        "meta": {"name_cn": "冷色扫光", "category": "光影", "desc": "冷色光条从左到右扫过"},
    },
    "sweep_white": {
        "type": "png_overlay_anim",
        "png": "sweeps/sweep_white.png",
        "mode": "screen",
        "opacity": 0.45,
        "anim": "sweep_lr",
        "meta": {"name_cn": "白色扫光", "category": "光影", "desc": "白色光条扫过，运镜感"},
    },
    "lens_flare": {
        "type": "geq_anim",
        "geq_kind": "lens_flare",
        "mode": "screen",
        "opacity": 0.7,
        "meta": {"name_cn": "镜头光晕", "category": "光影", "desc": "镜头光晕，从左下到右上移动"},
    },
    "god_rays": {
        "type": "geq_anim",
        "geq_kind": "god_rays",
        "mode": "screen",
        "opacity": 0.4,
        "meta": {"name_cn": "丁达尔光柱", "category": "光影", "desc": "从上方倾泻的光柱"},
    },
    "bloom": {
        "type": "ffmpeg_blend",
        "filter_inner": "gblur=sigma=8",
        "mode": "screen",
        "opacity": 0.35,
        "meta": {"name_cn": "辉光", "category": "光影", "desc": "高光泛出柔光"},
    },

    # ========== 3. 氛围粒子（10个） - geq 生成 + PNG ==========
    "snow": {
        "type": "geq_anim",
        "geq_kind": "snow",
        "mode": "screen",
        "opacity": 0.9,
        "meta": {"name_cn": "雪花飘落", "category": "氛围粒子", "desc": "雪花从上方飘落"},
    },
    "rain": {
        "type": "geq_anim",
        "geq_kind": "rain",
        "mode": "screen",
        "opacity": 0.7,
        "meta": {"name_cn": "雨滴", "category": "氛围粒子", "desc": "斜向雨滴"},
    },
    "petals": {
        "type": "geq_anim",
        "geq_kind": "petals",
        "mode": "screen",
        "opacity": 0.8,
        "meta": {"name_cn": "樱花飘落", "category": "氛围粒子", "desc": "粉色樱花花瓣飘落"},
    },
    "stars_white": {
        "type": "png_overlay_anim",
        "png": "particles/stars_white.png",
        "mode": "screen",
        "opacity": 0.7,
        "anim": "twinkle",
        "meta": {"name_cn": "白色星空", "category": "氛围粒子", "desc": "白色星星闪烁"},
    },
    "stars_gold": {
        "type": "png_overlay_anim",
        "png": "particles/stars_gold.png",
        "mode": "screen",
        "opacity": 0.75,
        "anim": "twinkle",
        "meta": {"name_cn": "金色星空", "category": "氛围粒子", "desc": "金色星星闪烁"},
    },
    "bokeh_warm": {
        "type": "png_overlay_anim",
        "png": "particles/bokeh_warm.png",
        "mode": "screen",
        "opacity": 0.5,
        "anim": "sway",
        "meta": {"name_cn": "暖色光斑", "category": "氛围粒子", "desc": "暖色光斑左右摇摆"},
    },
    "bokeh_cool": {
        "type": "png_overlay_anim",
        "png": "particles/bokeh_cool.png",
        "mode": "screen",
        "opacity": 0.5,
        "anim": "sway",
        "meta": {"name_cn": "冷色光斑", "category": "氛围粒子", "desc": "冷色光斑左右摇摆"},
    },
    "dust": {
        "type": "png_overlay_anim",
        "png": "particles/dust_1.png",
        "mode": "screen",
        "opacity": 0.55,
        "anim": "fall",
        "meta": {"name_cn": "灰尘飘落", "category": "氛围粒子", "desc": "灰尘颗粒缓慢飘落"},
    },
    "bubbles": {
        "type": "geq_anim",
        "geq_kind": "bubbles",
        "mode": "screen",
        "opacity": 0.6,
        "meta": {"name_cn": "气泡上升", "category": "氛围粒子", "desc": "透明气泡从下往上升"},
    },
    "fireworks": {
        "type": "geq_anim",
        "geq_kind": "fireworks",
        "mode": "screen",
        "opacity": 0.85,
        "meta": {"name_cn": "烟花", "category": "氛围粒子", "desc": "烟花绽放"},
    },

    # ========== 4. 边框装饰（9个） - PNG overlay + drawbox ==========
    "frame_film": {
        "type": "png_overlay",
        "png": "frames/frame_film.png",
        "mode": "normal",
        "opacity": 1.0,
        "meta": {"name_cn": "胶片框", "category": "边框", "desc": "胶片边框，复古感"},
    },
    "frame_polaroid": {
        "type": "png_overlay",
        "png": "frames/frame_polaroid.png",
        "mode": "normal",
        "opacity": 1.0,
        "meta": {"name_cn": "拍立得框", "category": "边框", "desc": "拍立得相纸边框"},
    },
    "frame_white_thick": {
        "type": "png_overlay",
        "png": "frames/frame_white_thick.png",
        "mode": "normal",
        "opacity": 1.0,
        "meta": {"name_cn": "白色粗框", "category": "边框", "desc": "白色粗边框，ins风"},
    },
    "frame_white_thin": {
        "type": "png_overlay",
        "png": "frames/frame_white_thin.png",
        "mode": "normal",
        "opacity": 1.0,
        "meta": {"name_cn": "白色细框", "category": "边框", "desc": "白色细边框，简洁"},
    },
    "frame_black_thin": {
        "type": "png_overlay",
        "png": "frames/frame_black_thin.png",
        "mode": "normal",
        "opacity": 1.0,
        "meta": {"name_cn": "黑色细框", "category": "边框", "desc": "黑色细边框，电影感"},
    },
    "vignette_light": {
        "type": "png_overlay",
        "png": "vignettes/vignette_light.png",
        "mode": "multiply",
        "opacity": 1.0,
        "meta": {"name_cn": "轻暗角", "category": "边框", "desc": "轻微暗角"},
    },
    "vignette_medium": {
        "type": "png_overlay",
        "png": "vignettes/vignette_medium.png",
        "mode": "multiply",
        "opacity": 1.0,
        "meta": {"name_cn": "中暗角", "category": "边框", "desc": "中等暗角"},
    },
    "vignette_strong": {
        "type": "png_overlay",
        "png": "vignettes/vignette_strong.png",
        "mode": "multiply",
        "opacity": 1.0,
        "meta": {"name_cn": "强暗角", "category": "边框", "desc": "强暗角，聚光感"},
    },
    "letterbox": {
        "type": "ffmpeg",
        "filter": "drawbox=x=0:y=0:w={w}:h={h}*0.12:t=fill:c=black,drawbox=x=0:y={h}*0.88:w={w}:h={h}*0.12:t=fill:c=black",
        "meta": {"name_cn": "电影遮幅", "category": "边框", "desc": "上下黑边，电影比例"},
    },

    # ========== 5. 复古胶片（5个） - FFmpeg 原生组合 ==========
    "vhs": {
        "type": "ffmpeg",
        "filter": "noise=alls=15:allf=t,eq=saturation=1.3:contrast=1.1,drawbox=x=0:y=ih*0.5:w=iw:h=1:t=fill:c=white@0.1",
        "meta": {"name_cn": "VHS录像带", "category": "复古胶片", "desc": "VHS噪点 + 扫描线 + 偏色"},
    },
    "old_film": {
        "type": "ffmpeg",
        "filter": "noise=alls=20:allf=t,eq=saturation=0.4:contrast=1.15:brightness=0.03:gamma=1.1,colorbalance=rs=0.15:gs=0.05:bs=-0.1",
        "meta": {"name_cn": "老电影", "category": "复古胶片", "desc": "褪色 + 颗粒 + 泛黄"},
    },
    "film_scratch": {
        "type": "ffmpeg",
        "filter": "noise=alls=3:allf=t,crop=iw:ih:0:0",
        "meta": {"name_cn": "胶片划痕", "category": "复古胶片", "desc": "轻微划痕噪点"},
    },
    "flicker": {
        "type": "ffmpeg",
        "filter": "eq=brightness='0.05+0.03*sin(t*8)'",
        "meta": {"name_cn": "闪烁", "category": "复古胶片", "desc": "亮度周期性闪烁"},
    },
    "halation": {
        "type": "ffmpeg_blend",
        "filter_inner": "eq=brightness=0.15:contrast=1.2,gblur=sigma=12",
        "mode": "screen",
        "opacity": 0.4,
        "meta": {"name_cn": "高光泛光", "category": "复古胶片", "desc": "高光区域泛红光晕（胶片特性）"},
    },
}


def get_overlay_path(png_rel: str) -> Optional[str]:
    """获取 overlays 下 PNG 文件的完整路径。"""
    if not png_rel:
        return None
    path = os.path.join(OVERLAYS_DIR, png_rel.replace("/", os.sep))
    if os.path.exists(path):
        return path
    logger.debug("特效素材不存在: %s", path)
    return None


def list_effects(category: str = None) -> List[Dict]:
    """列出所有特效（可按分类过滤）。

    返回: [{"name":, "name_cn":, "category":, "desc":, "type":, "png_available":}, ...]
    """
    result = []
    for name, eff in EFFECT_PRESETS.items():
        meta = eff.get("meta", {})
        if category and meta.get("category") != category:
            continue
        png_available = True
        if eff.get("type") in ("png_overlay", "png_overlay_anim"):
            png_available = bool(get_overlay_path(eff.get("png", "")))
        result.append({
            "name": name,
            "name_cn": meta.get("name_cn", name),
            "category": meta.get("category", ""),
            "desc": meta.get("desc", ""),
            "type": eff.get("type", ""),
            "png_available": png_available,
            "needs_input": eff.get("type") in ("png_overlay", "png_overlay_anim"),
        })
    return result


def list_effect_categories() -> List[str]:
    """列出所有特效分类。"""
    cats = set()
    for eff in EFFECT_PRESETS.values():
        meta = eff.get("meta", {})
        cat = meta.get("category")
        if cat:
            cats.add(cat)
    return sorted(cats)


def print_effects(category: str = None):
    """打印特效列表（用于 --list-effects CLI）。"""
    effects = list_effects(category)
    if not effects:
        print("无可用特效")
        return

    by_cat = {}
    for e in effects:
        by_cat.setdefault(e["category"], []).append(e)

    for cat in sorted(by_cat.keys()):
        print(f"\n【{cat}】")
        for e in by_cat[cat]:
            tag = ""
            if e["needs_input"] and not e["png_available"]:
                tag = " [素材缺失]"
            print(f"  {e['name']:<20} {e['name_cn']:<10}{tag}")
            if e["desc"]:
                print(f"{'':>24}{e['desc']}")

    total_png = sum(1 for e in effects if e["needs_input"])
    available_png = sum(1 for e in effects if e["needs_input"] and e["png_available"])
    print(f"\n共 {len(effects)} 个特效（{total_png} 个需要 PNG 素材，{available_png} 个已就绪）")
    if available_png < total_png:
        print(f"提示: {total_png - available_png} 个素材缺失，对应特效不可用")
        print(f"      素材目录: {OVERLAYS_DIR}")
        print(f"      生成命令: python assets/gen_overlays.py")


# ---------- 构建独立特效 ----------

def _build_geq_particle(kind: str, label: str, w: int, h: int,
                        duration: float) -> Optional[str]:
    """用 geq 滤镜生成粒子/光影动画素材（无需外部 PNG）。

    返回 filter_complex 片段，生成 [label] 流。
    """
    dur = max(duration, 1.0)
    if kind == "snow":
        # 雪花飘落：随机白点，Y 随时间下落循环
        return (
            f"color=c=black:s={w}x{h}:d={dur:.2f},"
            f"geq=r='if(lt(random(X+Y*W+T)*0.03,1)*lt(mod(Y+T*60,{h}),{h}),255,0)':"
            f"g='if(lt(random(X+Y*W+T)*0.03,1)*lt(mod(Y+T*60,{h}),{h}),255,0)':"
            f"b='if(lt(random(X+Y*W+T)*0.03,1)*lt(mod(Y+T*60,{h}),{h}),255,0)',"
            f"format=rgba[{label}]"
        )
    elif kind == "rain":
        # 雨滴：斜向白线
        return (
            f"color=c=black:s={w}x{h}:d={dur:.2f},"
            f"geq=r='if(lt(mod(X*0.3+Y+T*400,{h}+{w})-({h}+{w}-30),0)*255,0)':"
            f"g='if(lt(mod(X*0.3+Y+T*400,{h}+{w})-({h}+{w}-30),0)*255,0)':"
            f"b='if(lt(mod(X*0.3+Y+T*400,{h}+{w})-({h}+{w}-30),0)*255,0)',"
            f"format=rgba[{label}]"
        )
    elif kind == "petals":
        # 樱花飘落：粉色点，飘落 + 摇摆
        return (
            f"color=c=black:s={w}x{h}:d={dur:.2f},"
            f"geq=r='if(lt(random(X*7+Y*13+T)*0.02,1)*lt(mod(Y+T*40,{h}),{h}),255,0)':"
            f"g='if(lt(random(X*7+Y*13+T)*0.02,1)*lt(mod(Y+T*40,{h}),{h}),192,0)':"
            f"b='if(lt(random(X*7+Y*13+T)*0.02,1)*lt(mod(Y+T*40,{h}),{h}),203,0)',"
            f"format=rgba[{label}]"
        )
    elif kind == "lens_flare":
        # 镜头光晕：径向渐变光斑，从左下到右上移动
        cx0, cy0 = w // 5, int(h * 0.8)
        r = int(max(w, h) * 0.25)
        return (
            f"color=c=black:s={w}x{h}:d={dur:.2f},"
            f"geq=r='min(255,220*exp(-((X-({cx0}+T*{w//3}))^2+(Y-({cy0}-T*{h//2}))^2)/(2*{r}^2)))':"
            f"g='min(255,200*exp(-((X-({cx0}+T*{w//3}))^2+(Y-({cy0}-T*{h//2}))^2)/(2*{r}^2)))':"
            f"b='min(255,150*exp(-((X-({cx0}+T*{w//3}))^2+(Y-({cy0}-T*{h//2}))^2)/(2*{r}^2)))',"
            f"format=rgba[{label}]"
        )
    elif kind == "god_rays":
        # 丁达尔光柱：从上方倾斜的渐变光柱
        return (
            f"color=c=black:s={w}x{h}:d={dur:.2f},"
            f"geq=r='min(255,180*exp(-((X-{w//2}-Y*0.2)^2)/(2*({w//10})^2))*(1-Y/{h}))':"
            f"g='min(255,170*exp(-((X-{w//2}-Y*0.2)^2)/(2*({w//10})^2))*(1-Y/{h}))':"
            f"b='min(255,140*exp(-((X-{w//2}-Y*0.2)^2)/(2*({w//10})^2))*(1-Y/{h}))',"
            f"format=rgba[{label}]"
        )
    elif kind == "bubbles":
        # 气泡上升：随机点，Y 随时间上升
        return (
            f"color=c=black:s={w}x{h}:d={dur:.2f},"
            f"geq=r='if(lt(random(X*5+Y*7+T)*0.02,1)*lt(mod({h}-Y+T*50,{h}),{h}),200,0)':"
            f"g='if(lt(random(X*5+Y*7+T)*0.02,1)*lt(mod({h}-Y+T*50,{h}),{h}),220,0)':"
            f"b='if(lt(random(X*5+Y*7+T)*0.02,1)*lt(mod({h}-Y+T*50,{h}),{h}),255,0)',"
            f"format=rgba[{label}]"
        )
    elif kind == "fireworks":
        # 烟花：中心径向爆炸，周期性
        r = min(w, h) // 4
        return (
            f"color=c=black:s={w}x{h}:d={dur:.2f},"
            f"geq=r='min(255,255*exp(-((X-{w//2})^2+(Y-{h//2})^2)/(2*({r})^2*abs(sin(T*1.5))+10)))':"
            f"g='min(255,200*exp(-((X-{w//2})^2+(Y-{h//2})^2)/(2*({r})^2*abs(sin(T*1.5))+10)))':"
            f"b='min(255,100*exp(-((X-{w//2})^2+(Y-{h//2})^2)/(2*({r})^2*abs(sin(T*1.5))+10)))',"
            f"format=rgba[{label}]"
        )
    else:
        logger.warning("未知 geq 粒子类型: %s", kind)
        return None


def build_standalone_effect(effect_name: str, cur_label: str, w: int, h: int,
                             duration: float, idx: int,
                             intensity: float = 1.0,
                             input_offset: int = 0) -> Tuple[Optional[str], str, List[str]]:
    """构建单个独立特效。

    参数:
        effect_name: 特效名（EFFECT_PRESETS 的 key）
        cur_label: 当前流标签
        w, h: 视频宽高
        duration: 视频时长
        idx: 特效序号（用于生成唯一标签）
        intensity: 强度倍数 (0-2)，影响透明度
        input_offset: 已有 ffmpeg 输入数量（用于 PNG 输入索引计算）
    返回:
        (fragment, new_label, extra_inputs)
        fragment: filter_complex 片段
        new_label: 输出流标签
        extra_inputs: 需要作为 -i 添加的 PNG 路径列表
    """
    eff = EFFECT_PRESETS.get(effect_name)
    if not eff:
        logger.warning("未知特效: %s", effect_name)
        return None, cur_label, []

    new_label = f"eff{idx}_{effect_name}"
    extra_inputs: List[str] = []
    eff_type = eff.get("type")

    # 强度调整透明度
    base_opacity = eff.get("opacity", 1.0)
    opacity = max(0.0, min(1.0, base_opacity * intensity))

    if eff_type == "ffmpeg":
        # 纯 FFmpeg 滤镜，替换 {w} {h} 占位符
        filt = eff["filter"].replace("{w}", str(w)).replace("{h}", str(h))
        fragment = f"[{cur_label}]{filt}[{new_label}]"
        return fragment, new_label, []

    elif eff_type == "png_overlay":
        # 静态 PNG 叠加
        png_path = get_overlay_path(eff.get("png", ""))
        if not png_path:
            logger.warning("特效 %s 素材缺失，跳过", effect_name)
            return None, cur_label, []

        input_idx = input_offset + len(extra_inputs)
        extra_inputs.append(png_path)
        scaled_label = f"png{idx}_scaled"
        mode = eff.get("mode", "normal")

        if mode == "normal":
            # 直接 overlay（边框类）
            fragment = (
                f"[{input_idx}:v]scale={w}:{h},format=rgba,setsar=1[{scaled_label}];"
                f"[{cur_label}][{scaled_label}]overlay=0:0[{new_label}]"
            )
        else:
            # blend 混合（screen/multiply 等）
            fragment = (
                f"[{input_idx}:v]scale={w}:{h},format=rgba,setsar=1[{scaled_label}];"
                f"[{cur_label}][{scaled_label}]blend=all_mode={mode}:all_opacity={opacity:.2f}[{new_label}]"
            )
        return fragment, new_label, extra_inputs

    elif eff_type == "png_overlay_anim":
        # 动画 PNG 叠加（扫光/摇摆/飘落/闪烁）
        png_path = get_overlay_path(eff.get("png", ""))
        if not png_path:
            logger.warning("特效 %s 素材缺失，跳过", effect_name)
            return None, cur_label, []

        input_idx = input_offset + len(extra_inputs)
        extra_inputs.append(png_path)
        scaled_label = f"png{idx}_scaled"
        anim = eff.get("anim", "static")
        mode = eff.get("mode", "screen")

        # 动画位置表达式
        dur = max(duration, 1.0)
        if anim == "sweep_lr":
            # 从左到右扫过
            x_expr = f"'-{w}+{w}*t/{dur:.2f}'"
            y_expr = "0"
            fragment = (
                f"[{input_idx}:v]scale={w}:{h},format=rgba,setsar=1[{scaled_label}];"
                f"[{cur_label}][{scaled_label}]overlay=x={x_expr}:y={y_expr}[{new_label}]"
            )
        elif anim == "sway":
            # 左右摇摆
            x_expr = f"'{w//4}+sin(t/3)*{w//8}'"
            y_expr = "0"
            fragment = (
                f"[{input_idx}:v]scale={w}:{h},format=rgba,setsar=1[{scaled_label}];"
                f"[{cur_label}][{scaled_label}]overlay=x={x_expr}:y={y_expr}[{new_label}]"
            )
        elif anim == "fall":
            # 从上往下飘落（循环）
            y_expr = f"'mod(t*40,{h})'"
            x_expr = f"'{w//2}+sin(t/2)*{w//6}'"
            fragment = (
                f"[{input_idx}:v]scale={w}:{h},format=rgba,setsar=1[{scaled_label}];"
                f"[{cur_label}][{scaled_label}]overlay=x={x_expr}:y={y_expr}[{new_label}]"
            )
        elif anim == "twinkle":
            # 闪烁（用 blend + 时间相关 enable）
            # blend 不支持时间 opacity，用 overlay enable 模拟
            fragment = (
                f"[{input_idx}:v]scale={w}:{h},format=rgba,setsar=1[{scaled_label}];"
                f"[{cur_label}][{scaled_label}]blend=all_mode={mode}:all_opacity={opacity:.2f}[{new_label}]"
            )
        else:
            fragment = (
                f"[{input_idx}:v]scale={w}:{h},format=rgba,setsar=1[{scaled_label}];"
                f"[{cur_label}][{scaled_label}]overlay=0:0[{new_label}]"
            )
        return fragment, new_label, extra_inputs

    elif eff_type == "geq_anim":
        # geq 生成动画粒子（无需外部素材）
        geq_kind = eff.get("geq_kind")
        mode = eff.get("mode", "screen")
        gen_label = f"geq{idx}_gen"

        geq_frag = _build_geq_particle(geq_kind, gen_label, w, h, duration)
        if not geq_frag:
            return None, cur_label, []

        fragment = (
            f"{geq_frag};"
            f"[{cur_label}][{gen_label}]blend=all_mode={mode}:all_opacity={opacity:.2f}[{new_label}]"
        )
        return fragment, new_label, []

    elif eff_type == "ffmpeg_blend":
        # split + 滤镜 + blend（如 bloom/halation）
        inner_filter = eff.get("filter_inner", "gblur=sigma=8")
        mode = eff.get("mode", "screen")
        blend_orig = f"blend{idx}_orig"
        blend_raw = f"blend{idx}_raw"
        blend_inner = f"blend{idx}_inner"

        fragment = (
            f"[{cur_label}]split=2[{blend_orig}][{blend_raw}];"
            f"[{blend_raw}]{inner_filter}[{blend_inner}];"
            f"[{blend_orig}][{blend_inner}]blend=all_mode={mode}:all_opacity={opacity:.2f}[{new_label}]"
        )
        return fragment, new_label, []

    else:
        logger.warning("未知特效类型: %s（特效 %s）", eff_type, effect_name)
        return None, cur_label, []


def build_effects_chain(effect_names: List[str], cur_label: str, w: int, h: int,
                         duration: float, input_offset: int = 0,
                         intensity: float = 1.0) -> Tuple[str, str, List[str]]:
    """构建多个特效叠加的完整滤镜链。

    参数:
        effect_names: 特效名列表 ["snow", "light_leak_warm", ...]
        cur_label: 输入流标签
        w, h: 视频宽高
        duration: 视频时长
        input_offset: 已有 ffmpeg 输入数量（用于 PNG 输入索引）
        intensity: 特效强度倍数 (0-2)
    返回:
        (fragment, final_label, extra_inputs)
        fragment: 完整 filter_complex 片段
        final_label: 最终输出流标签
        extra_inputs: 所有需要作为 -i 添加的 PNG 路径列表
    """
    if not effect_names:
        return "", cur_label, []

    fragments = []
    current = cur_label
    all_extra_inputs: List[str] = []
    extra_input_offset = input_offset

    for idx, name in enumerate(effect_names):
        frag, new_label, extras = build_standalone_effect(
            name, current, w, h, duration, idx,
            intensity=intensity, input_offset=extra_input_offset
        )
        if frag:
            fragments.append(frag)
            current = new_label
            all_extra_inputs.extend(extras)
            extra_input_offset += len(extras)

    return ";".join(fragments), current, all_extra_inputs
