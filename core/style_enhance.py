# -*- coding: utf-8 -*-
"""小红书风格增强 + 自动二创叠加层模块。

在去重之后做两轮处理：
  1. 风格增强：9:16 竖屏适配 + 暖色调 + 磨皮 + 水印 + 淡入淡出
  2. 二创叠加：贴纸 overlay + 扫光 + 画中画 + 字幕条 + 噪点

全部在 ffmpeg filter_complex 里完成，不需要手工剪映。
"""
import os
import json
import random
import logging
import subprocess
import tempfile

import config
from . import dedup_video
from . import subtitle as subtitle_mod
from . import filters as filters_mod

logger = logging.getLogger("style_enhance")


def _get_ffmpeg():
    return dedup_video._get_ffmpeg()


def _probe(src_path):
    """探测视频基本信息。"""
    w, h = dedup_video.probe_resolution(src_path)
    d = dedup_video.probe_duration(src_path)
    return w, h, d


def _find_font(prefer_cute=False):
    """找一个支持中文的系统字体（支持偏向可爱卡哇伊/手写圆体字体）。"""
    if prefer_cute:
        cute_candidates = [
            r"C:\Windows\Fonts\STCAIYUN.TTF",  # 华文彩云（可爱圆润）
            r"C:\Windows\Fonts\STHUAPO.TTF",   # 华文琥珀（胖嘟嘟卡哇伊）
            r"C:\Windows\Fonts\SIMYOU.TTF",    # 幼圆（经典卡哇伊圆润体）
            r"C:\Windows\Fonts\FZSTK.TTF",     # 方正舒体
            r"C:\Windows\Fonts\msyhbd.ttc",    # 微软雅黑 Bold
        ]
        for p in cute_candidates:
            if os.path.exists(p):
                return p

    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _esc_path(p):
    """转义 ffmpeg filter 里的文件路径：反斜杠→正斜杠，冒号→\\:"""
    return p.replace("\\", "/").replace(":", r"\:")


