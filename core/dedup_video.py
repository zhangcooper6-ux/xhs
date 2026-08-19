# -*- coding: utf-8 -*-
"""视频去重模块（基于完整 ffmpeg，通过 imageio-ffmpeg 获取二进制）。

去重手段：
  - 水平镜像
  - 变速（视频+音频同步）
  - 四边裁剪并放大回原尺寸
  - 色彩调整（亮度/对比度/饱和度/伽马）
  - 音频变调 asetrate
  - 轻微噪点 noise
  - 轻微锐化 unsharp
  - 去除元数据
  - 强制重编码（libx264 + aac）
"""
import os
import sys
import json
import shutil
import logging
import subprocess

import config

logger = logging.getLogger("dedup_video")


def _get_ffmpeg():
    """获取完整版 ffmpeg 可执行文件路径。

    优先用 imageio-ffmpeg 自带的完整构建（系统 ffmpeg 是精简版，缺少音频编码器和多数滤镜）。
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        logger.warning("imageio-ffmpeg 不可用: %s", e)
    # 退回系统 ffmpeg
    return shutil.which("ffmpeg") or "ffmpeg"


def _get_ffprobe():
    """获取 ffprobe 路径。imageio-ffmpeg 不含 ffprobe，尝试系统版本。"""
    probe = shutil.which("ffprobe")
    if probe:
        return probe
    # imageio-ffmpeg 同目录下可能有
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        candidate = os.path.join(os.path.dirname(exe), "ffprobe.exe")
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    return None


def probe_duration(src_path):
    """获取视频时长（秒）。"""
    ffprobe = _get_ffprobe()
    if not ffprobe or not os.path.exists(src_path):
        return 0.0
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", src_path],
            stderr=subprocess.STDOUT, timeout=30,
        ).decode("utf-8", "ignore")
        d = json.loads(out).get("format", {}).get("duration")
        return float(d) if d else 0.0
    except Exception as e:
        logger.warning("探测时长失败 %s: %s", src_path, e)
        return 0.0


def probe_resolution(src_path):
    """获取视频分辨率 (w, h)。"""
    ffprobe = _get_ffprobe()
    if not ffprobe or not os.path.exists(src_path):
        return None, None
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", src_path],
            stderr=subprocess.STDOUT, timeout=30,
        ).decode("utf-8", "ignore")
        s = json.loads(out).get("streams", [{}])[0]
        return int(s.get("width", 0)), int(s.get("height", 0))
    except Exception as e:
        logger.warning("探测分辨率失败 %s: %s", src_path, e)
        return None, None


def _build_filter_chain(params, w, h):
    """构建视频滤镜链。"""
    vf = []

    # 裁剪四边
    crop_ratio = params.get("crop_ratio", 0.0)
    if crop_ratio and crop_ratio > 0 and w and h:
        cx = max(2, int(w * crop_ratio))
        cy = max(2, int(h * crop_ratio))
        # 保证偶数
        cx -= cx % 2
        cy -= cy % 2
        cw = w - 2 * cx
        ch = h - 2 * cy
        vf.append(f"crop={cw}:{ch}:{cx}:{cy}")
        # 放大回原尺寸
        vf.append(f"scale={w}:{h}")

    # 镜像
    if params.get("mirror", True):
        vf.append("hflip")

    # 色彩调整
    eq_parts = []
    if params.get("brightness", 0) != 0:
        eq_parts.append(f"brightness={params['brightness']}")
    if params.get("contrast", 1.0) != 1.0:
        eq_parts.append(f"contrast={params['contrast']}")
    if params.get("saturation", 1.0) != 1.0:
        eq_parts.append(f"saturation={params['saturation']}")
    if params.get("gamma", 1.0) != 1.0:
        eq_parts.append(f"gamma={params['gamma']}")
    if eq_parts:
        vf.append("eq=" + ":".join(eq_parts))

    # 噪点
    noise = params.get("noise_strength", 0)
    if noise and noise > 0:
        vf.append(f"noise=alls={noise}:allf=t")

    # 锐化
    if params.get("unsharp", False):
        vf.append("unsharp=5:5:0.8:5:5:0.0")

    # 偶数尺寸保证
    vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    return ",".join(vf) if vf else None


def _build_audio_filter_chain(params):
    """构建音频滤镜链。"""
    af = []
    tempo = params.get("audio_tempo", 1.0)
    if tempo and tempo != 1.0:
        # atempo 范围 [0.5, 2.0]，超出则链式
        t = tempo
        chain = []
        while t > 2.0:
            chain.append("atempo=2.0")
            t /= 2.0
        while t < 0.5:
            chain.append("atempo=0.5")
            t /= 0.5
        chain.append(f"atempo={t:.4f}")
        af.extend(chain)

    # 变调（不影响时长）：asetrate 改变采样率后重采样
    if params.get("audio_pitch", False):
        af.append("asetrate=44100*0.92,aresample=44100")

    return ",".join(af) if af else None


def dedup_video(src_path, dst_path, params=None):
    """对视频执行去重处理。

    参数:
        src_path: 源视频路径
        dst_path: 输出路径
        params: 去重参数（默认 config.VIDEO_DEDUP）
    返回:
        dst_path 或 None
    """
    if params is None:
        params = config.VIDEO_DEDUP

    if not os.path.exists(src_path):
        logger.error("源视频不存在: %s", src_path)
        return None

    ffmpeg = _get_ffmpeg()
    w, h = probe_resolution(src_path)
    duration = probe_duration(src_path)
    logger.info("源视频: %s  分辨率=%sx%s  时长=%.2fs",
                os.path.basename(src_path), w, h, duration)

    # 滤镜
    vf = _build_filter_chain(params, w, h)
    af = _build_audio_filter_chain(params)

    speed = params.get("speed", 1.0)

    # 速度通过 PTS 实现（视频）
    if speed and speed != 1.0:
        pts_filter = f"setpts={1.0/speed:.4f}*PTS"
        vf = f"{vf},{pts_filter}" if vf else pts_filter

    cmd = [ffmpeg, "-y", "-i", src_path]

    # 视频滤镜
    if vf:
        cmd += ["-vf", vf]
    # 音频滤镜
    if af:
        cmd += ["-af", af]

    # 编码参数
    cmd += [
        "-c:v", params.get("vcodec", "libx264"),
        "-preset", params.get("preset", "fast"),
        "-crf", str(params.get("crf", 18)),
        "-pix_fmt", "yuv420p",
        # 强制固定帧率 30fps，避免 setpts 变速后帧率异常（如 31.83fps）导致时长错乱
        "-r", str(params.get("output_fps", 30)),
        # 视频同步模式：CFR 固定帧率，丢弃/重复帧以匹配输出帧率
        "-vsync", "cfr",
        "-c:a", params.get("acodec", "aac"),
        "-b:a", "192k",
    ]

    # 元数据处理
    if params.get("strip_metadata", True):
        cmd += ["-map_metadata", "-1",
                "-metadata", "title=",
                "-metadata", "comment=",
                "-metadata", "encoder="]
    # 快速启动
    cmd += ["-movflags", "+faststart"]
    # 截断到最短流：避免视频/音频时长不一致导致末尾黑屏或静音
    cmd.append("-shortest")

    cmd.append(dst_path)

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    logger.info("执行 ffmpeg 去重...")
    logger.debug("命令: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            encoding="utf-8", errors="ignore")
        if result.returncode != 0:
            logger.error("ffmpeg 失败 (rc=%d):\n%s",
                         result.returncode, result.stderr[-2000:])
            return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg 超时")
        return None
    except Exception as e:
        logger.error("ffmpeg 异常: %s", e)
        return None

    if os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
        nw, nh = probe_resolution(dst_path)
        nd = probe_duration(dst_path)
        logger.info("视频去重完成: %s  分辨率=%sx%s  时长=%.2fs",
                    dst_path, nw, nh, nd)
        return dst_path
    logger.error("输出文件无效: %s", dst_path)
    return None
