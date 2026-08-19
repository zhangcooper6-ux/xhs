# -*- coding: utf-8 -*-
"""全局配置：路径、去重参数、剪映路径。"""
import os
import sys

# ---------- 路径 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DOWNLOAD_DIR = os.path.join(OUTPUT_DIR, "_downloads")   # 原始下载素材
DEDUP_DIR = os.path.join(OUTPUT_DIR, "dedup")            # 去重后成品
JIANYING_DRAFT_DIR = os.path.join(OUTPUT_DIR, "jianying_drafts")  # 生成的剪映草稿
ASSETS_DIR = os.path.join(BASE_DIR, "assets")            # 素材目录
STICKER_DIR = os.path.join(ASSETS_DIR, "stickers")       # 贴纸素材库
TEMP_DIR = os.path.join(BASE_DIR, "temp")                # 项目临时目录（避免占用 C 盘系统临时目录）
MODELS_DIR = os.path.join(BASE_DIR, "models")            # 模型目录（whisper 等，避免占用 C 盘）
WHISPER_MODEL_DIR = os.path.join(MODELS_DIR, "whisper")  # whisper 模型目录

# ---------- 剪映路径（自动探测，可手动覆盖） ----------
def _detect_jianying_draft_root():
    """返回剪映草稿根目录。"""
    local = os.environ.get("LOCALAPPDATA", "")
    candidate = os.path.join(local, "JianyingPro", "User Data", "Projects", "com.lveditor.draft")
    if os.path.isdir(candidate):
        return candidate
    return None

JIANYING_DRAFT_ROOT = _detect_jianying_draft_root()

# ---------- 网络请求 ----------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.xiaohongshu.com/",
}

# 请求超时（秒）
REQUEST_TIMEOUT = 20
# 下载失败重试次数
MAX_RETRIES = 3

# ---------- 视频去重参数 ----------
VIDEO_DEDUP = {
    "mirror": False,           # 水平镜像（默认关闭：会颠倒视频里的文字，破坏可读性；如需开启用 --mirror）
    "speed": 1.06,             # 变速（1.0=不变；1.06=加快6%）
    "crop_ratio": 0.06,        # 四边裁剪比例（0.06=各裁掉6%，裁后放大回原尺寸）
    "scale_factor": 1.06,      # 缩放系数（裁剪后放大回原尺寸）
    "brightness": 0.04,        # 亮度调整 (-1.0~1.0)
    "contrast": 1.05,          # 对比度 (1.0=不变)
    "saturation": 1.10,        # 饱和度 (1.0=不变)
    "gamma": 1.03,             # 伽马
    "audio_pitch": True,       # 音频变调（避免音轨查重）
    "audio_tempo": 1.06,       # 音频变速（与视频同步）
    "noise_strength": 2,       # 超精细像素抖动 (2)，既打破 Hash 查重又不影响视觉画质
    "unsharp": True,           # 精细微观锐化（修复原视频模糊感）
    "hd_enhance": True,        # 画质更胜原视频模式：高清 Lanczos 采样 + Vibrance 智能色彩 + 微观对比度
    "vibrance": 0.12,          # 自然饱和度 Boost（智能提升暗淡色彩，保护自然肤色）
    "strip_metadata": True,    # 去除元数据
    "reencode": True,          # 强制重编码
    "vcodec": "libx264",
    "acodec": "aac",
    "crf": 16,                 # 提升至 CRF 16 超清无损级画质（数值越小画质越高）
    "preset": "medium",        # x264 编码预设 (medium 相比 fast 保留更多纹理细节)
}

# ---------- 图片去重参数 ----------
IMAGE_DEDUP = {
    "mirror": False,           # 水平镜像（默认关闭：会颠倒图片里的文字；如需开启用 --mirror）
    "rotate": 0,               # 旋转角度（0/90/180/270），默认不旋转
    "crop_ratio": 0.06,        # 四边裁剪（关闭镜像后加强）
    "resize_back": True,       # 裁剪后放大回原尺寸
    "brightness": 1.05,        # 亮度因子 (1.0=不变)
    "contrast": 1.06,          # 对比度因子
    "saturation": 1.12,        # 饱和度因子
    "sharpness": 1.18,         # 锐度因子
    "noise_amount": 3,         # 超轻微噪点
    "quality": 98,             # 提升至 JPEG 98 超清画质
    "subsampling": 0,          # 4:4:4 全色度无损采样（无色彩边缘模糊）
    "strip_exif": True,        # 去除 EXIF
    "format": "JPEG",          # 输出格式 JPEG/PNG
    "reencode": True,          # 强制重编码（破坏原始哈希）
}

