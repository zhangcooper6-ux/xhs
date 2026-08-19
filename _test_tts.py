# -*- coding: utf-8 -*-
"""测试 TTS 模块：文本清理 + 配音生成 + 列表显示。"""
import os
import sys
import logging

# 设置 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8")
# 启用日志，查看 TTS 生成的详细错误
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

from core import tts

print("=" * 60)
print("1. 测试文本清理（提升自然度）")
print("=" * 60)
test_cases = [
    "#美食 探店啦！超好吃的蛋糕🍰 https://x.com/abc",
    "今天分享一个超好用的护肤品！！！真的绝了 #护肤 #好物分享 @小美",
    "这款面膜补水效果棒棒哒😊😊😊，强烈推荐！！！",
    "  多余空白  折叠  ",
    "无结尾标点的句子",
]
for t in test_cases:
    cleaned = tts.clean_text_for_tts(t)
    print(f"  原文: {t}")
    print(f"  清理: {cleaned}")
    print()

print("=" * 60)
print("2. 测试配音列表（26个）")
print("=" * 60)
print(tts.print_voices())

print()
print("=" * 60)
print("3. 测试 voice 解析")
print("=" * 60)
for v in ["xiaoxiao", "yunjian", "晓晓", "zh-CN-XiaoxiaoNeural", "unknown"]:
    resolved = tts.resolve_voice(v)
    print(f"  {v:<25} -> {resolved}")

print()
print("=" * 60)
print("4. 测试 edge-tts 是否可用 + 生成一句配音")
print("=" * 60)
if not tts._HAS_EDGE_TTS:
    print("  edge-tts 未安装，跳过生成测试")
    print("  安装命令: pip install edge-tts")
else:
    print(f"  edge-tts 已安装，尝试生成配音...")
    out = os.path.join("output", "_test_voice.mp3")
    os.makedirs("output", exist_ok=True)
    test_text = "大家好，今天给大家分享一款超好用的护肤好物，补水保湿效果真的非常棒。"
    r = tts.generate_tts(test_text, out, voice="xiaoxiao")
    if r and os.path.exists(r):
        size = os.path.getsize(r)
        dur = tts.probe_audio_duration(r)
        print(f"  ✅ 配音生成成功: {r}")
        print(f"     大小: {size} bytes, 时长: {dur:.2f}s")
    else:
        print(f"  ❌ 配音生成失败")

print()
print("测试完成")
