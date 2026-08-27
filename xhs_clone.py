# -*- coding: utf-8 -*-
"""小红书爆款笔记复刻 + 去重 工具（CLI）。

用法:
    python xhs_clone.py <小红书/抖音URL 或 本地视频文件> [选项]

示例:
    # 小红书
    python xhs_clone.py https://www.xiaohongshu.com/explore/xxxxxxxx
    # 抖音
    python xhs_clone.py https://www.douyin.com/video/xxxxxxxx
    python xhs_clone.py "https://v.douyin.com/xxxxxx/"
    # 本地视频文件
    python xhs_clone.py /path/to/video.mp4
    # 通用
    python xhs_clone.py xxxxxxxxxxxxxxxx --no-jianying
    python xhs_clone.py --list-drafts

功能:
    1. 解析笔记/视频（自动识别小红书 / 抖音 / 本地文件）
    2. 下载素材与文案
    3. 按类型去重：
       - 视频    : ffmpeg 去重 + 生成剪映草稿（剪映打开即可二次编辑/导出）
       - Live 图 : 静态图 Pillow 去重 + 动态视频 ffmpeg 去重 + 剪映草稿
       - 图文    : 每张图片 Pillow 去重
    4. 小红书风格增强 + 二创叠加 + 字幕处理
    5. 复刻文案（标题/正文/标签）保存为 content_clone.txt
"""
import os
import re
import sys
import json
import time
import argparse
import logging

# 预加载 torch：paddlepaddle 和 torch 同时加载会 DLL 冲突（paddle 先加载会导致 torch 的 shm.dll 失败）
# 必须在 import paddleocr 之前先 import torch，让 torch 的 DLL 先加载
try:
    import torch  # noqa: F401
except ImportError:
    pass

import config
from core import xhs_parser
from core import dedup_image
from core import dedup_video
from core import dedup_live
from core import jianying
from core import style_enhance
from core import douyin_parser
from core import copywrite as cw_mod
from core import cover_gen

# 将系统临时目录重定向到项目目录下，避免占用 C 盘空间
# Whisper 内部调用 ffmpeg 会用 TEMP 环境变量，这里提前设置
config.ensure_dirs()
os.environ["TEMP"] = config.TEMP_DIR
os.environ["TMP"] = config.TEMP_DIR