# ---------- Live 图去重参数 ----------
LIVE_DEDUP = {
    "image": IMAGE_DEDUP,      # 静态图部分复用图片去重参数
    "video": VIDEO_DEDUP,      # 动态视频部分复用视频去重参数
}

# ---------- 小红书风格增强参数（去重后的第二步）----------
# 目标：让成品更符合小红书爆款调性（9:16竖屏 / 暖色 / 明亮 / 鲜艳 / 质感超清）
XHS_STYLE = {
    # 目标尺寸（9:16 竖屏，小红书推荐）
    "target_width": 1080,
    "target_height": 1920,

    # 色调（暖色更温馨，符合小红书调性）
    "warm": 0.12,              # 暖色调强度 0~0.3（提红提黄压蓝）
    "brightness": 0.05,        # 提亮 (-1~1)
    "contrast": 1.08,          # 对比度
    "saturation": 1.15,        # 饱和度（小红书偏好鲜艳）
    "vibrance": 0.15,          # 智能自然饱和度提升
    "gamma": 1.02,             # 伽马（轻微提亮暗部）

    # 磨皮质感与画质重塑
    "denoise": 1.0,            # 柔和轻量级空域降噪 1.0（消除压缩块效应且保护皮肤与毛发纹理）
    "unsharp": True,           # 3x3 精细微观锐化
    "hd_enhance": True,        # 超清画质突破模式

    # 背景（9:16 填充用模糊背景）
    "blur_radius": 24,         # 背景模糊半径
    "blur_power": 2,           # 模糊幂次
    "darken_bg": 0.15,         # 背景压暗 0~0.4（让前景突出）

    # 边框/留白（默认关闭，需要时开启）
    "pad": 0,                  # 外边距像素（0=不加留白）
    "pad_color": "white",
    "border_width": 0,         # 边框宽度（0=无边框）
    "border_color": "white@0.9",

    # 片头片尾淡入淡出（秒）
    "fade_in": 0.4,
    "fade_out": 0.4,

    # 水印（默认关闭，命令行 --watermark "你的小红书号" 开启）
    "watermark_size": 36,
    "watermark_color": "white@0.85",
    "watermark_shadow": "black@0.6",
    "watermark_x": "w-tw-{fontsize}",   # 右下角
    "watermark_y": "h-th-{fontsize}-20",

    # 编码
    "crf": 16,
    "preset": "medium",
}

# 风格预设（命令行 --style-profile xxx 切换）
STYLE_PROFILES = {
    "default": XHS_STYLE,                  # 通用（明亮/暖色/磨皮）
    "lifestyle": dict(XHS_STYLE, warm=0.12, saturation=1.15, denoise=3),   # 生活家居
    "food": dict(XHS_STYLE, warm=0.18, saturation=1.25, contrast=1.10, brightness=0.06),  # 美食（更暖更鲜）
    "beauty": dict(XHS_STYLE, warm=0.08, saturation=1.10, denoise=5, brightness=0.07),    # 美妆（强磨皮/偏粉嫩）
    "scenery": dict(XHS_STYLE, warm=0.05, saturation=1.20, contrast=1.05, denoise=1),     # 风景（高饱和低磨皮）
    "minimal": dict(XHS_STYLE, warm=0.0, saturation=1.02, contrast=1.05, denoise=1, brightness=0.03),  # 极简冷淡风
}

