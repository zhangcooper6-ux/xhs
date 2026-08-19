# -*- coding: utf-8 -*-
"""诊断水印去除问题: 分析开头几帧的水印检测情况"""
import os
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import watermark_remove as wm

VIDEO = r"C:\Users\ALIENWARE\Downloads\有娃的家庭真得被上这个~ 就是这个#磁吸沥水架.mp4"
WM_TEXT = None
REPLACE_TEXT = "@我们仨fnk"

# 1. 采样 OCR 识别水印
print("=" * 60)
print("阶段1: 采样 OCR 识别水印")
print("=" * 60)
precomputed = wm.sample_frames_ocr_combined(
    VIDEO, ocr_samples=15, wm_text=WM_TEXT, replace_text=REPLACE_TEXT,
    subtitle_area=None)

watermark_positions = precomputed.get("watermark_positions", [])
best_template = precomputed.get("best_template")
sample_indices = precomputed.get("sample_indices", [])

print(f"\n采样帧索引: {sample_indices}")
print(f"识别到水印 {len(watermark_positions)} 次:")
for p in watermark_positions:
    print(f"  帧 {p[0]}: 位置=({p[1]},{p[2]}) 尺寸=({p[3]}x{p[4]})")

if best_template is not None:
    print(f"模板尺寸: {best_template.shape[1]}x{best_template.shape[0]}")

# 2. 计算搜索区域
w, h, fps, nb = wm._probe_video(VIDEO)
pos_only = [(p[1], p[2], p[3], p[4]) for p in watermark_positions]
search_region = wm._compute_search_region(pos_only, w, h, expand=2.0)
print(f"\n搜索区域: {search_region} (画面 {w}x{h})")

# 3. 逐帧分析开头 30 帧的水印检测
print("\n" + "=" * 60)
print("阶段2: 分析开头 60 帧的水印检测(含验证)")
print("=" * 60)
cap = cv2.VideoCapture(VIDEO)
last_pos = None
last_velocity = (0, 0)
max_jump = 80
velocity_alpha = 0.5
for frame_idx in range(0, 60, 1):
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok:
        continue

    # 模板匹配
    matched = wm._match_template_in_frame(frame, best_template, search_region, 0.5)

    # 模拟主循环逻辑
    use_rect = None
    used_source = None
    if matched is not None:
        mx, my, mrw, mrh = matched
        if last_pos is not None:
            lx, ly, _, _ = last_pos
            jump_dist = ((mx - lx) ** 2 + (my - ly) ** 2) ** 0.5
            if jump_dist > max_jump:
                pvx, pvy = last_velocity
                px = int(lx + pvx)
                py = int(ly + pvy)
                use_rect = (px, py, last_pos[2], last_pos[3])
                used_source = "predict"
            else:
                use_rect = matched
                used_source = "match"
        else:
            use_rect = matched
            used_source = "match"
    else:
        if last_pos is not None:
            lx, ly, lw, lh = last_pos
            pvx, pvy = last_velocity
            px = int(lx + pvx)
            py = int(ly + pvy)
            use_rect = (px, py, lw, lh)
            used_source = "predict"
        else:
            interp = wm._interpolate_position(watermark_positions, frame_idx, w, h)
            if interp is not None:
                use_rect = interp
                used_source = "interp"

    # 模拟验证
    verify_val = None
    action = "处理"
    if use_rect is not None and used_source in ("interp", "predict"):
        x, y, rw, rh = use_rect
        verify_val, verify_rect = wm._verify_watermark_at_position(
            frame, best_template, x, y, rw, rh, threshold=0.5, pad=30)
        if verify_val < 0.5 or verify_rect is None:
            action = "跳过(验证失败,水印不在该位置)"
            use_rect = None
        else:
            action = f"处理(验证通过 {verify_val:.2f})"
            use_rect = verify_rect

    if use_rect is None and used_source not in ("interp", "predict"):
        if matched is None and last_pos is None:
            interp = wm._interpolate_position(watermark_positions, frame_idx, w, h)
            if interp is None:
                action = "跳过(无插值)"

    # 更新运动状态
    if use_rect is not None:
        x, y, rw, rh = use_rect
        if last_pos is not None:
            new_vx = x - last_pos[0]
            new_vy = y - last_pos[1]
            smoothed_vx = int(last_velocity[0] * (1 - velocity_alpha) + new_vx * velocity_alpha)
            smoothed_vy = int(last_velocity[1] * (1 - velocity_alpha) + new_vy * velocity_alpha)
            last_velocity = (smoothed_vx, smoothed_vy)
        last_pos = (x, y, rw, rh)
    else:
        last_pos = None
        last_velocity = (0, 0)

    match_str = f"匹配={matched}" if matched else "匹配=None"
    print(f"帧 {frame_idx:3d}: {match_str}  来源={used_source}  验证={verify_val if verify_val is not None else 'N/A'}  → {action}")

cap.release()

# 4. 抽几帧保存,人工查看水印位置
print("\n" + "=" * 60)
print("阶段3: 保存开头帧查看水印实际位置")
print("=" * 60)
os.makedirs("debug_frames", exist_ok=True)
cap = cv2.VideoCapture(VIDEO)
for frame_idx in [0, 3, 6, 9, 12, 15, 20, 25, 30]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok:
        continue
    # 标记模板匹配位置和插值位置
    matched = wm._match_template_in_frame(frame, best_template, search_region, 0.5)
    interp = wm._interpolate_position(watermark_positions, frame_idx, w, h)
    annotated = frame.copy()
    if matched:
        x, y, rw, rh = matched
        cv2.rectangle(annotated, (x, y), (x + rw, y + rh), (0, 255, 0), 3)
        cv2.putText(annotated, "match", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
    if interp:
        x, y, rw, rh = interp
        cv2.rectangle(annotated, (x, y), (x + rw, y + rh), (0, 0, 255), 3)
        cv2.putText(annotated, "interp", (x, y + rh + 30), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
    # 标记搜索区域
    if search_region:
        sx, sy, sw, sh = search_region
        cv2.rectangle(annotated, (sx, sy), (sx + sw, sy + sh), (255, 255, 0), 2)
    cv2.imwrite(f"debug_frames/diag_{frame_idx:03d}.jpg", annotated)
    print(f"帧 {frame_idx}: 已保存 debug_frames/diag_{frame_idx:03d}.jpg (绿=匹配,红=插值,黄=搜索区)")
cap.release()
print("\n完成")
