# -*- coding: utf-8 -*-
"""xhs_clone.py 的 Web UI 后端（Flask）。

启动: python web_ui.py  →  浏览器访问 http://localhost:5000

设计:
  - 不 import xhs_clone，用 subprocess 调用，避免阻塞与依赖污染
  - 单任务模型：同一时间只跑一个任务，支持停止
  - SSE 实时推送日志，前端按行接收
  - /api/params 返回分组参数定义，前端动态渲染
  - API Key 等敏感信息只在当前会话内存中，不持久化
"""
import os
import sys
import json
import time
import threading
import subprocess
import queue
import re

from flask import Flask, request, jsonify, Response, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
XHS_CLONE = os.path.join(BASE_DIR, "xhs_clone.py")
# 自定义模板持久化文件（与浏览器 localStorage 解耦，换浏览器/清缓存不丢失）
CUSTOM_PRESETS_FILE = os.path.join(BASE_DIR, "config", "custom_presets.json")
# LLM 配置持久化文件（跨终端/跨浏览器/重启后自动还原）
LLM_CONFIG_FILE = os.path.join(BASE_DIR, "config", "llm_config.json")

app = Flask(__name__, static_folder=None)

# ========== 全局任务状态（单任务模型） ==========
class TaskState:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None          # subprocess.Popen
        self.thread = None           # 读取线程
        self.logs = []               # [{"t": ts, "msg": str, "level": "info"|"error"|"warn"}]
        self.status = "idle"         # idle | running | done | stopped | error
        self.task_id = 0
        self.started_at = 0
        self.finished_at = 0
        self.return_code = None
        self.cmd = []                # 实际执行的命令
        self.output_dir = None       # 推断的输出目录

    def reset(self, cmd, output_dir=None):
        self.logs = []
        self.status = "running"
        self.task_id += 1
        self.started_at = time.time()
        self.finished_at = 0
        self.return_code = None
        self.cmd = cmd
        self.output_dir = output_dir


state = TaskState()


