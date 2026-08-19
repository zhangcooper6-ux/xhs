# -*- coding: utf-8 -*-
"""端到端测试 apply_voiceover(fit_duration=True)——web_ui 默认路径。"""
import os, sys, subprocess, re, tempfile
sys.path.insert(0, os.path.dirname(__file__))
os.environ["VOLCENGINE_APP_ID"] = "1378041831"
os.environ["VOLCENGINE_ACCESS_KEY"] = "tqPDiNto1D6hX0tbKH3i1rXM0TLbxvq_"

from core import tts, dedup_video

ff = dedup_video._get_ffmpeg()
video = r'd:\xhs\output\dedup\6a75cc67000000003301bd43\video_dedup.mp4'
text = "家人们这个收纳神器真的绝了。它的头是硅胶头带颗粒的，可以把毛绒衣服上的毛毛都给你刮出来。比那种滚筒粘毛器好用太多了，真的超好用。"

print(f"video duration: {dedup_video.probe_duration(video):.2f}s")
print(f"text: {text}")

out = os.path.join(tempfile.gettempdir(), 'test_fit_true.mp4')
tmp_dir = os.path.join(tempfile.gettempdir(), 'fit_true_tmp')
os.makedirs(tmp_dir, exist_ok=True)

print(f"\n--- 调用 apply_voiceover (fit_duration=True) ---")
result = tts.apply_voiceover(
    video_path=video,
    text=text,
    out_path=out,
    voice="bd_dayi",
    provider="volcengine",
    fit_duration=True,  # web_ui 默认
    tmp_dir=tmp_dir,
    keep_bg_audio=False,
)
print(f"result: {result}")
if result:
    out_dur = dedup_video.probe_duration(out)
    print(f"output duration: {out_dur:.2f}s")

    # 检测静音段
    r = subprocess.run([ff, '-i', out, '-af',
                        'silencedetect=noise=-35dB:d=1.0', '-f', 'null', '-'],
                       capture_output=True, text=True, errors='ignore')
    starts, ends = [], []
    for line in r.stderr.splitlines():
        m = re.search(r'silence_start: ([\d.]+)', line)
        if m: starts.append(float(m.group(1)))
        m = re.search(r'silence_end: ([\d.]+)', line)
        if m: ends.append(float(m.group(1)))
    print(f"silence starts: {starts}")
    print(f"silence ends: {ends}")

    # 分段 whisper 识别
    seg1 = os.path.join(tempfile.gettempdir(), 'ft_seg1.m4a')
    seg2 = os.path.join(tempfile.gettempdir(), 'ft_seg2.m4a')
    seg3 = os.path.join(tempfile.gettempdir(), 'ft_seg3.m4a')
    subprocess.run([ff, '-y', '-i', out, '-ss', '0', '-t', '10', '-vn', '-c:a', 'copy', seg1],
                   capture_output=True, check=True)
    subprocess.run([ff, '-y', '-i', out, '-ss', '10', '-t', '10', '-vn', '-c:a', 'copy', seg2],
                   capture_output=True, check=True)
    subprocess.run([ff, '-y', '-i', out, '-ss', '20', '-t', '10', '-vn', '-c:a', 'copy', seg3],
                   capture_output=True, check=True)

    import whisper
    model = whisper.load_model('small')
    r1 = model.transcribe(seg1, language='zh')
    r2 = model.transcribe(seg2, language='zh')
    r3 = model.transcribe(seg3, language='zh')
    print(f"\nseg1(0-10s)   text: {r1['text']}")
    print(f"seg2(10-20s)  text: {r2['text']}")
    print(f"seg3(20-30s)  text: {r3['text']}")

    # 检查是否有循环：seg2/seg3 内容不应与 seg1 相同
    is_loop = (r2['text'].strip() and r2['text'] in r1['text']) or \
              (r3['text'].strip() and r3['text'] in r1['text'])
    print(f"\n是否有循环: {is_loop}")
    if not is_loop:
        print("✓ 修复有效！TTS 内容播放完后是静音，没有循环。")
    else:
        print("✗ 仍有循环！需要进一步排查。")

    # 清理
    for p in [out, seg1, seg2, seg3]:
        try: os.remove(p)
        except OSError: pass

import shutil
try:
    shutil.rmtree(tmp_dir)
except Exception:
    pass