def _write_text_file(text, out_dir, prefix="txt_"):
    """把文本写入 UTF-8 文件（绕过命令行中文编码问题）。"""
    os.makedirs(out_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".txt", prefix=prefix, dir=out_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _pick_stickers(creative, target_w):
    """随机选择贴纸文件。返回 [(path, pos_x_ratio, pos_y_ratio), ...]"""
    if not creative.get("stickers_enabled"):
        return []
    sticker_dir = config.STICKER_DIR
    if not os.path.isdir(sticker_dir):
        logger.warning("贴纸目录不存在: %s，跳过贴纸", sticker_dir)
        return []

    # 找所有贴纸 PNG
    all_stickers = [f for f in os.listdir(sticker_dir)
                    if f.endswith(".png") and not f.startswith("_")]
    if not all_stickers:
        return []

    count = min(creative.get("sticker_count", 2), 4)
    chosen = random.sample(all_stickers, min(count, len(all_stickers)))
    positions = creative.get("sticker_positions", [(0.05, 0.08), (0.85, 0.15)])
    random.shuffle(positions)

    result = []
    for i, name in enumerate(chosen):
        pos = positions[i % len(positions)]
        result.append((os.path.join(sticker_dir, name), pos[0], pos[1]))
    return result


def _build_creative_layers(cur_label, fc_parts, extra_inputs, target_w, target_h,
                           duration, creative, caption_text=None, tmp_dir=None):
    """在已有滤镜链基础上，叠加二创层（贴纸/扫光/画中画/字幕条/噪点）。

    参数:
        cur_label: 当前流标签
        fc_parts: filter_complex 片段列表（追加）
        extra_inputs: 额外输入文件列表（追加，对应 -i）
        target_w/h: 目标尺寸
        duration: 视频时长
        creative: 二创参数
        caption_text: 字幕文字
        tmp_dir: 临时文件目录
    返回:
        (新 cur_label, wm_files_to_clean)
    """
    wm_files = []
    input_idx = 1  # 额外输入从 [1:v] 开始（[0:v] 是主视频）

    # ---- 1. 贴纸 overlay ----
    stickers = _pick_stickers(creative, target_w)
    if stickers:
        opacity = creative.get("sticker_opacity", 0.02)
        size_ratio = creative.get("sticker_size_ratio", 0.12)
        sticker_px = int(target_w * size_ratio)
        animate = creative.get("sticker_animate", True)
        logger.info("添加 %d 个贴纸 (透明度 %.0f%%)", len(stickers), opacity * 100)

        for i, (path, px, py) in enumerate(stickers):
            inp_label = f"stk{i}_in"
            proc_label = f"stk{i}"
            extra_inputs.append(path)
            # 贴纸处理：缩放 + 设置透明度
            stk_filter = (
                f"[{input_idx}:v]format=rgba,"
                f"scale={sticker_px}:-1:flags=lanczos,"
                f"colorchannelmixer=aa={opacity:.4f}[{proc_label}]"
            )
            fc_parts.append(stk_filter)
            # overlay 位置（可选飘动动画）
            base_x = int(target_w * px)
            base_y = int(target_h * py)
            if animate:
                # 轻微飘动：sin 波动 ±5px
                x_expr = f"'{base_x}+5*sin(t*1.5+{i})'"
                y_expr = f"'{base_y}+5*cos(t*1.2+{i})'"
            else:
                x_expr = f"{base_x}"
                y_expr = f"{base_y}"
            new_label = f"stk{i}_out"
            fc_parts.append(
                f"[{cur_label}][{proc_label}]overlay={x_expr}:{y_expr}[{new_label}]")
            cur_label = new_label
            input_idx += 1

    # ---- 2. 扫光效果 ----
    if creative.get("sweep_enabled") and duration and duration > 3:
        sweep_count = creative.get("sweep_count", 2)
        sweep_opacity = creative.get("sweep_opacity", 0.05)
        sweep_w = max(2, int(target_w * creative.get("sweep_width_ratio", 0.06)))
        sweep_dur = creative.get("sweep_duration", 1.5)
        logger.info("添加扫光 x%d (透明度 %.0f%%)", sweep_count, sweep_opacity * 100)

        # 用 color 滤镜生成白色条，用 colorchannelmixer 设置整体透明度
        # 避免 geq（在某些 ffmpeg 版本上会 crash）
        sweep_label = "sweep_bar"
        alpha_hex = f"{int(sweep_opacity*255):02x}"
        sweep_gen = (
            f"color=c=white@{sweep_opacity:.4f}:s={sweep_w}x{target_h}:d={duration},"
            f"format=rgba[{sweep_label}]"
        )
        fc_parts.append(sweep_gen)

        # 多次扫光，用 enable 控制时间
        for i in range(sweep_count):
            start_t = (duration / (sweep_count + 1)) * (i + 1) - sweep_dur / 2
            start_t = max(0, start_t)
            end_t = min(duration, start_t + sweep_dur)
            # x 从 -sweep_w 扫到 target_w
            travel = target_w + sweep_w
            x_expr = f"'-{sweep_w}+(t-{start_t:.3f})*{travel/sweep_dur:.1f}'"
            new_label = f"sweep{i}_out"
            fc_parts.append(
                f"[{cur_label}][{sweep_label}]overlay={x_expr}:0:"
                f"enable='between(t,{start_t:.3f},{end_t:.3f})'[{new_label}]")
            cur_label = new_label

    # ---- 3. 画中画 ----
    pip_path = creative.get("_pip_path")  # 由调用方注入
    if creative.get("pip_enabled") and pip_path and os.path.exists(pip_path):
        scale = creative.get("pip_scale", 0.25)
        opacity = creative.get("pip_opacity", 0.85)
        border = creative.get("pip_border", 2)
        border_color = creative.get("pip_border_color", "white")
        pos = creative.get("pip_position", "bottom-right")
        logger.info("添加画中画: %s", os.path.basename(pip_path))

        pip_w = int(target_w * scale)
        pip_label = "pip_main"
        extra_inputs.append(pip_path)
        pip_filter = (
            f"[{input_idx}:v]scale={pip_w}:-1:flags=lanczos,setsar=1,"
            f"format=rgba,"
            f"colorchannelmixer=aa={opacity:.2f}"
        )
        if border > 0:
            pip_filter += f",pad=iw+{border*2}:ih+{border*2}:{border}:{border}:color={border_color}@1"
        pip_filter += f"[{pip_label}]"
        fc_parts.append(pip_filter)

        # 位置
        positions = {
            "top-left": (f"{int(target_w*0.03)}", f"{int(target_h*0.03)}"),
            "top-right": (f"W-w-{int(target_w*0.03)}", f"{int(target_h*0.03)}"),
            "bottom-left": (f"{int(target_w*0.03)}", f"H-h-{int(target_h*0.05)}"),
            "bottom-right": (f"W-w-{int(target_w*0.03)}", f"H-h-{int(target_h*0.05)}"),
            "center": ("(W-w)/2", "(H-h)/2"),
        }
        x, y = positions.get(pos, positions["bottom-right"])
        new_label = "pip_out"
        fc_parts.append(f"[{cur_label}][{pip_label}]overlay={x}:{y}[{new_label}]")
        cur_label = new_label
        input_idx += 1

    # ---- 4. 爆款 Hook 顶部标题卡（前3秒黄金留存关键）----
    top_hook = creative.get("top_hook", True)
    hook_text = creative.get("top_hook_text") or caption_text
    if top_hook and hook_text:
        # 清理 #标签 及其后的多余符号
        hook_text = re.sub(r"#\S+", "", hook_text).strip()
        hook_text = re.sub(r"\s+", " ", hook_text)

    if top_hook and hook_text:
        hook_style = creative.get("top_hook_style", "viral_yellow")
        prefer_cute = (hook_style == "kawaii")
        font = _find_font(prefer_cute=prefer_cute)
        if font:
            # 丰富多变的新潮/卡哇伊/红黄白风格配色
            if hook_style == "kawaii":
                bg_col, text_col = "pink@0.95", "white"        # 少女粉底白字 (卡哇伊)
            elif hook_style == "kawaii_purple":
                bg_col, text_col = "purple@0.90", "yellow"     # 梦幻紫底黄字 (卡哇伊)
            elif hook_style == "viral_red":
                bg_col, text_col = "red@0.90", "white"         # 爆款红底白字
            elif hook_style == "clean_white":
                bg_col, text_col = "white@0.95", "black"       # 极简白底黑字
            else:
                bg_col, text_col = "yellow@0.95", "black"      # 经典爆款黄底黑字 (viral_yellow)

            # 根据文字长度自动计算字号与边距，防止超出视频边界
            char_count = max(1, len(hook_text))
            max_usable_w = int(target_w * 0.88)  # 两边留 6% 边距安全区，坚决不超界
            
            # 单字估计宽度并动态缩小字号
            fontsize = int((target_h * 0.065) * 0.55)  # 基础字号 ~42px
            estimated_tw = char_count * fontsize + 40   # 包含内边距 padding
            if estimated_tw > max_usable_w:
                fontsize = int((max_usable_w - 40) / char_count)
                fontsize = max(24, fontsize)  # 保证最小可读字号 24px

            bar_y = int(target_h * 0.08)  # 居顶部约 8% 位置
            text_y = bar_y

            txt_file = _write_text_file(hook_text, tmp_dir, prefix="hook_")
            wm_files.append(txt_file)
            txt_escaped = _esc_path(txt_file)
            font_escaped = _esc_path(font)

            hook_label = "hook_out"
            fc_parts.append(
                f"[{cur_label}]drawtext=fontfile='{font_escaped}':"
                f"textfile='{txt_escaped}':fontsize={fontsize}:"
                f"fontcolor={text_col}:box=1:boxcolor={bg_col}:boxborderw=16:"
                f"x=(w-tw)/2:y={text_y}:enable='between(t,0,3.5)'[{hook_label}]")
            cur_label = hook_label
            logger.info("添加前3秒黄金 Hook 标题卡(Style=%s, Size=%d): %s", hook_style, fontsize, hook_text[:20])

    # ---- 5. 普通字幕条 ----
    if (creative.get("caption_enabled") and
            creative.get("caption_style", "bottom") != "none" and
            caption_text and not top_hook):
        font = _find_font()
        if font:
            style = creative.get("caption_style", "bottom")
            bg_color = creative.get("caption_bg_color", "black@0.45")
            text_color = creative.get("caption_text_color", "white")
            fontsize = creative.get("caption_fontsize", 42)
            bar_h = int(target_h * creative.get("caption_height_ratio", 0.08))

            # 写字幕文本到文件
            txt_file = _write_text_file(caption_text, tmp_dir, prefix="cap_")
            wm_files.append(txt_file)
            txt_escaped = _esc_path(txt_file)
            font_escaped = _esc_path(font)

            if style == "bottom":
                # 底部半透明黑条 + 居中文字
                bar_y = target_h - bar_h
                text_y = bar_y + (bar_h - fontsize) // 2
            else:  # top
                bar_y = 0
                text_y = (bar_h - fontsize) // 2

            bar_label = f"{cur_label}_bar"
            cap_label = "cap_out"
            fc_parts.append(
                f"[{cur_label}]drawbox=x=0:y={bar_y}:w=iw:h={bar_h}:"
                f"color={bg_color}:t=fill[{bar_label}]")
            fc_parts.append(
                f"[{bar_label}]drawtext=fontfile='{font_escaped}':"
                f"textfile='{txt_escaped}':fontsize={fontsize}:"
                f"fontcolor={text_color}:x=(w-tw)/2:y={text_y}"
                f":shadowcolor=black@0.5:shadowx=1:shadowy=1[{cap_label}]")
            cur_label = cap_label
            logger.info("添加字幕条: %s", caption_text[:30])

    # ---- 5. 噪点（极淡，破坏像素级查重）----
    if creative.get("noise_enabled") and creative.get("noise_strength", 0) > 0:
        ns = creative.get("noise_strength")
        new_label = "noised"
        fc_parts.append(f"[{cur_label}]noise=alls={ns}:allf=t[{new_label}]")
        cur_label = new_label

    return cur_label, wm_files


def _build_filter_complex(w, h, duration, params, creative=None,
                          watermark=None, caption_text=None, tmp_dir=None,
                          pip_path=None, subtitle_cfg=None, ass_path=None,
                          filter_preset=None, effects=None, effect_intensity=1.0,
                          filter_intensity=1.0):
    """构建完整滤镜链：风格增强 + 字幕抹除 + 二创叠加 + ASS新字幕。

    返回: (filter_complex, output_label, extra_inputs, wm_files)
    """
    if creative is None:
        creative = {}
    extra_inputs = []
    wm_files = []
    fc_parts = []

    target_w = params.get("target_width", 1080)
    target_h = params.get("target_height", 1920)
    target_w -= target_w % 2
    target_h -= target_h % 2

    _tmp_dir = tmp_dir or getattr(config, 'TEMP_DIR', tempfile.gettempdir())

    # === 风格增强层 ===
    # 前景：高精度 Lanczos 缩放 + 调色 + 自然饱和度 + 空域精细降噪 + 3x3 锐化
    hd_enhance = params.get("hd_enhance", True)
    scale_flags = "flags=lanczos+accurate_rnd+full_chroma_int"
    fg_scale = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:{scale_flags},"
                f"setsar=1")

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
        fg_scale += f",eq={':'.join(eq_parts)}"

    if params.get("warm", 0) != 0:
        wv = params["warm"]
        fg_scale += (f",colorbalance=rs={wv}:gs={wv*0.3}:bs={-wv}:"
                     f"rm={wv*0.5}:gm={wv*0.2}:bm={-wv*0.4}")

    vibrance = params.get("vibrance", 0.15 if hd_enhance else 0)
    if vibrance and vibrance > 0:
        fg_scale += f",vibrance=intensity={vibrance:.2f}"

    if params.get("denoise", 0) > 0:
        dn = params["denoise"]
        fg_scale += f",hqdn3d={dn}:{dn*0.7}:{dn*2}:{dn*1.5}"

    if params.get("unsharp", False):
        if hd_enhance:
            fg_scale += ",unsharp=3:3:0.45:3:3:0.25"
        else:
            fg_scale += ",unsharp=5:5:0.6:5:5:0.0"

    # 呼吸式微运镜动态平移（微剪裁 + 动态正弦微平移，100% 破坏静态像素帧特征哈希比对，增加画面镜头动感）
    if params.get("micro_zoom", True):
        fg_scale += (f",crop=w='trunc(iw*0.97/2)*2':h='trunc(ih*0.97/2)*2':"
                     f"x='(in_w-out_w)/2+4*sin(t*1.2)':y='(in_h-out_h)/2+4*cos(t*0.9)',"
                     f"scale={target_w}:{target_h}:{scale_flags}")

    # 判断原视频比例是否与目标画面接近：若接近则不做模糊背景叠加（避免"双层"效果）
    src_ratio = (w / h) if h > 0 else 0
    tgt_ratio = (target_w / target_h) if target_h > 0 else 0
    ratio_match = abs(src_ratio - tgt_ratio) < 0.05  # 容差约 5%（9:16=0.5625）
    layout_mode = params.get("layout_mode", "auto")

    if ratio_match:
        # 比例相同：直接缩放+调色，不加模糊背景
        fc_parts.append(f"[0:v]{fg_scale}[base]")
        cur_label = "base"
        logger.info("原视频比例 %.3f ≈ 目标比例 %.3f，跳过模糊背景叠加",
                    src_ratio, tgt_ratio)
    elif layout_mode == "crop_fill":
        # 满屏强裁切模式：直接拉伸充填 9:16，无任何上下黑/模糊边框
        full_crop_scale = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase:{scale_flags},"
                           f"crop={target_w}:{target_h},setsar=1")
        if eq_parts:
            full_crop_scale += f",eq={':'.join(eq_parts)}"
        if vibrance and vibrance > 0:
            full_crop_scale += f",vibrance=intensity={vibrance:.2f}"
        if params.get("micro_zoom", True):
            full_crop_scale += (f",crop=w='trunc(iw*0.97/2)*2':h='trunc(ih*0.97/2)*2':"
                                f"x='(in_w-out_w)/2+4*sin(t*1.2)':y='(in_h-out_h)/2+4*cos(t*0.9)',"
                                f"scale={target_w}:{target_h}:{scale_flags}")
        fc_parts.append(f"[0:v]{full_crop_scale}[base]")
        cur_label = "base"
        logger.info("启用满屏智能裁切模式 (1080x1920)")
    else:
        # 爆款卡片版式 / 边框模糊背景模式：给画中画原视频增加 4px 纯白高质感边框，消除模糊背景融合带来的重影杂乱感
        border_w = params.get("border_width", 4)
        border_col = params.get("border_color", "white@0.95")
        if border_w > 0:
            fg_scale += f",pad=iw+{border_w*2}:ih+{border_w*2}:{border_w}:{border_w}:color={border_col}"

        bg_scale = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase:{scale_flags},"
                    f"setsar=1,"
                    f"boxblur={params.get('blur_radius', 24)}:{params.get('blur_power', 2)}")
        if params.get("darken_bg", 0) > 0:
            bg_scale += f",eq=brightness=-{params['darken_bg']}:saturation={1 - params['darken_bg']*0.3}"

        fc_parts.append("[0:v]split=2[bg_raw][fg_raw]")
        fc_parts.append(f"[bg_raw]{bg_scale}[bg_blur]")
        fc_parts.append(f"[fg_raw]{fg_scale}[fg_main]")
        fc_parts.append("[bg_blur][fg_main]overlay=(W-w)/2:(H-h)/2[base]")
        cur_label = "base"
        logger.info("原视频比例 %.3f ≠ 目标比例 %.3f，启用爆款卡片边框+模糊背景融合",
                    src_ratio, tgt_ratio)

    # === 滤镜预设（在风格增强合成之后、字幕抹除之前应用，作用于整帧画面）===
    if filter_preset:
        filter_frag, filter_out_label = filters_mod.build_filter_chain(
            filter_preset, cur_label, target_w, target_h, duration,
            intensity=filter_intensity)
        if filter_frag:
            fc_parts.append(filter_frag)
            cur_label = filter_out_label
            preset_meta = filters_mod.get_preset(filter_preset) or {}
            meta_info = preset_meta.get("meta", {})
            logger.info("应用滤镜预设: %s (%s) - %s",
                        filter_preset,
                        meta_info.get("name_cn", ""),
                        meta_info.get("desc", ""))

    # === 独立特效（--effect 指定，可叠加多个，在滤镜之后、字幕之前）===
    if effects:
        # input_offset = 1(src) + 已有 extra_inputs 数量
        eff_frag, eff_label, eff_inputs = filters_mod.build_effects_chain(
            effects, cur_label, target_w, target_h, duration,
            input_offset=1 + len(extra_inputs),
            intensity=effect_intensity)
        if eff_frag:
            fc_parts.append(eff_frag)
            cur_label = eff_label
            extra_inputs.extend(eff_inputs)
            logger.info("应用 %d 个特效: %s", len(effects), ", ".join(effects))

    # === 字幕抹除（在风格调整之后、二创叠加之前）===
    if subtitle_cfg and subtitle_cfg.get("erase_enabled"):
        frag, new_label = subtitle_mod.build_erase_filter(
            cur_label, target_w, target_h, subtitle_cfg)
        if frag:
            fc_parts.append(frag)
            cur_label = new_label
            logger.info("抹除原字幕区域: %s", subtitle_cfg.get("erase_mode", "blur"))

    # === 水印 ===
    if watermark:
        font = _find_font()
        if font:
            wm_file = _write_text_file(watermark, _tmp_dir, prefix="wm_")
            wm_files.append(wm_file)
            font_escaped = _esc_path(font)
            wm_path_escaped = _esc_path(wm_file)
            fontsize = params.get("watermark_size", 36)
            color = params.get("watermark_color", "white@0.85")
            shadow = params.get("watermark_shadow", "black@0.6")
            pos_x = params.get("watermark_x", "w-tw-{fontsize}").replace("{fontsize}", str(fontsize))
            pos_y = params.get("watermark_y", "h-th-{fontsize}").replace("{fontsize}", str(fontsize)) + "-20"
            fc_parts.append(
                f"[{cur_label}]drawtext=fontfile='{font_escaped}':"
                f"textfile='{wm_path_escaped}':fontsize={fontsize}:fontcolor={color}:"
                f"shadowcolor={shadow}:shadowx=2:shadowy=2:"
                f"x={pos_x}:y={pos_y}[wmed]")
            cur_label = "wmed"

    # === 二创叠加层 ===
    if pip_path:
        creative = dict(creative, _pip_path=pip_path)
    cur_label, creative_wm_files = _build_creative_layers(
        cur_label, fc_parts, extra_inputs, target_w, target_h,
        duration, creative, caption_text, _tmp_dir)
    wm_files.extend(creative_wm_files)

    # === 淡入淡出 ===
    fade_in = params.get("fade_in", 0)
    fade_out = params.get("fade_out", 0)
    if fade_in or fade_out:
        filters = []
        if fade_in:
            filters.append(f"fade=t=in:st=0:d={fade_in}")
        if fade_out and duration and duration > fade_out:
            out_st = max(0.0, duration - fade_out)
            filters.append(f"fade=t=out:st={out_st:.3f}:d={fade_out}")
        if filters:
            fc_parts.append(f"[{cur_label}]{','.join(filters)}[faded]")
            cur_label = "faded"

    # === ASS 新字幕渲染（在淡入淡出之前）===
    if subtitle_cfg and subtitle_cfg.get("render_enabled") and ass_path and os.path.exists(ass_path):
        ass_escaped = _esc_path(ass_path)
        font_dir = subtitle_mod.find_font_dir()
        sub_filter = f"subtitles='{ass_escaped}'"
        if font_dir:
            sub_filter += f":fontsdir='{_esc_path(font_dir)}'"
        new_label = "subtitled"
        fc_parts.append(f"[{cur_label}]{sub_filter}[{new_label}]")
        cur_label = new_label
        logger.info("渲染新字幕: %s", os.path.basename(ass_path))

    # === 最终缩放 ===
    fc_parts.append(f"[{cur_label}]scale={target_w}:{target_h},setsar=1[out]")

    fc = ";".join(fc_parts)
    return fc, "out", extra_inputs, wm_files