# ========== 参数定义（分组，前端渲染依据） ==========
# type: bool(开关) / int / float / str / choice(单选) / multi(多选叠加)
# default: 默认值（None=不传，由 xhs_clone 用 config 默认）
# bool 类型 default 一律 False（即默认不开启该开关）
PARAM_GROUPS = [
    {
        "key": "input", "title": "输入源", "icon": "📥",
        "desc": "小红书/抖音 URL 或本地文件路径（支持拖拽）",
        "params": [
            {"key": "note", "flag": None, "label": "笔记 URL / 本地文件", "type": "str",
             "default": "", "placeholder": "https://www.xiaohongshu.com/explore/xxx 或 D:\\video.mp4",
             "help": "小红书 URL/note_id、抖音 URL/短链、或本地视频/图片文件路径。URL 需用英文双引号包住（这里自动处理）",
             "required": True},
            {"key": "enhance_only", "flag": "--enhance-only", "label": "仅增强模式", "type": "str",
             "default": "", "placeholder": "output/dedup/xxx/video_dedup.mp4",
             "help": "跳过下载/去重，直接对已有视频做风格增强+二创（用于迭代调参）。填写后上面的 URL 失效",
             "mutual_exclusive": "note"},
            {"key": "no_jianying", "flag": "--no-jianying", "label": "不生成剪映草稿", "type": "bool",
             "default": False, "help": "仅做 ffmpeg/Pillow 去重，不生成剪映草稿"},
            {"key": "verbose", "flag": "-v", "label": "详细日志", "type": "bool",
             "default": False, "help": "输出 DEBUG 级别调试日志"},
        ],
    },
    {
        "key": "dedup", "title": "去重参数", "icon": "🔄",
        "desc": "视频/图片去重：镜像/变速/裁剪/调色/噪点/编码",
        "params": [
            {"key": "hd_enhance", "flag": "--hd-enhance", "label": "画质更胜原视频模式", "type": "bool",
             "default": True, "help": "默认开启：高精度 Lanczos 缩放 + Vibrance 智能色彩 + 3x3 精细锐化，让画质相比原视频更清晰质感更好"},
            {"key": "mirror", "flag": "--mirror", "label": "水平镜像", "type": "bool",
             "default": False, "help": "开启水平镜像去重（注意：会颠倒视频/图片里的文字，含字幕/标题/招牌的笔记不要用）"},
            {"key": "speed", "flag": "--speed", "label": "视频变速", "type": "float",
             "default": 1.06, "min": 0.5, "max": 2.0, "step": 0.01, "help": "视频变速系数（默认 1.06 = 加快6%）"},
            {"key": "crop_ratio", "flag": "--crop-ratio", "label": "四边裁剪比例", "type": "float",
             "default": 0.06, "min": 0.0, "max": 0.2, "step": 0.01,
             "help": "四边裁剪比例（0.06=各裁6%，裁后放大回原尺寸）。0=不裁剪"},
            {"key": "preset", "flag": "--preset", "label": "x264 编码预设", "type": "choice",
             "default": "medium", "choices": ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
             "help": "越快文件越大但耗时短；越慢文件越小但耗时长。默认 medium 保留高精细细节"},
            {"key": "crf", "flag": "--crf", "label": "x264 画质 CRF", "type": "int",
             "default": 16, "min": 0, "max": 51, "help": "越小画质越好文件越大（默认 16 超清无损级；23=中等；28=小文件）"},
            {"key": "quality", "flag": "--quality", "label": "图片 JPEG 质量", "type": "int",
             "default": 98, "min": 50, "max": 100, "help": "图片 JPEG 质量（默认 98 超清）"},
            {"key": "img_format", "flag": "--img-format", "label": "图片输出格式", "type": "choice",
             "default": "JPEG", "choices": ["JPEG", "PNG", "WEBP"], "help": "PNG 无损但文件大；WEBP 体积小"},
            {"key": "img_brightness", "flag": "--img-brightness", "label": "图片亮度", "type": "float",
             "default": 1.05, "min": 0.5, "max": 2.0, "step": 0.01, "help": "图片亮度因子（1.0=不变，>1 提亮）"},
            {"key": "img_contrast", "flag": "--img-contrast", "label": "图片对比度", "type": "float",
             "default": 1.06, "min": 0.5, "max": 2.0, "step": 0.01, "help": "图片对比度因子（1.0=不变）"},
            {"key": "img_saturation", "flag": "--img-saturation", "label": "图片饱和度", "type": "float",
             "default": 1.12, "min": 0.0, "max": 2.0, "step": 0.01, "help": "图片饱和度因子（>1 更鲜艳）"},
            {"key": "img_sharpness", "flag": "--img-sharpness", "label": "图片锐度", "type": "float",
             "default": 1.18, "min": 0.5, "max": 3.0, "step": 0.01, "help": "图片锐度因子（>1 更锐利）"},
            {"key": "noise_amount", "flag": "--noise-amount", "label": "图片噪点强度", "type": "int",
             "default": 5, "min": 0, "max": 50, "help": "图片噪点强度 0-50（0=关闭）"},
            {"key": "no_strip_exif", "flag": "--no-strip-exif", "label": "保留图片 EXIF", "type": "bool",
             "default": False, "help": "保留图片 EXIF 元数据（默认去除）"},
        ],
    },
    {
        "key": "enhance", "title": "风格增强", "icon": "✨",
        "desc": "小红书风格增强 + 滤镜 + 特效",
        "params": [
            {"key": "no_enhance", "flag": "--no-enhance", "label": "关闭风格增强", "type": "bool",
             "default": False, "help": "只去重，不做竖屏适配/暖色/磨皮"},
            {"key": "style_profile", "flag": "--style-profile", "label": "风格预设", "type": "choice",
             "default": "default", "choices": ["default", "lifestyle", "food", "beauty", "scenery", "minimal"],
             "help": "default(通用) lifestyle(生活) food(美食) beauty(美妆) scenery(风景) minimal(极简)"},
            {"key": "filter", "flag": "--filter", "label": "滤镜预设", "type": "choice",
             "default": None, "choices": [], "dynamic": "filters",
             "help": "55 个滤镜预设（青橙/胶片/日系/美食/人像…），留空不应用。点右侧按钮查看全部"},
            {"key": "filter_intensity", "flag": "--filter-intensity", "label": "滤镜强度", "type": "float",
             "default": 1.0, "min": 0.0, "max": 2.0, "step": 0.1, "depends_on": "filter",
             "help": "滤镜预设强度倍数（影响 LUT 强度和预设内置特效 opacity）。需先选「滤镜预设」才会生效"},
            {"key": "effect", "flag": "--effect", "label": "独立特效（可叠加）", "type": "multi",
             "default": [], "choices": [], "dynamic": "effects",
             "help": "44 个特效，可叠加多个（雪花/漏光/胶片框/老电影…）"},
            {"key": "effect_intensity", "flag": "--effect-intensity", "label": "特效强度", "type": "float",
             "default": 1.0, "min": 0.0, "max": 2.0, "step": 0.1, "depends_on": "effect",
             "help": "特效强度倍数（影响透明度/混合强度）。需先选「独立特效」才会生效"},
        ],
    },
    {
        "key": "subtitle", "title": "字幕处理", "icon": "💬",
        "desc": "抹除原字幕 + 重新渲染 + whisper 识别",
        "params": [
            {"key": "no_erase_subtitle", "flag": "--no-erase-subtitle", "label": "不抹除原字幕", "type": "bool",
             "default": False, "help": "保留原视频字幕"},
            {"key": "erase_mode", "flag": "--erase-mode", "label": "字幕抹除方式", "type": "choice",
             "default": "blur", "choices": ["blur", "delogo", "cover", "inpaint", "ocr-inpaint"],
             "help": "blur(模糊) delogo(修复) cover(覆盖) inpaint(智能填充,推荐) ocr-inpaint(OCR精修,最自然)"},
            {"key": "blur_strength", "flag": "--blur-strength", "label": "blur 模糊半径", "type": "int",
             "default": 20, "min": 1, "max": 28, "depends_on": "erase_mode", "depends_value": "blur",
             "help": "blur 模式模糊半径（越大越模糊，默认 20，最大 28 受 ffmpeg boxblur 限制）"},
            {"key": "blur_power", "flag": "--blur-power", "label": "blur 模糊次数", "type": "int",
             "default": 3, "min": 1, "max": 5, "depends_on": "erase_mode", "depends_value": "blur",
             "help": "blur 模式模糊次数（默认 3）"},
            {"key": "inpaint_radius", "flag": "--inpaint-radius", "label": "inpaint 修复半径", "type": "int",
             "default": 4, "min": 1, "max": 15, "depends_on": "erase_mode", "depends_value_any": ["inpaint", "ocr-inpaint"],
             "help": "inpaint/ocr-inpaint 修复半径（默认 4）"},
            {"key": "inpaint_dilate", "flag": "--inpaint-dilate", "label": "inpaint 膨胀半径", "type": "int",
             "default": 2, "min": 0, "max": 10, "depends_on": "erase_mode", "depends_value_any": ["inpaint", "ocr-inpaint"],
             "help": "mask 膨胀半径（扩大修复区域覆盖描边，默认 2）"},
            {"key": "ocr_inpaint_samples", "flag": "--ocr-inpaint-samples", "label": "OCR 采样帧数", "type": "int",
             "default": 12, "min": 3, "max": 50, "depends_on": "erase_mode", "depends_value": "ocr-inpaint",
             "help": "ocr-inpaint 模式 OCR 采样帧数（默认 12，越多越准越慢）"},
            {"key": "cover_color", "flag": "--cover-color", "label": "cover 覆盖色", "type": "str",
             "default": "black", "placeholder": "black/white/red 或 #RRGGBB", "depends_on": "erase_mode", "depends_value": "cover",
             "help": "cover 模式覆盖色（默认 black）"},
            {"key": "cover_opacity", "flag": "--cover-opacity", "label": "cover 透明度", "type": "float",
             "default": 0.9, "min": 0.0, "max": 1.0, "step": 0.05, "depends_on": "erase_mode", "depends_value": "cover",
             "help": "cover 模式覆盖透明度（1=完全不透明，默认 0.9）"},
            {"key": "no_auto_detect_subtitle", "flag": "--no-auto-detect-subtitle", "label": "关闭字幕区域自动识别", "type": "bool",
             "default": False, "help": "用 config.SUBTITLE.erase_area 默认区域（底部 75%-95%）"},
            {"key": "detect_frames", "flag": "--detect-frames", "label": "字幕识别抽帧数", "type": "int",
             "default": 5, "min": 3, "max": 8, "help": "自动识别抽帧数量（3-8，默认 5，越多越准越慢）"},
            {"key": "subtitle_style", "flag": "--subtitle-style", "label": "新字幕样式", "type": "choice",
             "default": "bold_yellow", "choices": ["default", "bold_yellow", "clean_box", "cute_pink", "top_title"],
             "help": "default(白字黑边) bold_yellow(黄字醒目) clean_box(黑底框) cute_pink(粉色可爱) top_title(顶部标题)"},
            {"key": "subtitle_position", "flag": "--subtitle-position", "label": "字幕位置", "type": "choice",
             "default": "auto", "choices": ["auto", "bottom", "top", "center"],
             "help": "auto(跟随原字幕) / bottom(底部) / top(顶部) / center(中部)"},
            {"key": "subtitle_source", "flag": "--subtitle-source", "label": "字幕文本来源", "type": "choice",
             "default": "auto", "choices": ["auto", "whisper", "llm-fix", "note"],
             "help": "auto(智能) whisper(原识别) llm-fix(whisper+LLM纠错,推荐) note(笔记文案)"},
            {"key": "no_subtitle", "flag": "--no-subtitle", "label": "不渲染新字幕", "type": "bool",
             "default": False, "help": "只抹除原字幕，不渲染新字幕"},
            {"key": "whisper", "flag": "--whisper", "label": "whisper 语音识别", "type": "bool",
             "default": False, "help": "用 whisper 提取字幕/配音文本（需 pip install openai-whisper）"},
            {"key": "whisper_model", "flag": "--whisper-model", "label": "whisper 模型", "type": "choice",
             "default": "base", "choices": ["tiny", "base", "small", "medium"], "depends_on": "whisper",
             "help": "越大越准越慢（默认 base）"},
        ],
    },
    {
        "key": "voice", "title": "AI 配音", "icon": "🎤",
        "desc": "edge-tts（免费）/ 火山引擎 Seed-TTS 2.0",
        "params": [
            {"key": "voice", "flag": "--voice", "label": "配音音色", "type": "choice",
             "default": None, "choices": [], "dynamic": "voices", "audition": True,
             "help": "指定后自动启用配音。auto=根据笔记标签/标题自动选。点右侧试听"},
            {"key": "no_voice", "flag": "--no-voice", "label": "关闭 AI 配音", "type": "bool",
             "default": False, "help": "即使 config.TTS.enabled=True 也跳过配音"},
            {"key": "tts_provider", "flag": "--tts-provider", "label": "TTS 服务商", "type": "choice",
             "default": "edge", "choices": ["edge", "volcengine"],
             "help": "edge(免费,26音色) volcengine(火山引擎,需APP ID+Access Key,24音色,自然度更高)。配置自动保存到浏览器"},
            {"key": "volcengine_app_id", "flag": "--volcengine-app-id", "label": "火山引擎 APP ID", "type": "password",
             "default": "", "placeholder": "选 volcengine 时必填",
             "depends_on": "tts_provider", "depends_value": "volcengine",
             "help": "火山引擎控制台 → 语音合成 → 应用管理 → APP ID。配置自动保存到浏览器本地"},
            {"key": "volcengine_access_key", "flag": "--volcengine-access-key", "label": "火山引擎 Access Token", "type": "password",
             "default": "", "placeholder": "选 volcengine 时必填",
             "depends_on": "tts_provider", "depends_value": "volcengine",
             "help": "火山引擎控制台 → 语音合成 → 应用管理 → Access Token。配置自动保存到浏览器本地"},
            {"key": "voice_text", "flag": "--voice-text", "label": "配音文本", "type": "str",
             "default": "", "placeholder": "留空=用笔记标题+描述自动生成",
             "help": "自定义配音文本（默认用笔记标题+描述）"},
            {"key": "voice_rate", "flag": "--voice-rate", "label": "语速", "type": "str",
             "default": "", "placeholder": "+10% / -10%", "help": "如 +10%加快 / -10%减慢，默认 +0%"},
            {"key": "voice_pitch", "flag": "--voice-pitch", "label": "音调", "type": "str",
             "default": "", "placeholder": "+5Hz / -5Hz", "help": "如 +5Hz偏高 / -5Hz偏低，默认 +0Hz"},
            {"key": "voice_volume", "flag": "--voice-volume", "label": "音量", "type": "str",
             "default": "", "placeholder": "+20% / -20%", "help": "如 +20%变大 / -20%变小，默认 +0%"},
            {"key": "keep_bg_audio", "flag": "--keep-bg-audio", "label": "保留原音频作 BGM", "type": "bool",
             "default": False, "help": "原视频音频降音量混合（默认完全替换）"},
            {"key": "bg_volume", "flag": "--bg-volume", "label": "原 BGM 音量", "type": "float",
             "default": 0.15, "min": 0.0, "max": 1.0, "step": 0.05, "depends_on": "keep_bg_audio",
             "help": "保留BGM时的音量 0-1（默认 0.15=15%背景音）"},
            {"key": "no_fit_duration", "flag": "--no-fit-duration", "label": "不调整配音时长", "type": "bool",
             "default": False, "help": "配音可能比视频长或短（默认自动匹配视频时长）"},
            {"key": "no_clean_text", "flag": "--no-clean-text", "label": "不清理配音文本", "type": "bool",
             "default": False, "help": "保留#标签/emoji（可能影响自然度）"},
            {"key": "voice_rewrite", "flag": "--voice-rewrite", "label": "LLM 口播二创改写", "type": "bool",
             "default": False, "help": "用 LLM 对 whisper 识别的原口播做二创改写后再配音。勾选后自动启用 whisper 语音识别和 AI 配音（无需手动勾选）。配合「字幕文本来源」选 llm-fix 可先纠错再改写，质量更高"},
            {"key": "rewrite_style", "flag": "--rewrite-style", "label": "二创风格", "type": "choice",
             "default": "casual", "choices": ["casual", "professional", "passion", "gentle", "knowledge", "humor", "concise", "viral"],
             "depends_on": "voice_rewrite",
             "help": "casual(口语化) professional(专业带货) passion(激情推销) gentle(温柔种草) knowledge(知识科普) humor(幽默) concise(精简) viral(爆款脚本结构,参考小红书爆款素材库)"},
            {"key": "rewrite_track", "flag": "--rewrite-track", "label": "爆款赛道(viral专用)", "type": "choice",
             "default": None, "choices": ["home_goods", "beauty_makeup", "food_cooking", "mother_baby", "digital_review", "knowledge_tutorial", "lifestyle_vlog", "outfit_fashion"],
             "depends_on": "rewrite_style",
             "depends_value": "viral",
             "help": "viral 风格专用: 指定赛道加载对应爆款脚本结构。留空=自动识别。家居/美妆/美食/母婴/数码/知识/生活vlog/穿搭"},
        ],
    },
    {
        "key": "bgm", "title": "背景音乐 BGM", "icon": "🎵",
        "desc": "BGM 素材放 assets/bgm/，命名：风格_曲名.mp3",
        "params": [
            {"key": "bgm", "flag": "--bgm", "label": "BGM 选择", "type": "str",
             "default": "", "placeholder": "风格/曲名/auto（如 治愈 / 起风了 / auto）",
             "help": "指定后自动启用 BGM，与配音共存（BGM 为背景音）"},
            {"key": "no_bgm", "flag": "--no-bgm", "label": "关闭 BGM", "type": "bool",
             "default": False, "help": "即使 config.BGM.enabled=True 也跳过"},
            {"key": "bgm_volume", "flag": "--bgm-volume", "label": "BGM 音量", "type": "float",
             "default": 0.15, "min": 0.0, "max": 1.0, "step": 0.05, "depends_on": "bgm",
             "help": "BGM 音量 0-1（默认 0.15=15%背景音，配音为主）"},
            {"key": "no_bgm_fade", "flag": "--no-bgm-fade", "label": "关闭淡入淡出", "type": "bool",
             "default": False, "depends_on": "bgm", "help": "不给 BGM 加淡入淡出"},
        ],
    },
    {
        "key": "creative", "title": "二创叠加", "icon": "🎨",
        "desc": "贴纸/扫光/字幕条/噪点/画中画/水印",
        "params": [
            {"key": "no_stickers", "flag": "--no-stickers", "label": "关闭自动贴纸", "type": "bool",
             "default": False, "help": "关闭 emoji 贴纸 overlay"},
            {"key": "sticker_count", "flag": "--sticker-count", "label": "贴纸数量", "type": "int",
             "default": 2, "min": 1, "max": 4, "help": "贴纸数量（默认 2，最多 4）"},
            {"key": "sticker_opacity", "flag": "--sticker-opacity", "label": "贴纸透明度", "type": "float",
             "default": 0.02, "min": 0.0, "max": 1.0, "step": 0.01, "help": "贴纸透明度 0-1（默认 0.02=极淡）"},
            {"key": "no_sweep", "flag": "--no-sweep", "label": "关闭扫光", "type": "bool",
             "default": False, "help": "关闭扫光效果"},
            {"key": "sweep_opacity", "flag": "--sweep-opacity", "label": "扫光透明度", "type": "float",
             "default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01, "help": "扫光透明度 0-1（默认 0.05）"},
            {"key": "no_caption", "flag": "--no-caption", "label": "关闭字幕条", "type": "bool",
             "default": False, "help": "关闭底部字幕条"},
            {"key": "caption", "flag": "--caption", "label": "字幕条文字", "type": "str",
             "default": "", "placeholder": "留空=用笔记标题", "help": "自定义字幕条文字（默认自动用笔记标题）"},
            {"key": "caption_style", "flag": "--caption-style", "label": "字幕条样式", "type": "choice",
             "default": "bottom", "choices": ["bottom", "top", "none"], "help": "bottom(底部) / top(顶部) / none"},
            {"key": "no_noise", "flag": "--no-noise", "label": "关闭噪点纹理", "type": "bool",
             "default": False, "help": "关闭噪点纹理叠加"},
            {"key": "pip", "flag": "--pip", "label": "画中画素材路径", "type": "str",
             "default": "", "placeholder": "如 other.mp4", "help": "画中画素材视频路径"},
            {"key": "watermark", "flag": "--watermark", "label": "右下角水印", "type": "str",
             "default": "", "placeholder": "如 @我的小红书号", "help": "右下角静态水印文字"},
        ],
    },
    {
        "key": "watermark_remove", "title": "动态水印去除", "icon": "🩹",
        "desc": "OCR 模式（推荐）/ 颜色检测模式，针对移动水印",
        "params": [
            {"key": "wm_ocr", "flag": "--wm-ocr", "label": "OCR 模式（推荐）", "type": "bool",
             "default": False, "help": "PaddleOCR 识别水印文字，能区分水印和字幕，不误删字幕"},
            {"key": "remove_wm", "flag": "--remove-wm", "label": "颜色检测模式", "type": "bool",
             "default": False, "help": "亮度阈值检测+CSRT追踪（无需 paddleocr，可能误删亮色字幕）"},
            {"key": "wm_text", "flag": "--wm-text", "label": "水印文字关键词", "type": "str",
             "default": "", "placeholder": "如 @原创,搬运（逗号分隔）", "depends_on": "wm_ocr",
             "help": "OCR 模式：水印文字关键词，不指定则自动识别 @ + 常见水印词"},
            {"key": "wm_replace", "flag": "--wm-replace", "label": "替换文字（轨迹覆盖）", "type": "str",
             "default": "", "placeholder": "如 @你的小红书号",
             "help": "填写后只走替换模式：去除原水印后在原位置覆盖你的文字，跟随原水印移动轨迹（自动启用 OCR）。与「OCR 模式」互斥：填此项不需要再勾 OCR 模式"},
            {"key": "wm_replace_opacity", "flag": "--wm-replace-opacity", "label": "替换文字透明度", "type": "float",
             "default": 1.0, "min": 0.1, "max": 1.0, "step": 0.05, "depends_on": "wm_replace",
             "help": "替换文字不透明度 0.1-1.0（默认 1.0=完全不透明）。预渲染写入 alpha 通道，零逐帧损耗。0.5=半透明弱水印"},
            {"key": "wm_replace_style", "flag": "--wm-replace-style", "label": "替换文字样式", "type": "choice",
             "default": "white_black",
             "choices": ["white_black", "white_none", "black_white", "yellow_black", "red_white", "white_shadow", "pink_black", "cyan_black"],
             "depends_on": "wm_replace",
             "help": "white_black(白字黑边,默认) white_none(白字无边) black_white(黑字白边) yellow_black(黄字黑边,醒目) red_white(红字白边) white_shadow(白字阴影) pink_black(粉字黑边) cyan_black(青字黑边)"},
            {"key": "wm_replace_font_scale", "flag": "--wm-replace-font-scale", "label": "字体大小系数", "type": "float",
             "default": 1.0, "min": 0.3, "max": 3.0, "step": 0.1, "depends_on": "wm_replace",
             "help": "字体大小缩放系数（默认 1.0=用原水印高度，1.2=放大20%，0.8=缩小20%）"},
            {"key": "wm_replace_outline", "flag": "--wm-replace-outline", "label": "描边宽度", "type": "int",
             "default": -1, "min": 0, "max": 10, "depends_on": "wm_replace",
             "help": "手动指定描边宽度（默认 -1=用样式预设值，0=不描边，1-10=指定宽度）"},
            {"key": "wm_replace_bg", "flag": "--wm-replace-bg", "label": "加背景框", "type": "bool",
             "default": False, "depends_on": "wm_replace",
             "help": "替换文字加半透明黑色背景框（复杂背景下提升可读性）"},
            {"key": "wm_replace_image", "flag": "--wm-replace-image", "label": "替换水印图片（轨迹覆盖）", "type": "str",
             "default": "", "placeholder": "如 logo.png（带透明通道）",
             "help": "填 PNG 图片路径：去除原水印后在原位置覆盖你的图片，跟随原水印移动轨迹。适合 logo/二维码/复杂图形。与「替换文字」互斥（同时填图片优先）。默认走 OCR 模式，搭配「水印区域」改走颜色模式"},
            {"key": "wm_replace_image_opacity", "flag": "--wm-replace-image-opacity", "label": "图片不透明度", "type": "float",
             "default": 1.0, "min": 0.1, "max": 1.0, "step": 0.05, "depends_on": "wm_replace_image",
             "help": "替换图片不透明度 0.1-1.0（默认 1.0=完全不透明）。0.5=半透明弱水印"},
            {"key": "wm_replace_image_scale", "flag": "--wm-replace-image-scale", "label": "图片缩放系数", "type": "float",
             "default": 1.0, "min": 0.1, "max": 5.0, "step": 0.1, "depends_on": "wm_replace_image",
             "help": "图片缩放系数（默认 1.0=适应原水印宽度，1.2=放大20%，0.8=缩小20%）"},
            {"key": "wm_ocr_samples", "flag": "--wm-ocr-samples", "label": "OCR 采样帧数", "type": "int",
             "default": 25, "min": 3, "max": 50, "depends_on": "wm_ocr", "help": "OCR 模式采样帧数（默认 25，越多越准越慢）"},
            {"key": "wm_match_threshold", "flag": "--wm-match-threshold", "label": "OCR 匹配阈值", "type": "float",
             "default": 0.35, "min": 0.1, "max": 1.0, "step": 0.05, "depends_on": "wm_ocr",
             "help": "模板匹配置信度阈值 0-1（越低越宽松，默认 0.35，半透明水印建议 0.3-0.4）"},
            {"key": "wm_region", "flag": "--wm-region", "label": "水印区域(颜色模式)", "type": "str",
             "default": "", "placeholder": "x,y,w,h 如 5%,5%,30%,10%", "depends_on": "remove_wm",
             "help": "颜色模式：水印起始区域（像素或百分比），不指定则每帧自动检测"},
            {"key": "wm_threshold", "flag": "--wm-threshold", "label": "亮度阈值(颜色模式)", "type": "int",
             "default": 200, "min": 0, "max": 255, "depends_on": "remove_wm", "help": "水印检测亮度阈值（默认 200，越小越严格）"},
            {"key": "wm_min_area", "flag": "--wm-min-area", "label": "最小像素面积(颜色模式)", "type": "int",
             "default": 200, "min": 10, "max": 5000, "depends_on": "remove_wm", "help": "水印最小像素面积（默认 200，过滤小噪点）"},
            {"key": "wm_dilate", "flag": "--wm-dilate", "label": "膨胀半径", "type": "int",
             "default": 3, "min": 0, "max": 20, "help": "扩大修复区域覆盖文字描边（默认 3）"},
            {"key": "wm_no_track", "flag": "--wm-no-track", "label": "关闭追踪器(颜色模式)", "type": "bool",
             "default": False, "depends_on": "remove_wm", "help": "每帧独立检测，慢但更鲁棒"},
        ],
    },
    {
        "key": "cover", "title": "封面生成", "icon": "🖼️",
        "desc": "常规 4 种封面 + AI 生图封面（需 LLM）",
        "params": [
            {"key": "no_cover", "flag": "--no-cover", "label": "不生成封面", "type": "bool",
             "default": False, "help": "默认生成 4 种风格封面 + 保留原封面"},
            {"key": "no_copywrite", "flag": "--no-copywrite", "label": "不生成二创文案", "type": "bool",
             "default": False, "help": "默认生成爆款标题候选 + 正文 + 标签"},
            {"key": "cover_styles", "flag": "--cover-styles", "label": "封面风格", "type": "str",
             "default": "", "placeholder": "逗号分隔：bigtext,bottombar,topbar,magazine",
             "help": "留空=全部生成。bigtext(大字报) bottombar(底部条) topbar(顶部条) magazine(杂志拼图)"},
            {"key": "cover_title", "flag": "--cover-title", "label": "封面标题", "type": "str",
             "default": "", "placeholder": "留空=用二创 Top1 标题", "help": "封面标题文字"},
            {"key": "erase_cover_text", "flag": "--erase-cover-text", "label": "去除底图文字", "type": "bool",
             "default": False,
             "help": "用 PaddleOCR 检测底图原有文字（标题/水印/作者名）+ cv2.inpaint 修复后再合成新封面，避免文字重叠。原封面副本不受影响"},
            {"key": "n_titles", "flag": "--n-titles", "label": "标题候选数量", "type": "int",
             "default": 10, "min": 1, "max": 30, "help": "二创标题候选数量（默认 10）"},
            {"key": "ai_cover", "flag": "--ai-cover", "label": "AI 生图封面", "type": "bool",
             "default": False, "help": "调用文生图模型生成 6 种风格封面（需配合 LLM 服务商）"},
            {"key": "ai_cover_styles", "flag": "--ai-cover-styles", "label": "AI 封面风格", "type": "str",
             "default": "", "placeholder": "逗号分隔，留空=全部",
             "depends_on": "ai_cover",
             "help": "product_hero/before_after/hook_closeup/scene_lifestyle/list_infographic/emotion_card/pain_scenario"},
            {"key": "ai_cover_size", "flag": "--ai-cover-size", "label": "AI 生图尺寸", "type": "choice",
             "default": "1024x1024", "choices": ["1024x1024", "960x1280", "720x1080", "1792x1024", "1024x1792"],
             "depends_on": "ai_cover", "help": "豆包支持 960x1280；DALL-E3 支持 1792x1024/1024x1792"},
            {"key": "image_mode", "flag": "--image-mode", "label": "图片生成模式", "type": "choice",
             "default": "t2i", "choices": ["t2i", "i2i", "t2i+i2i"], "depends_on": "ai_cover",
             "help": "t2i=文生图(从零生成) i2i=图生图(基于原封面) t2i+i2i=混合模式(以图生图为主,原产品保真,prompt辅助场景美化)"},
            {"key": "reference_image", "flag": "--reference-image", "label": "参考图(图生图用)", "type": "text",
             "default": "", "depends_on": "ai_cover",
             "help": "i2i/t2i+i2i 模式的参考图。可填公网URL或本地图片路径。优先级:此处填写>原封面>本地视频抽帧。留空则自动选择"},
        ],
    },
    {
        "key": "llm", "title": "LLM 配置", "icon": "🤖",
        "desc": "三层 LLM：主/prompt/image。LLM 配置自动保存到浏览器，下次打开自动恢复",
        "params": [
            {"key": "llm", "flag": "--llm", "label": "启用 LLM", "type": "bool",
             "default": False, "help": "用 LLM 生成文案和封面标题（失败回退模板库）"},
            {"key": "llm_only", "flag": "--llm-only", "label": "强制 LLM（不回退）", "type": "bool",
             "default": False, "depends_on": "llm", "help": "失败不回退模板库（适合调试 API 配置）"},
            {"key": "llm_provider", "flag": "--llm-provider", "label": "主 LLM 服务商", "type": "choice",
             "default": None, "choices": ["doubao", "qwen", "deepseek", "openai", "grsai", "modelscope"],
             "help": "doubao(豆包) qwen(通义) deepseek openai grsai(国内直连,支持文生图) modelscope(魔搭,免费文生图Z-Image-Turbo,每日2000次)"},
            {"key": "llm_model", "flag": "--llm-model", "label": "主 LLM 模型名", "type": "str",
             "default": "", "placeholder": "留空=服务商默认（如 doubao-pro-32k）",
             "help": "如 doubao-pro-32k / qwen-plus / deepseek-v4-flash / gpt-4o-mini"},
            {"key": "llm_api_key", "flag": "--llm-api-key", "label": "主 LLM API Key", "type": "password",
             "default": "", "placeholder": "留空=用环境变量",
             "help": "主 LLM 的 API Key（留空则从环境变量读取）。配置自动保存到浏览器本地，下次打开自动恢复"},
            {"key": "grsai_image_model", "flag": "--grsai-image-model", "label": "GrsAI 文生图模型", "type": "choice",
             "default": None, "choices": ["sora-image", "gpt-image-1.5", "nano-banana", "nano-banana-pro"],
             "depends_on": "llm_provider", "depends_value": "grsai",
             "help": "仅 --llm-provider grsai 时生效。nano-banana-pro 质量最高(0.09元/张)"},
            {"key": "prompt_llm_provider", "flag": "--prompt-llm-provider", "label": "生图 prompt 服务商", "type": "choice",
             "default": None, "choices": ["doubao", "qwen", "deepseek", "openai", "grsai", "modelscope"],
             "depends_on": "ai_cover", "help": "生成英文生图 prompt 的 LLM（可与文生图服务商分离，推荐用便宜的对话模型）"},
            {"key": "prompt_llm_model", "flag": "--prompt-llm-model", "label": "生图 prompt 模型名", "type": "str",
             "default": "", "placeholder": "如 deepseek-v4-flash / qwen-plus", "depends_on": "ai_cover",
             "help": "生图 prompt 专用 LLM 模型名"},
            {"key": "prompt_llm_api_key", "flag": "--prompt-llm-api-key", "label": "生图 prompt API Key", "type": "password",
             "default": "", "placeholder": "留空=用环境变量", "depends_on": "ai_cover",
             "help": "生图 prompt 专用 LLM 的 API Key。仅当 prompt 服务商与主 LLM 不同时需要单独填"},
            {"key": "image_llm_provider", "flag": "--image-llm-provider", "label": "文生图服务商", "type": "choice",
             "default": None, "choices": ["doubao", "openai", "grsai", "modelscope"],
             "depends_on": "ai_cover", "help": "实际出图的服务商（必须支持文生图）。魔搭(modelscope)免费每日2000次。不指定则与主 LLM 相同"},
            {"key": "image_llm_model", "flag": "--image-llm-model", "label": "文生图模型名", "type": "str",
             "default": "", "placeholder": "魔搭: Tongyi-MAI/Z-Image-Turbo / grsai: nano-banana-pro",
             "depends_on": "ai_cover", "help": "魔搭 model ID 必须用「组织/仓库名」格式（如 Tongyi-MAI/Z-Image-Turbo），留空用默认。图生图推荐 grsai 的 nano-banana-pro"},
            {"key": "image_llm_api_key", "flag": "--image-llm-api-key", "label": "文生图 API Key", "type": "password",
             "default": "", "placeholder": "留空=用环境变量", "depends_on": "ai_cover",
             "help": "文生图专用 API Key/Token。魔搭填魔搭 Access Token(环境变量 MODELSCOPE_API_TOKEN)。主 LLM 用 deepseek、文生图用魔搭时需单独填魔搭 token"},
            {"key": "image_llm_fallback_provider", "flag": "--image-llm-fallback-provider", "label": "文生图备用服务商", "type": "choice",
             "default": None, "choices": ["doubao", "openai", "grsai", "modelscope"],
             "depends_on": "ai_cover", "help": "主服务商失败时自动切换（如 GrsAI 审核拦截→魔搭）。留空=不启用备用"},
            {"key": "image_llm_fallback_model", "flag": "--image-llm-fallback-model", "label": "文生图备用模型名", "type": "str",
             "default": "", "placeholder": "魔搭: Tongyi-MAI/Z-Image-Turbo (免费)",
             "depends_on": "ai_cover", "help": "备用服务商的模型名。魔搭纯文生图模型在图生图模式下会自动降级为文生图"},
            {"key": "image_llm_fallback_api_key", "flag": "--image-llm-fallback-api-key", "label": "文生图备用 API Key", "type": "password",
             "default": "", "placeholder": "留空=用环境变量", "depends_on": "ai_cover",
             "help": "备用服务商的 API Key/Token。魔搭填 Access Token"},
        ],
    },
]

# ========== 预设方案 ==========
PRESETS = [
    {
        "key": "default", "name": "小红书爆款默认", "icon": "🌟",
        "desc": "完整流程：去重+增强+字幕+封面+文案，最省心",
        "values": {"note": "", "style_profile": "default", "subtitle_source": "auto"},
    },
    {
        "key": "voiceover", "name": "口播视频", "icon": "🎤",
        "desc": "whisper 识别 + 自动配音 + 黄字字幕 + 口播二创",
        "values": {"note": "", "whisper": True, "whisper_model": "small",
                   "voice": "auto", "voice_rewrite": True, "rewrite_style": "casual",
                   "subtitle_style": "bold_yellow", "subtitle_source": "llm-fix",
                   "llm": True, "llm_provider": "deepseek"},
    },
    {
        "key": "image_note", "name": "图文笔记", "icon": "📷",
        "desc": "图片去重为主，保留 EXIF 关闭，PNG 输出",
        "values": {"note": "", "img_format": "PNG", "quality": 100, "noise_amount": 8, "no_jianying": True},
    },
    {
        "key": "ai_cover", "name": "AI 生图封面", "icon": "🤖",
        "desc": "三层 LLM 分离：deepseek 文案 + grsai 文生图，GrsAI 失败自动切魔搭",
        "values": {"note": "", "ai_cover": True, "llm": True, "llm_provider": "deepseek",
                   "image_llm_provider": "grsai", "image_llm_model": "nano-banana-pro",
                   "image_llm_fallback_provider": "modelscope",
                   "image_llm_fallback_model": "Tongyi-MAI/Z-Image-Turbo",
                   "prompt_llm_provider": "deepseek", "image_mode": "t2i"},
    },
    {
        "key": "fast_dedup", "name": "快速去重", "icon": "⚡",
        "desc": "只去重，关闭增强/封面/文案，ultrafast 编码",
        "values": {"note": "", "no_enhance": True, "no_cover": True, "no_copywrite": True,
                   "no_jianying": True, "preset": "ultrafast", "crf": 23},
    },
    {
        "key": "cinematic", "name": "电影感大片", "icon": "🎬",
        "desc": "青橙调色 + 电影遮幅 + 暗角",
        "values": {"note": "", "filter": "teal_orange", "effect": ["letterbox", "vignette_strong"],
                   "style_profile": "scenery"},
    },
    {
        "key": "food", "name": "美食种草", "icon": "🍜",
        "desc": "美食风格 + 鲜艳滤镜 + 晓颜配音",
        "values": {"note": "", "style_profile": "food", "filter": "food_vivid",
                   "voice": "xiaoyan", "bgm": "美食", "subtitle_style": "bold_yellow"},
    },
    {
        "key": "beauty", "name": "美妆口播", "icon": "💄",
        "desc": "美妆风格 + 柔肤滤镜 + 晓伊配音 + 粉色字幕",
        "values": {"note": "", "style_profile": "beauty", "filter": "portrait_soft",
                   "voice": "xiaoyi", "whisper": True, "subtitle_style": "cute_pink",
                   "watermark": "@你的小红书号"},
    },
]


# ========== 列表数据采集（从 core 模块读取，避免硬编码重复） ==========
def _collect_voices():
    """采集全部配音（edge 26 + 火山引擎 24）。"""
    try:
        from core import tts as tts_mod
        edge = []
        for alias, short_name in tts_mod.VOICE_PRESETS.items():
            edge.append({
                "alias": alias, "name": short_name, "provider": "edge",
                "desc": tts_mod.VOICE_DESCRIPTIONS.get(alias, alias),
            })
        volc = []
        for alias, voice_type in tts_mod.VOLCENGINE_VOICES.items():
            volc.append({
                "alias": alias, "name": voice_type, "provider": "volcengine",
                "desc": tts_mod.VOLCENGINE_VOICE_DESCRIPTIONS.get(alias, alias),
            })
        return {"edge": edge, "volcengine": volc}
    except Exception as e:
        return {"edge": [], "volcengine": [], "error": str(e)}


def _collect_filters():
    """采集全部滤镜预设（按分类）。"""
    try:
        from core import filters as f
        result = {}
        for name, preset in f.FILTER_PRESETS.items():
            meta = preset.get("meta", {})
            cat = meta.get("category", "其他")
            result.setdefault(cat, []).append({
                "name": name, "name_cn": meta.get("name_cn", name),
                "desc": meta.get("desc", ""),
                "has_lut": bool(preset.get("lut")),
                "lut_available": bool(f.get_lut_path(preset.get("lut", {}).get("file", ""))) if preset.get("lut") else False,
            })
        return result
    except Exception as e:
        return {"错误": [{"name": "", "name_cn": str(e), "desc": ""}], "error": str(e)}


def _collect_effects():
    """采集全部独立特效（按分类）。"""
    try:
        from core import filters as f
        result = {}
        for name, eff in f.EFFECT_PRESETS.items():
            meta = eff.get("meta", {})
            cat = meta.get("category", "其他")
            png_available = True
            if eff.get("type") in ("png_overlay", "png_overlay_anim"):
                png_available = bool(f.get_overlay_path(eff.get("png", "")))
            result.setdefault(cat, []).append({
                "name": name, "name_cn": meta.get("name_cn", name),
                "desc": meta.get("desc", ""), "type": eff.get("type", ""),
                "png_available": png_available,
            })
        return result
    except Exception as e:
        return {"错误": [{"name": "", "name_cn": str(e), "desc": ""}], "error": str(e)}


def _collect_ai_covers():
    """采集 AI 封面风格 + 改写风格 + LLM 服务商。"""
    try:
        from core import llm as llm_mod
        covers = []
        for name, cfg in llm_mod.AI_COVER_STYLES.items():
            covers.append({
                "name": name, "name_cn": cfg.get("name_cn", name),
                "desc": cfg.get("desc", ""),
                "hook_examples": cfg.get("hook_examples", []),
            })
        rewrites = []
        for name, cfg in llm_mod.REWRITE_STYLES.items():
            rewrites.append({"name": name, "name_cn": cfg.get("name_cn", name), "desc": cfg.get("desc", "")})
        providers = []
        for name, cfg in llm_mod.PROVIDER_CONFIGS.items():
            providers.append({
                "name": name, "name_cn": cfg.get("name_cn", name),
                "default_model": cfg.get("default_model"),
                "image_model": cfg.get("image_model"),
                "env_key": cfg.get("env_key"),
                "supports_image": bool(cfg.get("image_model")) or name == "grsai",
            })
        return {"covers": covers, "rewrites": rewrites, "providers": providers}
    except Exception as e:
        return {"covers": [], "rewrites": [], "providers": [], "error": str(e)}


def _collect_bgm():
    """采集可用 BGM 文件 + 推荐清单。"""
    try:
        from core import bgm as bgm_mod
        available = bgm_mod.scan_bgm_dir()
        files = [{"style": s, "name": n, "filename": fn} for s, n, fn, _ in available]
        recommendations = {k: v for k, v in bgm_mod.BGM_RECOMMENDATIONS.items()}
        return {"available": files, "recommendations": recommendations,
                "styles": list(bgm_mod.BGM_RECOMMENDATIONS.keys())}
    except Exception as e:
        return {"available": [], "recommendations": {}, "styles": [], "error": str(e)}


# ========== CLI 命令构建 ==========
def _is_default_value(val, default):
    """判断值是否等于参数定义的默认值（用于跳过冗余参数）。"""
    if default is None or default == "":
        return False
    if isinstance(default, bool):
        return False
    try:
        if isinstance(default, (int, float)):
            return float(val) == float(default)
    except (ValueError, TypeError):
        pass
    return str(val) == str(default)


def build_command(values):
    """把前端提交的参数值转成 xhs_clone.py 的 CLI 参数列表。

    values: {param_key: value} 字典
    返回: (cmd_list, errors)
    """
    errors = []
    # 建立 key -> param 定义索引
    param_index = {}
    for group in PARAM_GROUPS:
        for p in group["params"]:
            param_index[p["key"]] = p

    # 互斥校验
    # 清理 Windows 复制路径时常夹带的 Unicode 不可见控制字符（U+202A..U+202E 等）
    _clean_inv = lambda s: re.sub(r"[\u202a-\u202e\u200e\u200f\u00a0\ufeff]", "", s or "").strip()
    enhance_only = _clean_inv(values.get("enhance_only"))
    note = _clean_inv(values.get("note"))

    # 决定主输入
    cmd = [sys.executable, XHS_CLONE]
    if enhance_only:
        cmd += ["--enhance-only", enhance_only]
    elif note:
        cmd += [note]
    else:
        # 没有输入源
        errors.append("必须填写「笔记 URL / 本地文件」或「仅增强模式」路径")

    # 互斥：wm_ocr 与 remove_wm
    if values.get("wm_ocr") and values.get("remove_wm"):
        errors.append("OCR 模式(--wm-ocr) 与 颜色检测模式(--remove-wm) 互斥，请只选一个")
    # 互斥：wm_ocr 与 wm_replace（填了替换文字就不需要再勾 OCR，后端会自动启用）
    if values.get("wm_ocr") and values.get("wm_replace"):
        errors.append("「替换文字」已自动启用 OCR 模式，无需再勾「OCR 模式」，请取消勾选")

    # 依赖校验：voice_rewrite 需要 whisper + voice
    if values.get("voice_rewrite"):
        if not values.get("whisper"):
            errors.append("口播二创改写(--voice-rewrite) 需配合 --whisper 使用")
        if not values.get("voice"):
            errors.append("口播二创改写(--voice-rewrite) 需配合 --voice 使用")

    # 按参数定义顺序追加 CLI 参数（跳过 note/enhance_only，已单独处理）
    # 核心原则：值等于参数定义的 default 时跳过（xhs_clone.py 会用 config 默认值），
    # 只有用户主动修改的参数才带出，避免命令行冗长
    for group in PARAM_GROUPS:
        for p in group["params"]:
            key = p["key"]
            if key in ("note", "enhance_only"):
                continue
            flag = p.get("flag")
            if not flag:
                continue
            ptype = p.get("type")
            val = values.get(key)
            default = p.get("default")

            # 依赖检查：声明了 depends_on 但无 depends_value/depends_value_any 的纯开关依赖，
            # 当依赖值"为空"时跳过该参数（如 effect_intensity 依赖 effect，未选特效时不带出）
            dep = p.get("depends_on")
            if dep and "depends_value" not in p and "depends_value_any" not in p:
                dep_val = values.get(dep)
                if isinstance(dep_val, (list, tuple)):
                    dep_ok = len(dep_val) > 0
                elif isinstance(dep_val, bool):
                    dep_ok = dep_val
                elif isinstance(dep_val, str):
                    dep_ok = dep_val.strip() != ""
                else:
                    dep_ok = dep_val is not None and dep_val != ""
                if not dep_ok:
                    continue

            if ptype == "bool":
                if val:  # 开关开启
                    cmd.append(flag)
            elif ptype == "multi":
                # 多选叠加（--effect）
                if val:
                    if isinstance(val, str):
                        val = [v.strip() for v in val.split(",") if v.strip()]
                    for v in val:
                        cmd += [flag, str(v)]
            else:
                # int/float/str/choice/password
                if val is None:
                    continue
                if isinstance(val, str):
                    if val.strip() == "":
                        continue
                    val = val.strip()
                # 值等于默认值则跳过（后台 config 已有默认值，无需重复指定）
                if _is_default_value(val, default):
                    continue
                cmd += [flag, str(val)]

    return cmd, errors


def _classify_line(msg):
    """根据日志关键词识别阶段。返回 stage 或 None。"""
    low = msg.lower() if isinstance(msg, str) else ""
    if "步骤 1" in msg or "解析并下载" in msg or "识别为" in msg:
        return "download"
    if "步骤 2" in msg or ("去重" in msg and "失败" not in msg):
        return "dedup"
    if "风格增强" in msg or "应用风格" in msg:
        return "enhance"
    if "字幕" in msg and ("抹除" in msg or "渲染" in msg or "识别" in msg):
        return "subtitle"
    if "配音" in msg or "[edge-tts]" in msg or "[火山引擎]" in msg or "voiceover" in low:
        return "voice"
    if "bgm" in low or "背景音乐" in msg or "混入" in msg:
        return "bgm"
    if "步骤 3" in msg or "复刻文案" in msg:
        return "copywrite"
    if "步骤 4" in msg or "封面" in msg or "ai 封面" in low or "ai封面" in msg or "文生图" in msg or "图生图" in msg:
        return "cover"
    if "复刻完成" in msg or "✅" in msg:
        return "done"
    return None


def _log_level(msg):
    if not isinstance(msg, str):
        return "info"
    up = msg.upper()
    if "ERROR" in up or "错误" in msg or "失败" in msg or "异常" in msg:
        return "error"
    if "WARNING" in up or "警告" in msg or "⚠" in msg:
        return "warn"
    return "info"


def _decode_line(raw: bytes) -> str:
    """智能解码子进程输出的一行 bytes。

    优先 UTF-8（Python logging 输出），失败回退 GBK（ffmpeg/whisper 等外部工具输出）。
    Windows 中文系统下，ffmpeg 等工具默认输出 GBK 编码的中文，
    如果强制用 UTF-8 解码会产生乱码（替换字符 ï¿½）。
    """
    try:
        text = raw.decode("utf-8")
        # 检测是否含替换字符（UTF-8 解码失败的产物）
        if "\ufffd" not in text:
            return text
    except UnicodeDecodeError:
        pass
    # UTF-8 解码失败或含替换字符，尝试 GBK
    try:
        return raw.decode("gbk")
    except (UnicodeDecodeError, LookupError):
        # GBK 也失败，用 UTF-8 + replace 兜底
        return raw.decode("utf-8", errors="replace")


def _reader_thread(proc):
    """子进程 stdout/stderr 读取线程，逐行写入全局 logs。"""
    try:
        for raw_line in iter(proc.stdout.readline, b""):
            if not raw_line:
                break
            line = _decode_line(raw_line).rstrip("\r\n")
            if not line:
                continue
            stage = _classify_line(line)
            entry = {"t": time.time(), "msg": line, "level": _log_level(line)}
            if stage:
                entry["stage"] = stage
            with state.lock:
                state.logs.append(entry)
        proc.stdout.close()
    except Exception as e:
        with state.lock:
            state.logs.append({"t": time.time(), "msg": f"[web_ui] 读取线程异常: {e}", "level": "error"})


def _run_task(cmd):
    """启动子进程并后台读取输出。"""
    with state.lock:
        state.reset(cmd)

    # 启动子进程：合并 stderr 到 stdout，行缓冲
    # 关键：读取 bytes 而非 text，在 reader 中智能解码（UTF-8 优先，GBK 回退）
    # 这样 Python logging 的 UTF-8 输出和 ffmpeg/whisper 的 GBK 输出都能正确显示
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=BASE_DIR,
            bufsize=1,
            env=child_env,
        )
    except Exception as e:
        with state.lock:
            state.status = "error"
            state.finished_at = time.time()
            state.logs.append({"t": time.time(), "msg": f"[web_ui] 启动失败: {e}", "level": "error"})
        return

    with state.lock:
        state.process = proc

    # 启动读取线程
    t = threading.Thread(target=_reader_thread, args=(proc,), daemon=True)
    state.thread = t
    t.start()

    # 等待进程结束
    rc = proc.wait()
    with state.lock:
        state.return_code = rc
        state.finished_at = time.time()
        if state.status == "stopped":
            pass  # 已被停止
        elif rc == 0:
            state.status = "done"
            state.logs.append({"t": time.time(), "msg": f"[web_ui] 任务完成（返回码 {rc}）", "level": "info", "stage": "done"})
        else:
            state.status = "error"
            state.logs.append({"t": time.time(), "msg": f"[web_ui] 任务结束（返回码 {rc}）", "level": "error"})
        state.process = None


