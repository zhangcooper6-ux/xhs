# -*- coding: utf-8 -*-
"""字幕处理模块：自动识别字幕区域 + 抹除原字幕 + 自动提取文本 + 重新渲染 ASS 字幕。

流程：
  0. detect_subtitle_area: 抽帧 + 边缘检测 + 水平投影，自动找字幕区域
  1. erase_subtitle: 用 ffmpeg delogo/blur/cover 抹除原字幕区域
  2. extract_subtitle_text: 优先用 whisper 从音频识别，回退用笔记文案分句
  3. build_ass_subtitle: 生成 ASS 字幕文件（样式可配置）
  4. 渲染时由 style_enhance 用 subtitles 滤镜加载 ASS
"""
import os
import logging
import subprocess
import tempfile

import config
from . import dedup_video

logger = logging.getLogger("subtitle")


def _get_ffmpeg():
    return dedup_video._get_ffmpeg()


def _probe(src_path):
    w, h = dedup_video.probe_resolution(src_path)
    d = dedup_video.probe_duration(src_path)
    return w, h, d


# ---------- 0. 自动识别字幕区域 ----------

def _extract_frames(src_path, out_dir, duration, num_frames=5):
    """用 ffmpeg 抽取视频中段的若干帧（避免片头片尾）。

    返回帧文件路径列表。
    """
    ffmpeg = _get_ffmpeg()
    frames = []
    if not duration or duration < 1:
        # 默认抽 1s 处
        timestamps = [1.0]
    else:
        # 在视频 20%~80% 区间均匀抽 num_frames 帧
        start, end = duration * 0.2, duration * 0.8
        if end - start < 0.5:
            timestamps = [duration / 2]
        else:
            step = (end - start) / max(1, num_frames - 1)
            timestamps = [start + step * i for i in range(num_frames)]

    os.makedirs(out_dir, exist_ok=True)
    for i, t in enumerate(timestamps):
        out_png = os.path.join(out_dir, f"frame_{i:02d}.png")
        cmd = [
            ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", src_path,
            "-frames:v", "1", "-vf", "scale=480:-1",  # 缩小到 480 宽，加快分析
            out_png,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30,
                               encoding="utf-8", errors="ignore")
            if r.returncode == 0 and os.path.exists(out_png):
                frames.append(out_png)
        except Exception as e:
            logger.debug("抽帧失败 t=%.2f: %s", t, e)
    return frames


def _row_edge_projection(img):
    """计算灰度图每行的"边缘强度"（水平方向相邻像素差异之和）。

    文字行有大量高频边缘，背景行边缘强度低。
    返回每行的边缘强度列表。
    """
    from PIL import Image
    import itertools

    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()

    proj = [0] * h
    # 水平差分：相邻像素亮度差 > 阈值 计为边缘点
    threshold = 24
    for y in range(h):
        row_sum = 0
        prev = px[0, y]
        for x in range(1, w):
            cur = px[x, y]
            diff = abs(cur - prev)
            if diff >= threshold:
                row_sum += diff
            prev = cur
        proj[y] = row_sum
    return proj


def detect_subtitle_area(src_path, duration=None, num_frames=5):
    """自动检测视频字幕区域。

    策略：
      1. 抽取视频中段若干帧（缩小到 480 宽加速）
      2. 对每帧做水平边缘投影
      3. 多帧投影取平均 → 找出底部边缘强度高的连续行段
      4. 过滤掉太靠近顶部（>40% 位置才算"底部字幕"）和太短的区域

    返回: {"x": 0.0, "y": ratio_y, "w": 1.0, "h": ratio_h} 或 None（检测失败）
    """
    from PIL import Image

    if duration is None:
        _, _, duration = _probe(src_path)

    tmp_dir = tempfile.mkdtemp(prefix="sub_detect_", dir=config.TEMP_DIR if hasattr(config, 'TEMP_DIR') else None)
    try:
        frames = _extract_frames(src_path, tmp_dir, duration, num_frames)
        if not frames:
            logger.warning("抽帧失败，无法自动识别字幕区域")
            return None
        logger.debug("抽出 %d 帧用于字幕区域检测", len(frames))

        # 收集每帧的行投影
        projections = []
        frame_h = 0
        for fp in frames:
            try:
                img = Image.open(fp)
                frame_h = img.size[1]
                proj = _row_edge_projection(img)
                projections.append(proj)
            except Exception as e:
                logger.debug("处理帧失败 %s: %s", fp, e)

        if not projections or frame_h == 0:
            return None

        # 多帧取平均
        avg = [0.0] * frame_h
        for proj in projections:
            for y, v in enumerate(proj):
                avg[y] += v
        for y in range(frame_h):
            avg[y] /= len(projections)

        # 只在画面下部 50% 范围内找字幕（字幕一般在底部）
        bottom_start = int(frame_h * 0.5)
        sub_region = avg[bottom_start:]

        # 计算阈值：底部区域投影均值 + 标准差
        mean_v = sum(sub_region) / len(sub_region) if sub_region else 0
        max_v = max(sub_region) if sub_region else 0
        if max_v == 0:
            return None
        # 阈值：相对最大值的 50%
        # 字幕行峰值通常远高于背景（5-10倍），50% 能有效排除背景噪点
        # 之前用 30% 会把字幕上下方的弱噪点也包进来，导致区域偏大
        threshold_v = max_v * 0.50

        # 找连续超阈值的行段
        segments = []
        in_seg = False
        seg_start = 0
        for i, v in enumerate(sub_region):
            if v >= threshold_v:
                if not in_seg:
                    in_seg = True
                    seg_start = i
            else:
                if in_seg:
                    seg_len = i - seg_start
                    # 至少 8 行才算字幕段（过滤短噪点；按 480 宽缩放，8 行约对应原画面 1.5%+）
                    if seg_len >= 8:
                        segments.append((bottom_start + seg_start, bottom_start + i))
                    in_seg = False
        if in_seg:
            seg_len = len(sub_region) - seg_start
            if seg_len >= 8:
                segments.append((bottom_start + seg_start, frame_h))

        if not segments:
            logger.info("未检测到明显字幕区域，回退到默认区域")
            return None

        # 合并相邻段（间距 < 6 行的合并，避免字幕内部空隙被拆分）
        merged = [segments[0]]
        for s, e in segments[1:]:
            last_s, last_e = merged[-1]
            if s - last_e < 6:
                merged[-1] = (last_s, e)
            else:
                merged.append((s, e))

        # 选最靠下且足够长的段（字幕通常在底部）
        # 优先选长度 >= 15 行的；若都没有，选最长的
        candidates = [seg for seg in merged if (seg[1] - seg[0]) >= 15]
        if not candidates:
            candidates = merged
        # 选最靠下的
        best = max(candidates, key=lambda seg: seg[1])

        # 转换为比例
        y_ratio = best[0] / frame_h
        h_ratio = (best[1] - best[0]) / frame_h
        # 上下各扩 2% 画面高度（仅够覆盖描边/阴影，不过度外扩）
        # 之前 5% 会把字幕上下方背景也纳入抹除区域，导致区域偏大
        pad = 0.02
        y_ratio = max(0.0, y_ratio - pad)
        h_ratio = min(1.0 - y_ratio, h_ratio + pad * 2)

        # 字幕几乎都是全宽，x=0, w=1
        area = {"x": 0.0, "y": round(y_ratio, 3),
                "w": 1.0, "h": round(h_ratio, 3)}
        logger.info("自动检测字幕区域: y=%.0f%% h=%.0f%% (基于 %d 帧分析)",
                    y_ratio * 100, h_ratio * 100, len(projections))
        return area
    finally:
        # 清理临时帧
        for f in frames:
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