def enhance_video(src_path, dst_path, params=None, creative=None,
                  watermark=None, caption_text=None, pip_path=None,
                  subtitle_cfg=None, note_text=None,
                  tts_cfg=None, voice_text=None, bgm_cfg=None, wm_cfg=None,
                  filter_preset=None, effects=None, effect_intensity=1.0,
                  filter_intensity=1.0,
                  voice_rewrite=False, rewrite_style="casual",
                  rewrite_track=None,
                  subtitle_source="auto"):
    """对去重后的视频做风格增强 + 二创叠加 + 字幕抹除/重渲染。

    参数:
        src_path: 源视频（去重后的 video_dedup.mp4）
        dst_path: 输出路径
        params: 风格参数（默认 config.XHS_STYLE）
        creative: 二创参数（默认 config.CREATIVE）
        watermark: 水印文字
        caption_text: 字幕条文字（二创叠加层的简单字幕条）
        pip_path: 画中画素材视频路径
        subtitle_cfg: 字幕处理配置（抹除+渲染，见 config.SUBTITLE）
        note_text: 用于生成字幕的文本（whisper 关闭时用此文案分句）
        tts_cfg: AI 配音配置（见 config.TTS），启用后会在视频处理完成后替换/混合音频
        voice_text: 配音文本（优先用此参数；为空则用 note_text）
        bgm_cfg: 背景音乐配置（见 config.BGM），在配音之后混入 BGM
        wm_cfg: 动态水印去除配置（见 config.WM_REMOVE），启用后会先去水印再做后续处理
        filter_preset: 滤镜预设名（如 "teal_orange"），见 core/filters.py 的 FILTER_PRESETS。
            None=不应用滤镜。可用 --list-filters 查看全部预设。
        effects: 独立特效名列表（如 ["snow", "light_leak_warm"]），可叠加多个。
            见 core/filters.py 的 EFFECT_PRESETS。None/空=不应用特效。
            可用 --list-effects 查看全部 44 个特效。
        effect_intensity: 特效强度倍数 (0.0-2.0)，影响透明度/混合强度。
        filter_intensity: 滤镜预设强度倍数 (0.0-2.0)，作用于 LUT strength 和预设内置特效 opacity。
        voice_rewrite: 是否用 LLM 对 whisper 识别的口播内容做二创改写后再配音。
            True=改写（需配合 --whisper 和 --voice 使用，LLM 不可用时回退到原文本）。
            False=不改写（默认）。
            联动：当 subtitle_source="llm-fix" 时，二创改写前会先用 LLM 纠错 whisper 原文，
            基于纠错后的文本做改写，质量更高（多一次 LLM 调用）。
        rewrite_style: 二创改写风格（casual/professional/passion/gentle/knowledge/humor/concise/viral）。
            见 core/llm.py 的 REWRITE_STYLES。默认 "casual" 口语化。
            viral=参考小红书爆款脚本结构改写（需配合 rewrite_track 或自动识别赛道）。
        rewrite_track: 内容赛道 ID（viral 风格专用，如 home_goods/beauty_makeup/food_cooking/
            mother_baby/digital_review/knowledge_tutorial/lifestyle_vlog/outfit_fashion）。
            None=自动从原文识别赛道。非 viral 风格忽略此参数。
        subtitle_source: 字幕文本来源策略：
            "auto"=智能选择(whisper启用用whisper否则用note)
            "whisper"=强制用whisper原识别(可能有错别字)
            "llm-fix"=whisper识别+LLM纠错(减少错别字,需LLM)；同时作为 voice_rewrite 的预纠错输入
            "note"=用笔记文案分句(无错别字但可能和口播不完全一致)
            注意：voice_rewrite=True 时二创改写后的文本优先级最高（字幕和配音同源）
    返回:
        dst_path 或 None
    """
    if params is None:
        params = config.XHS_STYLE
    if creative is None:
        creative = dict(config.CREATIVE)
    if subtitle_cfg is None:
        subtitle_cfg = dict(config.SUBTITLE)

    if not os.path.exists(src_path):
        logger.error("源视频不存在: %s", src_path)
        return None

    ffmpeg = _get_ffmpeg()
    w, h, duration = _probe(src_path)
    logger.info("风格增强+二创+字幕: 源=%s  %sx%s  %.2fs",
                os.path.basename(src_path), w, h, duration)

    out_dir = os.path.dirname(dst_path)

    # === 动态水印去除（在所有其他处理之前，因为水印可能在任何位置） ===
    _combined_sample = None  # 联合采样结果（水印+字幕一次 OCR），供字幕阶段复用
    logger.info("水印去除条件检查: wm_cfg=%s, enabled=%s", wm_cfg is not None, wm_cfg.get("enabled") if wm_cfg else None)
    if wm_cfg and wm_cfg.get("enabled"):
        try:
            print("=== WM DEBUG: before import watermark_remove ===", flush=True)
            from . import watermark_remove as wm_mod
            print("=== WM DEBUG: after import, _HAS_CV2=", wm_mod._HAS_CV2, "===", flush=True)
            if not wm_mod._HAS_CV2:
                logger.warning("opencv-python 未安装，跳过动态水印去除（pip install opencv-python）")
            else:
                import time as _t
                t0 = _t.time()
                wm_out = os.path.join(out_dir, "_wm_removed.mp4")
                use_ocr = wm_cfg.get("use_ocr", False)
                print(f"=== WM DEBUG: use_ocr={use_ocr}, _HAS_PADDLE={wm_mod._HAS_PADDLE}, wm_out={wm_out} ===", flush=True)

                # 判断是否需要联合采样（水印走 OCR 且字幕走 ocr-inpaint）
                # 联合采样一次 OCR 同时提取水印和字幕文字，避免两个流程各自采样
                _need_subtitle_ocr = (subtitle_cfg.get("erase_enabled")
                                      and subtitle_cfg.get("erase_mode") == "ocr-inpaint")
                if use_ocr and _need_subtitle_ocr and wm_mod._HAS_PADDLE:
                    _sub_area = subtitle_cfg.get("erase_area", {"x": 0.0, "y": 0.78, "w": 1.0, "h": 0.18})
                    logger.info("=" * 40)
                    logger.info("联合 OCR 采样（水印+字幕一次扫描，省去重复采样）...")
                    _combined_sample = wm_mod.sample_frames_ocr_combined(
                        src_path,
                        ocr_samples=wm_cfg.get("ocr_samples", 25),
                        wm_text=wm_cfg.get("wm_text"),
                        replace_text=wm_cfg.get("replace_text"),
                        subtitle_area=_sub_area)

                if use_ocr:
                    # === OCR 模式 ===
                    print("=== WM DEBUG: use_ocr=True, _HAS_PADDLE=", wm_mod._HAS_PADDLE, "===", flush=True)
                    if not wm_mod._HAS_PADDLE:
                        logger.warning("paddleocr 未安装，OCR 模式不可用（pip install paddleocr），"
                                       "自动回退到颜色模式")
                        use_ocr = False
                    else:
                        replace_text = wm_cfg.get("replace_text")
                        replace_image = wm_cfg.get("replace_image")
                        print("=== WM DEBUG: calling remove_watermark_with_audio ===", flush=True)
                        print(f"=== WM DEBUG: src_path={src_path}, exists={os.path.exists(src_path)} ===", flush=True)
                        logger.info("=" * 40)
                        logger.info("动态水印去除（OCR 模式）: wm_text=%s samples=%s dilate=%s match=%s replace=%s",
                                    wm_cfg.get("wm_text") or "自动识别(@符号+常见水印词)",
                                    wm_cfg.get("ocr_samples", 10),
                                    wm_cfg.get("dilate", 3),
                                    wm_cfg.get("match_threshold", 0.5),
                                    replace_image or replace_text or "无")
                        r = wm_mod.remove_watermark_with_audio(
                            src_path, wm_out, tmp_dir=out_dir, use_ocr=True,
                            wm_text=wm_cfg.get("wm_text"),
                            ocr_samples=wm_cfg.get("ocr_samples", 10),
                            dilate=wm_cfg.get("dilate", 3),
                            match_threshold=wm_cfg.get("match_threshold", 0.5),
                            replace_text=replace_text,
                            replace_opacity=wm_cfg.get("replace_opacity", 1.0),
                            replace_style=wm_cfg.get("replace_style", "white_black"),
                            replace_font_scale=wm_cfg.get("replace_font_scale", 1.0),
                            replace_outline=wm_cfg.get("replace_outline"),
                            replace_bg=wm_cfg.get("replace_bg", False),
                            replace_image=replace_image,
                            replace_image_opacity=wm_cfg.get("replace_image_opacity", 1.0),
                            replace_image_scale=wm_cfg.get("replace_image_scale", 1.0),
                            precomputed=_combined_sample)

                if not use_ocr:
                    # === 颜色检测模式 ===
                    region = None
                    region_str = wm_cfg.get("region")
                    if region_str:
                        region = wm_mod._parse_region(region_str, w, h)
                    replace_image = wm_cfg.get("replace_image")
                    logger.info("=" * 40)
                    logger.info("动态水印去除（颜色模式）: threshold=%s min_area=%s dilate=%s track=%s region=%s replace=%s",
                                wm_cfg.get("threshold", 200),
                                wm_cfg.get("min_area", 200),
                                wm_cfg.get("dilate", 3),
                                wm_cfg.get("use_track", True),
                                region or "自动检测",
                                replace_image or "无")
                    r = wm_mod.remove_watermark_with_audio(
                        src_path, wm_out, tmp_dir=out_dir, use_ocr=False,
                        region=region,
                        threshold=wm_cfg.get("threshold", 200),
                        min_area=wm_cfg.get("min_area", 200),
                        dilate=wm_cfg.get("dilate", 3),
                        use_track=wm_cfg.get("use_track", True),
                        replace_image=replace_image,
                        replace_image_opacity=wm_cfg.get("replace_image_opacity", 1.0),
                        replace_image_scale=wm_cfg.get("replace_image_scale", 1.0))

                if r and os.path.exists(wm_out):
                    src_path = wm_out
                    w, h, duration = _probe(src_path)
                    logger.info("水印去除完成，耗时 %.1fs，后续处理基于去水印视频",
                                _t.time() - t0)
                else:
                    logger.warning("水印去除失败，后续处理仍用原视频")
        except BaseException as e:
            logger.error("水印去除异常: %s (%s)", e, type(e).__name__, exc_info=True)

    # 自动识别字幕区域（在抹除之前，覆盖默认 erase_area）
    _precomputed_sub_boxes = _combined_sample.get("subtitle_boxes") if _combined_sample else None
    if subtitle_cfg.get("erase_enabled") and subtitle_cfg.get("auto_detect", False):
        logger.info("自动识别字幕区域...")
        detected = subtitle_mod.detect_subtitle_area(
            src_path, duration, num_frames=subtitle_cfg.get("detect_frames", 5))
        if detected:
            subtitle_cfg = dict(subtitle_cfg, erase_area=detected)
            logger.info("使用自动识别的字幕区域: %s", detected)
            # 字幕区域变了，预采样的字幕框坐标可能不准，放弃复用
            _precomputed_sub_boxes = None
        else:
            logger.info("自动识别未成功，使用默认字幕区域: %s", subtitle_cfg.get("erase_area"))

    # === 字幕 inpaint 预处理（inpaint / ocr-inpaint 模式，在 ffmpeg 链之前逐帧修复） ===
    erase_mode = subtitle_cfg.get("erase_mode", "blur")
    if subtitle_cfg.get("erase_enabled") and erase_mode in ("inpaint", "ocr-inpaint"):
        import time as _t
        t0 = _t.time()
        inpaint_out = os.path.join(out_dir, "_subtitle_inpainted.mp4")
        logger.info("=" * 40)
        logger.info("字幕 inpaint 修复: 模式=%s 区域=%s",
                    erase_mode, subtitle_cfg.get("erase_area"))
        r = subtitle_mod.inpaint_subtitle(
            src_path, inpaint_out,
            area=subtitle_cfg.get("erase_area", {"x": 0.0, "y": 0.78, "w": 1.0, "h": 0.18}),
            mode=erase_mode,
            ocr_samples=subtitle_cfg.get("ocr_inpaint_samples", 12),
            inpaint_radius=subtitle_cfg.get("inpaint_radius", 4),
            dilate=subtitle_cfg.get("inpaint_dilate", 2),
            tmp_dir=out_dir,
            precomputed_boxes=_precomputed_sub_boxes)
        if r and os.path.exists(inpaint_out):
            src_path = inpaint_out
            w, h, duration = _probe(src_path)
            logger.info("字幕 inpaint 完成，耗时 %.1fs，后续处理基于修复后视频", _t.time() - t0)
        else:
            logger.warning("字幕 inpaint 失败，回退到 blur 模式")
            subtitle_cfg = dict(subtitle_cfg, erase_mode="blur")

    # 生成 ASS 字幕文件（如果启用渲染）
    ass_path = None
    whisper_segments = None  # whisper 识别的字幕（供配音用，即使字幕渲染关闭也会提取）
    rewritten_text = None    # LLM 二创改写后的文本（字幕和配音共用，保证内容一致）
    fixed_whisper_text = None  # llm-fix 纠错后的 whisper 全文（供 TTS 配音使用）

    # 当 whisper 启用时，先做语音识别（结果供字幕渲染和配音共用）
    # 这样即使用户 --no-subtitle 关闭字幕渲染，--voice 也能用 whisper 识别的文本配音
    if subtitle_cfg.get("whisper_enabled"):
        whisper_segments = subtitle_mod.transcribe_with_whisper(
            src_path,
            model_name=subtitle_cfg.get("whisper_model", "base"))

    # === LLM 字幕纠错（subtitle_source=llm-fix 时执行，与是否开启 voice_rewrite 解耦）===
    # 不管是否做二创改写、是否渲染字幕，只要 subtitle_source=llm-fix 就对 whisper 原文做纠错，
    # 纠错结果供 TTS 配音和字幕共用（避免 TTS 用未纠错的 whisper 原文导致错别字）
    if subtitle_source == "llm-fix" and whisper_segments:
        try:
            from . import llm as llm_mod
            if llm_mod.is_available():
                original_whisper_text = "。".join(
                    seg[2] for seg in whisper_segments if seg[2].strip())
                if original_whisper_text.strip():
                    logger.info("字幕/配音 LLM 纠错: whisper 原文%d字", len(original_whisper_text))
                    fixed = llm_mod.llm_fix_subtitle_text(original_whisper_text)
                    if fixed and fixed.strip():
                        logger.info("LLM 纠错成功: %d字 → %d字",
                                    len(original_whisper_text), len(fixed))
                        fixed_whisper_text = fixed
                    else:
                        logger.warning("LLM 纠错未返回有效文本，回退到 whisper 原文")
            else:
                logger.warning("LLM 不可用，字幕/配音纠错回退到 whisper 原文本")
        except Exception as e:
            logger.warning("LLM 字幕纠错异常: %s，回退到原文本", e)

    # === LLM 口播二创改写（提前到字幕渲染之前，让字幕和配音共用改写后文本）===
    # 这样字幕显示的内容和配音说的内容完全一致，节奏也能对上
    # 若 subtitle_source=llm-fix，先对 whisper 原文做 LLM 纠错，再基于纠错后文本做二创改写
    # （纠错后的输入更准确，二创质量更高；不加 llm-fix 则用 whisper 原文）
    if voice_rewrite and whisper_segments:
        try:
            from . import llm as llm_mod
            if llm_mod.is_available():
                original_text = "。".join(seg[2] for seg in whisper_segments if seg[2].strip())
                if original_text.strip():
                    # llm-fix 预纠错：复用前面已纠错的 fixed_whisper_text（避免重复调用 LLM）
                    if subtitle_source == "llm-fix" and fixed_whisper_text:
                        logger.info("二创输入复用已纠错文本: whisper 原文%d字 → LLM 纠错后%d字",
                                    len(original_text), len(fixed_whisper_text))
                        original_text = fixed_whisper_text
                    logger.info("=" * 40)
                    logger.info("LLM 口播二创改写: 风格=%s 输入文本%d字 目标时长=%.1fs",
                                rewrite_style, len(original_text), duration)
                    rewritten = llm_mod.llm_rewrite_speech(
                        original_text, style=rewrite_style, target_duration=duration,
                        track=rewrite_track)
                    if rewritten and rewritten.strip():
                        logger.info("二创改写成功，改写后%d字", len(rewritten))
                        logger.info("改写前60字: %s", original_text[:60])
                        logger.info("改写后60字: %s", rewritten[:60])
                        rewritten_text = rewritten
                        # 保存改写前后文本到文件，方便对比
                        try:
                            rewrite_path = os.path.join(out_dir, "voice_rewrite_compare.txt")
                            with open(rewrite_path, "w", encoding="utf-8") as f:
                                f.write("=" * 60 + "\n")
                                f.write("【改写前】（whisper 原文 + LLM 纠错）\n")
                                f.write("=" * 60 + "\n\n")
                                f.write(original_text + "\n\n")
                                f.write("=" * 60 + "\n")
                                f.write(f"【改写后】（LLM 二创风格: {rewrite_style}）\n")
                                f.write("=" * 60 + "\n\n")
                                f.write(rewritten + "\n\n")
                                f.write("=" * 60 + "\n")
                                f.write("【统计】\n")
                                f.write("=" * 60 + "\n")
                                f.write(f"改写前: {len(original_text)} 字\n")
                                f.write(f"改写后: {len(rewritten)} 字\n")
                                f.write(f"风格: {rewrite_style}\n")
                            logger.info("改写前后文本已保存: %s", rewrite_path)
                        except Exception as e_save:
                            logger.debug("保存改写对比文件失败: %s", e_save)
                    else:
                        logger.warning("LLM 二创改写失败，字幕和配音回退到原 whisper 文本")
                else:
                    logger.warning("whisper 识别文本为空，跳过口播二创")
            else:
                logger.warning("LLM 不可用，跳过口播二创（需配置 API Key）")
        except Exception as e:
            logger.warning("LLM 口播二创异常: %s，回退到原文本", e)

    if subtitle_cfg.get("render_enabled"):
        # 提取字幕文本，优先级：
        #   1. voice_rewrite=True 且改写成功 → 用改写后文本（字幕和配音同源）
        #   2. subtitle_source 决定：
        #      - "auto": whisper 启用→whisper，否则→note
        #      - "whisper": 强制用 whisper 原识别（可能有错别字）
        #      - "llm-fix": whisper 识别 + LLM 纠错（减少错别字，需 LLM）
        #      - "note": 用笔记文案分句（无错别字但可能和口播不完全一致）
        if rewritten_text:
            # 二创改写后的文本：重新按视频时长均匀分配时间戳，节奏稳定
            segments = subtitle_mod.split_text_to_segments(rewritten_text, duration)
            logger.info("字幕使用 LLM 改写后文本（%d 句，均匀分配时间戳）", len(segments))
        else:
            source_mode = subtitle_source or "auto"
            # auto 智能选择
            if source_mode == "auto":
                source_mode = "whisper" if whisper_segments else "note"

            if source_mode == "llm-fix" and whisper_segments:
                # whisper 识别 + LLM 纠错（复用前面已纠错的 fixed_whisper_text）
                fixed_text = fixed_whisper_text
                if fixed_text and fixed_text.strip():
                    # 纠错后文本按视频时长均匀分配时间戳
                    segments = subtitle_mod.split_text_to_segments(fixed_text, duration)
                    logger.info("字幕使用 LLM 纠错后文本（%d 句）", len(segments))
                else:
                    segments = whisper_segments
                    logger.info("字幕使用 whisper 原文本（纠错未生效）")
            elif source_mode == "note":
                # 用笔记文案分句（无错别字）
                segments = subtitle_mod.split_text_to_segments(
                    note_text or caption_text or "", duration)
                logger.info("字幕使用笔记文案分句（%d 句）", len(segments))
            else:
                # whisper 原识别（默认/whisper模式/auto回退）
                # 注意：若启用 AI 配音，字幕时间戳要和配音节奏一致。
                # whisper 原始时间戳是原说话人节奏，配音是 TTS 重新生成+atempo 变速，
                # 节奏完全不同，会导致字幕和语音对不上。
                # 解决：启用配音时，字幕也用 split_text_to_segments 按视频时长均匀分配
                if whisper_segments and tts_cfg and tts_cfg.get("enabled"):
                    whisper_text = "。".join(
                        seg[2] for seg in whisper_segments if seg[2].strip())
                    segments = subtitle_mod.split_text_to_segments(whisper_text, duration)
                    logger.info("字幕使用 whisper 文本（%d 句，按视频时长均匀分配，与配音同节奏）",
                                len(segments))
                else:
                    # 不启用配音：保留 whisper 原始时间戳（更精确）
                    segments = whisper_segments or subtitle_mod.split_text_to_segments(
                        note_text or caption_text or "", duration)
        if segments:
            style_name = subtitle_cfg.get("style", "default")
            style_cfg = config.SUBTITLE_STYLES.get(style_name, config.SUBTITLE_STYLES["default"])
            target_w = params.get("target_width", 1080)
            target_h = params.get("target_height", 1920)
            ass_path = os.path.join(out_dir, "subtitle.ass")
            # 字幕位置策略：auto=跟随原字幕区域，bottom/top/center=固定位置
            position = subtitle_cfg.get("position", "auto")
            if position == "auto" and subtitle_cfg.get("auto_detect"):
                # 用检测到的原字幕区域，让新字幕渲染到原位置
                position_area = subtitle_cfg.get("erase_area")
            elif position in ("bottom", "top", "center"):
                # 用户指定固定位置，不跟随原字幕
                position_area = None
                if position == "bottom":
                    style_cfg = dict(style_cfg, alignment=2)
                elif position == "top":
                    style_cfg = dict(style_cfg, alignment=8)
                else:  # center
                    style_cfg = dict(style_cfg, alignment=5, margin_v=0)
            else:
                position_area = None
            subtitle_mod.build_ass_subtitle(segments, style_cfg, ass_path, target_w, target_h,
                                            position_area=position_area)
        else:
            logger.warning("未获取到字幕文本，跳过新字幕渲染")
            subtitle_cfg = dict(subtitle_cfg, render_enabled=False)

    if caption_text is None and note_text:
        caption_text = note_text.split("。")[0].split("！")[0].split("!")[0]
        if len(caption_text) > 18:
            caption_text = caption_text[:18]

    fc, out_label, extra_inputs, wm_files = _build_filter_complex(
        w, h, duration, params, creative, watermark, caption_text, out_dir, pip_path,
        subtitle_cfg, ass_path, filter_preset, effects, effect_intensity,
        filter_intensity=filter_intensity)

    # 构建 ffmpeg 命令
    cmd = [ffmpeg, "-y", "-i", src_path]
    for inp in extra_inputs:
        cmd += ["-i", inp]
    cmd += ["-filter_complex", fc, "-map", f"[{out_label}]", "-map", "0:a?"]
    cmd += [
        "-c:v", "libx264",
        "-preset", params.get("preset", "medium"),
        "-crf", str(params.get("crf", 16)),
        "-pix_fmt", "yuv420p",
        "-color_range", "tv",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-x264-params", "me=hex:subme=7:no-fast-pskip=0:aq-mode=2:aq-strength=1.0",
        "-c:a", "aac",
        "-b:a", "256k",
        "-map_metadata", "-1",
        "-movflags", "+faststart",
        dst_path,
    ]

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    logger.info("执行风格增强+二创...")
    logger.debug("滤镜链: %s", fc)
    logger.debug("额外输入: %s", extra_inputs)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            encoding="utf-8", errors="ignore")
        if result.returncode != 0:
            # 打印完整 stderr + 滤镜链 + 命令，方便定位滤镜链构建错误
            # rc=4294967274 (即 -22/EINVAL) 通常是滤镜链参数错误
            logger.error("ffmpeg 失败 (rc=%d)", result.returncode)
            logger.error("滤镜链: %s", fc)
            logger.error("命令: %s", " ".join(f'"{a}"' if " " in str(a) else str(a) for a in cmd))
            # stderr 可能让日志过长，先打印最后 5000 字符，再视情况补全
            stderr_text = result.stderr or ""
            logger.error("stderr (最后 5000 字符):\n%s", stderr_text[-5000:])
            return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg 超时")
        return None
    except Exception as e:
        logger.error("ffmpeg 异常: %s", e)
        return None
    finally:
        for f in wm_files:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    if os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
        nw, nh, nd = _probe(dst_path)
        logger.info("视频处理完成: %s  %sx%s  %.2fs", dst_path, nw, nh, nd)

        # === AI 配音（视频处理完成后，替换/混合音频）===
        if tts_cfg and tts_cfg.get("enabled"):
            try:
                from . import tts as tts_mod
                provider = tts_cfg.get("provider", "edge")
                # 服务商可用性预检
                if provider == "volcengine":
                    if not tts_mod._has_volcengine():
                        if not tts_mod._HAS_REQUESTS:
                            logger.warning("requests 未安装，跳过火山引擎 TTS（pip install requests）")
                        else:
                            logger.warning("火山引擎 TTS 未配置环境变量，跳过 AI 配音"
                                           "（需设置 VOLCENGINE_APP_ID 和 VOLCENGINE_ACCESS_KEY）")
                        return dst_path
                else:
                    if not tts_mod._HAS_EDGE_TTS:
                        logger.warning("edge-tts 未安装，跳过 AI 配音（pip install edge-tts）")
                        return dst_path

                # 配音文本来源优先级：
                #   1. --voice-text（用户显式指定，最高优先级）
                #   2. LLM 改写后的文本（voice_rewrite=True 且改写成功，与字幕共用同一份文本）
                #   3. LLM 纠错后的 whisper 文本（subtitle_source=llm-fix 且纠错成功，避免错别字）
                #   4. whisper 识别的字幕（--whisper 启用且识别成功，文本时长接近视频）
                #   5. 笔记文案（note_text，兜底）
                # 注意：LLM 改写已在字幕渲染前完成（rewritten_text），这里直接复用，
                #       保证字幕显示内容和配音说的内容完全一致
                if voice_text and voice_text.strip():
                    tts_text = voice_text
                    tts_source = "voice-text（用户指定）"
                elif rewritten_text:
                    tts_text = rewritten_text
                    tts_source = f"whisper → LLM二创[{rewrite_style}]（与字幕同源）"
                elif fixed_whisper_text:
                    tts_text = fixed_whisper_text
                    tts_source = "whisper → LLM纠错（与字幕同源）"
                elif whisper_segments:
                    tts_text = "。".join(seg[2] for seg in whisper_segments if seg[2].strip())
                    tts_source = f"whisper 字幕（{len(whisper_segments)} 句）"
                else:
                    tts_text = note_text or ""
                    tts_source = "笔记文案"

                if not tts_text.strip():
                    logger.warning("配音文本为空，跳过 AI 配音")
                    return dst_path

                # 配音输出到临时文件，成功后替换原文件
                voiced_path = os.path.join(os.path.dirname(dst_path),
                                           "_voiced_tmp.mp4")
                logger.info("=" * 40)
                logger.info("应用 AI 配音: provider=%s voice=%s keep_bg=%s 文本来源=%s",
                            tts_cfg.get("provider", "edge"),
                            tts_cfg.get("voice", "xiaoxiao"),
                            tts_cfg.get("keep_bg_audio", False),
                            tts_source)
                logger.info("配音文本(前60字): %s", tts_text[:60])

                # condenser 回调：配音超长 ≥ 25% 时精简文本重 TTS（方案3分级策略）
                # 比纯 atempo 加速更自然，人声语速不变
                condenser = None
                try:
                    from . import llm as _llm_for_condense
                    if _llm_for_condense.is_available():
                        def _condenser(text, target_chars):
                            return _llm_for_condense.llm_condense_speech(text, target_chars)
                        condenser = _condenser
                except Exception:
                    pass

                r = tts_mod.apply_voiceover(
                    video_path=dst_path,
                    text=tts_text,
                    out_path=voiced_path,
                    voice=tts_cfg.get("voice", "xiaoxiao"),
                    rate=tts_cfg.get("rate", "+0%"),
                    pitch=tts_cfg.get("pitch", "+0Hz"),
                    volume=tts_cfg.get("volume", "+0%"),
                    keep_bg_audio=tts_cfg.get("keep_bg_audio", False),
                    bg_volume=tts_cfg.get("bg_volume", 0.15),
                    fit_duration=tts_cfg.get("fit_duration", True),
                    tmp_dir=os.path.dirname(dst_path),
                    provider=tts_cfg.get("provider", "edge"),
                    condenser=condenser,
                )

                if r and os.path.exists(r) and os.path.getsize(r) > 0:
                    # 用配音版替换原成品
                    try:
                        os.remove(dst_path)
                    except OSError:
                        pass
                    os.rename(r, dst_path)
                    nw, nh, nd = _probe(dst_path)
                    logger.info("AI 配音完成: %s  %sx%s  %.2fs", dst_path, nw, nh, nd)
                else:
                    logger.error("AI 配音失败，保留原音频版: %s", dst_path)
                    if os.path.exists(voiced_path):
                        try:
                            os.remove(voiced_path)
                        except OSError:
                            pass
            except Exception as e:
                logger.error("AI 配音异常: %s", e)

        # === 背景音乐 BGM（在配音之后混入，BGM 为背景，原音轨/配音为主）===
        if bgm_cfg and bgm_cfg.get("enabled"):
            try:
                from . import bgm as bgm_mod

                # 扫描可用 BGM
                available = bgm_mod.scan_bgm_dir(bgm_cfg.get("dir"))

                if not available:
                    logger.warning("BGM 目录无可用音乐文件，跳过 BGM。"
                                   "请把音乐放到 assets/bgm/（命名：风格_曲名.mp3），"
                                   "可用 --list-bgm 查看推荐清单")
                else:
                    # 选 BGM
                    selection = bgm_cfg.get("selection", "auto")
                    bgm_path = None
                    if selection == "auto":
                        # 自动选：用 note_text 作为标题/描述的兜底
                        style, bgm_path, matched = bgm_mod.auto_bgm(
                            [], note_text or caption_text or "", "", available)
                        logger.info("自动选BGM: 风格=%s 文件=%s",
                                    style,
                                    os.path.basename(bgm_path) if bgm_path else "无")
                        if matched:
                            logger.info("  命中关键词: %s", ",".join(matched[:5]))
                    else:
                        # 按风格/曲名选
                        bgm_path, style = bgm_mod.pick_bgm(selection, available)
                        if bgm_path:
                            logger.info("指定BGM: 风格=%s 文件=%s",
                                        style, os.path.basename(bgm_path))

                    if bgm_path:
                        voiced_path = os.path.join(os.path.dirname(dst_path),
                                                   "_bgm_tmp.mp4")
                        r = bgm_mod.mix_bgm_into_video(
                            video_path=dst_path,
                            bgm_path=bgm_path,
                            out_path=voiced_path,
                            bgm_volume=bgm_cfg.get("volume", 0.15),
                            fade=bgm_cfg.get("fade", True),
                            tmp_dir=os.path.dirname(dst_path),
                        )
                        if r and os.path.exists(r) and os.path.getsize(r) > 0:
                            try:
                                os.remove(dst_path)
                            except OSError:
                                pass
                            os.rename(r, dst_path)
                            nw, nh, nd = _probe(dst_path)
                            logger.info("BGM 混合完成: %s  %sx%s  %.2fs",
                                        dst_path, nw, nh, nd)
                        else:
                            logger.error("BGM 混合失败，保留无BGM版: %s", dst_path)
                            if os.path.exists(voiced_path):
                                try:
                                    os.remove(voiced_path)
                                except OSError:
                                    pass
                    else:
                        logger.warning("未找到匹配的 BGM 文件，跳过。"
                                       "可用 --list-bgm 查看可用素材")
            except Exception as e:
                logger.error("BGM 混合异常: %s", e)

        return dst_path
    logger.error("输出文件无效: %s", dst_path)
    return None