# ========== 路由 ==========
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    # 安全：只允许 web 目录下的文件
    safe = os.path.normpath(os.path.join(WEB_DIR, filename))
    if not safe.startswith(WEB_DIR):
        abort(404)
    return send_from_directory(WEB_DIR, filename)


@app.route("/api/params")
def api_params():
    resp = jsonify({"groups": PARAM_GROUPS, "presets": PRESETS,
                    "custom_presets": _load_custom_presets(),
                    "llm_config": _load_llm_config()})
    # 禁止浏览器缓存参数定义，确保后端改动后前端立即可见
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ========== LLM 配置服务端持久化（文件存储 config/llm_config.json） ==========
def _load_llm_config():
    """从 config/llm_config.json 读取 LLM 全量持久化配置（含 API Keys 与服务商映射）。"""
    try:
        if os.path.exists(LLM_CONFIG_FILE):
            with open(LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        print(f"[WARN] 读取 LLM 配置文件失败: {e}")
    return {"llm_config": {}, "provider_configs": {}}


def _save_llm_config(data):
    """保存 LLM 配置到 config/llm_config.json（跨终端/跨浏览器持久化）。"""
    try:
        os.makedirs(os.path.dirname(LLM_CONFIG_FILE), exist_ok=True)
        with open(LLM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[ERROR] 保存 LLM 配置文件失败: {e}")
        return False


@app.route("/api/llm-config", methods=["GET"])
def api_llm_config_get():
    return jsonify(_load_llm_config())


@app.route("/api/llm-config", methods=["POST"])
def api_llm_config_save():
    """保存 LLM 配置到后端 config/llm_config.json。"""
    data = request.get_json(force=True, silent=True) or {}
    if _save_llm_config(data):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "保存 LLM 配置文件失败"}), 500


