# -*- coding: utf-8 -*-
"""Live 图去重模块。

Live 图 = 静态图片 + 短视频。去重需同时处理两部分：
  - 静态图片：用 Pillow 去重（镜像/裁剪/色彩/噪点/EXIF）
  - 动态视频：用 ffmpeg 去重，并生成剪映草稿（可选）

输出结构：
  dedup/<note_id>/live_<idx>.jpg     去重后的静态图
  dedup/<note_id>/live_<idx>.mp4     去重后的动态视频
"""
import os
import logging

import config
from . import dedup_image
from . import dedup_video

logger = logging.getLogger("dedup_live")


def dedup_live_image(static_path, live_video_path, out_dir, idx, image_params=None,
                     video_params=None):
    """对一张 Live 图执行去重。

    参数:
        static_path: 静态图片路径
        live_video_path: 动态视频路径
        out_dir: 输出目录
        idx: 序号
        image_params: 图片去重参数
        video_params: 视频去重参数
    返回:
        {"image": ..., "video": ...}
    """
    if image_params is None:
        image_params = config.IMAGE_DEDUP
    if video_params is None:
        video_params = config.VIDEO_DEDUP

    os.makedirs(out_dir, exist_ok=True)
    result = {"image": None, "video": None}

    # 静态图去重
    if static_path and os.path.exists(static_path):
        dst_img = os.path.join(out_dir, f"live_{idx}.jpg")
        r = dedup_image.dedup_image(static_path, dst_img, image_params)
        result["image"] = r
    else:
        logger.warning("Live 图 %d 静态图缺失", idx)

    # 动态视频去重
    if live_video_path and os.path.exists(live_video_path):
        dst_vid = os.path.join(out_dir, f"live_{idx}.mp4")
        r = dedup_video.dedup_video(live_video_path, dst_vid, video_params)
        result["video"] = r
    else:
        logger.warning("Live 图 %d 动态视频缺失", idx)

    logger.info("Live 图 %d 去重完成: image=%s video=%s",
                idx, bool(result["image"]), bool(result["video"]))
    return result
