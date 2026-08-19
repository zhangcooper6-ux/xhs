# -*- coding: utf-8 -*-
"""动态水印去除模块（基于 OpenCV + 可选 PaddleOCR）。

针对视频中"移动的水印"（如 "@某某原创" 飘动文字），提供两种模式：

模式一：OCR 文字检测（推荐，最智能）
  1. 在采样帧上用 PaddleOCR 检测所有文字
  2. 按内容过滤（包含 @ 符号，或匹配 --wm-text 指定的关键词）
  3. 裁剪水印模板 → 全帧用模板匹配精确定位
  4. cv2.inpaint 修复
  优点：能区分水印和字幕，不会误删字幕
  缺点：需安装 paddleocr（包较大）

模式二：颜色检测 + 追踪（默认，无需额外依赖）
  1. 检测水印位置（颜色阈值 + 形态学，或用户指定区域）
  2. 可选用 CSRT 追踪器跟踪水印移动轨迹
  3. cv2.inpaint 修复
  优点：无需额外依赖，速度快
  缺点：可能误删亮色字幕，追踪会丢失

CLI：
  --remove-wm              启用动态水印去除
  --wm-text STR            OCR 模式：指定水印文字内容（如 "@原创"），只删匹配的文字
  --wm-ocr                 OCR 模式：强制启用 OCR（不指定 --wm-text 时自动识别 @ 符号水印）
  --wm-region x,y,w,h      颜色模式：指定水印起始矩形
  --wm-threshold INT       颜色模式：亮度检测阈值（0-255，默认 200）
  --wm-min-area INT        颜色模式：水印最小像素面积（默认 200）
  --wm-no-track            颜色模式：关闭追踪器，每帧独立检测
  --wm-dilate INT          膨胀半径（默认 3，扩大修复区域覆盖描边）
  --wm-ocr-samples INT     OCR 模式：采样帧数（默认 10，越多越准但越慢）
"""
import os
import logging
import subprocess

logger = logging.getLogger("wm_remove")

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

# 重要：paddlepaddle 和 torch 同时加载会 DLL 冲突（paddle 先加载会导致 torch 的 shm.dll 找不到依赖）
# 解决方案：在 import paddleocr 之前先 import torch，让 torch 先加载其 DLL
try:
    import torch  # noqa: F401  预加载 torch 的 DLL
except ImportError:
    pass

# PaddleOCR 是否可用
_ocr_instance = None
_HAS_PADDLE = False
try:
    from paddleocr import PaddleOCR
    _HAS_PADDLE = True
except ImportError:
    pass


# 替换文字样式预设（颜色/描边/阴影组合，预渲染阶段一次性生效，零逐帧损耗）
# 键 = --wm-replace-style 参数值
REPLACE_TEXT_STYLES = {
    "white_black":    {"text_color": (255, 255, 255), "outline_color": (0, 0, 0),       "outline_width": 3, "shadow": False},
    "white_none":     {"text_color": (255, 255, 255), "outline_color": None,             "outline_width": 0, "shadow": False},
    "black_white":    {"text_color": (0, 0, 0),       "outline_color": (255, 255, 255),  "outline_width": 3, "shadow": False},
    "yellow_black":   {"text_color": (255, 220, 0),   "outline_color": (0, 0, 0),       "outline_width": 3, "shadow": False},
    "red_white":      {"text_color": (255, 60, 60),   "outline_color": (255, 255, 255),  "outline_width": 2, "shadow": False},
    "white_shadow":   {"text_color": (255, 255, 255), "outline_color": None,             "outline_width": 0, "shadow": True},
    "pink_black":     {"text_color": (255, 105, 180), "outline_color": (0, 0, 0),       "outline_width": 2, "shadow": False},
    "cyan_black":     {"text_color": (0, 220, 220),   "outline_color": (0, 0, 0),       "outline_width": 2, "shadow": False},
}


def _get_ocr():
    """获取 PaddleOCR 实例（懒加载，首次调用时初始化模型）。

    兼容不同版本 PaddleOCR：
      - 老版（<2.7）：支持 show_log 参数
      - 新版（>=2.7）：移除了 show_log，需用 logging_level 替代
      - 3.x：参数名变为 use_textline_orientation 等
    """
    global _ocr_instance
    if _ocr_instance is None and _HAS_PADDLE:
        import inspect
        # 探测 PaddleOCR 支持的参数
        try:
            sig = inspect.signature(PaddleOCR.__init__)
            param_names = set(sig.parameters.keys())
        except Exception:
            param_names = set()

        kwargs = {"lang": "ch"}
        # 角度分类参数（3.x 改名）
        if "use_angle_cls" in param_names:
            kwargs["use_angle_cls"] = False
        elif "use_textline_orientation" in param_names:
            kwargs["use_textline_orientation"] = False
        # 日志参数（3.x 用 logging_level，2.x 用 show_log）
        if "logging_level" in param_names:
            kwargs["logging_level"] = "ERROR"
        elif "show_log" in param_names:
            kwargs["show_log"] = False

        try:
            _ocr_instance = PaddleOCR(**kwargs)
            logger.info("PaddleOCR 模型已加载 (参数: %s)", kwargs)
        except Exception as e:
            logger.warning("PaddleOCR 初始化失败: %s", e)
            # 兜底：只用最基础的 lang 参数
            try:
                _ocr_instance = PaddleOCR(lang="ch")
                logger.info("PaddleOCR 模型已加载 (基础参数)")
            except Exception as e2:
                logger.warning("PaddleOCR 兜底初始化也失败: %s", e2)
                return None
    return _ocr_instance


def _run_ocr_on_frame(frame, ocr=None, max_size=960):
    """对单帧运行 OCR，返回 [(box, text, score), ...]。

    box: 4 个点的坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]

    参数:
        max_size: 输入帧的最大边长，超过则等比缩放。
                  960 与 PaddleOCR 内部 det_limit_side_len 一致，既能识别小水印
                  又避免 4K 全帧 OCR 卡死。480 会丢失小水印（如 @xxx）。
    """
    ocr = ocr or _get_ocr()
    if ocr is None:
        return []
    # 输入尺寸保护：PaddleOCR 在超大帧（如 4K 2160x3840）上可能假死
    # 强制缩到 max_size 宽，坐标由调用方还原
    import cv2 as _cv2
    fh, fw = frame.shape[:2]
    scale = 1.0
    if max(fw, fh) > max_size:
        scale = float(max_size) / float(max(fw, fh))
        frame = _cv2.resize(frame, (int(fw * scale), int(fh * scale)),
                            interpolation=_cv2.INTER_AREA)
    try:
        result = ocr.ocr(frame, cls=False)
        if not result or not result[0]:
            return []
        items = []
        for line in result[0]:
            box, (text, score) = line[0], line[1]
            if text and score > 0.3:
                # 把 box 坐标还原到原帧尺寸
                if scale != 1.0:
                    box = [[p[0] / scale, p[1] / scale] for p in box]
                items.append((box, text, score))
        return items
    except Exception as e:
        logger.debug("OCR 异常: %s", e)
        return []


def _box_to_rect(box):
    """PaddleOCR 的 4 点 box 转为 (x, y, w, h) 矩形。"""
    import numpy as _np
    pts = _np.array(box, dtype=_np.int32)
    x = int(pts[:, 0].min())
    y = int(pts[:, 1].min())
    x2 = int(pts[:, 0].max())
    y2 = int(pts[:, 1].max())
    return (x, y, x2 - x, y2 - y)


def _is_watermark_text(text, wm_text=None, exclude_text=None):
    """判断文字内容是否是水印。

    判断规则（满足任一即视为水印）：
      1. 文字包含 @ 符号（如 "@某某原创"）
      2. 文字匹配用户指定的 --wm-text 关键词
      3. 文字包含常见水印关键词（原创/搬运/转载/水印/账号/关注）

    参数:
        exclude_text: 排除文字（如 --wm-replace 的替换文字），OCR 识别到该文字时跳过，
                      避免把自己的替换文字也当水印（两者都含 @ 时会互相干扰）
    """
    if not text:
        return False
    t = text.strip()
    # 排除替换文字（避免 --wm-replace "@xxx" 与水印识别互相干扰）
    if exclude_text:
        ex = exclude_text.strip()
        if ex and (ex in t or t in ex):
            return False
    # @ 符号是水印的强特征
    if "@" in t:
        return True
    # 用户指定关键词
    if wm_text:
        for kw in wm_text.split(","):
            kw = kw.strip()
            if kw and kw in t:
                return True
    # 常见水印关键词
    for kw in ("原创", "搬运", "转载", "水印", "账号", "关注", "小红书", "抖音", "快手"):
        if kw in t:
            return True
    return False


def _crop_template(frame, rect, padding=4):
    """从帧中裁剪水印模板（带 padding 扩展边界）。"""
    x, y, w, h = rect
    fh, fw = frame.shape[:2]
    x = max(0, x - padding)
    y = max(0, y - padding)
    x2 = min(fw, x + w + padding * 2)
    y2 = min(fh, y + h + padding * 2)
    return frame[y:y2, x:x2].copy(), (x, y, x2 - x, y2 - y)