@app.route("/api/llm-config", methods=["DELETE"])
def api_llm_config_delete():
    """重置/删除服务端 LLM 配置文件。"""
    try:
        if os.path.exists(LLM_CONFIG_FILE):
            os.remove(LLM_CONFIG_FILE)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ========== 自定义模板持久化（后端文件存储，避免 localStorage 丢失） ==========
def _load_custom_presets():
    """从 config/custom_presets.json 读取自定义模板。"""
    try:
        if os.path.exists(CUSTOM_PRESETS_FILE):
            with open(CUSTOM_PRESETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] 读取自定义模板失败: {e}")
    return []


def _save_custom_presets(presets):
    """保存自定义模板到 config/custom_presets.json。"""
    try:
        os.makedirs(os.path.dirname(CUSTOM_PRESETS_FILE), exist_ok=True)
        with open(CUSTOM_PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[ERROR] 保存自定义模板失败: {e}")
        return False


@app.route("/api/custom-presets", methods=["GET"])
def api_custom_presets_get():
    return jsonify(_load_custom_presets())


@app.route("/api/custom-presets", methods=["POST"])
def api_custom_presets_save():
    """保存（新增或更新）一个自定义模板。"""
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "模板名称不能为空"}), 400
    values = data.get("values") or {}
    key = data.get("key") or ("custom_" + str(int(time.time() * 1000)))
    presets = _load_custom_presets()
    # 若 key 已存在则更新，否则新增
    idx = next((i for i, p in enumerate(presets) if p.get("key") == key), None)
    preset = {"key": key, "name": name, "icon": "📌", "desc": "自定义", "values": values}
    if idx is not None:
        presets[idx] = preset
    else:
        presets.append(preset)
    if _save_custom_presets(presets):
        return jsonify({"ok": True, "preset": preset})
    return jsonify({"ok": False, "error": "保存失败"}), 500