# ---------- 1. 字幕抹除 ----------

def build_erase_filter(cur_label, target_w, target_h, sub_cfg):
    """构建抹除原字幕的 filter 片段。

    用 crop + boxblur + overlay：取出字幕区域→高斯模糊→覆盖回去。
    比 delogo 对大区域效果更好（delogo 对窄 logo 条效果好，对整条字幕会模糊扩散）。
    返回: (filter片段, 新标签) 或 (None, cur_label) 表示不需要 ffmpeg 抹除
    """
    mode = sub_cfg.get("erase_mode", "blur")

    # inpaint / ocr-inpaint 模式已在预处理阶段完成（逐帧 OpenCV 修复），
    # ffmpeg 链中不再添加抹除滤镜
    if mode in ("inpaint", "ocr-inpaint"):
        return None, cur_label
    # 字幕区域（相对画面比例）
    area = sub_cfg.get("erase_area", {"x": 0.0, "y": 0.78, "w": 1.0, "h": 0.18})
    x = int(target_w * area["x"])
    y = int(target_h * area["y"])
    w = int(target_w * area["w"])
    h = int(target_h * area["h"])
    # 保证区域在画面内
    x = max(0, min(x, target_w - 2))
    y = max(0, min(y, target_h - 2))
    w = max(2, min(w, target_w - x))
    h = max(2, min(h, target_h - y))

    blur_label = f"{cur_label}_subblur"
    out_label = f"{cur_label}_erased"

    if mode == "cover":
        # 纯色覆盖（最简单，适合原字幕有黑底的）
        color = sub_cfg.get("cover_color", "black")
        opacity = sub_cfg.get("cover_opacity", 0.9)
        return (
            f"[{cur_label}]drawbox=x={x}:y={y}:w={w}:h={h}:"
            f"color={color}@{opacity}:t=fill[{out_label}]",
            out_label,
        )
    elif mode == "delogo":
        # delogo 修复（对窄条好）
        # delogo 算法需要参考像素，要求：
        #   x >= 1 且 y >= 1（边缘要有参考像素，不能为 0）
        #   x + w <= frame_w - 1 且 y + h <= frame_h - 1（严格小于，留参考像素）
        dx = max(1, x)
        dy = max(1, y)
        dw = min(w, target_w - dx - 1)
        dh = min(h, target_h - dy - 1)
        if dw >= 4 and dh >= 4:
            return (
                f"[{cur_label}]delogo=x={dx}:y={dy}:w={dw}:h={dh}[{out_label}]",
                out_label,
            )
        # 区域过小，回退到下面的 blur 模式
        logger.warning("delogo 区域过小 (%dx%d)，回退到 blur 模式", dw, dh)
        # 重新计算 w/h（用原始区域，撤销上面的缩放）
        w = max(2, min(int(target_w * area["w"]), target_w - x))
        h = max(2, min(int(target_h * area["h"]), target_h - y))

    if mode in ("blur", "delogo"):
        # blur: split → crop → boxblur → overlay
        # delogo 区域过小时也回退到这里
        # 关键：[cur_label] 不能被引用两次（ffmpeg 限制），必须用 split 分成两路
        # 一路 crop 出字幕区域并强模糊，另一路作为 overlay 底图
        # boxblur 的 radius 参数上限是 28（超过会报 "Invalid chroma_param radius value"）
        # 这里强制 clamp 到 [1, 28]，避免 ffmpeg 崩溃
        blur_strength = max(1, min(int(sub_cfg.get("blur_strength", 20)), 28))
        blur_power = sub_cfg.get("blur_power", 3)
        base_a = f"{cur_label}_erase_a"
        base_b = f"{cur_label}_erase_b"
        crop_label = f"{cur_label}_subcrop"
        return (
            f"[{cur_label}]split=2[{base_a}][{base_b}];"
            f"[{base_a}]crop={w}:{h}:{x}:{y}[{crop_label}];"
            f"[{crop_label}]boxblur={blur_strength}:{blur_power}[{blur_label}];"
            f"[{base_b}][{blur_label}]overlay={x}:{y}[{out_label}]",
            out_label,
        )