# ---------- 二创叠加层参数（自动添加贴纸/扫光/画中画/字幕条）----------
CREATIVE = {
    # 贴纸 overlay
    "stickers_enabled": True,       # 是否自动添加贴纸
    "sticker_count": 2,             # 贴纸数量（1-4）
    "sticker_opacity": 0.02,        # 贴纸透明度（0-1，默认 2% 极淡，几乎看不见但能破坏像素查重）
    "sticker_size_ratio": 0.12,     # 贴纸大小占画面宽度的比例
    "sticker_positions": [           # 贴纸出现位置（相对画面比例 x,y）
        (0.05, 0.08),   # 左上
        (0.85, 0.15),   # 右上
        (0.08, 0.85),   # 左下
        (0.80, 0.80),   # 右下
    ],
    "sticker_animate": True,        # 贴纸轻微飘动动画

    # 扫光效果
    "sweep_enabled": True,          # 是否添加扫光
    "sweep_count": 2,               # 扫光次数
    "sweep_opacity": 0.05,          # 扫光透明度（默认 5%，很淡）
    "sweep_width_ratio": 0.06,      # 扫光条宽度占画面比例
    "sweep_duration": 1.5,          # 单次扫光持续时间（秒）
    "sweep_speed": 1.0,             # 扫光速度倍率

    # 画中画（默认关闭，需 --pip 指定素材）
    "pip_enabled": False,
    "pip_scale": 0.25,              # 画中画缩放比例
    "pip_position": "bottom-right", # 位置: top-left/top-right/bottom-left/bottom-right/center
    "pip_opacity": 0.85,            # 画中画透明度
    "pip_border": 2,                # 画中画边框像素
    "pip_border_color": "white",
    "pip_rounded": True,            # 圆角

    # 字幕条（底部装饰文字条）
    "caption_enabled": True,        # 是否添加底部字幕条
    "caption_height_ratio": 0.08,   # 字幕条高度占画面比例
    "caption_bg_color": "black@0.45",  # 字幕条背景色
    "caption_text_color": "white",  # 字幕文字颜色
    "caption_fontsize": 42,         # 字幕字号
    "caption_text": "",             # 字幕文字（空=自动用笔记标题）
    "caption_style": "bottom",      # 样式: bottom(底部条) / top(顶部标题条) / none

    # 噪点纹理（极淡，破坏像素级查重）
    "noise_enabled": True,
    "noise_strength": 3,            # 噪点强度（0-100，3=极淡）
}

# ---------- 字幕处理参数（抹除原字幕 + 重新渲染）----------
SUBTITLE = {
    # 抹除原字幕
    "erase_enabled": True,          # 是否抹除原视频字幕
    "erase_mode": "blur",           # blur(高斯模糊) / delogo(修复式) / cover(纯色覆盖) / inpaint(OpenCV智能填充) / ocr-inpaint(OCR精修，最自然)
    "erase_area": {                 # 字幕区域（相对画面比例）—— auto_detect 关闭时使用此默认值
        "x": 0.0, "y": 0.75, "w": 1.0, "h": 0.20,
    },
    "auto_detect": True,            # 自动识别字幕区域（抽帧+边缘检测，覆盖 erase_area）
    "detect_frames": 5,             # 自动识别抽帧数量（3-8，越多越准但越慢）
    "cover_color": "black",         # cover 模式的覆盖色（支持 black/white/red 或 #RRGGBB）
    "cover_opacity": 0.9,           # cover 模式的覆盖透明度（0-1，1=完全不透明）
    "blur_strength": 20,            # blur 模式的模糊半径（boxblur luma_radius，1-28，越大越模糊）
    "blur_power": 3,                # blur 模式的模糊次数（boxblur luma_power，1-5，越大越模糊）
    # inpaint / ocr-inpaint 模式参数（OpenCV TELEA 逐帧修复，无模糊感）
    "inpaint_radius": 4,            # inpaint 修复半径（默认 4，越大修复范围越大但可能模糊）
    "inpaint_dilate": 2,            # mask 膨胀半径（扩大修复区域覆盖文字描边）
    "ocr_inpaint_samples": 12,      # ocr-inpaint 模式 OCR 采样帧数（构建字幕文字 mask）

    # 新字幕渲染（ASS）
    "render_enabled": True,         # 是否渲染新字幕
    "whisper_enabled": False,       # 是否用 whisper 语音识别（需 pip install -U openai-whisper）
    "whisper_model": "base",        # whisper 模型: tiny/base/small/medium（越大越准越慢）
    "style": "bold_yellow",         # 字幕样式预设（见 SUBTITLE_STYLES）
    "position": "auto",             # 字幕位置: auto(跟随原字幕) / bottom / top / center
}

