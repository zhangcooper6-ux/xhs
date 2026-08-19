# -*- coding: utf-8 -*-
"""详细调试 apply_voiceover(fit_duration=True) 的每个环节。"""
import os, sys, tempfile, subprocess, re, logging
sys.path.insert(0, os.path.dirname(__file__))
os.environ["VOLCENGINE_APP_ID"] = "1378041831"
os.environ["VOLCENGINE_ACCESS_KEY"] = "tqPDiNto1D6hX0tbKH3i1rXM0TLbxvq_"

# 开启详细日志
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s [%(name)s] %(message)s')

from core import tts, dedup_video

ff = dedup_video._get_ffmpeg()
video = r'd:\xhs\output\dedup\6a75cc67000000003301bd43\video_dedup.mp4'
text = "家人们这个收纳神器真的绝了。它的头是硅胶头带颗粒的，可以把毛绒衣服上的毛毛都给你刮出来。比那种滚筒粘毛器好用太多了，真的超好用。"
tmp_dir = os.path.join(tempfile.gettempdir(), 'debug_flow_tmp')
os.makedirs(tmp_dir, exist_ok=True)

# 检查原视频流信息
print("=== 原视频流信息 ===")
ffprobe = dedup_video._get_ffprobe()
r = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', video],
                   capture_output=True, text=True, errors='ignore')
import json
streams = json.loads(r.stdout).get('streams', [])
for s in streams:
    print(f"  stream {s.get('index')}: {s.get('codec_type')} {s.get('codec_name')} "
          f"duration={s.get('duration')}")

# 手动执行 apply_voiceover 的每个步骤
print("\n=== 步骤1: 生成 TTS ===")
tts_path = os.path.join(tmp_dir, "voiceover_raw.mp3")
tts.generate_tts(text, tts_path, voice="bd_dayi", provider="volcengine")
tts_dur = tts.probe_audio_duration(tts_path)
print(f"TTS 时长: {tts_dur:.2f}s, 文件: {tts_path}")

print("\n=== 步骤2: _trim_tts_to_speech ===")
tts_path = tts._trim_tts_to_speech(tts_path, tmp_dir)
tts_dur = tts.probe_audio_duration(tts_path)
print(f"截断后 TTS 时长: {tts_dur:.2f}s, 文件: {tts_path}")

print("\n=== 步骤3: fit_audio_to_duration ===")
video_dur = dedup_video.probe_duration(video)
print(f"视频时长: {video_dur:.2f}s")
ratio = tts_dur / video_dur if video_dur > 0 else 0
print(f"ratio = {tts_dur:.2f} / {video_dur:.2f} = {ratio:.3f}")
if ratio < 0.3:
    print(f"ratio < 0.3, 调用 _pad_audio_with_silence")
    fitted_path = os.path.join(tmp_dir, "voiceover_fitted.mp3")
    fitted = tts._pad_audio_with_silence(tts_path, video_dur, fitted_path)
    fitted_dur = tts.probe_audio_duration(fitted)
    print(f"填充后音频时长: {fitted_dur:.2f}s, 文件: {fitted}")
    print(f"是否使用了新文件: {fitted != tts_path}")

    # 检查填充后音频的静音段
    r = subprocess.run([ff, '-i', fitted, '-af',
                        'silencedetect=noise=-35dB:d=1.0', '-f', 'null', '-'],
                       capture_output=True, text=True, errors='ignore')
    starts = []
    for line in r.stderr.splitlines():
        m = re.search(r'silence_start: ([\d.]+)', line)
        if m: starts.append(float(m.group(1)))
    print(f"填充后音频静音段 starts: {starts}")
else:
    print(f"ratio >= 0.3, 调用 fit_audio_to_duration")
    fitted_path = os.path.join(tmp_dir, "voiceover_fitted.mp3")
    fitted = tts.fit_audio_to_duration(tts_path, video_dur, fitted_path)
    fitted_dur = tts.probe_audio_duration(fitted)
    print(f"调整后音频时长: {fitted_dur:.2f}s")

print("\n=== 步骤4: replace_video_audio ===")
out = os.path.join(tempfile.gettempdir(), 'debug_out.mp4')
result = tts.replace_video_audio(video, fitted, out, keep_bg_audio=False)
print(f"replace_video_audio 结果: {result}")
if result:
    out_dur = dedup_video.probe_duration(out)
    print(f"输出视频时长: {out_dur:.2f}s")

    # 检查输出音轨静音段
    r = subprocess.run([ff, '-i', out, '-af',
                        'silencedetect=noise=-35dB:d=1.0', '-f', 'null', '-'],
                       capture_output=True, text=True, errors='ignore')
    starts = []
    for line in r.stderr.splitlines():
        m = re.search(r'silence_start: ([\d.]+)', line)
        if m: starts.append(float(m.group(1)))
    print(f"输出音轨静音段 starts: {starts}")

    # 检查输出流信息
    r = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', out],
                       capture_output=True, text=True, errors='ignore')
    streams = json.loads(r.stdout).get('streams', [])
    for s in streams:
        print(f"  output stream {s.get('index')}: {s.get('codec_type')} {s.get('codec_name')} "
              f"duration={s.get('duration')}")

    # whisper 识别
    import whisper
    model = whisper.load_model('small')
    seg1 = os.path.join(tempfile.gettempdir(), 'db_seg1.m4a')
    subprocess.run([ff, '-y', '-i', out, '-ss', '0', '-t', '10', '-vn', '-c:a', 'copy', seg1],
                   capture_output=True, check=True)
    r1 = model.transcribe(seg1, language='zh')
    print(f"seg1(0-10s) whisper: {r1['text']}")

    # 清理
    for p in [out, seg1]:
        try: os.remove(p)
        except OSError: pass

# 清理
import shutil
try:
    shutil.rmtree(tmp_dir)
except Exception:
    pass