def _match_template_in_frame(frame, template, search_region=None, threshold=0.5):
    """在帧中用模板匹配查找水印位置（支持多尺度，应对水印缩放）。

    search_region: (x, y, w, h) 限制搜索范围（大幅加速），None 则全图搜索
    返回: (x, y, w, h) 或 None

    性能优化：搜索区域 > 960 宽时，先缩放到 480 宽做粗匹配（快 20 倍），
    再在原图以粗匹配位置为中心的小区域内做 1.0 尺度精确匹配。
    4K 全图 matchTemplate 单帧 6 秒 → 降采样后 0.3 秒。
    """
    if template is None or template.size == 0:
        return None
    th, tw = template.shape[:2]
    fh, fw = frame.shape[:2]

    # 限制搜索范围（水印不会跑出某个大区域）
    if search_region:
        sx, sy, sw, sh = search_region
        sx = max(0, sx)
        sy = max(0, sy)
        sx2 = min(fw, sx + sw)
        sy2 = min(fh, sy + sh)
        search_area = frame[sy:sy2, sx:sx2]
    else:
        sx = sy = 0
        search_area = frame

    # 搜索区域必须比模板大
    if search_area.shape[0] < th or search_area.shape[1] < tw:
        return None

    sa_h, sa_w = search_area.shape[:2]

    # === 降采样粗匹配（大搜索区域时）===
    # 4K 全图 matchTemplate 单帧 6 秒，降采样到 480 宽只需 0.3 秒
    COARSE_MAX_W = 960  # 搜索区域宽度超过此值时启用降采样
    if sa_w > COARSE_MAX_W:
        coarse_scale = COARSE_MAX_W / sa_w
        coarse_w = int(sa_w * coarse_scale)
        coarse_h = int(sa_h * coarse_scale)
        coarse_area = cv2.resize(search_area, (coarse_w, coarse_h), interpolation=cv2.INTER_AREA)
        coarse_tmpl = cv2.resize(template, (max(2, int(tw * coarse_scale)), max(2, int(th * coarse_scale))),
                                 interpolation=cv2.INTER_AREA)
        try:
            res = cv2.matchTemplate(coarse_area, coarse_tmpl, cv2.TM_CCOEFF_NORMED)
            _, coarse_val, _, coarse_loc = cv2.minMaxLoc(res)
        except Exception:
            return None

        if coarse_val < threshold:
            return None

        # 粗匹配位置还原到原图坐标，在原图以该位置为中心做小范围精确匹配
        coarse_x = int(coarse_loc[0] / coarse_scale)
        coarse_y = int(coarse_loc[1] / coarse_scale)
        # 精确匹配区域：粗匹配位置 ± 模板大小（留余量给亚像素偏差）
        refine_pad = max(tw, th)
        rfx1 = max(0, coarse_x - refine_pad)
        rfy1 = max(0, coarse_y - refine_pad)
        rfx2 = min(sa_w, coarse_x + int(tw * coarse_scale) + refine_pad)
        rfy2 = min(sa_h, coarse_y + int(th * coarse_scale) + refine_pad)
        refine_area = search_area[rfy1:rfy2, rfx1:rfx2]
        if refine_area.shape[0] < th or refine_area.shape[1] < tw:
            # 精确匹配区域太小，直接用粗匹配结果
            matched_w = int(tw * coarse_scale)
            matched_h = int(th * coarse_scale)
            return (coarse_x + sx, coarse_y + sy, matched_w, matched_h)
        try:
            res = cv2.matchTemplate(refine_area, template, cv2.TM_CCOEFF_NORMED)
            _, refine_val, _, refine_loc = cv2.minMaxLoc(res)
        except Exception:
            # 精确匹配失败，用粗匹配结果
            matched_w = int(tw * coarse_scale)
            matched_h = int(th * coarse_scale)
            return (coarse_x + sx, coarse_y + sy, matched_w, matched_h)
        if refine_val >= threshold:
            return (refine_loc[0] + rfx1 + sx, refine_loc[1] + rfy1 + sy, tw, th)
        # 精确匹配置信度低，用粗匹配结果
        matched_w = int(tw * coarse_scale)
        matched_h = int(th * coarse_scale)
        return (coarse_x + sx, coarse_y + sy, matched_w, matched_h)

    # === 小搜索区域：直接 matchTemplate（快）===
    HIGH_CONFIDENCE = 0.8
    try:
        res = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= HIGH_CONFIDENCE:
            return (max_loc[0] + sx, max_loc[1] + sy, tw, th)
        if max_val < threshold:
            best_val = 0
            best_loc = None
            best_scale = 1.0
        else:
            best_val = max_val
            best_loc = max_loc
            best_scale = 1.0
    except Exception as e:
        logger.debug("模板匹配异常(scale=1.0): %s", e)
        best_val = 0
        best_loc = None
        best_scale = 1.0

    # 多尺度匹配：水印移动时可能有缩放变化
    for scale in (0.8, 0.9, 1.1, 1.2, 0.7, 1.3):
        new_w = max(2, int(tw * scale))
        new_h = max(2, int(th * scale))
        scaled_tmpl = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
        if search_area.shape[0] < scaled_tmpl.shape[0] or search_area.shape[1] < scaled_tmpl.shape[1]:
            continue
        try:
            res = cv2.matchTemplate(search_area, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale
        except Exception as e:
            logger.debug("模板匹配异常(scale=%.1f): %s", scale, e)

    if best_val >= threshold and best_loc is not None:
        matched_w = max(2, int(tw * best_scale))
        matched_h = max(2, int(th * best_scale))
        return (best_loc[0] + sx, best_loc[1] + sy, matched_w, matched_h)
    return None


def _verify_watermark_at_position(frame, template, x, y, w, h,
                                   threshold=0.5, pad=30):
    """在指定位置周围做局部模板验证，确认水印是否真的在该位置。

    用于验证 interp(插值)/predict(预测)来源的位置是否准确。
    只在小区域做 matchTemplate，速度极快（单帧 <1ms，不影响主流程性能）。

    解决问题：当模板匹配失败时，代码会用插值/预测位置做 inpaint + 替换文字，
    但水印可能不在该位置（尤其开头水印未出现/中间水印被遮挡期间），
    导致替换文字"提前覆盖"在无水印位置、原水印残留在别处。

    参数:
        frame: 当前帧
        template: 水印模板
        x, y, w, h: 待验证的位置
        threshold: 置信度阈值（与模板匹配一致，默认 0.5）
        pad: 验证区域外扩像素数（容忍预测位置的小偏差）
    返回:
        (置信度, 最佳位置 (x,y,w,h) 或 None)
    """
    if template is None or template.size == 0:
        return 0, None
    th, tw = template.shape[:2]
    fh, fw = frame.shape[:2]

    # 在指定位置周围小区域做 matchTemplate
    rx1 = max(0, x - pad)
    ry1 = max(0, y - pad)
    rx2 = min(fw, x + w + pad)
    ry2 = min(fh, y + h + pad)

    roi = frame[ry1:ry2, rx1:rx2]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return 0, None

    try:
        res = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        # 还原到全帧坐标
        best_x = max_loc[0] + rx1
        best_y = max_loc[1] + ry1
        return max_val, (best_x, best_y, tw, th)
    except Exception:
        return 0, None


def _compute_search_region(positions, frame_w, frame_h, expand=1.5):
    """根据采样帧中水印的位置列表，计算水印可能移动的范围（搜索区域）。

    positions: [(x, y, w, h), ...] 各采样帧中水印的位置
    返回: (x, y, w, h) 搜索区域，覆盖所有已知位置并扩展
    """
    if not positions:
        return None
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    x2s = [p[0] + p[2] for p in positions]
    y2s = [p[1] + p[3] for p in positions]

    x_min = min(xs)
    y_min = min(ys)
    x_max = max(x2s)
    y_max = max(y2s)

    w = x_max - x_min
    h = y_max - y_min
    # 各方向独立扩展（而非从中心扩展），确保边缘水印的搜索区域足够大
    # 每个方向扩展一个模板尺寸的量，并覆盖到画面边缘
    margin_x = int(w * (expand - 1.0)) + 20  # 至少 20px 余量
    margin_y = int(h * (expand - 1.0)) + 20
    # 如果水印靠近边缘（距离 < margin），直接扩展到画面边缘
    x = int(max(0, x_min - margin_x))
    y = int(max(0, y_min - margin_y))
    x2 = int(min(frame_w, x_max + margin_x))
    y2 = int(min(frame_h, y_max + margin_y))
    return (x, y, x2 - x, y2 - y)


def _prerender_text(text, font_size=32, text_color=(255, 255, 255),
                    outline_color=(0, 0, 0), outline_width=2,
                    opacity=1.0, shadow=False, bg_box=False):
    """用 PIL 预渲染文字到带透明通道的 numpy 数组（支持中文）。

    所有样式（颜色/描边/透明度/阴影/背景框）都在预渲染阶段一次性绘制完成，
    逐帧 overlay 时只需做 alpha 混合，零额外性能损耗。

    参数:
        text: 文字内容
        font_size: 字体大小（像素）
        text_color: 文字 RGB 色
        outline_color: 描边 RGB 色（None=不描边）
        outline_width: 描边宽度（0=不描边）
        opacity: 整体不透明度 0.0-1.0（直接写入 alpha 通道，overlay 零损耗）
        shadow: 是否绘制阴影（偏移 2px 的半透明黑色文字）
        bg_box: 是否绘制半透明黑色背景框
    返回:
        (rgba_array, width, height)  rgba_array 形状 (H, W, 4)
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow 未安装，无法预渲染中文文字")
        return None, 0, 0

    # 透明度钳制
    opacity = max(0.0, min(1.0, float(opacity)))
    if opacity <= 0:
        return None, 0, 0

    # 查找中文字体
    font_path = None
    for p in [r"C:\Windows\Fonts\simhei.ttf",
              r"C:\Windows\Fonts\msyh.ttf",
              r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simfang.ttf"]:
        if os.path.exists(p):
            font_path = p
            break
    if not font_path:
        logger.warning("未找到中文字体，无法预渲染文字")
        return None, 0, 0

    font = ImageFont.truetype(font_path, font_size)
    # 计算文字实际尺寸
    bbox = font.getbbox(text)
    shadow_offset = 2 if shadow else 0
    bg_pad = 6 if bg_box else 0
    tw = bbox[2] - bbox[0] + outline_width * 2 + 4 + shadow_offset + bg_pad * 2
    th = bbox[3] - bbox[1] + outline_width * 2 + 4 + shadow_offset + bg_pad * 2

    # 创建透明图像
    img = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    ox = -bbox[0] + outline_width + 2 + bg_pad
    oy = -bbox[1] + outline_width + 2 + bg_pad

    # 绘制半透明黑色背景框（在文字下方）
    if bg_box:
        bg_box_coords = (
            ox - outline_width - bg_pad,
            oy - outline_width - bg_pad,
            ox + (bbox[2] - bbox[0]) + outline_width + bg_pad,
            oy + (bbox[3] - bbox[1]) + outline_width + bg_pad,
        )
        # 背景框用半透明黑色（alpha=160 ≈ 63%）
        draw.rectangle(bg_box_coords, fill=(0, 0, 0, 160))

    # 绘制阴影（偏移 2px 的半透明黑色文字）
    if shadow:
        draw.text((ox + shadow_offset, oy + shadow_offset), text, font=font,
                  fill=(0, 0, 0, 128))

    # 绘制描边（偏移多次绘制）
    if outline_color and outline_width > 0:
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((ox + dx, oy + dy), text, font=font,
                          fill=outline_color + (255,))

    # 绘制主文字
    draw.text((ox, oy), text, font=font, fill=text_color + (255,))

    arr = np.array(img)
    # 应用整体透明度：直接缩放 alpha 通道（overlay 阶段零改动）
    if opacity < 1.0:
        arr[:, :, 3] = (arr[:, :, 3].astype(np.float32) * opacity).astype(np.uint8)
    return arr, tw, th


def _overlay_text_on_frame(frame, text_img, x, y, rw, rh):
    """在帧的指定区域 overlay 预渲染的文字图（带透明通道）。

    文字图按预渲染的原始尺寸绘制（不缩放），居中放置在原水印位置。支持部分出画。
    """
    if text_img is None or text_img.size == 0:
        return frame

    fh, fw = frame.shape[:2]
    # 直接用预渲染的原始尺寸（不再根据原水印区域缩放）
    src_h, src_w = text_img.shape[:2]
    new_w = src_w
    new_h = src_h

    # 居中放置在原水印位置（允许部分出画）
    paste_x = x + (rw - new_w) // 2
    paste_y = y + (rh - new_h) // 2

    # 计算画面内可见区域（处理部分出画）
    vis_x1 = max(0, paste_x)
    vis_y1 = max(0, paste_y)
    vis_x2 = min(fw, paste_x + new_w)
    vis_y2 = min(fh, paste_y + new_h)
    vis_w = vis_x2 - vis_x1
    vis_h = vis_y2 - vis_y1
    if vis_w < 2 or vis_h < 2:
        return frame  # 完全出画，不绘制

    # 裁剪文字图到可见部分
    offset_x = vis_x1 - paste_x
    offset_y = vis_y1 - paste_y
    resized = text_img[offset_y:offset_y + vis_h, offset_x:offset_x + vis_w]

    # alpha 混合
    roi = frame[vis_y1:vis_y2, vis_x1:vis_x2]
    alpha = resized[:, :, 3:4].astype(np.float32) / 255.0
    rgb = resized[:, :, :3].astype(np.float32)
    blended = (rgb * alpha + roi.astype(np.float32) * (1 - alpha)).astype(np.uint8)
    frame[vis_y1:vis_y2, vis_x1:vis_x2] = blended
    return frame


def _load_replace_image(path, opacity=1.0):
    """加载替换水印图片（PNG，带 alpha 通道），并应用不透明度。

    返回 BGRA 图像（带 alpha 通道），失败返回 None。
    路径为 None 或文件不存在返回 None。
    """
    if not path:
        return None
    if not os.path.exists(path):
        logger.warning("替换水印图片不存在: %s", path)
        return None
    try:
        # 用 IMREAD_UNCHANGED 保留 alpha 通道
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.warning("无法读取替换水印图片: %s", path)
            return None
        # 统一转成 BGRA（4 通道）
        if img.ndim == 2:
            # 灰度图 → BGRA
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        elif img.shape[2] == 4:
            pass  # 已是 BGRA
        else:
            logger.warning("替换水印图片通道数异常: %s", img.shape)
            return None
        # 应用不透明度到 alpha 通道
        if opacity < 1.0:
            opacity = max(0.1, min(1.0, float(opacity)))
            img[:, :, 3] = (img[:, :, 3].astype(np.float32) * opacity).astype(np.uint8)
        logger.info("已加载替换水印图片: %s (%dx%d, 不透明度 %.2f)",
                    os.path.basename(path), img.shape[1], img.shape[0], opacity)
        return img
    except Exception as e:
        logger.warning("加载替换水印图片异常: %s", e)
        return None


def _overlay_image_on_frame(frame, img, x, y, rw, rh, scale=1.0):
    """在帧的指定水印区域居中叠加替换图片（带 alpha 混合）。

    图片按 scale 系数缩放（scale=1.0 时适应原水印区域：宽度=rw，等比缩放高度）。
    支持部分出画。img 必须是 BGRA 4 通道图。

    参数:
        frame: BGR 帧（会被原地修改）
        img: BGRA 替换图片
        x, y, rw, rh: 原水印位置/区域
        scale: 缩放系数，1.0=适应原水印宽度，1.2=放大20%
    返回:
        处理后的帧
    """
    if img is None or img.size == 0:
        return frame
    fh, fw = frame.shape[:2]
    src_h, src_w = img.shape[:2]

    # 计算目标尺寸：以原水印宽度为基准，按图片原始宽高比等比缩放，再乘 scale
    if src_w <= 0:
        return frame
    base_w = max(2, rw)
    ratio = base_w / float(src_w)
    new_w = max(2, int(src_w * ratio * scale))
    new_h = max(2, int(src_h * ratio * scale))

    # 缩放图片
    if new_w != src_w or new_h != src_h:
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    else:
        resized = img

    # 居中放置在原水印位置（允许部分出画）
    paste_x = x + (rw - new_w) // 2
    paste_y = y + (rh - new_h) // 2

    # 计算画面内可见区域（处理部分出画）
    vis_x1 = max(0, paste_x)
    vis_y1 = max(0, paste_y)
    vis_x2 = min(fw, paste_x + new_w)
    vis_y2 = min(fh, paste_y + new_h)
    vis_w = vis_x2 - vis_x1
    vis_h = vis_y2 - vis_y1
    if vis_w < 2 or vis_h < 2:
        return frame  # 完全出画，不绘制

    # 裁剪图片到可见部分
    offset_x = vis_x1 - paste_x
    offset_y = vis_y1 - paste_y
    crop = resized[offset_y:offset_y + vis_h, offset_x:offset_x + vis_w]

    # alpha 混合
    roi = frame[vis_y1:vis_y2, vis_x1:vis_x2]
    alpha = crop[:, :, 3:4].astype(np.float32) / 255.0
    rgb = crop[:, :, :3].astype(np.float32)
    blended = (rgb * alpha + roi.astype(np.float32) * (1 - alpha)).astype(np.uint8)
    frame[vis_y1:vis_y2, vis_x1:vis_x2] = blended
    return frame


def _interpolate_position(watermark_positions, frame_idx, frame_w=0, frame_h=0, max_gap=30):
    """根据 OCR 采样到的位置，分段线性插值估算指定帧的水印位置。

    采用分段策略：如果两个采样点之间间隔超过 max_gap 帧，认为水印在这段
    期间消失（划出屏幕），不做跨越插值。这样能处理"划出屏幕又转向出来"的情况。

    watermark_positions: [(frame_idx, x, y, w, h), ...] 按 frame_idx 排序
    frame_w/frame_h: 画面尺寸，用于外推时裁剪到画面范围内
    max_gap: 最大允许插值的帧间隔，超过则认为水印消失
    返回: (x, y, w, h) 或 None（水印消失期间返回 None）
    """
    if not watermark_positions:
        return None
    sorted_pos = sorted(watermark_positions, key=lambda p: p[0])
    n = len(sorted_pos)

    # 在两个采样点之间，检查间隔是否过大（水印可能已消失）
    for i in range(n - 1):
        f1 = sorted_pos[i][0]
        f2 = sorted_pos[i + 1][0]
        if f1 <= frame_idx <= f2:
            gap = f2 - f1
            if gap > max_gap:
                # 间隔太大，水印可能在这段期间消失，返回 None 跳过修复
                return None
            t = (frame_idx - f1) / gap if gap > 0 else 0
            x = int(sorted_pos[i][1] + t * (sorted_pos[i + 1][1] - sorted_pos[i][1]))
            y = int(sorted_pos[i][2] + t * (sorted_pos[i + 1][2] - sorted_pos[i][2]))
            w = int(sorted_pos[i][3] + t * (sorted_pos[i + 1][3] - sorted_pos[i][3]))
            h = int(sorted_pos[i][4] + t * (sorted_pos[i + 1][4] - sorted_pos[i][4]))
            return (x, y, max(2, w), max(2, h))

    # 在采样范围之前 → 短距离外推（用前两个点）
    if frame_idx < sorted_pos[0][0]:
        gap = sorted_pos[0][0] - frame_idx
        if gap > max_gap:
            return None  # 太远，水印可能还没出现
        if n >= 2:
            f1, f2 = sorted_pos[0][0], sorted_pos[1][0]
            t = (frame_idx - f1) / (f2 - f1) if f2 > f1 else 0
            x = int(sorted_pos[0][1] + t * (sorted_pos[1][1] - sorted_pos[0][1]))
            y = int(sorted_pos[0][2] + t * (sorted_pos[1][2] - sorted_pos[0][2]))
            w = int(sorted_pos[0][3] + t * (sorted_pos[1][3] - sorted_pos[0][3]))
            h = int(sorted_pos[0][4] + t * (sorted_pos[1][4] - sorted_pos[0][4]))
        else:
            x, y, w, h = sorted_pos[0][1], sorted_pos[0][2], sorted_pos[0][3], sorted_pos[0][4]

    # 在采样范围之后 → 短距离外推（用最后两个点）
    else:
        gap = frame_idx - sorted_pos[-1][0]
        if gap > max_gap:
            return None  # 太远，水印可能已消失
        if n >= 2:
            f1, f2 = sorted_pos[-2][0], sorted_pos[-1][0]
            t = (frame_idx - f1) / (f2 - f1) if f2 > f1 else 0
            x = int(sorted_pos[-2][1] + t * (sorted_pos[-1][1] - sorted_pos[-2][1]))
            y = int(sorted_pos[-2][2] + t * (sorted_pos[-1][2] - sorted_pos[-2][2]))
            w = int(sorted_pos[-2][3] + t * (sorted_pos[-1][3] - sorted_pos[-2][3]))
            h = int(sorted_pos[-2][4] + t * (sorted_pos[-1][4] - sorted_pos[-2][4]))
        else:
            x, y, w, h = sorted_pos[-1][1], sorted_pos[-1][2], sorted_pos[-1][3], sorted_pos[-1][4]

    # 外推位置裁剪到画面范围内（水印部分出画也保留修复区域）
    if frame_w > 0 and frame_h > 0:
        x = max(-w // 2, min(x, frame_w - w // 2))
        y = max(-h // 2, min(y, frame_h - h // 2))
    return (x, y, max(2, w), max(2, h))


def _mad(values):
    """中位数绝对偏差（MAD）的标准差估计：1.4826 * median(|xi - median|)。

    对离群点鲁棒——当 OCR 漏识别部分字符导致个别 box 的 x1 大幅漂移时，
    标准差会被离群点拉大，而 MAD 几乎不受影响。
    例：[50,53,57,153,52,50,45,53,57,54] 的 std=28.7（超阈值），
    但 MAD=1.48（远低于阈值），正确判定为稳定。
    """
    arr = np.array(values, dtype=float)
    if len(arr) == 0:
        return 0.0
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return 1.4826 * mad


def _is_static_watermark(watermark_positions, pos_tolerance=8.0, min_samples=5):
    """检测水印是否是固定的（不移动的静态水印）。

    判定逻辑：把所有采样位置按 y 坐标聚类成"行"，每行内 x/y 的离散度都小于
    阈值，则视为固定水印。聚类是为了处理"多行固定水印"（如
    "@作者" + "禁止搬运" 两行，每行各自固定但 y 不同）。

    离散度用 MAD（中位数绝对偏差）而非标准差，因为 OCR 漏识别会导致个别
    采样点的 x1 大幅漂移（如"禁止抄袭搬运"被识别成"搬运"，x1 右移 100px），
    标准差会被单个离群点拉大误判为动态，而 MAD 对离群点鲁棒。

    参数:
        watermark_positions: [(frame_idx, x, y, w, h), ...]
        pos_tolerance: 位置离散度阈值（像素），小于此值视为固定不动
        min_samples: 至少这么多采样点才能判定（避免样本太少误判）
    返回:
        (is_static, union_rect)：
          is_static: True=固定水印
          union_rect: 固定水印的并集矩形 (x1, y1, x2, y2)，覆盖所有行；
                      非固定时返回 None
    """
    if len(watermark_positions) < min_samples:
        return False, None

    # 按 y 坐标聚类成行（y 相差 < 15 像素视为同一行）
    # 阈值 15：同一行水印的 y 漂移 < 5（OCR box 边界抖动），
    # 不同行间距通常 > 20（文字高度 + 行距），15 能可靠区分
    sorted_by_y = sorted(watermark_positions, key=lambda p: p[2])
    rows = []
    for pos in sorted_by_y:
        y = pos[2]
        placed = False
        for row in rows:
            if abs(row[0][2] - y) < 15:
                row.append(pos)
                placed = True
                break
        if not placed:
            rows.append([pos])

    # 每行检查 y 离散度 + x1 离散度（用 MAD 替代标准差）
    # 注意：OCR 漏识别部分字符会导致 box 起始 x 漂移（如"禁止抄袭"被识别成"抄袭"，
    # x1 右移），因此 x1 容差比 y 更宽松（3 倍）。
    all_static = True
    all_x1, all_y1, all_x2, all_y2 = [], [], [], []
    x_tol = pos_tolerance * 3  # x 方向容差放宽 3 倍（OCR 漏字导致 x1 漂移）
    for row in rows:
        if len(row) < 2:
            for p in row:
                all_x1.append(p[1])
                all_y1.append(p[2])
                all_x2.append(p[1] + p[3])
                all_y2.append(p[2] + p[4])
            continue
        ys = [p[2] for p in row]
        xs = [p[1] for p in row]
        # 用 MAD 替代标准差：对 OCR 漏识别导致的离群点鲁棒
        y_mad = _mad(ys)
        x_mad = _mad(xs)
        if y_mad > pos_tolerance or x_mad > x_tol:
            all_static = False
            logger.debug("行 y=%d 非静态: y_mad=%.2f(阈值%.1f), x_mad=%.2f(阈值%.1f), 样本=%d",
                         row[0][2], y_mad, pos_tolerance, x_mad, x_tol, len(row))
            break
        for p in row:
            all_x1.append(p[1])
            all_y1.append(p[2])
            all_x2.append(p[1] + p[3])
            all_y2.append(p[2] + p[4])

    if not all_static or not all_x1:
        return False, None

    # 取所有行的并集矩形（覆盖所有固定水印行）
    x1 = min(all_x1)
    y1 = min(all_y1)
    x2 = max(all_x2)
    y2 = max(all_y2)
    return True, (x1, y1, x2, y2)


def sample_frames_ocr_combined(video_path, ocr_samples=15,
                                wm_text=None, replace_text=None,
                                subtitle_area=None):
    """联合采样：一次 OCR 同时提取水印和字幕文字（避免两个流程各自采样）。

    参数:
        video_path: 输入视频
        ocr_samples: 采样帧数
        wm_text: 水印文字关键词（逗号分隔），None 则自动按 @ 符号识别
        replace_text: 替换文字（排除项，避免把自己的替换文字当水印）
        subtitle_area: 字幕区域 {"x","y","w","h"} 相对画面比例，None 则不提取字幕
    返回:
        {
            "watermark_positions": [(frame_idx, x, y, w, h), ...],
            "best_template": np.ndarray or None,
            "best_score": float,
            "subtitle_boxes": [(x1,y1,x2,y2), ...],   # 全帧坐标
            "sample_indices": [idx, ...],
        }
    """
    if not _HAS_CV2:
        logger.error("opencv-python 未安装")
        return None
    if not _HAS_PADDLE:
        logger.error("paddleocr 未安装")
        return None

    import cv2

    w, h, fps, nb = _probe_video(video_path)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("无法打开视频: %s", video_path)
        return None

    ocr = _get_ocr()
    if ocr is None:
        cap.release()
        return None

    total = nb if nb > 0 else int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        dur = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        total = int(dur * fps) if dur > 0 else 100

    # 首尾密集采样（各 25%），中间均匀采样（50%）
    sample_indices = []
    if ocr_samples >= total:
        sample_indices = list(range(0, total, max(1, total // 10)))
    else:
        head_count = max(3, ocr_samples // 4)
        tail_count = max(3, ocr_samples // 4)
        mid_count = ocr_samples - head_count - tail_count
        head_end = max(1, int(total * 0.20))
        for i in range(head_count):
            sample_indices.append(int(head_end * (i + 1) / (head_count + 1)))
        if mid_count > 0:
            mid_start = int(total * 0.20)
            mid_end = int(total * 0.80)
            for i in range(mid_count):
                sample_indices.append(int(mid_start + (mid_end - mid_start) * (i + 1) / (mid_count + 1)))
        tail_start = int(total * 0.80)
        for i in range(tail_count):
            sample_indices.append(int(tail_start + (total - tail_start) * (i + 1) / (tail_count + 1)))
    sample_indices = sorted(set(sample_indices))

    # 字幕区域像素坐标
    sub_sx = sub_sy = sub_sw = sub_sh = 0
    if subtitle_area:
        sub_sx = max(0, int(w * subtitle_area.get("x", 0.0)))
        sub_sy = max(0, int(h * subtitle_area.get("y", 0.75)))
        sub_sw = min(w, int(w * subtitle_area.get("w", 1.0)))
        sub_sh = min(h, int(h * subtitle_area.get("h", 0.20)))
        sub_sx2 = min(w, sub_sx + sub_sw)
        sub_sy2 = min(h, sub_sy + sub_sh)
        sub_sw = sub_sx2 - sub_sx
        sub_sh = sub_sy2 - sub_sy

    watermark_positions = []
    best_template = None
    best_score = 0
    subtitle_boxes = []

    for idx in sample_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue

        # _run_ocr_on_frame 内部已做缩放保护（4K → 480 宽），返回的 box 坐标已还原到原帧
        ocr_results = _run_ocr_on_frame(frame, ocr, max_size=960)
        for box, text, score in ocr_results:
            rect = _box_to_rect(box)

            if _is_watermark_text(text, wm_text, exclude_text=replace_text):
                watermark_positions.append((idx, rect[0], rect[1], rect[2], rect[3]))
                logger.info("  帧 %d: 识别水印 \"%s\" (置信度 %.2f) 位置=%s",
                            idx, text, score, rect)
                area = rect[2] * rect[3]
                if area > best_score:
                    tmpl, _ = _crop_template(frame, rect, padding=4)
                    best_template = tmpl
                    best_score = area
            else:
                # 非水印文字 → 可能是字幕
                if subtitle_area and len(text.strip()) >= 2:
                    cx = rect[0] + rect[2] / 2
                    cy = rect[1] + rect[3] / 2
                    # 文字中心落在字幕区域内才算字幕
                    if sub_sx <= cx <= sub_sx + sub_sw and sub_sy <= cy <= sub_sy + sub_sh:
                        subtitle_boxes.append((rect[0], rect[1],
                                               rect[0] + rect[2], rect[1] + rect[3]))

    cap.release()

    logger.info("联合采样完成: 水印 %d 次, 字幕 %d 个框 (共 %d 帧)",
                len(watermark_positions), len(subtitle_boxes), len(sample_indices))

    return {
        "watermark_positions": watermark_positions,
        "best_template": best_template,
        "best_score": best_score,
        "subtitle_boxes": subtitle_boxes,
        "sample_indices": sample_indices,
        "w": w, "h": h, "fps": fps, "nb": nb,
    }


def remove_watermark_ocr(video_path, out_path, wm_text=None, ocr_samples=10,
                         dilate=3, match_threshold=0.5, show_progress=True,
                         replace_text=None,
                         replace_opacity=1.0, replace_style="white_black",
                         replace_font_scale=1.0, replace_outline=None,
                         replace_bg=False,
                         replace_image=None, replace_image_opacity=1.0,
                         replace_image_scale=1.0,
                         precomputed=None):
    """OCR 模式去除动态水印。

    流程:
      1. 均匀采样 N 帧，运行 PaddleOCR
      2. 按内容过滤水印（@ 符号 / --wm-text 关键词 / 常见水印词）
      3. 裁剪水印模板 + 计算水印移动范围（搜索区域）
      4. 逐帧用模板匹配精确定位水印 → cv2.inpaint 修复
      5. 若指定 replace_text/replace_image，在原水印位置覆盖（跟随移动轨迹）
         同时指定时图片优先

    参数:
        video_path: 输入视频
        out_path: 输出视频（无音频，需用 remove_watermark_with_audio 合成）
        wm_text: 水印文字关键词（逗号分隔），None 则自动按 @ 符号识别
        ocr_samples: OCR 采样帧数（默认 10，越多越准但越慢）
        dilate: 膨胀半径
        match_threshold: 模板匹配置信度阈值（0-1，越低越宽松）
        show_progress: 是否打印进度
        replace_text: 替换文字（如 "@你的号"），在原水印轨迹位置覆盖
        replace_opacity: 替换文字不透明度 0.0-1.0（默认 1.0=不透明）。预渲染阶段写入 alpha，零逐帧损耗
        replace_style: 替换文字样式预设（white_black/white_none/black_white/yellow_black/
            red_white/white_shadow/pink_black/cyan_black）。默认 white_black 白字黑边
        replace_font_scale: 字体大小缩放系数（默认 1.0=用原水印高度，1.2=放大20%）
        replace_outline: 手动指定描边宽度（None=用样式预设的 outline_width，0=不描边，>0=指定宽度）
        replace_bg: 是否绘制半透明黑色背景框（True/False，默认 False）
        replace_image: 替换水印图片路径（PNG 带 alpha），在原水印轨迹位置覆盖自己的图片。
            与 replace_text 互斥，同时指定时图片优先
        replace_image_opacity: 图片不透明度 0.1-1.0（默认 1.0）
        replace_image_scale: 图片缩放系数（默认 1.0=适应原水印宽度，1.2=放大20%）
        precomputed: 预采样结果（来自 sample_frames_ocr_combined），有则跳过阶段1采样
    返回:
        out_path 成功 / None 失败
    """
    if not _HAS_CV2:
        logger.error("opencv-python 未安装，请运行: pip install opencv-python")
        return None
    if not _HAS_PADDLE:
        logger.error("paddleocr 未安装，请运行: pip install paddleocr")
        return None
    if not os.path.exists(video_path):
        logger.error("视频不存在: %s", video_path)
        return None

    print(f"=== WM DEBUG: remove_watermark_ocr started, video={video_path} ===", flush=True)
    w, h, fps, nb = _probe_video(video_path)
    print(f"=== WM DEBUG: probed {w}x{h} {fps}fps {nb} frames ===", flush=True)
    logger.info("OCR 水印去除: %s  %sx%s  %.2ffps  %d帧",
                os.path.basename(video_path), w, h, fps, nb)

    # === 阶段 1: 采样帧 OCR 识别水印 ===
    if precomputed and precomputed.get("watermark_positions"):
        # 复用预采样结果（来自 sample_frames_ocr_combined，避免重复 OCR）
        logger.info("=" * 40)
        logger.info("阶段 1/3: 复用预采样 OCR 结果（跳过重复采样）...")
        watermark_positions = precomputed["watermark_positions"]
        best_template = precomputed.get("best_template")
        best_score = precomputed.get("best_score", 0)
        sample_indices = precomputed.get("sample_indices", [])
        # total 用于阶段2的进度显示
        total = nb if nb > 0 else (precomputed.get("nb") or 0)
        logger.info("复用水印识别 %d 次, 模板尺寸 %sx%s",
                    len(watermark_positions),
                    best_template.shape[1] if best_template is not None else 0,
                    best_template.shape[0] if best_template is not None else 0)
    else:
        print(f"=== WM DEBUG: 阶段1 采样, precomputed={precomputed is None}, ocr_samples={ocr_samples} ===", flush=True)
        logger.info("=" * 40)
        logger.info("阶段 1/3: 采样 %d 帧运行 OCR 识别水印...", ocr_samples)

        cap = cv2.VideoCapture(video_path)
        print(f"=== WM DEBUG: cap opened={cap.isOpened()} ===", flush=True)
        if not cap.isOpened():
            logger.error("无法打开视频: %s", video_path)
            return None

        print("=== WM DEBUG: getting OCR instance ===", flush=True)
        ocr = _get_ocr()
        print(f"=== WM DEBUG: ocr={ocr is not None} ===", flush=True)
        if ocr is None:
            logger.error("PaddleOCR 不可用")
            cap.release()
            return None

        # 均匀采样帧索引
        total = nb if nb > 0 else int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            # 回退：读取时长计算
            dur = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            total = int(dur * fps) if dur > 0 else 100

        sample_indices = []
        if ocr_samples >= total:
            sample_indices = list(range(0, total, max(1, total // 10)))
        else:
            # 首尾密集采样（各 25%），中间均匀采样（50%）
            # 高密度采样捕捉水印进入/离开/转向的所有转折点
            head_count = max(3, ocr_samples // 4)   # 25% 在开头
            tail_count = max(3, ocr_samples // 4)   # 25% 在结尾
            mid_count = ocr_samples - head_count - tail_count  # 50% 在中间

            # 开头密集采样（前 20% 时长）
            head_end = max(1, int(total * 0.20))
            for i in range(head_count):
                sample_indices.append(int(head_end * (i + 1) / (head_count + 1)))

            # 中间均匀采样（20%~80%）
            if mid_count > 0:
                mid_start = int(total * 0.20)
                mid_end = int(total * 0.80)
                for i in range(mid_count):
                    sample_indices.append(int(mid_start + (mid_end - mid_start) * (i + 1) / (mid_count + 1)))

            # 结尾密集采样（后 20% 时长）
            tail_start = int(total * 0.80)
            for i in range(tail_count):
                sample_indices.append(int(tail_start + (total - tail_start) * (i + 1) / (tail_count + 1)))

        sample_indices = sorted(set(sample_indices))

        watermark_positions = []  # 各采样帧中水印的位置 [(frame_idx, x, y, w, h), ...]
        best_template = None      # 最清晰的水印模板
        best_score = 0

        print(f"=== WM DEBUG: 采样 {len(sample_indices)} 帧, indices={sample_indices[:5]}... ===", flush=True)
        _ocr_call_count = 0
        for idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                print(f"=== WM DEBUG: frame {idx} read failed ===", flush=True)
                continue

            # _run_ocr_on_frame 内部已做缩放保护，返回坐标已还原
            ocr_results = _run_ocr_on_frame(frame, ocr, max_size=960)
            _ocr_call_count += 1
            for box, text, score in ocr_results:
                if _is_watermark_text(text, wm_text, exclude_text=replace_text):
                    # _run_ocr_on_frame 已还原坐标
                    rect = _box_to_rect(box)
                    watermark_positions.append((idx, rect[0], rect[1], rect[2], rect[3]))
                    logger.info("  帧 %d: 识别水印 \"%s\" (置信度 %.2f) 位置=%s",
                                idx, text, score, rect)
                    # 选置信度最高 + 面积最大的作为模板
                    area = rect[2] * rect[3]
                    if area > best_score:
                        tmpl, _ = _crop_template(frame, rect, padding=4)
                        best_template = tmpl
                        best_score = area

        cap.release()
        print(f"=== WM DEBUG: 采样完成, OCR 调用 {_ocr_call_count} 次, 识别水印 {len(watermark_positions)} 个 ===", flush=True)

    if not watermark_positions or best_template is None:
        print(f"=== WM DEBUG: 识别失败! positions={len(watermark_positions)}, template={best_template is not None} ===", flush=True)
        logger.warning("OCR 未识别到水印文字（已采样 %d 帧）", len(sample_indices))
        logger.warning("可能原因: 1) 视频无水印 2) 水印不是文字 3) OCR 漏识别")
        logger.warning("建议: 尝试 --wm-text \"水印关键词\" 指定匹配内容，或用颜色模式 --wm-region")
        return None

    # 计算搜索区域（水印移动范围）—— watermark_positions 现在是 (idx,x,y,w,h) 格式
    pos_only = [(p[1], p[2], p[3], p[4]) for p in watermark_positions]
    search_region = _compute_search_region(pos_only, w, h, expand=2.0)
    logger.info("识别到水印 %d 次，模板尺寸 %sx%s，搜索区域 %s",
                len(watermark_positions),
                best_template.shape[1], best_template.shape[0],
                search_region)

    # 固定水印检测：若采样位置稳定（MAD < 阈值），跳过逐帧模板匹配，
    # 用并集矩形对所有帧做 inpaint + 覆盖。避免模板匹配失败导致的跳来跳去。
    is_static, static_union = _is_static_watermark(watermark_positions)
    print(f"=== WM DEBUG: 固定水印检测: is_static={is_static}, static_union={static_union}, samples={len(watermark_positions)} ===", flush=True)
    logger.info("固定水印检测: is_static=%s, static_union=%s, samples=%d",
                is_static, static_union, len(watermark_positions))
    if is_static:
        sx1, sy1, sx2, sy2 = static_union
        print(f"=== WM DEBUG: 检测到固定水印，用并集矩形 ({sx1},{sy1},{sx2},{sy2}) 处理所有帧 ===", flush=True)
        logger.info("检测到固定水印（位置稳定），跳过逐帧模板匹配，"
                    "用并集矩形 (%d,%d,%d,%d) 处理所有帧", sx1, sy1, sx2, sy2)
    else:
        print("=== WM DEBUG: 非固定水印，走动态路径（逐帧模板匹配）===", flush=True)
        static_union = None

    # 预渲染替换文字（如有）—— 所有样式在此一次性生效，逐帧 overlay 零损耗
    replace_text_img = None
    if replace_text:
        # 字体大小直接用原水印高度（不再压缩），并应用缩放系数
        avg_h = sum(p[4] for p in watermark_positions) / len(watermark_positions)
        avg_w = sum(p[3] for p in watermark_positions) / len(watermark_positions)
        font_size = max(20, int(avg_h * max(0.3, float(replace_font_scale))))

        # 解析样式预设
        style_cfg = REPLACE_TEXT_STYLES.get(replace_style, REPLACE_TEXT_STYLES["white_black"])
        text_color = style_cfg["text_color"]
        outline_color = style_cfg["outline_color"]
        outline_width = style_cfg["outline_width"] if replace_outline is None else max(0, int(replace_outline))
        shadow = style_cfg.get("shadow", False)

        replace_text_img, _, _ = _prerender_text(
            replace_text, font_size=font_size,
            text_color=text_color, outline_color=outline_color, outline_width=outline_width,
            opacity=replace_opacity, shadow=shadow, bg_box=bool(replace_bg))
        if replace_text_img is not None:
            logger.info("已预渲染替换文字: \"%s\" (字体 %dpx, 样式 %s, 透明度 %.2f, 描边 %d, 阴影 %s, 背景框 %s, 原水印 %.0fx%.0f)",
                        replace_text, font_size, replace_style, replace_opacity,
                        outline_width, shadow, bool(replace_bg), avg_w, avg_h)
        else:
            logger.warning("替换文字预渲染失败，将只去除水印不覆盖")

    # 预加载替换水印图片（如有）—— 图片优先于文字，同时指定时禁用文字 overlay
    replace_img = _load_replace_image(replace_image, replace_image_opacity)
    if replace_img is not None:
        replace_text_img = None  # 图片优先，避免双重覆盖

    # === 阶段 2: 逐帧模板匹配 + inpaint 修复 + 可选覆盖替换文字/图片 ===
    logger.info("=" * 40)
    if replace_img is not None:
        logger.info("阶段 2/3: 逐帧模板匹配 + inpaint 修复 + 轨迹覆盖替换图片...")
    elif replace_text_img is not None:
        logger.info("阶段 2/3: 逐帧模板匹配 + inpaint 修复 + 轨迹覆盖替换文字...")
    else:
        logger.info("阶段 2/3: 逐帧模板匹配 + inpaint 修复...")

    # 用 ffmpeg 管道写入（支持 4K，OpenCV VideoWriter 的 mp4v 不支持 4K）
    ffmpeg = _get_ffmpeg()
    ffmpeg_cmd = [ffmpeg, "-y",
                  "-f", "rawvideo",
                  "-pix_fmt", "bgr24",
                  "-s", f"{w}x{h}",
                  "-r", str(fps),
                  "-i", "-",
                  "-c:v", "libx264",
                  "-preset", "ultrafast",
                  "-crf", "18",
                  "-pix_fmt", "yuv420p",
                  out_path]
    try:
        writer_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL)
    except Exception as e:
        logger.error("启动 ffmpeg 管道失败: %s", e)
        return None
    if writer_proc.stdin is None:
        logger.error("ffmpeg 管道 stdin 不可用")
        writer_proc.kill()
        return None

    def _write_frame(f):
        """向 ffmpeg 管道写入一帧。"""
        try:
            writer_proc.stdin.write(f.tobytes())
        except (BrokenPipeError, OSError):
            pass

    cap = cv2.VideoCapture(video_path)
    frame_idx = 0
    removed_count = 0
    replaced_count = 0
    match_fail_count = 0
    interpolated_count = 0
    rejected_count = 0  # 模板匹配但被运动连续性检查拒绝的帧数
    verify_fail_count = 0  # interp/predict 位置验证失败的帧数（水印不在该位置）

    # 运动连续性追踪状态
    last_pos = None        # 上一帧有效位置 (x, y, w, h)
    last_velocity = (0, 0)  # 上一帧速度 (vx, vy)
    max_jump = 80          # 单帧最大允许位移（像素），超过判定为误匹配
    velocity_alpha = 0.5   # 速度平滑系数（越小越平滑）

    def _inpaint_and_overlay(frame, x, y, rw, rh):
        """对指定区域做 inpaint 修复 + 可选覆盖替换文字/图片。返回处理后的帧。

        支持水印部分出画（x/y 为负或超出画面边界）的情况。
        """
        vis_x = max(0, x)
        vis_y = max(0, y)
        vis_x2 = min(w, x + rw)
        vis_y2 = min(h, y + rh)
        vis_w = vis_x2 - vis_x
        vis_h = vis_y2 - vis_y
        if vis_w < 2 or vis_h < 2:
            if replace_img is not None:
                return _overlay_image_on_frame(frame.copy(), replace_img, x, y, rw, rh, replace_image_scale)
            if replace_text_img is not None:
                return _overlay_text_on_frame(frame.copy(), replace_text_img, x, y, rw, rh)
            return frame

        pad = max(dilate * 2, 10)
        cx1 = max(0, vis_x - pad)
        cy1 = max(0, vis_y - pad)
        cx2 = min(w, vis_x2 + pad)
        cy2 = min(h, vis_y2 + pad)
        crop = frame[cy1:cy2, cx1:cx2].copy()
        local_mask = np.zeros((crop.shape[0], crop.shape[1]), dtype=np.uint8)
        lx = vis_x - cx1
        ly = vis_y - cy1
        local_mask[ly:ly+vis_h, lx:lx+vis_w] = 255
        if dilate > 0:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
            local_mask = cv2.dilate(local_mask, k, iterations=1)
        inpainted_crop = cv2.inpaint(crop, local_mask, 3, cv2.INPAINT_TELEA)
        processed = frame.copy()
        processed[cy1:cy2, cx1:cx2] = inpainted_crop
        if replace_img is not None:
            processed = _overlay_image_on_frame(processed, replace_img, x, y, rw, rh, replace_image_scale)
        elif replace_text_img is not None:
            processed = _overlay_text_on_frame(processed, replace_text_img, x, y, rw, rh)
        return processed

    try:
        import time as _time
        _t_match_total = 0.0
        _t_inpaint_total = 0.0
        _t_write_total = 0.0
        _t_read_total = 0.0

        # === 固定水印快速路径：跳过逐帧模板匹配，对所有帧用并集矩形处理 ===
        if is_static and static_union is not None:
            sx1, sy1, sx2, sy2 = static_union
            sw = sx2 - sx1
            sh = sy2 - sy1
            print(f"=== WM DEBUG: 进入静态快速路径! 矩形({sx1},{sy1},{sx2},{sy2}) 尺寸{sw}x{sh} ===", flush=True)
            logger.info("固定水印快速路径: 矩形 (%d,%d,%d,%d) 尺寸 %dx%d，覆盖所有帧",
                        sx1, sy1, sx2, sy2, sw, sh)
            while True:
                _t0 = _time.time()
                ok, frame = cap.read()
                _t1 = _time.time()
                _t_read_total += (_t1 - _t0)
                if not ok:
                    break
                _t2 = _time.time()
                processed = _inpaint_and_overlay(frame, sx1, sy1, sw, sh)
                _t3 = _time.time()
                _t_inpaint_total += (_t3 - _t2)
                _write_frame(processed)
                _t4 = _time.time()
                _t_write_total += (_t4 - _t3)
                removed_count += 1
                if replace_img is not None or replace_text_img is not None:
                    replaced_count += 1
                frame_idx += 1
                if show_progress and frame_idx % 30 == 0:
                    pct = (frame_idx / nb * 100) if nb > 0 else 0
                    logger.info("水印去除进度: %d/%d 帧 (%.1f%%)", frame_idx, nb, pct)
        else:
            # === 动态水印路径：逐帧模板匹配 + 运动预测 + 插值 ===
            while True:
                _t0 = _time.time()
                ok, frame = cap.read()
                _t1 = _time.time()
                _t_read_total += (_t1 - _t0)
                if not ok:
                    break

                # 当前帧水印位置的确定优先级：
                # 1. 模板匹配（需通过运动连续性检查）
                # 2. 运动预测（上一帧位置 + 速度）
                # 3. OCR 采样位置插值
                # 4. 跳过（水印消失期间）

                _t2 = _time.time()
                matched_rect = _match_template_in_frame(frame, best_template, search_region, match_threshold)
                _t3 = _time.time()
                _t_match_total += (_t3 - _t2)
                use_rect = None
                used_source = None  # "match" / "predict" / "interp"

                if matched_rect is not None:
                    mx, my, mrw, mrh = matched_rect
                    # 运动连续性检查：和上一帧位置对比，如果跳跃太大判定为误匹配
                    if last_pos is not None:
                        lx, ly, _, _ = last_pos
                        jump_dist = ((mx - lx) ** 2 + (my - ly) ** 2) ** 0.5
                        if jump_dist > max_jump:
                            # 误匹配！水印不可能跳这么远，用预测位置代替
                            rejected_count += 1
                            # 用上一帧位置 + 速度预测
                            pvx, pvy = last_velocity
                            px = int(lx + pvx)
                            py = int(ly + pvy)
                            use_rect = (px, py, last_pos[2], last_pos[3])
                            used_source = "predict"
                        else:
                            use_rect = matched_rect
                            used_source = "match"
                    else:
                        # 第一帧，无历史位置，直接用匹配结果
                        use_rect = matched_rect
                        used_source = "match"
                else:
                    # 模板匹配失败 → 先尝试运动预测（如果有历史位置）
                    if last_pos is not None:
                        lx, ly, lw, lh = last_pos
                        pvx, pvy = last_velocity
                        px = int(lx + pvx)
                        py = int(ly + pvy)
                        use_rect = (px, py, lw, lh)
                        used_source = "predict"
                    else:
                        # 无历史位置 → 用 OCR 采样位置插值
                        interp = _interpolate_position(watermark_positions, frame_idx, w, h)
                        if interp is not None:
                            use_rect = interp
                            used_source = "interp"

                if use_rect is not None:
                    x, y, rw, rh = use_rect

                    # 对 interp(插值)/predict(预测)来源做局部验证，避免在无水印处做 inpaint + 替换
                    # 解决问题：开头水印未出现/中间水印被遮挡期间，插值/预测位置可能不准，
                    # 直接 inpaint 会破坏画面，替换文字会"提前覆盖"在无水印位置。
                    # 验证只在 (模板尺寸+pad) 的小区域做 matchTemplate，单帧 <1ms，不影响性能。
                    if used_source in ("interp", "predict"):
                        verify_val, verify_rect = _verify_watermark_at_position(
                            frame, best_template, x, y, rw, rh,
                            threshold=match_threshold, pad=30)
                        if verify_val < match_threshold or verify_rect is None:
                            # 验证失败：水印不在该位置，跳过该帧（不 inpaint，不替换文字）
                            _write_frame(frame)
                            match_fail_count += 1
                            verify_fail_count += 1
                            # 重置运动状态（水印可能已消失，下次重新检测）
                            last_pos = None
                            last_velocity = (0, 0)
                            frame_idx += 1
                            if show_progress and frame_idx <= 30:
                                logger.debug("帧 %d: %s 位置验证失败(置信度 %.2f < %.2f)，跳过",
                                             frame_idx - 1, used_source, verify_val, match_threshold)
                            continue
                        # 验证通过：用验证后的精确位置（比插值/预测更准）
                        use_rect = verify_rect
                        x, y, rw, rh = use_rect
                        used_source = used_source + "_verified"

                    _t4 = _time.time()
                    processed = _inpaint_and_overlay(frame, x, y, rw, rh)
                    _t5 = _time.time()
                    _t_inpaint_total += (_t5 - _t4)
                    _write_frame(processed)
                    _t6 = _time.time()
                    _t_write_total += (_t6 - _t5)
                    removed_count += 1
                    if used_source.startswith("interp"):
                        interpolated_count += 1
                    if replace_img is not None or replace_text_img is not None:
                        replaced_count += 1
                    # 更新运动状态
                    if last_pos is not None:
                        new_vx = x - last_pos[0]
                        new_vy = y - last_pos[1]
                        # 速度平滑（指数移动平均）
                        smoothed_vx = int(last_velocity[0] * (1 - velocity_alpha) + new_vx * velocity_alpha)
                        smoothed_vy = int(last_velocity[1] * (1 - velocity_alpha) + new_vy * velocity_alpha)
                        last_velocity = (smoothed_vx, smoothed_vy)
                    last_pos = (x, y, rw, rh)
                else:
                    _t4 = _time.time()
                    _write_frame(frame)
                    _t5 = _time.time()
                    _t_write_total += (_t5 - _t4)
                    match_fail_count += 1
                    # 水印消失期间，重置运动状态（下次重新检测时不会因速度过大被拒）
                    last_pos = None
                    last_velocity = (0, 0)

                frame_idx += 1
                if show_progress and (frame_idx % 10 == 0 or frame_idx <= 5):
                    pct = (frame_idx / total * 100) if total > 0 else 0
                    logger.info("水印去除进度: %d/%d 帧 (%.1f%%), 已修复 %d 帧(插值 %d, 拒绝误匹配 %d, 位置验证失败 %d), 覆盖 %d 帧",
                                frame_idx, total, pct, removed_count, interpolated_count,
                                rejected_count, verify_fail_count, replaced_count)
                    if frame_idx % 30 == 0:
                        logger.info("  耗时分布: 读帧=%.1fs 匹配=%.1fs inpaint=%.1fs 写帧=%.1fs",
                                    _t_read_total, _t_match_total, _t_inpaint_total, _t_write_total)
    finally:
        cap.release()
        try:
            writer_proc.stdin.close()
        except Exception:
            pass
        try:
            writer_proc.wait(timeout=60)
        except Exception:
            writer_proc.kill()

    # 简化统计：匹配+预测 = 修复 - 插值
    match_or_predict = removed_count - interpolated_count
    logger.info("=" * 40)
    logger.info("阶段 3/3: 完成! 共 %d 帧, 修复 %d 帧(匹配/预测 %d + 插值 %d), "
                "拒绝误匹配 %d, 位置验证失败 %d, 覆盖 %d 帧, 未匹配 %d 帧",
                frame_idx, removed_count, match_or_predict,
                interpolated_count, rejected_count, verify_fail_count,
                replaced_count, match_fail_count)
    logger.info("耗时分布: 读帧=%.1fs 匹配=%.1fs inpaint=%.1fs 写帧=%.1fs",
                _t_read_total, _t_match_total, _t_inpaint_total, _t_write_total)
    return out_path if frame_idx > 0 else None


def _get_ffmpeg():
    """获取 ffmpeg 路径（用于抽帧/合成）。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg") or "ffmpeg"


def _get_ffprobe():
    import shutil
    p = shutil.which("ffprobe")
    if p:
        return p
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        cand = os.path.join(os.path.dirname(exe), "ffprobe.exe")
        if os.path.exists(cand):
            return cand
    except Exception:
        pass
    return None


def _probe_video(path):
    """探测视频基本信息 (w, h, fps, frame_count)。"""
    ffprobe = _get_ffprobe()
    if not ffprobe:
        return 1080, 1920, 30.0, 0
    import json
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,nb_frames,r_frame_rate",
             "-show_entries", "format=duration",
             "-of", "json", path],
            stderr=subprocess.STDOUT, timeout=30,
        ).decode("utf-8", "ignore")
        d = json.loads(out)
        s = d.get("streams", [{}])[0]
        w = int(s.get("width", 1080))
        h = int(s.get("height", 1920))
        # fps: r_frame_rate 形如 "30/1"
        rfr = s.get("r_frame_rate", "30/1")
        try:
            num, den = rfr.split("/")
            fps = float(num) / float(den) if float(den) else 30.0
        except Exception:
            fps = 30.0
        nb = int(s.get("nb_frames", 0))
        if nb <= 0:
            dur = float(d.get("format", {}).get("duration", 0))
            nb = int(dur * fps) if dur > 0 else 0
        return w, h, fps, nb
    except Exception as e:
        logger.warning("探测视频信息失败: %s", e)
        return 1080, 1920, 30.0, 0