@app.route("/api/custom-presets", methods=["DELETE"])
def api_custom_presets_delete():
    """按 key 删除一个自定义模板。"""
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("key")
    if not key:
        return jsonify({"ok": False, "error": "缺少 key"}), 400
    presets = _load_custom_presets()
    new_presets = [p for p in presets if p.get("key") != key]
    if len(new_presets) == len(presets):
        return jsonify({"ok": False, "error": "未找到该模板"}), 404
    if _save_custom_presets(new_presets):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "保存失败"}), 500


@app.route("/api/voices")
def api_voices():
    return jsonify(_collect_voices())


@app.route("/api/effects")
def api_effects():
    return jsonify(_collect_effects())


@app.route("/api/filters")
def api_filters():
    return jsonify(_collect_filters())


@app.route("/api/ai-covers")
def api_ai_covers():
    return jsonify(_collect_ai_covers())


@app.route("/api/bgm")
def api_bgm():
    return jsonify(_collect_bgm())


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(silent=True) or {}
    values = data.get("values", {})
    cmd, errors = build_command(values)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    with state.lock:
        if state.status == "running":
            return jsonify({"ok": False, "errors": ["已有任务正在运行，请先停止"]}), 409

    # 后台线程执行（避免阻塞请求）
    threading.Thread(target=_run_task, args=(cmd,), daemon=True).start()
    return jsonify({"ok": True, "cmd": cmd, "task_id": state.task_id + 1})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with state.lock:
        if state.status != "running" or state.process is None:
            return jsonify({"ok": False, "msg": "没有正在运行的任务"})
        try:
            state.process.terminate()
            state.status = "stopped"
            state.logs.append({"t": time.time(), "msg": "[web_ui] 已发送停止信号（terminate）", "level": "warn"})
        except Exception as e:
            return jsonify({"ok": False, "msg": f"停止失败: {e}"}), 500
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    """一次性状态快照（非流式）。"""
    with state.lock:
        return jsonify({
            "status": state.status,
            "task_id": state.task_id,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "return_code": state.return_code,
            "cmd": state.cmd,
            "log_count": len(state.logs),
        })


