# -*- coding: utf-8 -*-
"""剪映（Jianying / CapCut）草稿生成器。

功能：
  - 在剪映草稿目录下创建一个新草稿
  - 将去重后的视频导入为时间线片段
  - 在片段上附加剪映原生去重效果（镜像 / 变速 / 色彩）
  - 用户打开剪映即可看到草稿，导出后即得到“剪映处理过”的去重视频

说明：
  剪映草稿格式为私有 JSON，不同版本字段略有差异。本生成器基于公开的
  draft_content / draft_meta_info 结构，采用“导入视频 + 片段变换”的最小可用
  草稿，兼容大多数剪映 4.x / 5.x 版本。若版本不兼容，去重后的视频文件本身
  已可直接使用（ffmpeg 已完成去重），用户也可手动在剪映里导入。
"""
import os
import json
import uuid
import time
import shutil
import logging

import config
from . import dedup_video

logger = logging.getLogger("jianying")


def _new_id():
    """生成剪映风格的 ID（不带横线的 uuid）。"""
    return uuid.uuid4().hex


def _now_ms():
    return int(time.time() * 1000)


def _to_jy_path(path):
    """转换为剪映里使用的相对/绝对路径（Windows 反斜杠）。"""
    return os.path.abspath(path).replace("/", "\\")


def probe_media_info(video_path):
    """探测视频信息（用于草稿字段）。"""
    w, h = dedup_video.probe_resolution(video_path)
    duration_us = int(dedup_video.probe_duration(video_path) * 1_000_000)
    return {
        "width": w or 1080,
        "height": h or 1920,
        "duration": duration_us,
    }