def _detect_watermark_mask(gray, threshold=200, min_area=200, dilate=3):
    """从单帧灰度图检测亮色水印区域。

    返回: mask（与灰度图同尺寸的二值图，水印区域为 255）或 None
    """
    # 1. 亮度阈值：水印通常是亮色（白/浅色）
    _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)

    # 2. 形态学操作：开运算去噪点，闭运算填文字内部空洞
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    # 3. 连通域过滤：去掉过小区域（噪点）和过大区域（背景）
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n <= 1:
        return None

    mask = np.zeros_like(binary)
    img_area = gray.shape[0] * gray.shape[1]
    added = 0
    for i in range(1, n):  # 0 是背景
        a = stats[i, cv2.CC_STAT_AREA]
        # 过滤过小（噪点）和过大（半屏背景误检）
        if a < min_area or a > img_area * 0.3:
            continue
        mask[labels == i] = 255
        added += 1

    if added == 0:
        return None

    # 4. 膨胀：扩大修复区域，覆盖文字描边/阴影
    if dilate > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
        mask = cv2.dilate(mask, k, iterations=1)

    return mask


def _parse_region(region_str, frame_w, frame_h):
    """解析 "x,y,w,h" 或 "x%,y%,w%,h%" 格式的区域。"""
    if not region_str:
        return None
    parts = [p.strip() for p in region_str.split(",")]
    if len(parts) != 4:
        logger.warning("水印区域格式错误（应为 x,y,w,h）: %s", region_str)
        return None
    try:
        vals = []
        for p in parts:
            if p.endswith("%"):
                vals.append(float(p[:-1]) / 100.0)
            else:
                vals.append(float(p))
        x, y, w, h = vals
        # 百分比转像素
        if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
            x = int(x * frame_w)
            y = int(y * frame_h)
            w = int(w * frame_w)
            h = int(h * frame_h)
        else:
            x, y, w, h = int(x), int(y), int(w), int(h)
        # 边界保护
        x = max(0, min(x, frame_w - 2))
        y = max(0, min(y, frame_h - 2))
        w = max(10, min(w, frame_w - x))
        h = max(10, min(h, frame_h - y))
        return (x, y, w, h)
    except Exception as e:
        logger.warning("水印区域解析失败: %s", e)
        return None