# 字幕样式预设（ASS [V4+ Styles] 参数）
SUBTITLE_STYLES = {
    "default": {                    # 小红书常见：白字黑描边，底部居中
        "fontname": "黑体",
        "fontsize": 56,
        "primary_color": "white",
        "outline_color": "black",
        "back_color": "black",
        "outline": 3,
        "shadow": 1,
        "bold": True,
        "italic": False,
        "alignment": 2,             # 2=底部居中, 8=顶部居中
        "margin_v": 120,
        "background_box": False,
    },
    "bold_yellow": {                # 粗体大字，黄字黑描边（醒目）
        "fontname": "黑体",
        "fontsize": 64,
        "primary_color": "yellow",
        "outline_color": "black",
        "back_color": "black",
        "outline": 4,
        "shadow": 2,
        "bold": True,
        "italic": False,
        "alignment": 2,
        "margin_v": 120,
        "background_box": False,
    },
    "clean_box": {                  # 白字 + 半透明黑底框（清晰）
        "fontname": "微软雅黑",
        "fontsize": 52,
        "primary_color": "white",
        "outline_color": "black",
        "back_color": "black",
        "outline": 0,
        "shadow": 0,
        "bold": False,
        "italic": False,
        "alignment": 2,
        "margin_v": 80,
        "background_box": True,
    },
    "cute_pink": {                  # 粉色圆体（可爱风）
        "fontname": "微软雅黑",
        "fontsize": 54,
        "primary_color": "pink",
        "outline_color": "white",
        "back_color": "black",
        "outline": 2,
        "shadow": 1,
        "bold": True,
        "italic": False,
        "alignment": 2,
        "margin_v": 120,
        "background_box": False,
    },
    "top_title": {                  # 顶部标题条
        "fontname": "黑体",
        "fontsize": 50,
        "primary_color": "white",
        "outline_color": "black",
        "back_color": "black",
        "outline": 3,
        "shadow": 1,
        "bold": True,
        "italic": False,
        "alignment": 8,             # 8=顶部居中
        "margin_v": 80,
        "background_box": False,
    },
}

# ---------- AI 配音参数（多服务商：edge-tts / 火山引擎 Seed-TTS）----------
# 用 --voice <别名> 选择配音，--list-voices 查看全部配音（edge 26 个 + 火山引擎 24 个）
# 配音生成后会替换/混合到视频音轨
TTS = {
    "enabled": False,               # 是否启用 AI 配音（默认关闭，用 --voice 开启）
    "provider": "edge",             # TTS 服务商: edge(默认,免费) / volcengine(火山引擎Seed-TTS 2.0,需API key)
    "voice": "xiaoxiao",            # 默认配音（晓晓，最热门）。完整列表见 core/tts.py VOICE_PRESETS
                                    # 火山引擎用 bd_ 前缀别名，如 bd_xiaoyan（清新女声）
    "rate": "+0%",                  # 语速：+10%加快 / -10%减慢 / +0%正常（火山引擎自动转换为整数）
    "pitch": "+0Hz",               # 音调：+5Hz偏高 / -5Hz偏低 / +0Hz正常（火山引擎自动转换为整数）
    "volume": "+0%",                # 音量：+20%变大 / -20%变小 / +0%正常（火山引擎自动转换为整数）
    "clean_text": True,             # 清理文本（去#标签/emoji/URL，提升自然度）

    # 音频合成策略
    "keep_bg_audio": False,         # True=保留原视频音频作为BGM（降音量混合）；False=完全替换
    "bg_volume": 0.15,              # 保留BGM时的音量（0-1，0.15=15%背景音）
    "fit_duration": True,           # 调整配音时长以匹配视频时长（atempo 变速不变调）

    # 配音文本来源（按优先级）
    # 1. --voice-text 显式指定
    # 2. 笔记标题 + 描述（默认）
    "text_source": "note",          # note(笔记标题+描述) / whisper(语音识别原视频) / manual(用 --voice-text)
}