def build_draft_content(video_path, draft_id, params=None):
    """构建 draft_content.json 内容。

    在片段上附加镜像 + 变速 + 色彩变换（剪映原生效果）。
    """
    if params is None:
        params = config.VIDEO_DEDUP

    info = probe_media_info(video_path)
    w, h = info["width"], info["height"]
    duration_us = info["duration"]

    material_id = _new_id()
    segment_id = _new_id()
    video_track_id = _new_id()
    audio_track_id = _new_id()
    audio_material_id = _new_id()
    audio_segment_id = _new_id()

    speed = params.get("speed", 1.0)
    mirror = params.get("mirror", True)
    # 剪映的 source_timerange 用微秒
    target_duration = int(duration_us / speed) if speed else duration_us

    abs_path = _to_jy_path(video_path)

    draft = {
        "id": draft_id,
        "canvas_config": {
            "width": w,
            "height": h,
            "ratio": "original"
        },
        "duration": target_duration,
        "version": "3.0.0",
        "create_time": _now_ms(),
        "update_time": _now_ms(),
        "materials": {
            "videos": [
                {
                    "id": material_id,
                    "type": "video",
                    "local_id": _new_id(),
                    "category_id": "",
                    "category_name": "local",
                    "duration": duration_us,
                    "width": w,
                    "height": h,
                    "path": abs_path,
                    "material_name": os.path.basename(video_path),
                    "source_platform": 0,
                    "is_ai_generate_content": False,
                    "matting": None,
                    "stable": None,
                    "mask": None,
                    "intensify_audio": 0,
                    "audio_fade": None,
                    "has_audio": True,
                    "is_copyright": False,
                    "is_text_edit_overdub": False,
                    "extra_material_option": 0,
                    "source": None,
                    "local_material_id": "",
                    "beats": None,
                    "content_type": 0,
                    "enhance": None,
                    "vocal_beautify": None,
                    "is_subtitle_recognition": False,
                    "is_text_edit": False,
                    "is_favorite": False,
                    "is_unified_beauty": False,
                    "source_video_id": "",
                    "team_id": "",
                    "title": "",
                    "video_algorithm": {},
                    "is_mute": False,
                    "bubble_info": None
                }
            ],
            "audios": [
                {
                    "id": audio_material_id,
                    "type": "audio",
                    "local_id": _new_id(),
                    "category_id": "",
                    "category_name": "local",
                    "duration": duration_us,
                    "path": abs_path,
                    "source_platform": 0,
                    "name": os.path.basename(video_path),
                    "source": None,
                    "team_id": "",
                    "tone_category_id": "",
                    "tone_category_name": "",
                    "tone_speaker": "",
                    "tone_type": "",
                    "uuid": _new_id(),
                    "is_favorite": False,
                    "is_text_edit_overdub": False,
                    "local_material_id": "",
                    "matting": None,
                    "is_copyright": False,
                    "is_subtitle_recognition": False,
                    "music_id": "",
                    "intensify_audio_path": "",
                    "original_speed": speed,
                    "speed": speed,
                    "volume": 1.0,
                    "fade": None,
                    "beats": None,
                    "type_name": "",
                    "extra_material_option": 0,
                    "source_video_id": material_id
                }
            ],
            "placeholders": [],
            "texts": [],
            "stickers": [],
            "effects": [],
            "video_effects": [],
            "transitions": [],
            "sound_channels": [],
            "material_animations": [],
            "material_colors": [],
            "masks": [],
            "shapes": [],
            "digital_humans": [],
            "drafts": [],
            "footage_chromas": [],
            "log_color_wheels": [],
            "primary_color_wheels": [],
            "smart_crops": [],
            "smart_relights": [],
            "templates": [],
            "tracks_v2": [],
            "group_info": []
        },
        "tracks": [
            {
                "id": video_track_id,
                "type": "video",
                "segments": [
                    {
                        "id": segment_id,
                        "material_id": material_id,
                        "rendering_index": 0,
                        "is_placeholder": False,
                        "target_timerange": {
                            "start": 0,
                            "duration": target_duration
                        },
                        "source_timerange": {
                            "start": 0,
                            "duration": duration_us
                        },
                        "speeds": [speed],
                        "common_keyframes": [],
                        "keyframe_refs": [],
                        "volume": 1.0,
                        "extra_material_refs": [],
                        "source": None,
                        "is_placeholder": False,
                        "enabled": True,
                        "last_nonzero_volume": 1.0,
                        "clip": {
                            "transform": {
                                "x": 0.0,
                                "y": 0.0
                            },
                            "scale": {
                                "x": 1.0,
                                "y": 1.0
                            },
                            "alpha": 1.0,
                            "rotation": 0.0,
                            # 镜像：hflip=1 表示水平翻转
                            "hflip": 1 if mirror else 0,
                            "vflip": 0
                        },
                        "origin_material_id": material_id,
                        "responds": [],
                        "uniform_scale": {
                            "on": True,
                            "value": 1.0
                        },
                        "mix_info": None
                    }
                ],
                "attribute": 0,
                "flag": 0,
                "is_main": 1,
                "is_mix": False,
                "is_animation": False,
                "is_overlay": False
            },
            {
                "id": audio_track_id,
                "type": "audio",
                "segments": [
                    {
                        "id": audio_segment_id,
                        "material_id": audio_material_id,
                        "rendering_index": 0,
                        "is_placeholder": False,
                        "target_timerange": {
                            "start": 0,
                            "duration": target_duration
                        },
                        "source_timerange": {
                            "start": 0,
                            "duration": duration_us
                        },
                        "speeds": [speed],
                        "common_keyframes": [],
                        "keyframe_refs": [],
                        "volume": 1.0,
                        "extra_material_refs": [],
                        "source": None,
                        "enabled": True,
                        "last_nonzero_volume": 1.0
                    }
                ],
                "attribute": 0,
                "flag": 0,
                "is_main": 0,
                "is_mix": False,
                "is_animation": False,
                "is_overlay": False
            }
        ],
        "draft_share_info": None,
        "capability": {
            "version": 1
        }
    }
    return draft


def build_draft_meta_info(draft_id, draft_name, content):
    """构建 draft_meta_info.json。"""
    return {
        "id": draft_id,
        "draft_name": draft_name,
        "draft_id": draft_id,
        "duration": content.get("duration", 0),
        "create_time": _now_ms(),
        "update_time": _now_ms(),
        "cover": "",
        "canvas_config": content.get("canvas_config", {}),
        "version": "3.0.0",
        "task_id": "",
        "source": "default",
        "draft_fold_path": "",
        "related": False,
        "is_invisible": False,
        "thumbnail": {
            "music_status": 0,
            "image_status": 0
        },
        "is_default_draft_folder": False,
        "draft_cloud_capcut_purchase_info": "",
        "cloud_materials": [],
        "draft_cloud_materials": []
    }


