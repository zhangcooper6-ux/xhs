# -*- coding: utf-8 -*-
"""调试：检查原视频音轨和 TTS 实际内容。"""
import os, sys, tempfile, subprocess
sys.path.insert(0, os.path.dirname(__file__))
os.environ["VOLCENGINE_APP_ID"] = "1378041831"
os.environ["VOLCENGINE_ACCESS_KEY"] = "tqPDiNto1D6hX0tbKH3i1rXM0TLbxvq_"
from core import tts, dedup_video
import whisper

ff = dedup_video._get_ffmpeg()
video = r'd:\xhs\output\dedup\6a75cc67000000003301bd43\video_dedup.mp4'
tmp = tempfile.gettempdir()

# 1. 检查原视频音轨
print('=== 原视频(video_dedup.mp4)音轨 ===')
orig_audio = os.path.join(tmp, 'orig_audio.m4a')
subprocess.run([ff, '-y', '-i', video, '-vn', '-c:a', 'copy', orig_audio],
               capture_output=True, check=True)
model = whisper.load_model('small')
r = model.transcribe(orig_audio, language='zh')
print('原视频音轨 whisper:', r['text'][:200])
os.remove(orig_audio)

# 2. 生成 TTS 并检查内容
print()
print('=== TTS 生成 ===')
text = '家人们这个收纳神器真的绝了。它的头是硅胶头带颗粒的，可以把毛绒衣服上的毛毛都给你刮出来。比那种滚筒粘毛器好用太多了，真的超好用。'
tts_path = os.path.join(tmp, 'tts_check.mp3')
tts.generate_tts(text, tts_path, voice='bd_dayi', provider='volcengine')
print('TTS 时长:', tts.probe_audio_duration(tts_path))
r2 = model.transcribe(tts_path, language='zh')
print('TTS whisper:', r2['text'])
os.remove(tts_path)
