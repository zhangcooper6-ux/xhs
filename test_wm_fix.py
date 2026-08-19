# -*- coding: utf-8 -*-
"""快速验证水印去除修复: 只跑去水印+替换文字,检查开头帧"""
import os
import sys
import time
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import watermark_remove as wm

VIDEO = r"C:\Users\ALIENWARE\Downloads\有娃的家庭真得被上这个~ 就是这个#磁吸沥水架.mp4"
OUT = r"D:\xhs\output\test_wm_fix.mp4"
REPLACE_TEXT = "@我们仨fnk"

# 预采样
print("预采样 OCR...")
precomputed = wm.sample_frames_ocr_combined(
    VIDEO, ocr_samples=15, wm_text=None, replace_text=REPLACE_TEXT, subtitle_area=None)
print(f"识别水印 {len(precomputed.get('watermark_positions', []))} 次")

# 去水印(含替换文字)
print(f"\n开始去水印(含替换文字 '{REPLACE_TEXT}')...")
t0 = time.time()
result = wm.remove_watermark_ocr(
    VIDEO, OUT, wm_text=None, ocr_samples=15,
    replace_text=REPLACE_TEXT, precomputed=precomputed, show_progress=True)
elapsed = time.time() - t0
print(f"\n去水印完成: {result}")
print(f"耗时: {elapsed:.1f}s")

# 抽帧检查开头
print("\n抽帧检查开头 30 帧...")
cap = cv2.VideoCapture(OUT)
os.makedirs("verify_frames", exist_ok=True)
for idx in [0, 3, 5, 6, 11, 12, 15, 20, 25, 27, 30]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    if ok:
        cv2.imwrite(f"verify_frames/out_{idx:03d}.jpg", frame)
        print(f"  帧 {idx}: 已保存 verify_frames/out_{idx:03d}.jpg")
cap.release()
print("\n完成,请查看 verify_frames/ 下的图片确认开头无提前覆盖")