@app.route("/api/run/status")
def api_run_status_sse():
    """SSE 流式推送日志。客户端传 ?since=索引 从指定位置开始。"""
    since = int(request.args.get("since", 0))

    def event_stream():
        idx = since
        last_status = None
        idle_count = 0
        while True:
            with state.lock:
                logs = state.logs[idx:]
                cur_status = state.status
                cur_rc = state.return_code
                cur_finished = state.finished_at
            # 推送新日志
            for entry in logs:
                payload = {"t": entry["t"], "msg": entry["msg"], "level": entry.get("level", "info")}
                if "stage" in entry:
                    payload["stage"] = entry["stage"]
                yield f"event: log\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            idx += len(logs)
            # 状态变化时推送
            if cur_status != last_status:
                last_status = cur_status
                yield f"event: status\ndata: {json.dumps({'status': cur_status, 'return_code': cur_rc, 'finished_at': cur_finished}, ensure_ascii=False)}\n\n"
            # 任务结束则推送 done 并退出
            if cur_status in ("done", "stopped", "error", "idle") and idx >= len(state.logs):
                yield f"event: end\ndata: {json.dumps({'status': cur_status, 'return_code': cur_rc}, ensure_ascii=False)}\n\n"
                # 推送产出文件
                yield f"event: outputs\ndata: {json.dumps(_scan_outputs(), ensure_ascii=False)}\n\n"
                break
            # 心跳 / 等待
            time.sleep(0.3)
            idle_count += 1
            if idle_count % 20 == 0:
                yield f"event: ping\ndata: {json.dumps({'ts': time.time()}, ensure_ascii=False)}\n\n"

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