# 火山引擎 Seed-TTS 2.0 配置说明（仅当 TTS.provider="volcengine" 时生效）:
#   环境变量（必填）:
#     VOLCENGINE_APP_ID       - 火山引擎控制台 → 语音合成 → 应用管理的 APP ID
#     VOLCENGINE_ACCESS_KEY   - 火山引擎控制台 → 语音合成 → 应用管理的 Access Token
#   环境变量（可选）:
#     VOLCENGINE_TTS_RESOURCE_ID - 资源 ID，默认 seed-tts-2.0，可改为 seed-tts-1.0
#   获取方式: https://console.volcengine.com/speech/service/8
#   API 文档: https://www.volcengine.com/docs/6561/1598757

# ---------- 背景音乐（BGM）参数 ----------
# 用 --bgm <风格/曲名/auto> 选择 BGM，--list-bgm 查看可用 BGM + 推荐清单
# BGM 文件放在 assets/bgm/，命名规范：风格_曲名.mp3（如 治愈_起风了.mp3）
# 工具不内置音乐文件（版权原因），需自行下载，推荐清单见 core/bgm.py BGM_RECOMMENDATIONS
BGM = {
    "enabled": False,               # 是否启用 BGM（默认关闭，用 --bgm 开启）
    "dir": os.path.join(ASSETS_DIR, "bgm"),   # BGM 素材目录
    "volume": 0.15,                 # BGM 音量 0-1（0.15 = 15% 背景音，配音为主）
    "fade": True,                   # BGM 自动淡入（0.5s）+ 淡出（1s）
    "selection": "auto",            # 选择方式: auto(按笔记标签自动) / <风格名> / <曲名>
}

# ---------- 动态水印去除参数（OpenCV） ----------
# 用 --remove-wm 启用，针对视频中移动的水印（如 @原作者 飘动文字）
# 需要 pip install opencv-python
# 原理: 逐帧检测水印位置（颜色阈值/用户指定区域） → cv2.inpaint 修复
WM_REMOVE = {
    "enabled": False,           # 是否启用（默认关闭，用 --remove-wm 开启）
    "use_ocr": False,           # OCR 模式（推荐）：用 PaddleOCR 识别水印文字，能区分水印和字幕
    "wm_text": None,            # OCR 模式：水印文字关键词（逗号分隔，如 "@原创,搬运"），None=自动按 @ 符号识别
    "ocr_samples": 25,          # OCR 模式：采样帧数（默认 25，首尾密集采样，越多越准但越慢）
    "region": None,             # 颜色模式：水印起始区域 "x,y,w,h"（像素或百分比，None=每帧自动检测）
    "threshold": 200,           # 颜色模式：亮度阈值 0-255（水印通常是亮色 >200，越小越严格）
    "min_area": 200,            # 颜色模式：水印最小像素面积（过滤小噪点）
    "dilate": 3,                # 膨胀半径（扩大修复区域覆盖文字描边）
    "use_track": True,          # 颜色模式：是否用 CSRT 追踪器跟踪水印移动
    "match_threshold": 0.35,    # OCR 模式：模板匹配置信度阈值（0-1，越低越宽松；半透明水印建议 0.3-0.4）
    "replace_text": None,       # OCR 模式：替换文字（如 "@你的号"），在原水印轨迹位置覆盖自己的文字
    # 图形水印替换（OCR/颜色模式通用）：去除原水印后在原位置叠加自己的 PNG 图片
    # 与 replace_text 互斥（同时设置时图片优先）。适合 logo/二维码/复杂图形水印
    "replace_image": None,          # PNG 图片路径（带 alpha 通道），None=不替换图片
    "replace_image_opacity": 1.0,   # 图片不透明度 0.1-1.0（默认 1.0）
    "replace_image_scale": 1.0,     # 图片缩放系数（默认 1.0=适应原水印区域宽高，1.2=放大20%）
}

