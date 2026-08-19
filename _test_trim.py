# -*- coding: utf-8 -*-
"""测试 _trim_tts_to_speech 是否误截断句间停顿。"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
os.environ["VOLCENGINE_APP_ID"] = "1378041831"
os.environ["VOLCENGINE_ACCESS_KEY"] = "tqPDiNto1D6hX0tbKH3i1rXM0TLbxvq_"

from core import tts, dedup_video
import subprocess, re

ff = dedup_video._get_ffmpeg()
tmp = tempfile.gettempdir()

# 用较长的文本（包含多个句子，会有句间停顿）
text = "家人们这个收纳神器真的绝了。它的头是硅胶头带颗粒的，可以把毛绒衣服上的毛毛都给你刮出来。比那种滚筒粘毛器好用太多了，真的超好用，可以讲是我用过最好的了。"

out = os.path.join(tmp, 'test_long_tts.mp3')
print(f"文本长度: {len(text)} 字")
print(f"文本内容: {text}")
print("生成 TTS...")

r = tts.generate_tts(text, out, voice="bd_dayi", provider="volcengine")
if not r:
    print("TTS 生成失败!")
    sys.exit(1)

raw_dur = tts.probe_audio_duration(out)
print(f"\n原始 TTS 时长: {raw_dur:.2f}s")

# 检测静音段
r2 = subprocess.run([ff, '-i', out, '-af',
                     'silencedetect=noise=-35dB:d=1.0', '-f', 'null', '-'],
                    capture_output=True, text=True, errors='ignore')
segments = []
cur_start = None
for line in r2.stderr.splitlines():
    m = re.search(r'silence_start: ([\d.]+)', line)
    if m:
        cur_start = float(m.group(1))
    m = re.search(r'silence_end: ([\d.]+)', line)
    if m and cur_start is not None:
        segments.append((cur_start, float(m.group(1))))
        cur_start = None
print(f"静音段 (>=1s, <-35dB): {segments}")

# 调用 _trim_tts_to_speech
trimmed = tts._trim_tts_to_speech(out, tmp)
trim_dur = tts.probe_audio_duration(trimmed)
print(f"\n截断后 TTS 时长: {trim_dur:.2f}s")
print(f"是否截断: {trim_dur < raw_dur - 0.5}")
if trim_dur < raw_dur - 0.5:
    print(f"  截断了 {raw_dur - trim_dur:.2f}s")
    # 检查截断是否正确（尾部静音 vs 句间停顿）
    # 找尾部静音段
    trailing = [s for s in segments if s[1] >= raw_dur - 0.5]
    if trailing:
        print(f"  尾部静音段: {trailing}")
        print(f"  截断位置应该在 {trailing[0][0]:.2f}s 附近")
    else:
        print(f"  警告: 没有尾部静音段，但被截断了! 这是误截断!")
        mid_silence = [s for s in segments if s[1] < raw_dur - 0.5]
        print(f"  句间停顿段: {mid_silence}")
        print(f"  可能误截断在 {mid_silence[-1] if mid_silence else '?'}")

# 用 whisper 识别截断前后的内容
import whisper
model = whisper.load_model('small')
r_before = model.transcribe(out, language='zh')
r_after = model.transcribe(trimmed, language='zh')
print(f"\n截断前 whisper: {r_before['text']}")
print(f"截断后 whisper: {r_after['text']}")
print(f"\n内容是否完整: {len(r_after['text']) >= len(r_before['text']) * 0.8}")

# 清理
for p in [out, trimmed]:
    if p and os.path.exists(p):
        try: os.remove(p)
        except OSError: pass