def _scan_outputs():
    """扫描 output/dedup 目录，返回最近修改的产出目录文件列表。"""
    out_root = os.path.join(BASE_DIR, "output", "dedup")
    result = []
    if not os.path.isdir(out_root):
        return {"dirs": []}
    try:
        # 找最近修改的子目录
        subdirs = []
        for name in os.listdir(out_root):
            p = os.path.join(out_root, name)
            if os.path.isdir(p):
                subdirs.append((os.path.getmtime(p), name, p))
        subdirs.sort(reverse=True)
        for _, name, p in subdirs[:5]:
            files = []
            for fn in sorted(os.listdir(p)):
                fp = os.path.join(p, fn)
                if os.path.isfile(fp):
                    size = os.path.getsize(fp)
                    files.append({"name": fn, "size": size, "path": fp})
            result.append({"dir": name, "files": files})
    except Exception as e:
        return {"dirs": [], "error": str(e)}
    return {"dirs": result}


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """生成 CLI 命令预览（前端也可本地生成，这里作为校验）。"""
    data = request.get_json(silent=True) or {}
    values = data.get("values", {})
    cmd, errors = build_command(values)
    return jsonify({"cmd": cmd, "errors": errors, "shell": _to_shell(cmd)})


def _to_shell(cmd):
    """把命令列表转成可复制的 shell 字符串（带引号转义）。"""
    parts = []
    for i, c in enumerate(cmd):
        if i == 0:
            parts.append(c)
            continue
        if i == 1:
            parts.append(c)
            continue
        # 含空格或特殊字符则加引号
        if re.search(r"[\s&|<>\"\']", c):
            # 转义内部双引号
            escaped = c.replace('"', '\\"')
            parts.append(f'"{escaped}"')
        else:
            parts.append(c)
    return " ".join(parts)