# ---------- 1b. OpenCV inpaint 抹除（更自然的修复，无模糊感） ----------

def _cv2_available():
    """检查 opencv-python 是否可用。"""
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _paddle_available():
    """检查 paddleocr 是否可用（ocr-inpaint 模式需要）。"""
    try:
        import torch  # noqa: F401  预加载避免 DLL 冲突
    except ImportError:
        pass
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        return True
    except ImportError:
        return False


def inpaint_subtitle(video_path, out_path, area, mode="inpaint",
                      ocr_samples=12, inpaint_radius=4, dilate=2,
                      tmp_dir=None, show_progress=True,
                      precomputed_boxes=None):
    """用 OpenCV inpaint 逐帧修复字幕区域（比 ffmpeg blur/delogo 更自然，无模糊感）。

    两种模式：
      mode="inpaint":     对整个字幕矩形区域做 TELEA inpaint，算法根据区域外像素智能填充
      mode="ocr-inpaint": 用 PaddleOCR 识别字幕文字精确位置，只对文字像素做 inpaint，
                          背景完全保留（最自然，但需要 paddleocr 且较慢）

    参数:
        video_path: 源视频
        out_path: 输出视频（含原音频，用 ffmpeg 合成）
        area: 字幕区域 {"x":, "y":, "w":, "h":} 相对画面比例
        mode: "inpaint" 或 "ocr-inpaint"
        ocr_samples: ocr-inpaint 模式的采样帧数（用于构建字幕文字 mask 模板）
        inpaint_radius: inpaint 修复半径（默认 4，越大修复范围越大但可能模糊）
        dilate: mask 膨胀半径（扩大修复区域覆盖文字描边，默认 2）
        tmp_dir: 临时文件目录
        show_progress: 是否打印进度
        precomputed_boxes: 预采样的字幕文字框 [(x1,y1,x2,y2),...]（全帧坐标），
                          来自水印阶段联合采样，有则跳过 OCR 采样避免重复
    返回:
        out_path 成功 / None 失败
    """
    if not _cv2_available():
        logger.error("opencv-python 未安装，无法使用 inpaint 模式（pip install opencv-python）")
        return None

    import cv2
    import numpy as np

    if mode == "ocr-inpaint" and not _paddle_available():
        logger.warning("paddleocr 未安装，ocr-inpaint 不可用，回退到 inpaint 模式")
        mode = "inpaint"

    tmp_dir = tmp_dir or os.path.dirname(out_path) or tempfile.gettempdir()
    os.makedirs(tmp_dir, exist_ok=True)

    # 探测视频信息
    w, h, duration = _probe(video_path)
    if not w or not h:
        logger.error("无法探测视频分辨率")
        return None

    # 字幕区域（相对比例 → 像素）
    sx = int(w * area.get("x", 0.0))
    sy = int(h * area.get("y", 0.78))
    sw = int(w * area.get("w", 1.0))
    sh = int(h * area.get("h", 0.18))
    # 边界保护
    sx = max(0, min(sx, w - 2))
    sy = max(0, min(sy, h - 2))
    sw = max(2, min(sw, w - sx))
    sh = max(2, min(sh, h - sy))

    logger.info("字幕 inpaint 修复: %s  %dx%d  区域=(%d,%d,%d,%d)  模式=%s",
                os.path.basename(video_path), w, h, sx, sy, sw, sh, mode)

    # 获取 fps 和帧数
    ffprobe = dedup_video._get_ffprobe()
    fps = 30.0
    nb_frames = 0
    if ffprobe:
        import json
        try:
            out = subprocess.check_output(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=nb_frames,r_frame_rate",
                 "-show_entries", "format=duration",
                 "-of", "json", video_path],
                stderr=subprocess.STDOUT, timeout=30,
            ).decode("utf-8", "ignore")
            d = json.loads(out)
            s = d.get("streams", [{}])[0]
            r = s.get("r_frame_rate", "30/1")
            if "/" in r:
                num, den = r.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 30.0
            nb_frames = int(s.get("nb_frames", 0))
            if not nb_frames and duration:
                nb_frames = int(duration * fps)
        except Exception:
            pass

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("无法打开视频: %s", video_path)
        return None

    # 临时无声视频
    silent_path = os.path.join(tmp_dir, "_subtitle_inpaint_silent.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(silent_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        logger.error("无法创建输出视频")
        cap.release()
        return None

    # === ocr-inpaint 模式：预采样字幕文字位置 ===
    ocr = None
    subtitle_boxes = []  # [(x1,y1,x2,y2), ...] OCR 识别到的字幕文字框
    if mode == "ocr-inpaint":
        if precomputed_boxes:
            # 复用水印阶段联合采样的字幕框（避免重复 OCR）
            logger.info("复用预采样字幕文字框: %d 个（跳过重复 OCR）", len(precomputed_boxes))
            subtitle_boxes = list(precomputed_boxes)
        else:
            from . import watermark_remove as wm_mod
            ocr = wm_mod._get_ocr()
            if ocr is not None:
                logger.info("OCR 采样字幕文字位置（%d 帧）...", ocr_samples)
                # 在视频中段采样
                sample_indices = _sample_frame_indices(nb_frames, ocr_samples)
                for fi in sample_indices:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                    ok, frame = cap.read()
                    if not ok:
                        continue
                    # 只在字幕区域内做 OCR（加速）
                    crop = frame[sy:sy+sh, sx:sx+sw]
                    if crop.size == 0:
                        continue
                    # _run_ocr_on_frame 内部已做缩放保护，返回坐标已还原到 crop 坐标
                    items = wm_mod._run_ocr_on_frame(crop, ocr, max_size=960)
                    for box, text, score in items:
                        if not text or len(text.strip()) < 2:
                            continue
                        # 跳过水印文字（@ 符号或水印关键词）
                        if wm_mod._is_watermark_text(text):
                            continue
                        # box 坐标已还原到 crop 坐标，加偏移转全帧坐标
                        rect = wm_mod._box_to_rect(box)
                        x1 = rect[0] + sx
                        y1 = rect[1] + sy
                        x2 = rect[0] + rect[2] + sx
                        y2 = rect[1] + rect[3] + sy
                        subtitle_boxes.append((x1, y1, x2, y2))
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                logger.info("OCR 识别到字幕文字框: %d 个", len(subtitle_boxes))

    # === 预计算 mask 和 kernel（避免逐帧重建，大幅提速）===
    # ocr-inpaint 模式：subtitle_boxes 固定，mask 每帧相同，只需创建一次
    # inpaint 模式：区域固定，mask 也固定
    precomputed_mask = None
    precomputed_crop_box = None  # (cy1, cy2, cx1, cx2) inpaint 模式的裁剪框
    precomputed_crop_mask = None  # inpaint 模式相对于 crop 的 mask
    dilate_kernel = None
    if dilate > 0:
        dilate_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))

    if mode == "ocr-inpaint" and subtitle_boxes:
        precomputed_mask = np.zeros((h, w), dtype=np.uint8)
        for (x1, y1, x2, y2) in subtitle_boxes:
            x1 = max(0, min(x1, w - 1))
            y1 = max(0, min(y1, h - 1))
            x2 = max(1, min(x2, w))
            y2 = max(1, min(y2, h))
            precomputed_mask[y1:y2, x1:x2] = 255
        if dilate_kernel is not None and precomputed_mask.any():
            precomputed_mask = cv2.dilate(precomputed_mask, dilate_kernel, iterations=1)
        if precomputed_mask.any():
            logger.info("预计算字幕 mask 完成（%d 个文字框，面积 %.1f%%）",
                        len(subtitle_boxes),
                        np.count_nonzero(precomputed_mask) * 100.0 / (w * h))
        else:
            logger.info("字幕 mask 为空，将跳过 inpaint 直接复制原帧")
    else:
        # inpaint 模式：预计算 crop 区域和 mask
        pad = max(8, sh // 2)
        cy1 = max(0, sy - pad)
        cy2 = min(h, sy + sh + pad)
        cx1 = max(0, sx - pad)
        cx2 = min(w, sx + sw + pad)
        mh = cy2 - cy1
        mw = cx2 - cx1
        precomputed_crop_box = (cy1, cy2, cx1, cx2)
        precomputed_crop_mask = np.zeros((mh, mw), dtype=np.uint8)
        rx1 = sx - cx1
        ry1 = sy - cy1
        rx2 = rx1 + sw
        ry2 = ry1 + sh
        precomputed_crop_mask[ry1:ry2, rx1:rx2] = 255
        if dilate_kernel is not None:
            precomputed_crop_mask = cv2.dilate(precomputed_crop_mask, dilate_kernel, iterations=1)

    # === 逐帧处理 ===
    frame_idx = 0
    inpainted_count = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if mode == "ocr-inpaint":
                # 用预计算的 mask（固定，每帧相同）
                if precomputed_mask is not None and precomputed_mask.any():
                    processed = cv2.inpaint(frame, precomputed_mask, inpaint_radius, cv2.INPAINT_TELEA)
                    inpainted_count += 1
                else:
                    processed = frame
            else:
                # inpaint 模式：用预计算的 crop mask
                cy1, cy2, cx1, cx2 = precomputed_crop_box
                crop = frame[cy1:cy2, cx1:cx2]
                inpainted_crop = cv2.inpaint(crop, precomputed_crop_mask, inpaint_radius, cv2.INPAINT_TELEA)
                processed = frame.copy()
                processed[cy1:cy2, cx1:cx2] = inpainted_crop
                inpainted_count += 1

            writer.write(processed)
            frame_idx += 1

            if show_progress and frame_idx % 30 == 0:
                pct = (frame_idx / nb_frames * 100) if nb_frames > 0 else 0
                logger.info("字幕 inpaint 进度: %d/%d 帧 (%.1f%%)", frame_idx, nb_frames, pct)
    finally:
        cap.release()
        writer.release()

    if frame_idx == 0:
        logger.error("未处理任何帧")
        return None

    logger.info("字幕 inpaint 完成: 共 %d 帧, 修复 %d 帧", frame_idx, inpainted_count)

    # 用 ffmpeg 合成原音频
    # 注意：视频流用 copy（OpenCV 已编码好），只编码音频，避免重编码耗时
    ffmpeg = _get_ffmpeg()
    cmd = [ffmpeg, "-y",
           "-i", silent_path,
           "-i", video_path,
           "-map", "0:v:0", "-map", "1:a:0?",
           "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k",
           "-shortest",
           out_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                                encoding="utf-8", errors="ignore")
        if result.returncode != 0:
            logger.error("ffmpeg 合成音频失败: %s", result.stderr[-500:] if result.stderr else "")
            return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg 合成音频超时")
        return None
    except Exception as e:
        logger.error("ffmpeg 合成异常: %s", e)
        return None

    # 清理临时文件
    try:
        os.remove(silent_path)
    except Exception:
        pass

    return out_path


def _sample_frame_indices(total_frames, n_samples):
    """生成均匀分布的采样帧索引（首尾密集）。"""
    if total_frames <= 0 or n_samples <= 0:
        return []
    if n_samples >= total_frames:
        return list(range(total_frames))
    # 首尾各取 20%，中间均匀采样
    indices = []
    # 首 20% 区域
    head_end = max(1, int(total_frames * 0.2))
    tail_start = min(total_frames - 1, int(total_frames * 0.8))
    head_count = max(1, n_samples // 4)
    tail_count = max(1, n_samples // 4)
    mid_count = n_samples - head_count - tail_count

    for i in range(head_count):
        idx = int(i * head_end / head_count)
        indices.append(idx)
    for i in range(mid_count):
        idx = head_end + int(i * (tail_start - head_end) / max(1, mid_count))
        indices.append(idx)
    for i in range(tail_count):
        idx = tail_start + int(i * (total_frames - tail_start) / max(1, tail_count))
        indices.append(min(idx, total_frames - 1))

    return sorted(set(indices))


# ---------- 2. 字幕文本提取 ----------

def clean_asr_hallucinations(text: str, similarity_threshold: float = 0.65) -> str:
    """清除 ASR 幻觉导致的重复文本。

    针对 Whisper small 等模型在静音段产生的幻觉：
      1. 句内短语重复：如 "毛绒衣服上放系里面的毛绒衣服上放系里面的..."
         → 用正则匹配连续重复的 3~30 字短语，保留一份
      2. 跨句全域重复：后段重复前段已说过的句子
         → 用 SequenceMatcher 模糊比对子句，相似度 >= threshold 剔除

    参数:
        text: 原始识别文本
        similarity_threshold: 子句相似度阈值（默认 0.65），>= 该值视为重复
    返回:
        清理后的文本
    """
    if not text or not text.strip():
        return text

    import re
    from difflib import SequenceMatcher

    cleaned = text

    # === 1. 句内短语去重 ===
    # 匹配模式：(3~30 字符的中文/字母/数字短语) 后紧跟相同短语（中间允许少量标点/空格）
    # 示例："毛绒衣服上放系里面的毛绒衣服上放系里面的" → "毛绒衣服上放系里面的"
    # 用反向引用 \1 确保前后完全一致
    phrase_pattern = re.compile(
        r"([\u4e00-\u9fa5A-Za-z0-9]{3,30})[\s,，。、；;:：!！?？\-]*\1"
    )
    # 循环替换，处理同句中重复 3 次以上的情况（"ABABAB" → "AB"）
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = phrase_pattern.sub(r"\1", cleaned)

    # === 2. 跨句全域去重 ===
    # 按句号/问号/叹号/换行切分为子句
    clause_split = re.compile(r"[。！？!?\n]+")
    clauses = [c.strip() for c in clause_split.split(cleaned) if c.strip()]
    if len(clauses) <= 1:
        return cleaned

    seen_clauses = []  # 已确认保留的子句（用于模糊比对）
    kept = []
    for clause in clauses:
        is_dup = False
        for seen in seen_clauses:
            # SequenceMatcher 计算相似度
            ratio = SequenceMatcher(None, clause, seen).ratio()
            if ratio >= similarity_threshold:
                logger.debug("ASR 幻觉去重: 剔除重复子句 (相似度 %.2f): %s", ratio, clause[:30])
                is_dup = True
                break
        if not is_dup:
            kept.append(clause)
            seen_clauses.append(clause)

    result = "。".join(kept)
    if result and not result.endswith(("。", "！", "？", "!", "?")):
        result += "。"

    if len(result) < len(cleaned) - 5:
        logger.info("ASR 幻觉去重: %d字 → %d字 (剔除重复 %d字)",
                    len(cleaned), len(result), len(cleaned) - len(result))
    return result


def transcribe_with_whisper(video_path, model_name="base", language="zh"):
    """用 whisper 从视频音频识别字幕。

    返回: [(start, end, text), ...] 或 None（失败/未安装）

    说明:
        Whisper 的 language="zh" 训练数据简繁混合，默认输出常偏向繁体。
        通过 initial_prompt 用简体中文提示词引导模型输出简体；
        同时若安装了 opencc-python-reimplemented，做一次繁→简转换兜底。

    兼容性:
        Whisper 内部调用 ffmpeg -f s16le 转 PCM，但系统 PATH 里的 ffmpeg 可能是
        精简版（无 s16le muxer/pipe 协议）。这里改用项目自带的完整版 ffmpeg
        先把音频提取成 16kHz mono wav，再用 numpy 读取后直接传给 whisper，
        彻底绕过 whisper.load_audio 内部的 ffmpeg 调用。
    """
    try:
        import whisper  # type: ignore
    except ImportError:
        logger.info("whisper 未安装，回退到文案分句（pip install -U openai-whisper 启用语音识别）")
        return None

    # 简体中文提示词：引导 whisper 输出简体（社区公认有效）
    initial_prompt = "以下是普通话的句子，使用简体中文输出。"

    logger.info("用 whisper 识别语音（模型 %s，简体中文）...", model_name)
    try:
        # 指定模型加载路径到项目目录（D:\xhs\models\whisper），避免占用 C 盘默认缓存
        download_root = config.WHISPER_MODEL_DIR
        os.makedirs(download_root, exist_ok=True)
        model = whisper.load_model(model_name, download_root=download_root)

        # 用项目完整版 ffmpeg 提取 16kHz mono 16-bit wav，绕过 whisper 内部 ffmpeg 调用
        audio_array = _extract_audio_for_whisper(video_path)
        if audio_array is None:
            # 回退：让 whisper 自己加载（可能因精简版 ffmpeg 失败）
            logger.warning("音频预提取失败，回退到 whisper 默认加载（可能因 ffmpeg 精简版失败）")
            result = model.transcribe(
                video_path, language=language, verbose=False,
                initial_prompt=initial_prompt)
        else:
            result = model.transcribe(
                audio_array, language=language, verbose=False,
                initial_prompt=initial_prompt)

        segments = []
        for seg in result.get("segments", []):
            text = seg.get("text", "").strip()
            if text:
                segments.append((seg["start"], seg["end"], text))
        if segments:
            segments = _to_simplified_chinese(segments)
            # ASR 幻觉去重：句内短语重复 + 跨句全域重复
            # 1. 对每个 segment 单独做句内短语去重
            cleaned_segs = []
            for start, end, text in segments:
                cleaned = clean_asr_hallucinations(text)
                if cleaned.strip():
                    cleaned_segs.append((start, end, cleaned))
            # 2. 对整段文本做跨句去重（剔除重复出现的子句）
            full_text = "。".join(s[2] for s in cleaned_segs if s[2].strip())
            if full_text.strip():
                dedup_full = clean_asr_hallucinations(full_text)
                if dedup_full != full_text:
                    # 全文被去重过，重新切分为 segments（保持原时间戳近似）
                    import re as _re
                    new_clauses = [c.strip() for c in _re.split(r"[。！？!?\n]+", dedup_full) if c.strip()]
                    if new_clauses and len(new_clauses) <= len(cleaned_segs):
                        # 把去重后的子句按原 segments 的时间戳顺序重新分配
                        # 去重可能减少子句数，剩余 segments 时间戳合并到末尾
                        result_segs = []
                        for i, clause in enumerate(new_clauses):
                            if i < len(cleaned_segs):
                                result_segs.append((cleaned_segs[i][0], cleaned_segs[i][1], clause))
                        # 如果去重后子句比原 segments 少，保留剩余时间戳不丢失
                        if len(new_clauses) < len(cleaned_segs):
                            last_clause = new_clauses[-1] if new_clauses else ""
                            last_end = cleaned_segs[-1][1]
                            last_start = cleaned_segs[len(new_clauses) - 1][1] if len(new_clauses) > 0 else 0
                            # 不追加空 segment，保留有效内容即可
                        segments = result_segs if result_segs else cleaned_segs
                    else:
                        segments = cleaned_segs
                else:
                    segments = cleaned_segs
        logger.info("whisper 识别到 %d 句字幕（简体，已 ASR 幻觉去重）", len(segments))
        return segments if segments else None
    except Exception as e:
        logger.warning("whisper 识别失败: %s，回退到文案分句", e)
        return None


def _extract_audio_for_whisper(video_path, sr=16000):
    """用项目完整版 ffmpeg 提取音频，返回 whisper 所需的 float32 numpy 数组。

    Whisper 期望 16kHz mono float32 数组（值域 [-1, 1]）。
    用 ffmpeg 提取成 16-bit PCM wav 临时文件，再用 Python wave 模块读取，
    完全绕过 whisper.load_audio 内部对 ffmpeg s16le/pipe 的依赖。

    返回: np.ndarray 或 None（失败）
    """
    import tempfile
    import wave
    import numpy as np

    ffmpeg = _get_ffmpeg()
    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=config.TEMP_DIR if hasattr(config, 'TEMP_DIR') else None)
    tmp_wav.close()
    try:
        cmd = [
            ffmpeg, "-nostdin", "-threads", "0",
            "-i", video_path,
            "-vn",                    # 不要视频
            "-ac", "1",               # 单声道
            "-ar", str(sr),           # 16kHz
            "-acodec", "pcm_s16le",   # 16-bit PCM
            "-y",                     # 覆盖
            tmp_wav.name,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")[-500:]
            logger.warning("ffmpeg 提取音频失败: %s", err)
            return None

        # 用 wave 模块读取 16-bit PCM wav → float32 numpy
        with wave.open(tmp_wav.name, "rb") as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        audio = np.frombuffer(raw, np.int16).flatten().astype(np.float32) / 32768.0
        logger.info("音频预提取完成: %d 采样（%.1fs）", len(audio), len(audio) / sr)
        return audio
    except Exception as e:
        logger.warning("音频预提取异常: %s", e)
        return None
    finally:
        try:
            os.unlink(tmp_wav.name)
        except Exception:
            pass


def _to_simplified_chinese(segments):
    """把 whisper 识别结果转成简体中文（若安装了 opencc）。

    未安装 opencc 时静默跳过（initial_prompt 已能解决大部分情况）。
    """
    try:
        from opencc import OpenCC  # type: ignore
    except ImportError:
        return segments
    try:
        cc = OpenCC("t2s")  # 繁体 → 简体
        out = []
        for start, end, text in segments:
            out.append((start, end, cc.convert(text)))
        logger.info("已用 opencc 转换为简体中文")
        return out
    except Exception as e:
        logger.debug("opencc 转换失败，保持原文: %s", e)
        return segments


def split_text_to_segments(text, duration, max_chars=18):
    """把文案按标点分句，均匀分配到视频时长。

    返回: [(start, end, text), ...]
    """
    if not text or not text.strip():
        return []
    # 按中文标点和换行分句
    import re
    sentences = re.split(r"[。！？!?\n；;]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return []

    # 过长的再切：优先按逗号/顿号等次级标点切，避免词语从中间被硬切断
    final = []
    for s in sentences:
        if len(s) > max_chars:
            # 先按次级标点切（逗号/顿号/空格）
            import re
            parts = re.split(r"[，,、\s]+", s)
            parts = [p.strip() for p in parts if p.strip()]
            cur = ""
            for p in parts:
                if cur and len(cur) + len(p) + 1 > max_chars:
                    final.append(cur)
                    cur = p
                else:
                    cur = cur + p if not cur else cur + p
            if cur:
                final.append(cur)
            # 若切完仍有超长片段（单个词超过 max_chars），硬切兜底
            final = [x if len(x) <= max_chars * 2 else x[:max_chars*2] for x in final]
        else:
            final.append(s)

    if not final:
        return []

    # 均匀分配时间
    seg_dur = duration / len(final)
    segments = []
    for i, s in enumerate(final):
        start = i * seg_dur
        end = min(duration, (i + 1) * seg_dur)
        segments.append((start, end, s))
    return segments


def extract_subtitle_text(video_path, note_text, duration, use_whisper=False,
                          whisper_model="base"):
    """提取字幕文本。优先 whisper，回退文案分句。

    返回: (segments, source)
      segments: [(start, end, text), ...]
      source: "whisper" / "note_text"（标识来源，供配音模块判断是否用字幕作配音）
    """
    if use_whisper:
        segs = transcribe_with_whisper(video_path, whisper_model)
        if segs:
            return segs, "whisper"
    # 回退：用笔记文案分句
    logger.info("用笔记文案分句生成字幕")
    return split_text_to_segments(note_text, duration), "note_text"


# ---------- 3. ASS 字幕生成 ----------

def _ass_color(rgba_str):
    """把 'white'/'black'/'#RRGGBB'/'rgba(r,g,b,a)' 转 ASS 颜色 &HAABBGGRR。

    简化：只支持预设颜色名和 #RRGGBB。
    """
    presets = {
        "white": "&H00FFFFFF",
        "black": "&H00000000",
        "yellow": "&H0000FFFF",
        "red": "&H000000FF",
        "blue": "&H00FF0000",
        "green": "&H0000FF00",
        "pink": "&H00FF80FF",
        "cyan": "&H00FFFF00",
    }
    s = rgba_str.strip().lower()
    if s in presets:
        return presets[s]
    if s.startswith("#") and len(s) == 7:
        r = int(s[1:3], 16)
        g = int(s[3:5], 16)
        b = int(s[5:7], 16)
        return f"&H00{b:02X}{g:02X}{r:02X}"
    return "&H00FFFFFF"  # 默认白


def build_ass_subtitle(segments, style_cfg, dst_path, target_w=1080, target_h=1920,
                       position_area=None):
    """生成 ASS 字幕文件。

    参数:
        segments: [(start, end, text), ...]
        style_cfg: 字幕样式配置（见 config.SUBTITLE_STYLES）
        dst_path: .ass 输出路径
        target_w/h: 视频尺寸
        position_area: 可选，{"x":, "y":, "w":, "h":} 相对画面比例
                       指定后字幕会渲染到该区域中心（跟随原字幕位置）
                       None 时用 style_cfg 里的 alignment/margin_v
    返回:
        dst_path
    """
    fontname = style_cfg.get("fontname", "黑体")
    fontsize = style_cfg.get("fontsize", 56)
    primary = _ass_color(style_cfg.get("primary_color", "white"))
    outline_col = _ass_color(style_cfg.get("outline_color", "black"))
    back_col = _ass_color(style_cfg.get("back_color", "black"))
    outline = style_cfg.get("outline", 2)
    shadow = style_cfg.get("shadow", 1)
    bold = -1 if style_cfg.get("bold", True) else 0
    italic = -1 if style_cfg.get("italic", False) else 0
    alignment = style_cfg.get("alignment", 2)  # 2=底部居中
    margin_v = style_cfg.get("margin_v", 100)
    bg_box = style_cfg.get("background_box", False)

    # 如果指定了位置区域，根据区域中心计算 alignment 和 margin_v
    # 让新字幕渲染到原字幕所在位置
    if position_area and target_h > 0:
        area_y = position_area.get("y", 0.75)
        area_h = position_area.get("h", 0.20)
        area_center_y = area_y + area_h / 2  # 区域中心(比例)

        # ASS alignment: 1=左下 2=中下 3=右下 4=左中 5=中中 6=右中 7=左上 8=中上 9=右上
        # 保留原水平对齐(取个位)，垂直位置根据区域中心决定
        horiz = alignment % 3  # 1=左 2=中 0=右(转成3)
        if horiz == 0:
            horiz = 3

        if area_center_y < 0.33:
            # 区域在上部 1/3 → 顶部对齐(7/8/9)
            alignment = 6 + horiz  # 7/8/9
            # margin_v = 区域中心距顶部距离(像素)
            margin_v = int(target_h * area_center_y - fontsize / 2)
            margin_v = max(0, margin_v)
        elif area_center_y > 0.66:
            # 区域在下部 1/3 → 底部对齐(1/2/3)
            alignment = horiz  # 1/2/3
            # margin_v = 区域中心距底部距离(像素)
            margin_v = int(target_h * (1 - area_center_y) - fontsize / 2)
            margin_v = max(0, margin_v)
        else:
            # 区域在中部 → 中部对齐(4/5/6)
            alignment = 3 + horiz  # 4/5/6
            # 中部对齐时 margin_v 是距视频中心的偏移
            offset_px = int(target_h * (area_center_y - 0.5))
            margin_v = abs(offset_px)
        logger.info("字幕位置跟随原字幕区域: alignment=%d margin_v=%d (中心 y=%.0f%%)",
                    alignment, margin_v, area_center_y * 100)

    def fmt_time(t):
        h_ = int(t // 3600)
        m_ = int((t % 3600) // 60)
        s_ = t % 60
        return f"{h_}:{m_:02d}:{s_:05.2f}"

    lines = []
    lines.append("[Script Info]")
    lines.append("Title: XHS Subtitle")
    lines.append("ScriptType: v4.00+")
    lines.append(f"PlayResX: {target_w}")
    lines.append(f"PlayResY: {target_h}")
    # WrapStyle: 2=在行尾自动换行（\N），文字超过 MarginL/MarginR 边距时折行
    # 0=智能换行（可能溢出）, 1=行尾换行, 2=自动换行不溢出, 3=不换行
    lines.append("WrapStyle: 2")
    lines.append("ScaledBorderAndShadow: yes")
    lines.append("")
    lines.append("[V4+ Styles]")
    lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                 "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                 "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                 "Alignment, MarginL, MarginR, MarginV, Encoding")
    # BorderStyle: 1=描边+阴影, 3=不透明底框
    border_style = 3 if bg_box else 1
    # 左右边距：按目标宽度的 8% 计算（1080px → 约 86px），保证文字不贴边不溢出
    margin_lr = max(60, int(target_w * 0.08))
    lines.append(
        f"Style: Default,{fontname},{fontsize},{primary},&H000000FF,"
        f"{outline_col},{back_col},{bold},{italic},0,0,100,100,0,0,"
        f"{border_style},{outline},{shadow},{alignment},{margin_lr},{margin_lr},{margin_v},1")
    lines.append("")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    for start, end, text in segments:
        # ASS 特殊字符转义
        safe = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{fmt_time(start)},{fmt_time(end)},Default,,0,0,0,,{safe}")

    content = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("生成 ASS 字幕: %s (%d 句)", dst_path, len(segments))
    return dst_path


def find_font_dir():
    """返回一个只包含 TTF/TTC/OTF 字体的干净目录，用于 ffmpeg subtitles 滤镜的 fontsdir。

    C:\\Windows\\Fonts 目录下包含 .fon 位图字体文件（如 vgafix.fon），
    libass 无法解析会报 "Error getting metadata for embedded font" 警告，
    严重时导致 ffmpeg 崩溃（rc=4294967274）。
    这里创建一个干净的临时目录，只复制项目实际使用的 TTF/TTC 字体。
    """
    import shutil

    sys_fonts = r"C:\Windows\Fonts"
    if not os.path.isdir(sys_fonts):
        return None

    # 在项目 temp 下创建持久字体目录（首次复制，后续复用）
    # 避免占用 C 盘系统临时目录空间
    font_dir = os.path.join(config.TEMP_DIR if hasattr(config, 'TEMP_DIR') else tempfile.gettempdir(), "xhs_fonts_clean")
    os.makedirs(font_dir, exist_ok=True)

    # 项目实际使用的字体：ASS 字幕用"黑体"(SimHei)，drawtext 也用这些
    needed_fonts = [
        "simhei.ttf", "msyh.ttf", "msyh.ttc", "arial.ttf",
        "simsun.ttc", "simkai.ttf", "stxihei.ttf",
    ]
    for name in needed_fonts:
        src = os.path.join(sys_fonts, name)
        dst = os.path.join(font_dir, name)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

    # 确认目录里至少有一个有效字体
    if any(f.lower().endswith((".ttf", ".ttc", ".otf")) for f in os.listdir(font_dir)):
        return font_dir
    # 兜底：回退到系统字体目录（虽然可能有 .fon 警告，但至少能找到字体）
    return sys_fonts