def remove_watermark(video_path, out_path, region=None, threshold=200,
                     min_area=200, dilate=3, use_track=True, show_progress=True,
                     replace_image=None, replace_image_opacity=1.0,
                     replace_image_scale=1.0):
    """去除视频中的动态水印。

    参数:
        video_path: 输入视频
        out_path: 输出视频（无音频，需后续合成）
        region: 水印起始区域 (x, y, w, h)，None 则自动检测
        threshold: 亮度阈值（0-255），水印通常是亮色，>threshold 视为水印候选
        min_area: 水印最小像素面积，过滤小噪点
        dilate: 膨胀半径，扩大修复区域覆盖描边
        use_track: 是否用 CSRT 追踪器跟踪水印移动（True=追踪快，False=每帧独立检测）
        show_progress: 是否打印进度
        replace_image: 替换水印图片路径（PNG 带 alpha），去除原水印后在原位置叠加自己的图片
        replace_image_opacity: 图片不透明度 0.1-1.0（默认 1.0）
        replace_image_scale: 图片缩放系数（默认 1.0=适应原水印宽度）
    返回:
        out_path 成功 / None 失败
    """
    if not _HAS_CV2:
        logger.error("opencv-python 未安装，请运行: pip install opencv-python")
        return None
    if not os.path.exists(video_path):
        logger.error("视频不存在: %s", video_path)
        return None

    # 预加载替换水印图片（一次性，逐帧 overlay 零加载损耗）
    replace_img = _load_replace_image(replace_image, replace_image_opacity)

    w, h, fps, nb = _probe_video(video_path)
    logger.info("动态水印去除: %s  %sx%s  %.2ffps  %d帧",
                os.path.basename(video_path), w, h, fps, nb)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("无法打开视频: %s", video_path)
        return None

    # 用 ffmpeg 管道写入（支持 4K，OpenCV VideoWriter 的 mp4v 不支持 4K）
    ffmpeg = _get_ffmpeg()
    ffmpeg_cmd = [ffmpeg, "-y",
                  "-f", "rawvideo",
                  "-pix_fmt", "bgr24",
                  "-s", f"{w}x{h}",
                  "-r", str(fps),
                  "-i", "-",
                  "-c:v", "libx264",
                  "-preset", "ultrafast",
                  "-crf", "18",
                  "-pix_fmt", "yuv420p",
                  out_path]
    try:
        writer_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL)
    except Exception as e:
        logger.error("启动 ffmpeg 管道失败: %s", e)
        cap.release()
        return None
    if writer_proc.stdin is None:
        logger.error("ffmpeg 管道 stdin 不可用")
        writer_proc.kill()
        cap.release()
        return None

    def _write_frame(f):
        try:
            writer_proc.stdin.write(f.tobytes())
        except (BrokenPipeError, OSError):
            pass

    # === region=None 时：前几帧降采样自动检测水印位置，然后初始化追踪器 ===
    # 避免 4K 视频每帧全图扫描+全帧 inpaint（每帧 6-7 秒 → 降到 0.1-0.3 秒）
    detected_region = region
    if detected_region is None and use_track:
        detected_region = _auto_detect_watermark_region(
            cap, w, h, threshold=threshold, min_area=min_area,
            max_detect_frames=5, samples_per_frame=3)
        if detected_region is not None:
            logger.info("自动检测到水印区域: (%d, %d, %d, %d) 尺寸 %dx%d",
                        detected_region[0], detected_region[1],
                        detected_region[2], detected_region[3],
                        detected_region[2], detected_region[3])
            # 重置到第一帧，后续用追踪器处理
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        else:
            logger.warning("自动检测未找到水印，改用每帧独立检测（较慢）")

    # 追踪器（CSRT 精度高但慢）
    tracker = None
    if use_track and detected_region is not None:
        try:
            tracker = cv2.TrackerCSRT_create()
        except Exception:
            try:
                tracker = cv2.legacy.TrackerCSRT_create()
            except Exception:
                logger.warning("CSRT 追踪器不可用，改用每帧独立检测")
                tracker = None

    # 初始化追踪器（用第一帧 + 检测到的区域）
    if tracker is not None:
        ok, first_frame = cap.read()
        if not ok:
            logger.error("无法读取视频第一帧")
            cap.release()
            try:
                writer_proc.kill()
            except Exception:
                pass
            return None
        rx, ry, rw, rh = detected_region
        bbox = (rx, ry, rw, rh)
        ok = tracker.init(first_frame, bbox)
        if not ok:
            logger.warning("追踪器初始化失败，改用每帧独立检测")
            tracker = None
        else:
            # 处理第一帧
            processed = _process_frame_with_region(first_frame, detected_region, dilate)
            if replace_img is not None:
                rx, ry, rw, rh = detected_region
                processed = _overlay_image_on_frame(processed, replace_img, rx, ry, rw, rh, replace_image_scale)
            _write_frame(processed)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 1)

    # 逐帧处理
    frame_idx = 1 if tracker is not None else 0
    removed_count = 0
    detect_fail_count = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if tracker is not None:
                # 追踪模式：只对追踪区域做局部 inpaint（快）
                ok2, bbox = tracker.update(frame)
                if ok2:
                    rx, ry, rw, rh = [int(v) for v in bbox]
                    # 边界保护
                    rx = max(0, min(rx, w - 2))
                    ry = max(0, min(ry, h - 2))
                    rw = max(10, min(rw, w - rx))
                    rh = max(10, min(rh, h - ry))
                    processed = _process_frame_with_region(frame, (rx, ry, rw, rh), dilate)
                    if replace_img is not None:
                        processed = _overlay_image_on_frame(
                            processed, replace_img, rx, ry, rw, rh, replace_image_scale)
                    removed_count += 1
                else:
                    # 追踪失败，本帧不处理
                    processed = frame
                    detect_fail_count += 1
            else:
                # 每帧独立检测模式（fallback，4K 下较慢）
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                mask = _detect_watermark_mask(gray, threshold, min_area, dilate)
                if mask is not None:
                    # 优化：只对 mask 区域做局部 inpaint，避免全帧 inpaint
                    # 找到 mask 的 bounding box，只 crop 该区域
                    ys, xs = np.where(mask > 0)
                    if len(xs) > 0 and len(ys) > 0:
                        pad = max(dilate * 2, 10)
                        cx1 = max(0, int(xs.min()) - pad)
                        cy1 = max(0, int(ys.min()) - pad)
                        cx2 = min(w, int(xs.max()) + pad)
                        cy2 = min(h, int(ys.max()) + pad)
                        crop = frame[cy1:cy2, cx1:cx2]
                        crop_mask = mask[cy1:cy2, cx1:cx2]
                        inpainted_crop = cv2.inpaint(crop, crop_mask, 3, cv2.INPAINT_TELEA)
                        processed = frame.copy()
                        processed[cy1:cy2, cx1:cx2] = inpainted_crop
                        # 在 mask bounding box 位置叠加替换图片
                        if replace_img is not None:
                            mx = int(xs.min())
                            my = int(ys.min())
                            mw = int(xs.max()) - mx + 1
                            mh = int(ys.max()) - my + 1
                            processed = _overlay_image_on_frame(
                                processed, replace_img, mx, my, mw, mh, replace_image_scale)
                        removed_count += 1
                    else:
                        processed = cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
                        removed_count += 1
                else:
                    processed = frame

            _write_frame(processed)
            frame_idx += 1

            if show_progress and frame_idx % 30 == 0:
                pct = (frame_idx / nb * 100) if nb > 0 else 0
                logger.info("水印去除进度: %d/%d 帧 (%.1f%%), 已修复 %d 帧",
                            frame_idx, nb, pct, removed_count)
    finally:
        cap.release()
        try:
            writer_proc.stdin.close()
        except Exception:
            pass
        try:
            writer_proc.wait(timeout=60)
        except Exception:
            writer_proc.kill()

    if frame_idx == 0:
        logger.error("未处理任何帧")
        return None

    logger.info("水印去除完成: 共 %d 帧, 修复 %d 帧, 追踪/检测失败 %d 帧",
                frame_idx, removed_count, detect_fail_count)
    return out_path