def create_jianying_draft(video_path, draft_name=None, copy_video=True,
                          install_to_jianying=False, params=None):
    """创建一个包含指定视频的剪映草稿。

    参数:
        video_path: 已去重的视频文件
        draft_name: 草稿名（默认 XHS去重_时间戳）
        copy_video: 是否将视频复制进草稿目录（推荐 True，便于剪映识别）
        install_to_jianying: 是否复制到剪映草稿根目录（默认 False，只生成在 output/jianying_drafts；
                             设 True 会复制到剪映官方草稿目录并注册到 root_meta_info.json）
        params: 去重参数（用于镜像/变速效果）
    返回:
        (draft_dir, draft_content_path) 或 (None, None)
    """
    if not os.path.exists(video_path):
        logger.error("视频不存在: %s", video_path)
        return None, None

    config.ensure_dirs()
    if draft_name is None:
        draft_name = f"XHS去重_{int(time.time())}"

    # 替换非法文件名字符
    safe_name = "".join(c for c in draft_name if c not in '<>:"/\\|?*').strip()
    if not safe_name:
        safe_name = f"XHS_{int(time.time())}"

    draft_id = _new_id()
    # 剪映草稿目录名 = 草稿名
    draft_dir = os.path.join(config.JIANYING_DRAFT_DIR, safe_name)
    os.makedirs(draft_dir, exist_ok=True)

    # 视频文件（复制进草稿目录，剪映更易识别）
    if copy_video:
        video_in_draft = os.path.join(draft_dir, os.path.basename(video_path))
        if not os.path.exists(video_in_draft):
            shutil.copy2(video_path, video_in_draft)
        video_ref = video_in_draft
    else:
        video_ref = video_path

    # 生成 draft_content.json
    content = build_draft_content(video_ref, draft_id, params)
    content_path = os.path.join(draft_dir, "draft_content.json")
    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, separators=(",", ":"))

    # 生成 draft_meta_info.json
    meta = build_draft_meta_info(draft_id, safe_name, content)
    meta_path = os.path.join(draft_dir, "draft_meta_info.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))

    # 复制到剪映草稿根目录
    jy_target = None
    if install_to_jianying and config.JIANYING_DRAFT_ROOT:
        jy_target = os.path.join(config.JIANYING_DRAFT_ROOT, safe_name)
        try:
            if os.path.exists(jy_target):
                shutil.rmtree(jy_target)
            shutil.copytree(draft_dir, jy_target)
            # 更新 root_meta_info.json
            _register_in_root_meta(jy_target, draft_id, safe_name, content)
            logger.info("已安装到剪映草稿目录: %s", jy_target)
        except Exception as e:
            logger.warning("安装到剪映草稿目录失败: %s（草稿仍保存在 %s）", e, draft_dir)
            jy_target = None
    elif install_to_jianying and not config.JIANYING_DRAFT_ROOT:
        logger.warning("未检测到剪映草稿目录，草稿仅保存在 %s", draft_dir)

    logger.info("剪映草稿已生成: %s", draft_dir)
    return draft_dir, content_path


def _register_in_root_meta(draft_dir, draft_id, draft_name, content):
    """更新剪映根目录下的 root_meta_info.json。"""
    root_meta_path = os.path.join(config.JIANYING_DRAFT_ROOT, "root_meta_info.json")
    root_meta = {"drafts": []}
    if os.path.exists(root_meta_path):
        try:
            with open(root_meta_path, "r", encoding="utf-8") as f:
                root_meta = json.load(f)
        except Exception:
            root_meta = {"drafts": []}
    if "drafts" not in root_meta:
        root_meta["drafts"] = []

    entry = {
        "draft_id": draft_id,
        "draft_name": draft_name,
        "draft_fold_path": draft_dir,
        "duration": content.get("duration", 0),
        "create_time": _now_ms(),
        "update_time": _now_ms(),
        "cover": "",
        "is_invisible": False
    }
    # 去重
    root_meta["drafts"] = [d for d in root_meta["drafts"]
                           if d.get("draft_id") != draft_id]
    root_meta["drafts"].append(entry)

    try:
        with open(root_meta_path, "w", encoding="utf-8") as f:
            json.dump(root_meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("更新 root_meta_info.json 失败: %s", e)


def list_jianying_drafts():
    """列出剪映草稿根目录下的现有草稿。"""
    if not config.JIANYING_DRAFT_ROOT:
        return []
    result = []
    for name in os.listdir(config.JIANYING_DRAFT_ROOT):
        p = os.path.join(config.JIANYING_DRAFT_ROOT, name)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "draft_content.json")):
            result.append(name)
    return result