@app.route("/api/voice-preview", methods=["POST"])
def api_voice_preview():
    """试听配音：用 edge-tts 生成一小段样音，返回 mp3 路径供前端播放。
    仅支持 edge 服务商（火山引擎需 API key，不支持免费试听）。"""
    data = request.get_json(silent=True) or {}
    voice = data.get("voice", "xiaoxiao")
    rate = data.get("rate", "+0%") or "+0%"
    pitch = data.get("pitch", "+0Hz") or "+0Hz"
    text = data.get("text", "你好，这是配音试听样例。小红书爆款笔记复刻工具。").strip()
    if not text:
        text = "你好，这是配音试听样例。"

    # 火山引擎音色不支持免费试听
    if voice.startswith("bd_"):
        return jsonify({"ok": False, "msg": "火山引擎音色需 API Key，不支持免费试听，请用 --voice bd_xxx 实际运行体验"}), 400

    try:
        from core import tts as tts_mod
    except Exception as e:
        return jsonify({"ok": False, "msg": f"tts 模块加载失败: {e}"}), 500

    if not tts_mod._HAS_EDGE_TTS:
        return jsonify({"ok": False, "msg": "edge-tts 未安装，请运行: pip install edge-tts"}), 500

    # 生成临时文件
    preview_dir = os.path.join(BASE_DIR, "output", "_voice_previews")
    os.makedirs(preview_dir, exist_ok=True)
    safe_voice = re.sub(r"[^A-Za-z0-9_\-]", "_", voice)
    out_path = os.path.join(preview_dir, f"preview_{safe_voice}.mp3")

    try:
        r = tts_mod.generate_tts(text, out_path, voice=voice, rate=rate, pitch=pitch,
                                 volume="+0%", clean=True, provider="edge")
        if r and os.path.exists(r):
            return jsonify({"ok": True, "url": f"/api/preview-file/{os.path.basename(r)}",
                            "path": r})
        return jsonify({"ok": False, "msg": "生成失败，请检查网络或 voice 别名"}), 500
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/preview-file/<filename>")
def api_preview_file(filename):
    """提供试听音频文件。"""
    preview_dir = os.path.join(BASE_DIR, "output", "_voice_previews")
    safe = os.path.normpath(os.path.join(preview_dir, filename))
    if not safe.startswith(preview_dir):
        abort(404)
    if not os.path.isfile(safe):
        abort(404)
    return send_from_directory(preview_dir, filename)


@app.route("/api/open-file", methods=["POST"])
def api_open_file():
    """在系统资源管理器中打开/选中产出文件（Windows）。"""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "msg": "文件不存在"}), 404
    try:
        import subprocess as sp
        if sys.platform.startswith("win"):
            # explorer /select 选中文件
            sp.Popen(["explorer", "/select,", path])
        elif sys.platform == "darwin":
            sp.Popen(["open", path])
        else:
            sp.Popen(["xdg-open", os.path.dirname(path)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/list-drafts")
def api_list_drafts():
    """列出剪映草稿。"""
    try:
        import config
        from core import jianying
        drafts = jianying.list_jianying_drafts()
        return jsonify({"ok": True, "root": config.JIANYING_DRAFT_ROOT or "(未检测到)", "drafts": drafts})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


if __name__ == "__main__":
    print("=" * 60)
    print("  xhs_clone Web UI")
    print("  浏览器访问: http://localhost:5000")
    print("=" * 60)
    # 将系统临时目录重定向到项目目录下，避免占用 C 盘空间
    # Whisper 内部调用 ffmpeg 会用 TEMP 环境变量
    import config
    config.ensure_dirs()
    os.environ["TEMP"] = config.TEMP_DIR
    os.environ["TMP"] = config.TEMP_DIR
    print(f"  临时目录: {config.TEMP_DIR}")
    # threaded=True 支持多请求（SSE + 其他 API 并行）
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