# ---------- 滤镜与特效（50+ 预设） ----------
# 用 --filter <预设名> 启用，预设定义在 core/filters.py 的 FILTER_PRESETS
# 用 --list-filters 查看全部预设（按分类组织：基础/电影感/胶片/风格化/黑白/美食风景/人像）
# 预设架构：每个预设 = 原生调色（eq/colorbalance/vibrance/hqdn3d） + 可选 LUT（.cube） + 特效（暗角/颗粒/漏光/柔焦等）
# LUT 文件位于 assets/luts/（15 个 .cube，由 assets/gen_luts.py 生成）
# 叠加素材位于 assets/overlays/（漏光/粒子/边框/暗角，由 assets/gen_overlays.py 生成）
FILTER = {
    "preset": None,             # 默认不应用滤镜（用 --filter <name> 指定）
    # 推荐预设速查：
    #   生活日常   warm_sunny / fresh / vivid
    #   美食       food_vivid / food_warm / rich
    #   风景旅行   scenery_bright / sunset_warm / vivid
    #   人像美妆   portrait_soft / pink_girl / cool_white
    #   电影感     teal_orange / cinematic / hollywood
    #   胶片质感   kodak_portra / fuji_400h / polaroid
    #   风格化     japanese / ins_style / hongkong / cyberpunk
    #   黑白       bw_classic / bw_high_contrast / bw_film
}

# ---------- 封面生成 ----------
COVER = {
    "enabled": True,            # 是否生成封面（默认开启，--no-cover 关闭）
    "styles": ["bigtext", "bottombar", "topbar", "magazine"],  # 生成的风格
    "n_titles": 10,             # 文案二创生成的标题候选数量
    "title_max_len": 12,        # 封面标题最大字数（超出截断）
    "frame_time_ratio": 0.3,    # 视频抽帧位置（0.3=跳过片头黑屏）
}

# ---------- AI 生图封面（调用文生图模型，可选） ----------
# 需配合 --llm-provider doubao（豆包 Seedream）或 --llm-provider openai（DALL-E 3）使用
# 默认关闭，用 --ai-cover 开启
AI_COVER = {
    "enabled": False,           # 是否启用 AI 生图封面（--ai-cover 开启）
    "styles": [                 # AI 封面风格（6 种）
        "product_hero",         # 产品主角：产品特写 + 爽点文案
        "before_after",         # 前后对比：左右分屏效果对比
        "hook_closeup",         # 钩子特写：夸张视觉 + 大字钩子
        "scene_lifestyle",      # 场景生活：使用场景 + 氛围感
        "list_infographic",     # 清单信息图：数字步骤 + 简洁信息
        "emotion_card",         # 情绪卡片：纯色背景 + 大字情绪
    ],
    "image_size": "1024x1024",  # 生图尺寸（豆包:1024x1024/960x1280/720x1080；DALL-E3:1024x1024/1792x1024/1024x1792）
}

# ---------- 文案二创 ----------
COPYWRITE = {
    "enabled": True,            # 是否生成二创文案（默认开启，--no-copywrite 关闭）
    "n_titles": 10,             # 生成标题候选数量
}

# ---------- LLM 大模型（可选，用于文案和封面标题二创） ----------
# 环境变量配置（三选一）：
#   豆包: setx LLM_PROVIDER "doubao" && setx ARK_API_KEY "your-key"
#   通义: setx LLM_PROVIDER "qwen" && setx DASHSCOPE_API_KEY "your-key"
#   OpenAI: setx LLM_PROVIDER "openai" && setx OPENAI_API_KEY "sk-xxx"
LLM = {
    "enabled": False,           # 是否启用 LLM（--llm 开启）
    "provider": "doubao",       # 服务商: doubao/qwen/openai
    "model": None,              # 模型名（None=用服务商默认）
    "api_key": None,            # API Key（None=从环境变量读取，建议用环境变量）
    "base_url": None,           # API 地址（None=用服务商默认）
    "timeout": 30,              # 超时秒数
    "max_retries": 2,           # 失败重试次数
}

# ---------- 日志 ----------
LOG_LEVEL = "INFO"


def ensure_dirs():
    """创建所有必要的输出目录。"""
    for d in (OUTPUT_DIR, DOWNLOAD_DIR, DEDUP_DIR, JIANYING_DRAFT_DIR, TEMP_DIR):
        os.makedirs(d, exist_ok=True)