def _auto_detect_watermark_region(cap, frame_w, frame_h, threshold=200,
                                   min_area=200, max_detect_frames=5,
                                   samples_per_frame=3):
    """降采样自动检测水印区域（用于颜色模式 region=None 时的快速初始化）。

    在前几帧中采样，缩放到 480 宽做水印检测，检测到则返回水印区域（原分辨率坐标）。
    避免 4K 视频每帧全图扫描的巨大开销。

    返回: (x, y, w, h) 或 None（未检测到）
    """
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        return None

    # 采样帧索引（前 1/3 区域均匀采样）
    sample_max = min(total, max_detect_frames * samples_per_frame)
    sample_step = max(1, total // (sample_max + 1))
    sample_indices = list(range(sample_step, min(total, sample_max * sample_step + 1), sample_step))[:max_detect_frames]

    scale = 480.0 / frame_w if frame_w > 480 else 1.0
    detect_w = int(frame_w * scale)
    detect_h = int(frame_h * scale)
    # 降采样后的最小面积阈值
    small_min_area = max(20, int(min_area * scale * scale))

    best_region = None
    best_area = 0

    for idx in sample_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        # 降采样
        if scale != 1.0:
            small = cv2.resize(frame, (detect_w, detect_h), interpolation=cv2.INTER_AREA)
        else:
            small = frame
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = _detect_watermark_mask(gray, threshold, small_min_area, dilate=2)
        if mask is None:
            continue
        # 找到 mask 的 bounding box
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            continue
        x1, y1 = int(xs.min() / scale), int(ys.min() / scale)
        x2, y2 = int(xs.max() / scale) + 1, int(ys.max() / scale) + 1
        rw = x2 - x1
        rh = y2 - y1
        # 过滤过大区域（可能是误检）
        if rw * rh > frame_w * frame_h * 0.3:
            continue
        area = rw * rh
        if area > best_area:
            best_area = area
            best_region = (x1, y1, rw, rh)

    # 重置到第一帧
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return best_region


def _process_frame_with_region(frame, region, dilate=3):
    """对单帧的指定区域做 inpaint 修复。"""
    x, y, w, h = region
    fh, fw = frame.shape[:2]
    # 边界保护
    x = max(0, min(x, fw - 2))
    y = max(0, min(y, fh - 2))
    w = max(2, min(w, fw - x))
    h = max(2, min(h, fh - y))

    mask = np.zeros((fh, fw), dtype=np.uint8)
    mask[y:y+h, x:x+w] = 255

    # 膨胀扩大修复区域
    if dilate > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
        mask = cv2.dilate(mask, k, iterations=1)

    return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)


