# -*- coding: utf-8 -*-
"""端到端测试 TTS 配音流程：在真实视频上替换音频。"""
import os
import sys
import logging

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

from core import tts
from core import dedup_video

# 找一个已存在的视频文件
test_video = r"output\dedup\6a2124b40000000006031f91\video_dedup.mp4"
if not os.path.exists(test_video):
    print(f"测试视频不存在: {test_video}")
    sys.exit(1)

# 探测视频信息
w, h = dedup_video.probe_resolution(test_video)
dur = dedup_video.probe_duration(test_video)
print(f"测试视频: {test_video}")
print(f"  分辨率: {w}x{h}  时长: {dur:.2f}s")

# 测试 1: 默认配音（晓晓）完全替换原音频
print()
print("=" * 60)
print("测试 1: 晓晓配音 + 完全替换原音频")
print("=" * 60)
out1 = os.path.join("output", "_test_voice_replace.mp4")
test_text = "大家好，今天给大家分享一款超好用的厨房好物，沥水篮二合一，能洗水果蔬菜，还能淘米，厨房有一个真的太方便了。"
r = tts.apply_voiceover(
    video_path=test_video,
    text=test_text,
    out_path=out1,
    voice="xiaoxiao",
    keep_bg_audio=False,
)
if r and os.path.exists(r):
    w2, h2 = dedup_video.probe_resolution(r)
    d2 = dedup_video.probe_duration(r)
    print(f"  ✅ 成功: {r}")
    print(f"     分辨率: {w2}x{h2}  时长: {d2:.2f}s  大小: {os.path.getsize(r)} bytes")
else:
    print(f"  ❌ 失败")

# 测试 2: 云健配音 + 保留BGM
print()
print("=" * 60)
print("测试 2: 云健配音 + 保留原音频BGM(15%)")
print("=" * 60)
out2 = os.path.join("output", "_test_voice_mix.mp4")
r = tts.apply_voiceover(
    video_path=test_video,
    text=test_text,
    out_path=out2,
    voice="yunjian",
    keep_bg_audio=True,
    bg_volume=0.15,
)
if r and os.path.exists(r):
    print(f"  ✅ 成功: {r}  大小: {os.path.getsize(r)} bytes")
else:
    print(f"  ❌ 失败")

# 测试 3: 语速调整
print()
print("=" * 60)
print("测试 3: 晓伊配音 + 语速+15%")
print("=" * 60)
out3 = os.path.join("output", "_test_voice_fast.mp4")
r = tts.apply_voiceover(
    video_path=test_video,
    text=test_text,
    out_path=out3,
    voice="xiaoyi",
    rate="+15%",
)
if r and os.path.exists(r):
    print(f"  ✅ 成功: {r}  大小: {os.path.getsize(r)} bytes")
else:
    print(f"  ❌ 失败")

print()
print("=" * 60)
print("测试完成！请播放以下文件检查配音自然度：")
print(f"  替换音频: {out1}")
print(f"  混合BGM:  {out2}")
print(f"  快语速:   {out3}")
print("=" * 60)