def setup_logging(verbose=False):
    level = logging.DEBUG if verbose else config.LOG_LEVEL
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def clone_text(note, out_dir):
    """复刻文案：标题 + 正文 + 标签，保存为 txt 和 json。"""
    os.makedirs(out_dir, exist_ok=True)
    # 文本（可直接复制使用）
    txt_path = os.path.join(out_dir, "content_clone.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(note.title + "\n\n")
        f.write(note.desc + "\n")
        if note.tags:
            f.write("\n" + " ".join(f"#{t}" for t in note.tags) + "\n")
    # JSON（结构化）
    json_path = os.path.join(out_dir, "content_clone.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "note_id": note.note_id,
            "type": note.note_type,
            "is_live": note.is_live,
            "title": note.title,
            "desc": note.desc,
            "tags": note.tags,
            "cloned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f, ensure_ascii=False, indent=2)
    logging.getLogger("cli").info("文案已复刻: %s", txt_path)
    return txt_path


def process_video_note(note, use_jianying, params, enhance_params=None,
                       creative_params=None, watermark=None, caption_text=None, pip_path=None,
                       subtitle_cfg=None, tts_cfg=None, voice_text=None, bgm_cfg=None,
                       wm_cfg=None, filter_preset=None, effects=None, effect_intensity=1.0,
                       filter_intensity=1.0,
                       voice_rewrite=False, rewrite_style="casual",
                       rewrite_track=None, subtitle_source="auto"):
    """处理视频笔记：去重 → 风格增强+二创+字幕 → 剪映草稿。"""
    log = logging.getLogger("cli")
    out_dir = os.path.join(config.DEDUP_DIR, note.note_id)
    os.makedirs(out_dir, exist_ok=True)

    src = next((m["local"] for m in note.media_items
                if m.get("kind") == "video" and m.get("local")), None)
    if not src:
        log.error("未找到已下载的视频文件")
        return None

    # 步骤 2a: 去重
    dst_dedup = os.path.join(out_dir, "video_dedup.mp4")
    result = dedup_video.dedup_video(src, dst_dedup, params)
    if not result:
        log.error("视频去重失败")
        return None

    # 步骤 2b: 小红书风格增强 + 二创叠加 + 字幕处理 + AI配音
    final_video = result
    if enhance_params:
        dst_final = os.path.join(out_dir, "video_final.mp4")
        log.info("应用风格增强 + 二创 + 字幕处理" +
                 (" + AI配音..." if tts_cfg and tts_cfg.get("enabled") else "..."))
        # 用笔记标题+描述作为字幕文本来源（whisper 关闭时）
        note_text = (note.title or "") + "。" + (note.desc or "")
        r = style_enhance.enhance_video(
            result, dst_final, enhance_params, creative_params,
            watermark, caption_text, pip_path, subtitle_cfg, note_text,
            tts_cfg, voice_text, bgm_cfg, wm_cfg, filter_preset,
            effects, effect_intensity,
            filter_intensity=filter_intensity,
            voice_rewrite=voice_rewrite, rewrite_style=rewrite_style,
            subtitle_source=subtitle_source)
        if r:
            final_video = r
        else:
            log.warning("风格增强失败，使用去重后的视频作为最终成品")

    # 步骤 2c: 生成剪映草稿（用最终成品）
    jy = None
    if use_jianying:
        log.info("生成剪映草稿...")
        jy_dir, _ = jianying.create_jianying_draft(
            final_video,
            draft_name=f"XHS视频_{note.note_id[:8]}",
            params=params,
        )
        jy = jy_dir
    return {"video": result, "final": final_video, "jianying": jy, "out_dir": out_dir}


def process_image_note(note, params):
    """处理图文笔记。"""
    log = logging.getLogger("cli")
    out_dir = os.path.join(config.DEDUP_DIR, note.note_id)
    os.makedirs(out_dir, exist_ok=True)

    # 根据输出格式决定扩展名
    fmt = (params.get("format") or "JPEG").upper()
    if fmt == "PNG":
        ext = ".png"
    elif fmt == "WEBP":
        ext = ".webp"
    else:
        ext = ".jpg"

    results = []
    for idx, item in enumerate(note.media_items):
        if item.get("kind") != "image" or not item.get("local"):
            continue
        src = item["local"]
        dst = os.path.join(out_dir, f"image_{idx}{ext}")
        r = dedup_image.dedup_image(src, dst, params)
        results.append(r)
    return {"images": [r for r in results if r], "out_dir": out_dir}


def process_live_note(note, use_jianying, image_params, video_params,
                      enhance_params=None, creative_params=None,
                      watermark=None, caption_text=None, pip_path=None,
                      subtitle_cfg=None, tts_cfg=None, voice_text=None, bgm_cfg=None,
                      wm_cfg=None, filter_preset=None, effects=None, effect_intensity=1.0,
                      filter_intensity=1.0,
                      voice_rewrite=False, rewrite_style="casual", subtitle_source="auto"):
    """处理 Live 图笔记：去重 → 风格增强+二创+配音（仅动态视频）。"""
    log = logging.getLogger("cli")
    out_dir = os.path.join(config.DEDUP_DIR, note.note_id)
    os.makedirs(out_dir, exist_ok=True)

    live_results = []
    plain_images = []

    for idx, item in enumerate(note.media_items):
        if item.get("kind") != "image":
            continue
        if item.get("live_photo") and item["live_photo"].get("local"):
            # Live 图
            r = dedup_live.dedup_live_image(
                item["local"],
                item["live_photo"]["local"],
                out_dir, idx, image_params, video_params,
            )
            # 对动态视频做风格增强 + 二创 + 配音
            if enhance_params and r.get("video"):
                dst_enh = os.path.join(out_dir, f"live_{idx}_final.mp4")
                log.info("Live 图 %d 应用风格增强+二创" +
                         ("+AI配音..." if tts_cfg and tts_cfg.get("enabled") else "..."), idx)
                note_text = (note.title or "") + "。" + (note.desc or "")
                er = style_enhance.enhance_video(
                    r["video"], dst_enh, enhance_params, creative_params,
                    watermark, caption_text, pip_path, subtitle_cfg, note_text,
                    tts_cfg, voice_text, bgm_cfg, wm_cfg, filter_preset,
                    effects, effect_intensity,
                    filter_intensity=filter_intensity,
                    voice_rewrite=voice_rewrite, rewrite_style=rewrite_style,
                    rewrite_track=rewrite_track,
                    subtitle_source=subtitle_source)
                if er:
                    r["final"] = er
            live_results.append(r)
        else:
            # 普通图片
            dst = os.path.join(out_dir, f"image_{idx}.jpg")
            r = dedup_image.dedup_image(item["local"], dst, image_params)
            plain_images.append(r)

    # 为第一个 Live 视频生成剪映草稿（用增强后的）
    jy = None
    if use_jianying:
        first_live_vid = next(
            (r.get("final") or r.get("video") for r in live_results if r.get("video")), None)
        if first_live_vid:
            log.info("为 Live 视频生成剪映草稿...")
            jy_dir, _ = jianying.create_jianying_draft(
                first_live_vid,
                draft_name=f"XHS_Live_{note.note_id[:8]}",
                params=video_params,
            )
            jy = jy_dir

    return {
        "live": live_results,
        "images": [r for r in plain_images if r],
        "jianying": jy,
        "out_dir": out_dir,
    }


def _build_creative_params(args):
    """根据 CLI 参数构建二创叠加层配置。"""
    c = dict(config.CREATIVE)
    if args.no_stickers:
        c["stickers_enabled"] = False
    if args.sticker_count is not None:
        c["sticker_count"] = max(1, min(4, args.sticker_count))
    if args.sticker_opacity is not None:
        c["sticker_opacity"] = max(0.0, min(1.0, args.sticker_opacity))
    if args.no_sweep:
        c["sweep_enabled"] = False
    if args.sweep_opacity is not None:
        c["sweep_opacity"] = max(0.0, min(1.0, args.sweep_opacity))
    if args.caption_style is not None:
        c["caption_style"] = args.caption_style
        c["caption_enabled"] = args.caption_style != "none"
    # --no-caption 优先级最高，最后生效（覆盖 --caption-style 的设置）
    if args.no_caption:
        c["caption_enabled"] = False
    if args.no_noise:
        c["noise_enabled"] = False
    if args.pip:
        c["pip_enabled"] = True
    return c


def _build_subtitle_params(args):
    """根据 CLI 参数构建字幕处理配置。"""
    s = dict(config.SUBTITLE)
    if args.no_erase_subtitle:
        s["erase_enabled"] = False
    if args.erase_mode is not None:
        s["erase_mode"] = args.erase_mode
    if args.blur_strength is not None:
        s["blur_strength"] = max(1, min(28, args.blur_strength))
    if args.blur_power is not None:
        s["blur_power"] = max(1, min(5, args.blur_power))
    if args.inpaint_radius is not None:
        s["inpaint_radius"] = max(1, min(15, args.inpaint_radius))
    if args.inpaint_dilate is not None:
        s["inpaint_dilate"] = max(0, min(10, args.inpaint_dilate))
    if args.ocr_inpaint_samples is not None:
        s["ocr_inpaint_samples"] = max(3, min(50, args.ocr_inpaint_samples))
    if args.cover_color is not None:
        s["cover_color"] = args.cover_color
    if args.cover_opacity is not None:
        s["cover_opacity"] = max(0.0, min(1.0, args.cover_opacity))
    if args.no_auto_detect_subtitle:
        s["auto_detect"] = False
    if args.detect_frames is not None:
        s["detect_frames"] = max(3, min(8, args.detect_frames))
    if args.subtitle_style is not None:
        s["style"] = args.subtitle_style
    if args.subtitle_position is not None:
        s["position"] = args.subtitle_position
    if args.no_subtitle:
        s["render_enabled"] = False
    if args.whisper:
        s["whisper_enabled"] = True
    if args.whisper_model is not None:
        s["whisper_model"] = args.whisper_model
    s["subtitle_source"] = args.subtitle_source or "auto"
    return s


def _build_tts_params(args):
    """根据 CLI 参数构建 AI 配音配置。"""
    t = dict(config.TTS)
    # 火山引擎 TTS 授权：CLI 参数覆盖环境变量（tts.py 从环境变量读取）
    if getattr(args, "volcengine_app_id", None):
        os.environ["VOLCENGINE_APP_ID"] = args.volcengine_app_id
    if getattr(args, "volcengine_access_key", None):
        os.environ["VOLCENGINE_ACCESS_KEY"] = args.volcengine_access_key
    if getattr(args, "elevenlabs_api_key", None):
        os.environ["ELEVENLABS_API_KEY"] = args.elevenlabs_api_key
        t["elevenlabs_api_key"] = args.elevenlabs_api_key
    if getattr(args, "use_s2s", False):
        t["use_s2s"] = True
    if args.tts_provider is not None:
        t["provider"] = args.tts_provider
    if args.voice is not None:
        # --voice <别名> 即启用配音
        t["enabled"] = True
        t["voice"] = args.voice
    if args.no_voice:
        t["enabled"] = False
    if args.voice_rate is not None:
        t["rate"] = args.voice_rate
    if args.voice_pitch is not None:
        t["pitch"] = args.voice_pitch
    if args.voice_volume is not None:
        t["volume"] = args.voice_volume
    if args.keep_bg_audio:
        t["keep_bg_audio"] = True
    if args.bg_volume is not None:
        t["bg_volume"] = max(0.0, min(1.0, args.bg_volume))
    if args.no_fit_duration:
        t["fit_duration"] = False
    if args.no_clean_text:
        t["clean_text"] = False
    # 智能校验：若选择了火山/ElevenLabs的音色但没指定 provider，自动切换
    if t.get("enabled") and t.get("voice", "").startswith("bd_") and t.get("provider") == "edge":
        t["provider"] = "volcengine"
    elif t.get("enabled") and t.get("voice", "").startswith("el_") and t.get("provider") == "edge":
        t["provider"] = "elevenlabs"
    return t


def _build_bgm_params(args):
    """根据 CLI 参数构建 BGM 配置。"""
    b = dict(config.BGM)
    if args.bgm is not None:
        # --bgm <风格/曲名/auto> 即启用 BGM
        b["enabled"] = True
        b["selection"] = args.bgm
    if args.no_bgm:
        b["enabled"] = False
    if args.bgm_volume is not None:
        b["volume"] = max(0.0, min(1.0, args.bgm_volume))
    if args.no_bgm_fade:
        b["fade"] = False
    return b


def _build_wm_params(args):
    """根据 CLI 参数构建动态水印去除配置。"""
    w = dict(config.WM_REMOVE)
    if args.remove_wm:
        w["enabled"] = True
    # OCR 模式参数
    if args.wm_ocr:
        w["use_ocr"] = True
        w["enabled"] = True  # --wm-ocr 隐含启用
    if args.wm_text is not None:
        w["wm_text"] = args.wm_text
        w["use_ocr"] = True  # 指定 --wm-text 隐含启用 OCR 模式
        w["enabled"] = True
    if args.wm_ocr_samples is not None:
        w["ocr_samples"] = max(3, min(50, args.wm_ocr_samples))
    if args.wm_match_threshold is not None:
        w["match_threshold"] = max(0.1, min(1.0, args.wm_match_threshold))
    # 替换文字（沿原水印轨迹覆盖自己的文字）
    if args.wm_replace is not None:
        w["replace_text"] = args.wm_replace
        w["use_ocr"] = True  # 替换文字需要 OCR 模式定位轨迹
        w["enabled"] = True
        # 替换文字样式（预渲染阶段生效，零逐帧损耗）
        w["replace_opacity"] = max(0.1, min(1.0, args.wm_replace_opacity))
        w["replace_style"] = args.wm_replace_style
        w["replace_font_scale"] = max(0.3, min(3.0, args.wm_replace_font_scale))
        # -1 = 用样式预设值（web_ui 默认），转成 None 传给后端
        outline_val = args.wm_replace_outline
        w["replace_outline"] = None if (outline_val is None or outline_val < 0) else outline_val
        w["replace_bg"] = args.wm_replace_bg
    # 替换水印图片（PNG 带 alpha，沿原水印轨迹覆盖自己的图片）
    # 同时设置 replace_image 和 replace_text 时，图片优先
    if args.wm_replace_image is not None:
        w["replace_image"] = args.wm_replace_image
        w["enabled"] = True  # 指定 --wm-replace-image 隐含启用
        # 图片优先级高于文字：清除 replace_text 避免双重覆盖
        w["replace_text"] = None
        w["replace_image_opacity"] = max(0.1, min(1.0, args.wm_replace_image_opacity))
        w["replace_image_scale"] = max(0.1, min(5.0, args.wm_replace_image_scale))
    # 颜色模式参数
    if args.wm_region is not None:
        w["region"] = args.wm_region
    if args.wm_threshold is not None:
        w["threshold"] = max(0, min(255, args.wm_threshold))
    if args.wm_min_area is not None:
        w["min_area"] = max(10, args.wm_min_area)
    if args.wm_dilate is not None:
        w["dilate"] = max(0, min(20, args.wm_dilate))
    if args.wm_no_track:
        w["use_track"] = False
    # 图片替换的默认模式：未显式指定 OCR/颜色模式时，默认用 OCR 模式
    # （更智能，能区分水印和字幕；用户指定 --wm-region 则走颜色模式）
    if (w.get("replace_image") and w.get("enabled")
            and not w.get("use_ocr") and w.get("region") is None):
        w["use_ocr"] = True
    return w


def _parse_input(note_input, log):
    """自动识别输入来源并解析。

    支持三种输入：
      1. 小红书笔记 URL / note_id → xhs_parser
      2. 抖音视频 URL / 短链 / 分享文本 → douyin_parser
      3. 本地视频文件路径 → 直接构造 NoteData（跳过下载，直接去重+增强）

    返回: NoteData
    """
    text = note_input.strip()
    # 清理 Windows 复制路径时常夹带的 Unicode 不可见控制字符
    # 这些字符肉眼不可见，但会导致 os.path.exists / URL 解析失败
    # U+202A..U+202E 双向格式化、U+200E/U+200F 方向标记、U+00A0 不间断空格、U+FEFF BOM
    text = re.sub(r"[\u202a-\u202e\u200e\u200f\u00a0\ufeff]", "", text).strip()

    # 1. 本地文件路径
    if os.path.exists(text):
        abs_path = os.path.abspath(text)
        base = os.path.splitext(os.path.basename(text))[0]

        # 1a. 本地视频文件（.mp4/.mov/.webm/.avi）
        if re.search(r"\.(mp4|mov|webm|avi|m4v)$", text, re.IGNORECASE):
            log.info("识别为本地视频文件: %s", text)
            note_id = f"local_{base[:16]}"
            dl_dir = os.path.join(config.DOWNLOAD_DIR, note_id)
            os.makedirs(dl_dir, exist_ok=True)
            local_copy = os.path.join(dl_dir, f"video_0{os.path.splitext(text)[1]}")
            if os.path.abspath(abs_path) != os.path.abspath(local_copy):
                import shutil
                shutil.copy2(abs_path, local_copy)
                log.info("已复制到: %s", local_copy)
            return xhs_parser.NoteData(
                note_id=note_id,
                note_type="video",
                title=base,
                desc=base,
                tags=[],
                media_items=[{"kind": "video", "url": "", "local": local_copy}],
                raw={"source": "local", "path": abs_path},
            )

        # 1b. 本地图片文件（.jpg/.jpeg/.png/.webp/.bmp）
        if re.search(r"\.(jpg|jpeg|png|webp|bmp)$", text, re.IGNORECASE):
            log.info("识别为本地图片文件: %s", text)
            note_id = f"local_{base[:16]}"
            dl_dir = os.path.join(config.DOWNLOAD_DIR, note_id)
            os.makedirs(dl_dir, exist_ok=True)
            local_copy = os.path.join(dl_dir, f"image_0{os.path.splitext(text)[1]}")
            if os.path.abspath(abs_path) != os.path.abspath(local_copy):
                import shutil
                shutil.copy2(abs_path, local_copy)
                log.info("已复制到: %s", local_copy)
            return xhs_parser.NoteData(
                note_id=note_id,
                note_type="image",
                title=base,
                desc=base,
                tags=[],
                media_items=[{"kind": "image", "url": "", "local": local_copy}],
                raw={"source": "local", "path": abs_path},
            )

    # 2. 抖音
    if douyin_parser.is_douyin_input(text):
        log.info("识别为抖音链接，使用抖音解析器")
        return douyin_parser.parse_note(text)

    # 3. 小红书（默认）
    log.info("识别为小红书链接")
    return xhs_parser.parse_note(text)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="小红书爆款笔记复刻 + 去重工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
        fromfile_prefix_chars=None,  # 禁用 @file 语法，避免 --wm-replace "@xxx" 被误解析
    )
    parser.add_argument("note", nargs="?",
                        help="小红书 URL/note_id 或 抖音 URL/短链 或 本地视频文件路径")
    parser.add_argument("--no-jianying", action="store_true",
                        help="不生成剪映草稿（仅做 ffmpeg/Pillow 去重）")
    parser.add_argument("--speed", type=float, default=None,
                        help="视频变速（默认 1.06）")
    parser.add_argument("--mirror", action="store_true",
                        help="开启水平镜像去重（注意：会颠倒视频/图片里的文字，含字幕/标题/招牌的笔记不要用）")
    parser.add_argument("--no-mirror", action="store_true",
                        help="（已废弃，镜像默认关闭）兼容旧参数")
    parser.add_argument("--quality", type=int, default=None,
                        help="图片 JPEG 质量（默认 95）")
    parser.add_argument("--crop-ratio", type=float, default=None,
                        help="四边裁剪比例（默认 0.06=各裁掉6%%，同时作用于视频和图片）。"
                             "裁剪后会放大回原尺寸，破坏边缘查重特征。0=不裁剪")
    parser.add_argument("--img-brightness", type=float, default=None,
                        help="图片亮度因子（1.0=不变，默认 1.05。>1 提亮，<1 压暗）")
    parser.add_argument("--img-contrast", type=float, default=None,
                        help="图片对比度因子（1.0=不变，默认 1.06）")
    parser.add_argument("--img-saturation", type=float, default=None,
                        help="图片饱和度因子（1.0=不变，默认 1.12。>1 更鲜艳）")
    parser.add_argument("--img-sharpness", type=float, default=None,
                        help="图片锐度因子（1.0=不变，默认 1.18。>1 更锐利）")
    parser.add_argument("--noise-amount", type=int, default=None,
                        help="图片噪点强度 0-50（默认 5。轻微噪点破坏像素级查重，0=关闭）")
    parser.add_argument("--no-strip-exif", action="store_true",
                        help="保留图片 EXIF 元数据（默认去除，强制重编码时已自动去除）")
    parser.add_argument("--img-format", default=None,
                        choices=["JPEG", "PNG", "WEBP"],
                        help="图片输出格式（默认 JPEG）。PNG 无损但文件大；WEBP 体积小")
    parser.add_argument("--preset", default=None,
                        choices=["ultrafast", "superfast", "veryfast", "faster",
                                 "fast", "medium", "slow", "slower", "veryslow"],
                        help="x264 编码预设（默认 fast）。越快文件越大但耗时短；越慢文件越小但耗时长。"
                             "长视频超时可改 ultrafast/veryfast；追求小文件可改 medium/slow")
    parser.add_argument("--crf", type=int, default=None,
                        help="x264 画质（默认 16，范围 0-51）。数值越小画质越好文件越大；"
                             "16 为超清无损级画质；23 约 medium 质量；28 仍可接受但文件更小")
    parser.add_argument("--hd-enhance", action="store_true", default=None,
                        help="开启画质提升超越原视频模式（高精度 Lanczos 缩放 + Vibrance 智能色彩 + 3x3 精细微观锐化）")
    parser.add_argument("--no-hd-enhance", action="store_true",
                        help="关闭画质提升超越原视频模式")
    parser.add_argument("--no-enhance", action="store_true",
                        help="关闭小红书风格增强（只去重，不做竖屏适配/暖色/磨皮）")
    parser.add_argument("--style-profile", default="default",
                        choices=list(config.STYLE_PROFILES.keys()),
                        help="风格预设: default(通用) lifestyle(生活家居) food(美食) beauty(美妆) scenery(风景) minimal(极简)")
    parser.add_argument("--filter", default=None,
                        help="滤镜预设名（50+ 可选，用 --list-filters 查看全部）。"
                             "如 --filter teal_orange（青橙电影感）/ kodak_portra（胶片）/ japanese（日系）/ "
                             "food_vivid（美食鲜艳）/ portrait_soft（柔肤美白）。默认不应用滤镜")
    parser.add_argument("--list-filters", action="store_true",
                        help="列出全部滤镜预设（按分类: 基础/电影感/胶片/风格化/黑白/美食风景/人像）")
    parser.add_argument("--effect", action="append", default=[], metavar="NAME",
                        help="应用特效（可多次指定叠加多个）。用 --list-effects 查看全部 44 个特效。"
                             "如 --effect snow --effect light_leak_warm（雪花+暖色漏光）/ "
                             "--effect frame_film（胶片框）/ --effect old_film（老电影）")
    parser.add_argument("--list-effects", action="store_true",
                        help="列出全部特效（按分类: 画面/光影/氛围粒子/边框/复古胶片）")
    parser.add_argument("--effect-intensity", type=float, default=1.0,
                        help="特效强度倍数 0.0-2.0（默认 1.0，影响独立特效透明度/混合强度）")
    parser.add_argument("--filter-intensity", type=float, default=1.0,
                        help="滤镜预设强度倍数 0.0-2.0（默认 1.0，作用于滤镜 LUT 强度和预设内置特效 opacity）")
    parser.add_argument("--voice-rewrite", action="store_true",
                        help="用 LLM 对 whisper 识别的原口播内容做二创改写后再配音。"
                             "需配合 --whisper 和 --voice 使用，会保留原意但改变表达风格。"
                             "LLM 不可用时自动回退到原文本。默认关闭。"
                             "配合 --subtitle-source llm-fix 可先纠错再改写，质量更高")
    parser.add_argument("--rewrite-style", default="casual",
                        choices=["casual", "professional", "passion", "gentle",
                                 "knowledge", "humor", "concise", "viral"],
                        help="口播二创风格: casual(口语化) professional(专业带货) "
                             "passion(激情推销) gentle(温柔种草) knowledge(知识科普) "
                             "humor(幽默段子) concise(精简干练) "
                             "viral(爆款脚本结构,参考小红书爆款素材库改写)。默认 casual")
    parser.add_argument("--rewrite-track", default=None,
                        choices=[None, "home_goods", "beauty_makeup", "food_cooking",
                                 "mother_baby", "digital_review", "knowledge_tutorial",
                                 "lifestyle_vlog", "outfit_fashion"],
                        help="viral 风格专用: 指定内容赛道以加载对应爆款脚本结构。"
                             "留空=自动从原文识别赛道。home_goods(家居) beauty_makeup(美妆) "
                             "food_cooking(美食) mother_baby(母婴) digital_review(数码) "
                             "knowledge_tutorial(知识) lifestyle_vlog(生活vlog) outfit_fashion(穿搭)")
    parser.add_argument("--list-rewrite-styles", action="store_true",
                        help="列出全部口播二创风格")
    parser.add_argument("--watermark", default=None,
                        help='水印文字，如 --watermark "@我的小红书号"（默认不加）')
    # 二创叠加层参数
    parser.add_argument("--no-stickers", action="store_true",
                        help="关闭自动贴纸 overlay")
    parser.add_argument("--sticker-count", type=int, default=None,
                        help="贴纸数量（默认 2，最多 4）")
    parser.add_argument("--sticker-opacity", type=float, default=None,
                        help="贴纸透明度 0-1（默认 0.02 = 2%%，极淡）")
    parser.add_argument("--no-sweep", action="store_true",
                        help="关闭扫光效果")
    parser.add_argument("--sweep-opacity", type=float, default=None,
                        help="扫光透明度 0-1（默认 0.05）")
    parser.add_argument("--no-caption", action="store_true",
                        help="关闭底部字幕条")
    parser.add_argument("--caption", default=None,
                        help="自定义字幕条文字（默认自动用笔记标题）")
    parser.add_argument("--caption-style", default=None,
                        choices=["bottom", "top", "none"],
                        help="字幕条样式: bottom(底部) / top(顶部) / none")
    parser.add_argument("--no-noise", action="store_true",
                        help="关闭噪点纹理")
    parser.add_argument("--pip", default=None,
                        help="画中画素材视频路径（如 --pip other.mp4）")
    parser.add_argument("--list-drafts", action="store_true",
                        help="列出当前剪映草稿目录下的草稿")
    parser.add_argument("--enhance-only", default=None,
                        help="跳过下载/去重，直接对已有的视频文件做风格增强+二创（用于迭代调参）")
    # 字幕处理参数
    parser.add_argument("--erase-subtitle", action="store_true", default=None,
                        help="抹除原视频字幕区域（默认开启，--no-erase-subtitle 关闭）")
    parser.add_argument("--no-erase-subtitle", action="store_true",
                        help="不抹除原视频字幕")
    parser.add_argument("--erase-mode", default=None,
                        choices=["blur", "delogo", "cover", "inpaint", "ocr-inpaint"],
                        help="字幕抹除方式: blur(高斯模糊) / delogo(修复式,窄条好) / "
                             "cover(纯色覆盖) / inpaint(OpenCV智能填充,无模糊感,推荐) / "
                             "ocr-inpaint(OCR精修,只修复文字像素,背景完全保留,最自然)")
    parser.add_argument("--blur-strength", type=int, default=None,
                        help="blur 模式模糊半径（boxblur luma_radius，1-28，越大越模糊，默认 20）")
    parser.add_argument("--blur-power", type=int, default=None,
                        help="blur 模式模糊次数（boxblur luma_power，1-5，越大越模糊，默认 3）")
    parser.add_argument("--inpaint-radius", type=int, default=None,
                        help="inpaint/ocr-inpaint 模式修复半径（默认 4，越大修复范围越大但可能模糊）")
    parser.add_argument("--inpaint-dilate", type=int, default=None,
                        help="inpaint/ocr-inpaint 模式 mask 膨胀半径（默认 2，扩大修复区域覆盖文字描边）")
    parser.add_argument("--ocr-inpaint-samples", type=int, default=None,
                        help="ocr-inpaint 模式 OCR 采样帧数（默认 12，越多越准但越慢）")
    parser.add_argument("--cover-color", default=None,
                        help="cover 模式覆盖色（black/white/red 或 #RRGGBB，默认 black）")
    parser.add_argument("--cover-opacity", type=float, default=None,
                        help="cover 模式覆盖透明度（0-1，1=完全不透明，默认 0.9）")
    parser.add_argument("--no-auto-detect-subtitle", action="store_true",
                        help="关闭字幕区域自动识别（用 config.SUBTITLE.erase_area 默认值）")
    parser.add_argument("--detect-subtitle-only", default=None,
                        help="只检测字幕区域并打印结果，不做其他处理（用于调试/确认区域）")
    parser.add_argument("--detect-frames", type=int, default=None,
                        help="字幕区域自动识别抽帧数量（3-8，默认 5）")
    parser.add_argument("--subtitle-style", default=None,
                        choices=list(config.SUBTITLE_STYLES.keys()),
                        help="新字幕样式预设: default/bold_yellow/clean_box/cute_pink/top_title")
    parser.add_argument("--subtitle-position", default=None,
                        choices=["auto", "bottom", "top", "center"],
                        help="字幕位置: auto(跟随原字幕位置) / bottom(底部) / top(顶部) / center(中部)，默认 auto")
    parser.add_argument("--subtitle-source", default="auto",
                        choices=["auto", "whisper", "llm-fix", "note"],
                        help="字幕文本来源: auto(智能选择) / whisper(原识别,可能有错别字) / "
                            "llm-fix(whisper识别+LLM纠错,减少错别字,推荐) / note(笔记文案,无错别字)。"
                            "默认 auto。llm-fix 需配合 --llm-provider 使用")
    parser.add_argument("--no-subtitle", action="store_true",
                        help="不渲染新字幕（只抹除原字幕）")
    parser.add_argument("--whisper", action="store_true",
                        help="用 whisper 语音识别提取字幕（需 pip install -U openai-whisper）")
    parser.add_argument("--whisper-model", default=None,
                        choices=["tiny", "base", "small", "medium"],
                        help="whisper 模型大小（默认 base）")
    # AI 配音参数（支持 edge-tts 和 火山引擎 Seed-TTS 2.0）
    parser.add_argument("--tts-provider", default=None,
                        choices=["edge", "volcengine", "elevenlabs"],
                        help="TTS 服务商: edge(edge-tts,默认,免费) / "
                             "volcengine(火山引擎Seed-TTS 2.0,需API key) / "
                             "elevenlabs(ElevenLabs 超真实/S2S变声,需API key)。")
    parser.add_argument("--volcengine-app-id", default=None,
                        help="火山引擎 TTS 的 APP ID（覆盖环境变量 VOLCENGINE_APP_ID）。")
    parser.add_argument("--volcengine-access-key", default=None,
                        help="火山引擎 TTS 的 Access Token（覆盖环境变量 VOLCENGINE_ACCESS_KEY）。")
    parser.add_argument("--elevenlabs-api-key", default=None,
                        help="ElevenLabs API Key（覆盖环境变量 ELEVENLABS_API_KEY）。")
    parser.add_argument("--use-s2s", action="store_true",
                        help="启用 ElevenLabs Speech-to-Speech 变声模式（100% 继承原视频情绪语气与停顿）")
    parser.add_argument("--voice", default=None,
                        help='AI 配音别名（如 xiaoxiao/yunjian 或 el_rachel/el_domi 或 Voice ID，--list-voices 查看全部）。'
                             '指定后自动启用配音，替换原音频。')
    parser.add_argument("--voice-text", default=None,
                        help='配音文本（默认用笔记标题+描述自动生成）')
    parser.add_argument("--voice-rate", default=None,
                        help='配音语速（如 +10%%加快 / -10%%减慢，默认 +0%% 正常）')
    parser.add_argument("--voice-pitch", default=None,
                        help='配音音调（如 +5Hz偏高 / -5Hz偏低，默认 +0Hz 正常）')
    parser.add_argument("--voice-volume", default=None,
                        help='配音音量（如 +20%%变大 / -20%%变小，默认 +0%% 正常）')
    parser.add_argument("--keep-bg-audio", action="store_true",
                        help="保留原视频音频作为BGM（降低音量混合），默认完全替换原音频")
    parser.add_argument("--bg-volume", type=float, default=None,
                        help="保留BGM时的音量 0-1（默认 0.15 = 15%% 背景音）")
    parser.add_argument("--no-voice", action="store_true",
                        help="关闭 AI 配音（即使 config.TTS.enabled=True 也跳过）")
    parser.add_argument("--no-fit-duration", action="store_true",
                        help="不调整配音时长以匹配视频（配音可能比视频长或短）")
    parser.add_argument("--no-clean-text", action="store_true",
                        help="不清理配音文本（保留#标签/emoji，可能影响自然度）")
    parser.add_argument("--list-voices", action="store_true",
                        help="列出所有可用的 AI 配音（edge-tts 26 个 + 火山引擎 24 个）")
    # 背景音乐 BGM 参数
    parser.add_argument("--bgm", default=None,
                        help='背景音乐：风格名(如 治愈/欢快/古风) / 曲名 / auto(按笔记标签自动选)。'
                             '指定后自动启用BGM，与配音共存（BGM为背景音）。'
                             '音乐文件放 assets/bgm/，命名：风格_曲名.mp3。--list-bgm 查看推荐清单')
    parser.add_argument("--bgm-volume", type=float, default=None,
                        help="BGM 音量 0-1（默认 0.15 = 15%% 背景音，配音为主）")
    parser.add_argument("--no-bgm", action="store_true",
                        help="关闭 BGM（即使 config.BGM.enabled=True 也跳过）")
    parser.add_argument("--no-bgm-fade", action="store_true",
                        help="不给 BGM 加淡入淡出")
    parser.add_argument("--list-bgm", action="store_true",
                        help="列出 assets/bgm/ 可用 BGM + 小红书热门 BGM 推荐清单")
    # 动态水印去除（OpenCV）
    parser.add_argument("--remove-wm", action="store_true",
                        help="启用动态水印去除（OpenCV 逐帧 inpaint 修复）。"
                             "针对视频中移动的水印（如 @原作者 飘动文字）。"
                             "需 pip install opencv-python。处理较慢（逐帧 CPU）")
    parser.add_argument("--wm-region", default=None,
                        help='水印起始区域 "x,y,w,h"（像素或百分比，如 "100,100,300,80" 或 "5%%,5%%,30%%,10%%"）。'
                             "不指定则每帧自动检测亮色区域（可能误删字幕，建议指定区域更精确）")
    parser.add_argument("--wm-threshold", type=int, default=None,
                        help="水印检测亮度阈值 0-255（默认 200，水印通常是亮色 >200；"
                             "越小越严格（漏检多但误删少），越大越宽松（可能误删字幕）")
    parser.add_argument("--wm-min-area", type=int, default=None,
                        help="水印最小像素面积（默认 200，过滤小噪点；小水印可降到 100）")
    parser.add_argument("--wm-dilate", type=int, default=None,
                        help="膨胀半径（默认 3，扩大修复区域覆盖文字描边；"
                             "水印有粗描边可加到 5-8）")
    parser.add_argument("--wm-no-track", action="store_true",
                        help="颜色模式：关闭 CSRT 追踪器，每帧独立检测。慢但更鲁棒"
                             "（水印变形/快速移动时追踪会失败，用此参数改用每帧检测）")
    # OCR 模式参数
    parser.add_argument("--wm-ocr", action="store_true",
                        help="启用 OCR 模式（推荐）：用 PaddleOCR 识别水印文字内容，"
                             "能区分水印和字幕，不会误删字幕。需 pip install paddleocr。"
                             "自动识别包含 @ 符号的水印，或用 --wm-text 指定关键词")
    parser.add_argument("--wm-text", default=None,
                        help='OCR 模式：水印文字关键词（逗号分隔），如 --wm-text "@原创,搬运"。'
                             "不指定时自动识别包含 @ 的文字 + 常见水印词（原创/搬运/转载等）")
    parser.add_argument("--wm-ocr-samples", type=int, default=None,
                        help="OCR 模式：采样帧数（默认 10，越多越准但越慢）。"
                             "水印移动范围大时可加到 15-20")
    parser.add_argument("--wm-match-threshold", type=float, default=None,
                        help="OCR 模式：模板匹配置信度阈值 0-1（默认 0.5，越低越宽松可能误匹配，"
                             "越高越严格可能漏匹配）")
    parser.add_argument("--wm-replace", default=None,
                        help='OCR 模式：替换文字（如 "@你的号"），去除原水印后在原位置覆盖自己的文字，'
                             '跟随原水印移动轨迹。自动启用 OCR 模式。'
                             '注意：与 --watermark 不同，--wm-replace 是沿原水印轨迹动态覆盖，'
                             '--watermark 是在右下角静态叠加。')
    parser.add_argument("--wm-replace-opacity", type=float, default=1.0,
                        help="替换文字不透明度 0.1-1.0（默认 1.0=完全不透明）。"
                             "预渲染阶段写入 alpha 通道，零逐帧性能损耗。"
                             "0.5=半透明适合不喧宾夺主的弱水印")
    parser.add_argument("--wm-replace-style", default="white_black",
                        choices=["white_black", "white_none", "black_white", "yellow_black",
                                 "red_white", "white_shadow", "pink_black", "cyan_black"],
                        help="替换文字样式预设（默认 white_black 白字黑边）。"
                             "white_none(白字无边) black_white(黑字白边) yellow_black(黄字黑边,醒目) "
                             "red_white(红字白边) white_shadow(白字阴影) pink_black(粉字黑边) cyan_black(青字黑边)")
    parser.add_argument("--wm-replace-font-scale", type=float, default=1.0,
                        help="替换文字字体大小缩放系数（默认 1.0=用原水印高度，1.2=放大20%%，0.8=缩小20%%）")
    parser.add_argument("--wm-replace-outline", type=int, default=None,
                        help="手动指定描边宽度（默认 None=用样式预设值，0=不描边，1-10=指定宽度）")
    parser.add_argument("--wm-replace-bg", action="store_true", default=False,
                        help="替换文字加半透明黑色背景框（提升复杂背景下的可读性）")
    parser.add_argument("--wm-replace-image", default=None,
                        help='替换水印图片路径（PNG，带 alpha 通道），去除原水印后在原位置叠加自己的图片，'
                             '跟随原水印移动轨迹。适合 logo/二维码/复杂图形水印。'
                             '自动启用水印去除；与 --wm-replace 互斥（同时指定时图片优先）。'
                             '默认用 OCR 模式定位（识别文字水印轨迹），搭配 --wm-region 改用颜色模式。'
                             '注意：与 --watermark 不同，--wm-replace-image 是沿原水印轨迹动态覆盖，'
                             '--watermark 是在右下角静态叠加。')
    parser.add_argument("--wm-replace-image-opacity", type=float, default=1.0,
                        help="替换图片不透明度 0.1-1.0（默认 1.0=完全不透明）。"
                             "0.5=半透明适合不喧宾夺主的弱水印")
    parser.add_argument("--wm-replace-image-scale", type=float, default=1.0,
                        help="替换图片缩放系数（默认 1.0=适应原水印宽度，1.2=放大20%%，0.8=缩小20%%）")
    # BGM 下载助手
    parser.add_argument("--download-bgm", action="store_true",
                        help="从 assets/bgm/_sources.json 批量下载免版税音乐并自动重命名归类。"
                             "搭配 --dry-run 可预览不下载；搭配 --list-sources 查看合法音乐来源")
    parser.add_argument("--dry-run", action="store_true",
                        help="（与 --download-bgm 搭配）仅预览将下载的文件，不实际下载")
    parser.add_argument("--list-sources", action="store_true",
                        help="列出合法免版税音乐来源（Pixabay/Bensound/FMA 等）")
    # 封面生成 + 文案二创
    parser.add_argument("--no-cover", action="store_true",
                        help="不生成封面（默认生成 6 种爆款风格封面 + 保留原封面）")
    parser.add_argument("--no-copywrite", action="store_true",
                        help="不生成二创文案（默认生成爆款标题候选 + 正文 + 标签）")
    parser.add_argument("--cover-styles", default=None,
                        help="封面风格（逗号分隔）: bigtext(四宫格干货合集) / bottombar(反向种草劝退) / "
                             "topbar(垂直人群信任) / magazine(单图大字爆款) / vlog_card(VLOG微透卡片) / "
                             "list_banner(清单三图横幅)。默认全部 6 种生成")
    parser.add_argument("--cover-title", default=None,
                        help="封面标题文字（默认用二创 Top1 标题）")
    parser.add_argument("--erase-cover-text", action="store_true",
                        help="去除底图上原有的文字（标题/水印/作者名等）再合成新封面。"
                             "用 PaddleOCR 检测文字 + cv2.inpaint 修复，需安装 paddleocr。"
                             "原封面副本(cover_原封面.jpg)不受影响。默认关闭")
    parser.add_argument("--n-titles", type=int, default=None,
                        help="二创标题候选数量（默认 10）")
    # AI 生图封面（调用文生图模型，需配合 --llm-provider doubao 或 openai）
    parser.add_argument("--ai-cover", action="store_true",
                        help="启用 AI 生图封面：调用文生图模型生成 7 种风格封面"
                            "（产品主角/前后对比/钩子特写/场景生活/清单信息图/情绪卡片/场景痛点）。"
                            "需配合 --llm-provider doubao(豆包Seedream) 或 openai(DALL-E 3)。默认关闭")
    parser.add_argument("--ai-cover-styles", default=None,
                        help="AI 封面风格（逗号分隔）: product_hero/before_after/hook_closeup/"
                            "scene_lifestyle/list_infographic/emotion_card/pain_scenario。默认全部生成")
    parser.add_argument("--ai-cover-size", default=None,
                        help="AI 生图尺寸: 1024x1024(默认) / 960x1280 / 720x1080(豆包) / "
                            "1792x1024 / 1024x1792(DALL-E 3)")
    parser.add_argument("--list-ai-covers", action="store_true",
                        help="列出全部 AI 生图封面风格")
    # LLM 大模型参数（可选，用于文案和封面标题二创）
    parser.add_argument("--llm", action="store_true",
                        help="启用 LLM 大模型生成文案和封面标题（需配置 API Key，失败回退模板库）。"
                             "环境变量配置见 README「LLM 大模型」章节")
    parser.add_argument("--llm-only", action="store_true",
                        help="强制用 LLM，失败不回退模板库（适合调试 API 配置）")
    parser.add_argument("--llm-provider", default=None,
                        choices=["doubao", "qwen", "deepseek", "openai", "grsai", "modelscope"],
                        help="LLM 服务商: doubao(豆包) / qwen(通义千问) / deepseek / openai / "
                             "grsai(GrsAI聚合,支持文生图,国内直连) / "
                             "modelscope(魔搭,免费文生图Z-Image-Turbo,每日2000次)。"
                             "也可用环境变量 LLM_PROVIDER 指定")
    parser.add_argument("--llm-model", default=None,
                        help="LLM 模型名（如 doubao-pro-32k / qwen-plus / deepseek-v4-flash / "
                             "gpt-4o-mini / sora-image(GrsAI文生图)）。不指定则用服务商默认")
    parser.add_argument("--grsai-image-model", default=None,
                        choices=["sora-image", "gpt-image-1.5", "nano-banana", "nano-banana-pro"],
                        help="GrsAI 文生图模型（仅 --llm-provider grsai 时生效）: "
                             "sora-image(GPT4o,0.02元/张,默认) / gpt-image-1.5(0.02元/张) / "
                             "nano-banana(0.022元/张) / nano-banana-pro(0.09元/张)")
    parser.add_argument("--llm-api-key", default=None,
                        help="LLM API Key（建议用环境变量 ARK_API_KEY/DASHSCOPE_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY）")
    parser.add_argument("--prompt-llm-provider", default=None,
                        choices=["doubao", "qwen", "deepseek", "openai", "grsai", "modelscope"],
                        help="生图 prompt 专用 LLM 服务商（仅 --ai-cover 时生效）: "
                             "用于生成英文生图 prompt 和封面钩子文案，可与文生图服务商分离。"
                             "例如 --llm-provider grsai --prompt-llm-provider deepseek "
                             "= 文生图用 grsai sora-image，生图 prompt 用 deepseek-v4-flash（更便宜）。"
                             "不指定则与 --llm-provider 相同")
    parser.add_argument("--prompt-llm-model", default=None,
                        help="生图 prompt 专用 LLM 模型名（仅 --prompt-llm-provider 时生效，"
                             "如 deepseek-v4-flash / gpt-4o / qwen-plus）。不指定则用服务商默认")
    parser.add_argument("--prompt-llm-api-key", default=None,
                        help="生图 prompt 专用 LLM API Key（仅 --prompt-llm-provider 时生效）。"
                             "不指定则从环境变量读取。用于 prompt 服务商与主服务商不同时分别授权")
    parser.add_argument("--image-llm-provider", default=None,
                        choices=["doubao", "openai", "grsai", "modelscope"],
                        help="文生图/图生图专用 LLM 服务商（仅 --ai-cover 时生效）: "
                             "用于把图片生成独立到支持文生图的服务商。"
                             "例如 --llm-provider deepseek --image-llm-provider modelscope "
                             "= 二创文案用 deepseek，文生图用魔搭Z-Image-Turbo(免费)。"
                             "不指定则与 --llm-provider 相同")
    parser.add_argument("--image-llm-model", default=None,
                        help="文生图/图生图模型名（仅 --ai-cover 时生效，"
                             "如 nano-banana-pro / sora-image / gpt-image-1.5 / dall-e-3）。"
                             "图生图模式推荐 nano-banana-pro（效果最好）。不指定则用服务商默认")
    parser.add_argument("--image-llm-api-key", default=None,
                        help="文生图/图生图专用 LLM API Key（仅 --image-llm-provider 时生效）。"
                             "不指定则从环境变量读取。例如主 LLM 用 deepseek，文生图用 grsai 时需单独填 grsai 的 key")
    # 文生图备用服务商（负载均衡/故障转移）：主服务商失败时自动切换
    # 典型场景：主用 GrsAI gpt-image-2，遇到 output_moderation 审核拦截时切换到魔搭 Z-Image-Turbo
    parser.add_argument("--image-llm-fallback-provider", default=None,
                        choices=["doubao", "openai", "grsai", "modelscope"],
                        help="文生图备用服务商（主服务商失败时自动切换，仅 --ai-cover 时生效）。"
                             "典型用法：--image-llm-provider grsai --image-llm-fallback-provider modelscope "
                             "= 主用 GrsAI，失败时切换到魔搭(免费)")
    parser.add_argument("--image-llm-fallback-model", default=None,
                        help="文生图备用模型名（配合 --image-llm-fallback-provider 使用）。"
                             "魔搭推荐 Tongyi-MAI/Z-Image-Turbo（免费，不支持图生图会自动降级为文生图）")
    parser.add_argument("--image-llm-fallback-api-key", default=None,
                        help="文生图备用 API Key（魔搭用 token，None=从环境变量读取）")
    parser.add_argument("--image-mode", default="t2i",
                        choices=["t2i", "i2i", "t2i+i2i"],
                        help="AI 封面图片生成模式（仅 --ai-cover 时生效）: "
                             "t2i=文生图（默认，从零生成） | "
                             "i2i=图生图（基于原封面重新生成） | "
                             "t2i+i2i=混合模式（以图生图为主，原产品保真，prompt 辅助场景美化）")
    parser.add_argument("--reference-image", default=None,
                        help="图生图/t2i+i2i 模式的参考图（仅 --ai-cover 时生效）。"
                             "可传公网 URL 或本地图片路径。"
                             "优先级: --reference-image > 原封面 URL > 本地视频抽帧。"
                             "本地路径会自动转 base64 data URL 传给图生图 API")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="详细日志")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    log = logging.getLogger("cli")

    config.ensure_dirs()

    if args.list_drafts:
        drafts = jianying.list_jianying_drafts()
        print("剪映草稿目录:", config.JIANYING_DRAFT_ROOT or "(未检测到)")
        if not drafts:
            print("  (无草稿)")
        for d in drafts:
            print("  -", d)
        return 0

    # 列出所有 AI 配音（edge-tts + 火山引擎）
    if args.list_voices:
        from core import tts as tts_mod
        if not tts_mod._HAS_EDGE_TTS:
            print("⚠️ edge-tts 未安装，请先运行: pip install edge-tts")
            print("   edge-tts 配音仍可查看，但需安装后才能使用。")
            print()
        if not tts_mod._has_volcengine():
            print("⚠️ 火山引擎 TTS 未配置：")
            if not tts_mod._HAS_REQUESTS:
                print("   1. 安装 requests: pip install requests")
            print("   2. 设置环境变量: VOLCENGINE_APP_ID 和 VOLCENGINE_ACCESS_KEY")
            print("   火山引擎音色列表仍可查看，但需配置后才能使用。")
            print()
        print(tts_mod.print_voices())
        return 0

    # 列出全部滤镜预设
    if args.list_filters:
        from core import filters as filters_mod
        print("=" * 60)
        print("滤镜与特效预设（50+）")
        print("=" * 60)
        print()
        print(f"LUT 目录: {filters_mod.LUTS_DIR}")
        print(f"叠加素材目录: {filters_mod.OVERLAYS_DIR}")
        print()
        filters_mod.print_presets()
        print()
        print("用法: python xhs_clone.py \"URL\" --filter <预设名>")
        print("示例: python xhs_clone.py \"URL\" --filter teal_orange")
        return 0

    # 列出全部特效
    if args.list_effects:
        from core import filters as filters_mod
        print("=" * 60)
        print("独立特效（44 个，可用 --effect 叠加）")
        print("=" * 60)
        print()
        print(f"叠加素材目录: {filters_mod.OVERLAYS_DIR}")
        print()
        filters_mod.print_effects()
        print()
        print("用法: python xhs_clone.py \"URL\" --effect <特效名> [--effect <特效名2> ...]")
        print("示例:")
        print("  python xhs_clone.py \"URL\" --effect snow --effect light_leak_warm")
        print("  python xhs_clone.py \"URL\" --effect frame_film --effect vignette_strong")
        print("  python xhs_clone.py \"URL\" --effect old_film --effect flicker")
        print("  python xhs_clone.py \"URL\" --effect snow --effect-intensity 1.5")
        return 0

    # 列出口播二创风格
    if args.list_rewrite_styles:
        from core import llm as llm_mod
        print("=" * 60)
        print("口播二创风格（配合 --voice-rewrite 使用）")
        print("=" * 60)
        print()
        print("用法: python xhs_clone.py \"URL\" --whisper --voice auto --voice-rewrite --rewrite-style <风格>")
        print()
        for name, cfg in llm_mod.REWRITE_STYLES.items():
            print(f"  {name:<14} {cfg['name_cn']:<8} {cfg['desc']}")
        print()
        print("viral 风格（爆款脚本结构）赛道选项（--rewrite-track）:")
        print("  home_goods(家居) beauty_makeup(美妆) food_cooking(美食) "
              "mother_baby(母婴)\n  digital_review(数码) knowledge_tutorial(知识) "
              "lifestyle_vlog(生活vlog) outfit_fashion(穿搭)")
        print("  留空=自动从原文识别赛道（推荐）")
        print()
        print("示例:")
        print("  python xhs_clone.py \"URL\" --whisper --voice auto --voice-rewrite --rewrite-style professional")
        print("  python xhs_clone.py \"URL\" --whisper --voice xiaoxiao --voice-rewrite --rewrite-style gentle")
        print("  python xhs_clone.py \"URL\" --whisper --voice auto --voice-rewrite --rewrite-style viral")
        print("  python xhs_clone.py \"URL\" --whisper --voice auto --voice-rewrite --rewrite-style viral --rewrite-track home_goods")
        return 0

    # 列出 AI 生图封面风格
    if args.list_ai_covers:
        from core import llm as llm_mod
        print("=" * 60)
        print("AI 生图封面风格（配合 --ai-cover 使用）")
        print("=" * 60)
        print()
        print("用法: python xhs_clone.py \"URL\" --llm-provider doubao --ai-cover")
        print("      python xhs_clone.py \"URL\" --llm-provider doubao --ai-cover --ai-cover-styles product_hero,before_after")
        print()
        print("支持的生图模型:")
        for name, cfg in llm_mod.PROVIDER_CONFIGS.items():
            im = cfg.get("image_model")
            tag = f"  {im}" if im else "  (不支持)"
            print(f"  {name:<12} {cfg['name_cn']:<16}{tag}")
        print()
        print("封面风格:")
        for name, cfg in llm_mod.AI_COVER_STYLES.items():
            print(f"  {name:<20} {cfg['name_cn']:<8} {cfg['desc']}")
            hooks = " / ".join(cfg.get("hook_examples", []))
            print(f"  {'':20} 钩子示例: {hooks}")
        print()
        print("示例:")
        print("  python xhs_clone.py \"URL\" --llm-provider doubao --ai-cover")
        print("  python xhs_clone.py \"URL\" --llm --llm-provider openai --ai-cover --ai-cover-styles hook_closeup,emotion_card")
        return 0

    if args.list_bgm:
        from core import bgm as bgm_mod
        available = bgm_mod.scan_bgm_dir()
        print("=" * 60)
        print("背景音乐（BGM）")
        print("=" * 60)
        print()
        print(f"BGM 目录: {config.BGM.get('dir', os.path.join(config.ASSETS_DIR, 'bgm'))}")
        print()
        if available:
            print(f"✅ 已有 {len(available)} 个 BGM 文件:")
            for style, name, fn, full in available:
                print(f"   [{style}] {name}  ({fn})")
        else:
            print("⚠️  BGM 目录暂无音乐文件")
            print("   请自行下载音乐放到该目录，文件名规范：风格_曲名.mp3")
            print("   （如：治愈_起风了.mp3 / 欢快_蜜雪冰城.mp3）")
        print()
        print("-" * 60)
        print("小红书热门 BGM 推荐清单（按风格分类）")
        print("-" * 60)
        print("可按曲名自行搜索下载，保存为：风格_曲名.mp3")
        print()
        for style, songs in bgm_mod.BGM_RECOMMENDATIONS.items():
            print(f"【{style}】")
            for i, s in enumerate(songs, 1):
                print(f"  {i}. {s}  →  保存为: {style}_{s}.mp3")
            print()
        print("-" * 60)
        print("用法:")
        print("  python xhs_clone.py URL --bgm auto          # 自动选风格")
        print('  python xhs_clone.py URL --bgm 治愈          # 按风格选')
        print('  python xhs_clone.py URL --bgm 起风了        # 按曲名选')
        print("  python xhs_clone.py URL --bgm auto --voice auto  # 配音+BGM")
        print("  python xhs_clone.py URL --bgm 治愈 --bgm-volume 0.2")
        return 0

    # 列出合法免版税音乐来源
    if args.list_sources:
        from core import bgm_downloader
        bgm_downloader.print_sources_guide()
        return 0

    # 批量下载 BGM（从 _sources.json）
    if args.download_bgm:
        from core import bgm_downloader
        # 若 _sources.json 不存在，先创建模板
        sources_path = os.path.join(config.ASSETS_DIR, "bgm", "_sources.json")
        if not os.path.exists(sources_path):
            print("未找到 _sources.json，已为你创建模板：")
            print(f"  {sources_path}")
            bgm_downloader.create_sources_template(sources_path)
            print()
            print("请编辑该文件，填入免版税音乐直链后重新运行 --download-bgm")
            print("可用 --list-sources 查看合法音乐来源（Pixabay 等）")
            return 0
        ok, skipped, failed = bgm_downloader.download_all(dry_run=args.dry_run)
        if not args.dry_run and ok > 0:
            print()
            print(f"✅ 成功下载 {ok} 个 BGM，可用 --list-bgm 查看")
        elif ok == 0 and skipped > 0 and failed == 0:
            print()
            print("所有条目已存在或为空，无需下载")
        return 0 if failed == 0 else 1

    # 仅检测字幕区域（调试用）
    if args.detect_subtitle_only:
        from core import subtitle as subtitle_mod
        src = os.path.abspath(args.detect_subtitle_only)
        if not os.path.exists(src):
            print(f"文件不存在: {src}")
            return 1
        w, h = dedup_video.probe_resolution(src)
        d = dedup_video.probe_duration(src)
        print(f"视频: {os.path.basename(src)}  {w}x{h}  {d:.2f}s")
        n_frames = 5
        if args.detect_frames is not None:
            n_frames = max(3, min(8, args.detect_frames))
        print(f"抽帧数量: {n_frames}")
        print("检测中...")
        area = subtitle_mod.detect_subtitle_area(src, d, num_frames=n_frames)
        print()
        print("=" * 50)
        if area:
            print(f"✅ 检测到字幕区域（相对画面比例）:")
            print(f"   x = {area['x']:.3f}  (左)")
            print(f"   y = {area['y']:.3f}  (上边界，占画面高度比例)")
            print(f"   w = {area['w']:.3f}  (宽度比例)")
            print(f"   h = {area['h']:.3f}  (高度比例)")
            print()
            if w and h:
                px = int(w * area['x'])
                py = int(h * area['y'])
                pw = int(w * area['w'])
                ph = int(h * area['h'])
                print(f"   像素坐标（原视频 {w}x{h}）:")
                print(f"   起点: ({px}, {py})  尺寸: {pw}x{ph}")
            print()
            print("提示：此区域会自动用于抹除。如要覆盖，编辑 config.py 的 SUBTITLE.erase_area。")
        else:
            print("⚠️ 未检测到明显字幕区域，将使用默认区域:")
            print(f"   {config.SUBTITLE['erase_area']}")
            print("提示：可尝试 --detect-frames 8 增加抽帧数量，或确认视频是否有字幕。")
        print("=" * 50)
        return 0

    # 仅做风格增强（用于对已有去重视频迭代调参，不重新下载/去重）
    if args.enhance_only:
        src = os.path.abspath(args.enhance_only)
        if not os.path.exists(src):
            print(f"文件不存在: {src}")
            return 1
        enhance_params = None if args.no_enhance else dict(
            config.STYLE_PROFILES.get(args.style_profile, config.XHS_STYLE))
        creative_params = _build_creative_params(args)
        subtitle_cfg = _build_subtitle_params(args)
        tts_cfg = _build_tts_params(args)
        bgm_cfg = _build_bgm_params(args)
        wm_cfg = _build_wm_params(args)
        caption_text = args.caption or ""
        voice_text = args.voice_text or ""
        # 滤镜预设（--filter 指定）
        filter_preset = args.filter or config.FILTER.get("preset")
        if filter_preset:
            from core import filters as filters_mod
            if not filters_mod.get_preset(filter_preset):
                print(f"未知滤镜预设: {filter_preset}（用 --list-filters 查看可用预设）")
                return 1
        # 独立特效（--effect 指定，可叠加多个）
        effects = args.effect or []
        effect_intensity = args.effect_intensity
        filter_intensity = args.filter_intensity
        if effects:
            from core import filters as filters_mod
            for eff_name in effects:
                if eff_name not in filters_mod.EFFECT_PRESETS:
                    print(f"未知特效: {eff_name}（用 --list-effects 查看可用特效）")
                    return 1
        # enhance-only 模式下 --voice auto：用文件名+voice_text 做关键词匹配
        if tts_cfg.get("enabled") and tts_cfg.get("voice") == "auto":
            from core import tts as tts_mod
            base_name = os.path.splitext(os.path.basename(src))[0]
            chosen, scene, matched = tts_mod.auto_voice(
                [], base_name, voice_text, provider=tts_cfg.get("provider", "edge"))
            tts_cfg = dict(tts_cfg, voice=chosen)
            print(f"  自动配音: {chosen} ({scene})")
            if matched:
                print(f"    命中关键词: {', '.join(matched[:5])}")
        pip_path = os.path.abspath(args.pip) if args.pip else None
        out_dir = os.path.dirname(src) or "."
        base = os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(out_dir, f"{base}_enhanced.mp4")
        log.info("仅做风格增强+二创+字幕" +
                 ("+滤镜" if filter_preset else "") +
                 (f"+特效[{len(effects)}个]" if effects else "") +
                 ("+AI配音" if tts_cfg.get("enabled") else "") +
                 ("+BGM" if bgm_cfg.get("enabled") else "") + f": {src} -> {dst}")
        # enhance-only 模式下没有 note_text，用 caption_text 作为字幕来源
        r = style_enhance.enhance_video(
            src, dst, enhance_params, creative_params,
            args.watermark, caption_text, pip_path, subtitle_cfg,
            caption_text, tts_cfg, voice_text, bgm_cfg, wm_cfg, filter_preset,
            effects, effect_intensity,
            filter_intensity=filter_intensity,
            voice_rewrite=args.voice_rewrite, rewrite_style=args.rewrite_style,
            subtitle_source=args.subtitle_source)
        if r:
            print(f"\n✅ 风格增强完成: {r}")
            return 0
        print("风格增强失败")
        return 3

    if not args.note:
        parser.print_help()
        return 1

    # 构建去重参数（覆盖 config）
    # 镜像默认关闭（避免颠倒文字）；--mirror 显式开启；--no-mirror 向后兼容（无效，默认即关）
    vparams = dict(config.VIDEO_DEDUP)
    if args.speed is not None:
        vparams["speed"] = args.speed
        vparams["audio_tempo"] = args.speed
    if args.mirror:
        vparams["mirror"] = True
    if args.hd_enhance:
        vparams["hd_enhance"] = True
    elif args.no_hd_enhance:
        vparams["hd_enhance"] = False
    if args.preset is not None:
        vparams["preset"] = args.preset
    if args.crf is not None:
        vparams["crf"] = max(0, min(51, args.crf))
    if args.crop_ratio is not None:
        vparams["crop_ratio"] = max(0.0, min(0.2, args.crop_ratio))

    iparams = dict(config.IMAGE_DEDUP)
    if args.mirror:
        iparams["mirror"] = True
    if args.quality is not None:
        iparams["quality"] = max(50, min(100, args.quality))
    if args.crop_ratio is not None:
        iparams["crop_ratio"] = max(0.0, min(0.2, args.crop_ratio))
    if args.img_brightness is not None:
        iparams["brightness"] = max(0.5, min(2.0, args.img_brightness))
    if args.img_contrast is not None:
        iparams["contrast"] = max(0.5, min(2.0, args.img_contrast))
    if args.img_saturation is not None:
        iparams["saturation"] = max(0.0, min(2.0, args.img_saturation))
    if args.img_sharpness is not None:
        iparams["sharpness"] = max(0.5, min(3.0, args.img_sharpness))
    if args.noise_amount is not None:
        iparams["noise_amount"] = max(0, min(50, args.noise_amount))
    if args.no_strip_exif:
        iparams["strip_exif"] = False
    if args.img_format is not None:
        iparams["format"] = args.img_format

    # 风格增强参数（默认开启，--no-enhance 关闭）
    enhance_params = None if args.no_enhance else dict(config.STYLE_PROFILES.get(args.style_profile, config.XHS_STYLE))
    if enhance_params:
        if args.hd_enhance:
            enhance_params["hd_enhance"] = True
        elif args.no_hd_enhance:
            enhance_params["hd_enhance"] = False
        if args.crf is not None:
            enhance_params["crf"] = max(0, min(51, args.crf))
        if args.preset is not None:
            enhance_params["preset"] = args.preset
    creative_params = _build_creative_params(args)
    watermark = args.watermark
    pip_path = os.path.abspath(args.pip) if args.pip else None
    tts_cfg = _build_tts_params(args)
    bgm_cfg = _build_bgm_params(args)
    wm_cfg = _build_wm_params(args)
    voice_text = args.voice_text or ""
    # 滤镜预设（--filter 指定，None=不应用）
    filter_preset = args.filter or config.FILTER.get("preset")
    if filter_preset:
        from core import filters as filters_mod
        if not filters_mod.get_preset(filter_preset):
            log.error("未知滤镜预设: %s（用 --list-filters 查看可用预设）", filter_preset)
            return 1
    # 独立特效（--effect 指定，可叠加多个）
    effects = args.effect or []
    effect_intensity = args.effect_intensity
    filter_intensity = args.filter_intensity
    if effects:
        from core import filters as filters_mod
        for eff_name in effects:
            if eff_name not in filters_mod.EFFECT_PRESETS:
                log.error("未知特效: %s（用 --list-effects 查看可用特效）", eff_name)
                return 1

    use_jy = not args.no_jianying

    # 1. 解析 + 下载（自动识别平台：小红书 / 抖音 / 本地文件）
    log.info("=" * 60)
    log.info("步骤 1/3  解析并下载笔记")
    if enhance_params:
        extras = []
        if creative_params.get("stickers_enabled"):
            extras.append(f"贴纸x{creative_params['sticker_count']}({creative_params['sticker_opacity']:.0%})")
        if creative_params.get("sweep_enabled"):
            extras.append("扫光")
        if creative_params.get("caption_enabled"):
            extras.append("字幕条")
        if creative_params.get("noise_enabled"):
            extras.append("噪点")
        if pip_path:
            extras.append("画中画")
        voice_tag = ""
        if tts_cfg.get("enabled"):
            voice_tag = (f" 配音[{tts_cfg.get('provider', 'edge')}:{tts_cfg.get('voice')}"
                         + (f" 语速{tts_cfg.get('rate')}" if tts_cfg.get("rate", "+0%") != "+0%" else "")
                         + (" 保留BGM" if tts_cfg.get("keep_bg_audio") else " 替换音频")
                         + "]")
        bgm_tag = ""
        if bgm_cfg.get("enabled"):
            bgm_tag = (f" BGM[{bgm_cfg.get('selection')}"
                       + f" 音量{bgm_cfg.get('volume', 0.15):.0%}"
                       + (" 淡变" if bgm_cfg.get("fade", True) else "")
                       + "]")
        log.info("         风格: %s + 二创[%s]%s%s%s%s%s", args.style_profile,
                 ",".join(extras) if extras else "无",
                 f" 水印[{watermark}]" if watermark else "",
                 voice_tag, bgm_tag,
                 f" 滤镜[{filter_preset}]" if filter_preset else "",
                 f" 特效[{','.join(effects)}]" if effects else "")
    log.info("=" * 60)
    try:
        note = _parse_input(args.note, log)
    except Exception as e:
        log.error("解析失败: %s", e)
        return 2
    print(note)
    print(f"  标题: {note.title}")
    print(f"  类型: {note.note_type}  Live: {note.is_live}")
    print(f"  媒体: {len(note.media_items)} 个")

    # 处理 --voice auto：根据笔记标签/标题自动选配音
    if tts_cfg.get("enabled") and tts_cfg.get("voice") == "auto":
        from core import tts as tts_mod
        chosen, scene, matched = tts_mod.auto_voice(
            note.tags, note.title, note.desc, provider=tts_cfg.get("provider", "edge"))
        tts_cfg = dict(tts_cfg, voice=chosen)
        print(f"  自动配音: {chosen} ({scene})")
        if matched:
            print(f"    命中关键词: {', '.join(matched[:5])}")

    # === LLM 初始化（必须在视频处理之前，因为口播二创/AI封面/字幕纠错需要 LLM）===
    # 任何需要 LLM 的功能开启时，都要用 CLI 参数重新初始化客户端
    use_llm = args.llm
    llm_only = args.llm_only
    # 注意：--subtitle-source llm-fix 需要 LLM 做字幕纠错，需纳入 needs_llm 判定
    needs_llm = use_llm or args.voice_rewrite or args.ai_cover or (
        getattr(args, "subtitle_source", None) == "llm-fix")
    if needs_llm:
        from core import llm as llm_mod
        # 用 CLI 参数覆盖环境变量（CLI 优先级最高）
        llm_kwargs = {}
        if args.llm_provider:
            llm_kwargs["provider"] = args.llm_provider
        if args.llm_api_key:
            llm_kwargs["api_key"] = args.llm_api_key
        if args.llm_model:
            llm_kwargs["model"] = args.llm_model
        # 重置全局客户端（用新参数初始化）
        llm_mod._client_instance = llm_mod.LLMClient(**llm_kwargs)
        if llm_mod.is_available():
            cfg = llm_mod.PROVIDER_CONFIGS.get(llm_mod._client_instance.provider, {})
            log.info("✅ LLM 已启用: %s 模型=%s",
                     cfg.get("name_cn", llm_mod._client_instance.provider),
                     llm_mod._client_instance.model)
        else:
            log.warning("⚠️ LLM 不可用，将回退到本地模板库（如需强制用 LLM 请加 --llm-only）")
            if llm_only:
                log.error("已指定 --llm-only 但 LLM 不可用，请检查 API Key 配置")

    # 2. 去重
    log.info("=" * 60)
    log.info("步骤 2/3  去重 + 风格增强 + 二创叠加" +
             (" + AI配音" if tts_cfg.get("enabled") else "") +
             (" + BGM" if bgm_cfg.get("enabled") else ""))
    log.info("=" * 60)
    # 字幕条文字：优先用 --caption，否则用笔记标题
    caption_text = args.caption if args.caption is not None else note.title
    subtitle_cfg = _build_subtitle_params(args)
    result = None
    if note.note_type == "video":
        result = process_video_note(note, use_jy, vparams, enhance_params,
                                     creative_params, watermark, caption_text, pip_path,
                                     subtitle_cfg, tts_cfg, voice_text, bgm_cfg, wm_cfg,
                                     filter_preset, effects, effect_intensity,
                                     filter_intensity=filter_intensity,
                                     voice_rewrite=args.voice_rewrite, rewrite_style=args.rewrite_style,
                                     rewrite_track=args.rewrite_track,
                                     subtitle_source=args.subtitle_source)
    elif note.is_live:
        result = process_live_note(note, use_jy, iparams, vparams, enhance_params,
                                    creative_params, watermark, caption_text, pip_path,
                                    subtitle_cfg, tts_cfg, voice_text, bgm_cfg, wm_cfg,
                                    filter_preset, effects, effect_intensity,
                                    filter_intensity=filter_intensity,
                                    voice_rewrite=args.voice_rewrite, rewrite_style=args.rewrite_style,
                                    rewrite_track=args.rewrite_track,
                                    subtitle_source=args.subtitle_source)
    else:
        result = process_image_note(note, iparams)

    if not result:
        log.error("去重失败")
        return 3

    # 3. 复刻文案
    log.info("=" * 60)
    log.info("步骤 3/4  复刻文案")
    log.info("=" * 60)
    out_dir = result.get("out_dir") or os.path.join(config.DEDUP_DIR, note.note_id)
    txt = clone_text(note, out_dir)

    # 4. 二创文案 + 封面生成
    log.info("=" * 60)
    log.info("步骤 4/4  二创文案 + 爆款封面生成")
    log.info("=" * 60)

    cw_result = None
    cover_result = None

    # 4a. 二创文案
    if not args.no_copywrite:
        n_titles = args.n_titles or config.COPYWRITE.get("n_titles", 10)
        llm_tag = " [LLM]" if use_llm else ""
        log.info("生成二创文案%s（%d 个标题候选）...", llm_tag, n_titles)
        try:
            cw_result = cw_mod.generate_copywriting(
                note.title, note.desc, note.tags, n_titles=n_titles,
                use_llm=use_llm, llm_only=llm_only)
            cw_path = cw_mod.save_copywriting(cw_result, out_dir)
            source_tag = "LLM" if cw_result.get("source") == "llm" else "模板库"
            log.info("内容类型: %s  关键词: %s  来源: %s",
                     cw_result["content_type"], ", ".join(cw_result["keywords"]), source_tag)
            log.info("Top 3 标题:")
            for i, (t, s) in enumerate(cw_result["titles"][:3], 1):
                log.info("  %d. [%.0f分] %s", i, s, t)
        except Exception as e:
            log.warning("二创文案生成失败: %s", e)
    else:
        log.info("已跳过二创文案（--no-copywrite）")

    # 4b. 封面生成
    if not args.no_cover:
        # 封面标题：优先 --cover-title，其次二创 Top1，最后原标题
        if args.cover_title:
            cover_title = args.cover_title
        elif cw_result and cw_result["titles"]:
            cover_title = cw_result["titles"][0][0]
        else:
            cover_title = note.title or "精彩内容"

        # 封面风格
        if args.cover_styles:
            styles = [s.strip() for s in args.cover_styles.split(",") if s.strip()]
        else:
            styles = config.COVER.get("styles", ["bigtext", "bottombar", "topbar", "magazine", "vlog_card", "list_banner"])

        # 获取原封面 URL（小红书/抖音视频有，其他来源可能没有）
        cover_url = None
        cover_local = None  # 解析阶段已下载的原封面本地路径
        video_path_for_cover = None
        if note.note_type == "video":
            for m in note.media_items:
                if m.get("kind") == "video":
                    cover_url = m.get("cover")
                    cover_local = m.get("cover_local")
                    video_path_for_cover = m.get("local")
                    break
        elif note.is_live:
            for m in note.media_items:
                if m.get("kind") == "image":
                    video_path_for_cover = m.get("live_photo", {}).get("local") if m.get("live_photo") else None
                    if not video_path_for_cover:
                        video_path_for_cover = m.get("local")
                    break
        else:
            # 图文笔记：用第一张图作为底图
            for m in note.media_items:
                if m.get("kind") == "image":
                    video_path_for_cover = m.get("local")
                    break

        # 如果有最终成品视频，优先用成品抽帧
        if note.note_type == "video" and result.get("final") and os.path.exists(result["final"]):
            video_path_for_cover = result["final"]

        # 收集 whisper 识别的字幕文本（供 LLM 生成封面标题参考）
        subtitle_text_for_llm = None
        if use_llm and subtitle_cfg.get("whisper_enabled"):
            try:
                from core import subtitle as sub_mod
                # 从已处理的视频中提取字幕文本（如果有 whisper 识别结果）
                video_for_sub = video_path_for_cover
                if video_for_sub and os.path.exists(video_for_sub):
                    segments, _ = sub_mod.extract_subtitle_text(
                        video_for_sub, note_text=(note.title or "") + "。" + (note.desc or ""),
                        subtitle_cfg=subtitle_cfg)
                    if segments:
                        subtitle_text_for_llm = " ".join(s.get("text", "") for s in segments)
                        log.info("已提取字幕文本供 LLM 参考 (%d 字)", len(subtitle_text_for_llm))
            except Exception as e:
                log.debug("提取字幕文本失败: %s", e)

        llm_cover_tag = " [LLM标题]" if use_llm else ""
        erase_tag = " [去文字]" if args.erase_cover_text else ""
        log.info("生成封面%s%s（%d 种风格）: 标题=%s",
                 llm_cover_tag, erase_tag, len(styles), cover_title)
        try:
            cover_result = cover_gen.generate_covers(
                cover_title, cover_url, video_path_for_cover, out_dir, styles,
                use_llm=use_llm, llm_only=llm_only,
                original_title=note.title, original_desc=note.desc,
                tags=note.tags, subtitle_text=subtitle_text_for_llm,
                image_params=iparams,
                erase_base_text=args.erase_cover_text,
                cover_local=cover_local)
            # LLM 可能重新生成了封面标题
            if cover_result.get("title_source") == "llm":
                log.info("LLM 封面标题: %s", cover_result.get("title"))
            if cover_result.get("original"):
                log.info("原封面已保留: %s", cover_result["original"])
            if cover_result.get("original_dedup"):
                log.info("原封面去重版: %s", cover_result["original_dedup"])
            if cover_result.get("base_text_erased"):
                log.info("底图文字已去除: cover_base_notext.jpg")
            for style, path in cover_result.get("covers", {}).items():
                log.info("封面[%s]: %s", style, os.path.basename(path))
        except Exception as e:
            log.warning("封面生成失败: %s", e)

        # === AI 生图封面（可选，--ai-cover 开启）===
        if args.ai_cover:
            from core import llm as llm_mod
            log.info("=" * 50)
            log.info("[AI生图] 触发 AI 生图封面流程（--ai-cover 已开启）")
            log.info("[AI生图] 配置: provider=%s model=%s mode=%s styles=%s",
                     args.image_llm_provider or args.llm_provider,
                     args.image_llm_model or "默认",
                     args.image_mode or "t2i",
                     args.ai_cover_styles or "全部")
            log.info("=" * 50)

            # 先配置各层 LLM 客户端（必须在 is_image_generation_available() 检查之前）
            # 1) 生图 prompt 专用 LLM 客户端（可与文生图服务商分离）
            #    例如: --llm-provider grsai --prompt-llm-provider deepseek
            #    文生图用 grsai sora-image，生图 prompt 用 deepseek-v4-flash（更便宜）
            if args.prompt_llm_provider or args.prompt_llm_model:
                prompt_ok = llm_mod.set_prompt_client(
                    provider=args.prompt_llm_provider,
                    model=args.prompt_llm_model,
                    api_key=args.prompt_llm_api_key)
                if prompt_ok:
                    log.info("生图 prompt 使用独立 LLM: %s %s",
                             args.prompt_llm_provider or "默认",
                             args.prompt_llm_model or "默认模型")
                else:
                    log.warning("生图 prompt 专用 LLM 不可用，回退到主 LLM 服务商")

            # 2) 文生图/图生图专用 LLM 客户端（可与对话服务商分离）
            #    例如: --llm-provider deepseek --image-llm-provider grsai
            #    二创文案用 deepseek-v4-flash，文生图用 grsai nano-banana-pro
            if args.image_llm_provider or args.image_llm_model:
                image_ok = llm_mod.set_image_client(
                    provider=args.image_llm_provider,
                    model=args.image_llm_model,
                    api_key=args.image_llm_api_key)
                if image_ok:
                    log.info("文生图/图生图使用独立 LLM: %s %s",
                             args.image_llm_provider or "默认",
                             args.image_llm_model or "默认模型")
                else:
                    log.warning("文生图专用 LLM 不可用，回退到主 LLM 服务商")

            # 配置文生图备用客户端（主服务商失败时自动切换，如 GrsAI 审核拦截 → 魔搭）
            if args.image_llm_fallback_provider or args.image_llm_fallback_model:
                fb_ok = llm_mod.set_image_fallback_client(
                    provider=args.image_llm_fallback_provider,
                    model=args.image_llm_fallback_model,
                    api_key=args.image_llm_fallback_api_key)
                if fb_ok:
                    log.info("文生图备用 LLM: %s %s（主 LLM 失败时自动切换）",
                             args.image_llm_fallback_provider or "默认",
                             args.image_llm_fallback_model or "默认模型")
                else:
                    log.warning("文生图备用 LLM 不可用，备用机制不生效")

            # 检查生图能力（此时 image 客户端已配置完成，会用 image 客户端判断）
            if not llm_mod.is_image_generation_available():
                log.error("AI 生图封面: 当前 LLM 服务商不支持文生图，跳过")
                log.error("支持文生图的服务商: 豆包(--llm-provider doubao) / OpenAI(--llm-provider openai) / GrsAI(--llm-provider grsai) / 魔搭(--llm-provider modelscope,免费)")
                log.error("或用 --image-llm-provider grsai/modelscope 单独指定文生图服务商")
            else:
                ai_styles = None
                if args.ai_cover_styles:
                    ai_styles = [s.strip() for s in args.ai_cover_styles.split(",") if s.strip()]
                    # 校验风格名
                    for s in ai_styles:
                        if s not in llm_mod.AI_COVER_STYLES:
                            log.error("未知 AI 封面风格: %s（用 --list-ai-covers 查看可用风格）", s)
                            ai_styles = None
                            break

                ai_size = args.ai_cover_size or config.AI_COVER.get("image_size", "1024x1024")

                # 图生图模式：准备参考图 URL
                # 优先级: --reference-image > 原封面 URL > 本地视频抽帧
                # 注意：cover_url 是上面从 note.media_items 提取的局部变量（NoteData 没有 cover_url 属性）
                image_mode = args.image_mode or "t2i"
                reference_image_url = None
                if image_mode in ("i2i", "t2i+i2i"):
                    mode_name = "图生图" if image_mode == "i2i" else "混合"
                    # 1. 用户显式指定的参考图（最高优先级）
                    if args.reference_image:
                        ref_input = args.reference_image.strip().strip("\"'")
                        if ref_input.startswith(("http://", "https://", "data:")):
                            # 公网 URL 或 data URL，直接用
                            reference_image_url = ref_input
                            log.info("%s模式参考图: 用户指定 URL %s",
                                     mode_name, reference_image_url[:80])
                        elif os.path.exists(ref_input):
                            # 本地图片文件 → 转 base64 data URL
                            try:
                                import base64 as _b64
                                with open(ref_input, "rb") as _f:
                                    img_b64 = _b64.b64encode(_f.read()).decode("ascii")
                                # 根据扩展名判断 MIME
                                ext = os.path.splitext(ref_input)[1].lower()
                                mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                                        ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
                                reference_image_url = f"data:{mime};base64,{img_b64}"
                                log.info("%s模式参考图: 用户指定本地图片 %s (%d KB, 转 base64)",
                                         mode_name, os.path.basename(ref_input),
                                         len(img_b64) * 3 // 4 // 1024)
                            except Exception as e:
                                log.warning("%s模式: 读取本地参考图失败(%s)，回退到文生图模式",
                                            mode_name, e)
                                image_mode = "t2i"
                        else:
                            log.warning("%s模式: --reference-image 指定的路径不存在: %s，回退到其他来源",
                                        mode_name, ref_input)
                    # 2. 原封面 URL
                    if not reference_image_url and image_mode in ("i2i", "t2i+i2i") and cover_url:
                        reference_image_url = cover_url
                        log.info("%s模式参考图: 原封面 URL %s",
                                 mode_name, reference_image_url[:80])
                    # 3. 本地视频/图片抽帧
                    if not reference_image_url and image_mode in ("i2i", "t2i+i2i"):
                        if video_path_for_cover and os.path.exists(video_path_for_cover):
                            try:
                                from core import cover_gen as cg
                                r = cg.get_cover_base_image(None, video_path_for_cover, out_dir)
                                if r and r[0] and os.path.exists(r[0]):
                                    ref_path = r[0]
                                    import base64 as _b64
                                    with open(ref_path, "rb") as _f:
                                        img_b64 = _b64.b64encode(_f.read()).decode("ascii")
                                    reference_image_url = f"data:image/jpeg;base64,{img_b64}"
                                    log.info("%s模式参考图: 本地抽帧转 base64 (%s, %d KB)",
                                             mode_name, os.path.basename(ref_path),
                                             len(img_b64) * 3 // 4 // 1024)
                                else:
                                    log.warning("%s模式: 本地抽帧失败，回退到文生图模式", mode_name)
                                    image_mode = "t2i"
                            except Exception as e:
                                log.warning("%s模式: 本地参考图准备失败(%s)，回退到文生图模式",
                                            mode_name, e)
                                image_mode = "t2i"
                        else:
                            log.warning("%s模式未找到参考图（无原封面 URL 和本地视频），回退到文生图模式",
                                        mode_name)
                            image_mode = "t2i"

                # 检测内容类型（用于生图 prompt 和钩子文案）
                content_type = None
                try:
                    content_type = cw_mod.detect_content_type(
                        note.title or "", note.desc or "", note.tags or [])
                except Exception:
                    pass

                mode_label = {"i2i": "图生图", "t2i+i2i": "混合(t2i+i2i)"}.get(image_mode, "文生图")
                log.info("=" * 50)
                log.info("生成 AI %s封面（%d 种风格，尺寸 %s）",
                         mode_label,
                         len(ai_styles) if ai_styles else len(llm_mod.AI_COVER_STYLES),
                         ai_size)
                try:
                    # 文生图模型优先级: --image-llm-model > --grsai-image-model > 服务商默认
                    img_model = args.image_llm_model
                    if not img_model and args.llm_provider == "grsai":
                        img_model = args.grsai_image_model  # 兼容旧参数
                    ai_result = cover_gen.generate_ai_covers(
                        cover_title,
                        original_title=note.title,
                        original_desc=note.desc,
                        tags=note.tags,
                        content_type=content_type,
                        out_dir=out_dir,
                        styles=ai_styles,
                        image_size=ai_size,
                        image_model=img_model,
                        image_mode=image_mode,
                        reference_image_url=reference_image_url)
                    ai_covers = ai_result.get("covers", {})
                    if ai_covers:
                        log.info("AI %s封面完成: %d 种成功", mode_label, len(ai_covers))
                        for style, path in ai_covers.items():
                            log.info("AI封面[%s]: %s", style, os.path.basename(path))
                    if ai_result.get("failed"):
                        log.warning("AI %s封面失败: %s", mode_label, ", ".join(ai_result["failed"]))
                except Exception as e:
                    log.warning("AI %s封面生成异常: %s", mode_label, e)
    else:
        log.info("已跳过封面生成（--no-cover）")

    # 汇总
    print()
    print("=" * 60)
    print("✅ 复刻完成！成品目录:")
    print(f"   {out_dir}")
    print("=" * 60)
    print(f"   原文案: {txt}")
    if cw_result:
        print(f"   二创文案: copywriting_clone.txt")
        if cw_result["titles"]:
            print(f"   📌 推荐标题: {cw_result['titles'][0][0]}")
    if cover_result:
        if cover_result.get("original"):
            print(f"   原封面: {os.path.basename(cover_result['original'])}")
        if cover_result.get("original_dedup"):
            print(f"   原封面去重版: {os.path.basename(cover_result['original_dedup'])}")
        for style, path in cover_result.get("covers", {}).items():
            print(f"   封面[{style}]: {os.path.basename(path)}")
    if note.note_type == "video":
        final = result.get("final") or result.get("video")
        if result.get("final"):
            print(f"   最终成品(增强后): {final}")
            print(f"   仅去重版: {result.get('video')}")
        else:
            print(f"   视频: {final}")
        if result.get("jianying"):
            print(f"   剪映草稿: {result['jianying']}")
    elif note.is_live:
        n_live = len([r for r in result.get("live", []) if r.get("video")])
        n_enh = len([r for r in result.get("live", []) if r.get("final")])
        n_img = len(result.get("images", []))
        print(f"   Live 图(去重): {n_live} 组, 其中增强版: {n_enh}")
        print(f"   普通图(去重): {n_img} 张")
        if result.get("jianying"):
            print(f"   剪映草稿: {result['jianying']}")
    else:
        print(f"   图片: {len(result.get('images', []))} 张")

    if use_jy and config.JIANYING_DRAFT_ROOT:
        print()
        print("提示: 打开「剪映」→ 草稿，即可看到新草稿，可直接编辑/导出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