def remove_watermark_with_audio(video_path, out_path, tmp_dir=None, use_ocr=False, **kwargs):
    """去除水印并保留原音频（用 ffmpeg 合成）。

    流程: 1. OpenCV 处理视频流（无音频） → 2. ffmpeg 把原视频音频合成回去

    参数:
        use_ocr: True 用 OCR 模式（remove_watermark_ocr），False 用颜色模式（remove_watermark）
        其余 kwargs 传给对应的处理函数
    """
    print(f"=== WM DEBUG: remove_watermark_with_audio called, _HAS_CV2={_HAS_CV2}, use_ocr={use_ocr} ===", flush=True)
    if not _HAS_CV2:
        logger.error("opencv-python 未安装，请运行: pip install opencv-python")
        return None

    tmp_dir = tmp_dir or os.path.dirname(out_path)
    os.makedirs(tmp_dir, exist_ok=True)

    # 1. OpenCV 处理视频流
    silent_path = os.path.join(tmp_dir, "_wm_removed_silent.mp4")
    if use_ocr:
        r = remove_watermark_ocr(video_path, silent_path, **kwargs)
    else:
        r = remove_watermark(video_path, silent_path, **kwargs)
    if not r or not os.path.exists(silent_path):
        logger.error("水印去除失败")
        return None

    # 2. ffmpeg 合成原音频
    ffmpeg = _get_ffmpeg()
    cmd = [ffmpeg, "-y",
           "-i", silent_path,           # 处理后的视频流（无音频）
           "-i", video_path,            # 原视频（取音频）
           "-map", "0:v:0", "-map", "1:a:0?",
           "-c:v", "copy",              # 视频流直接复制（已编码好）
           "-c:a", "aac", "-b:a", "192k",
           "-shortest",
           out_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                encoding="utf-8", errors="ignore")
        if result.returncode != 0:
            logger.error("合成音频失败:\n%s", result.stderr[-1500:])
            # 失败时返回无声视频作为兜底
            import shutil
            shutil.copy2(silent_path, out_path)
            logger.warning("已用无声视频兜底: %s", out_path)
    except subprocess.TimeoutExpired:
        logger.error("合成音频超时")
        import shutil
        shutil.copy2(silent_path, out_path)
    except Exception as e:
        logger.error("合成音频异常: %s", e)
        import shutil
        shutil.copy2(silent_path, out_path)
    finally:
        # 清理临时文件
        try:
            if os.path.exists(silent_path):
                os.remove(silent_path)
        except Exception:
            pass

    return out_path if os.path.exists(out_path) else None
