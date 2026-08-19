# -*- coding: utf-8 -*-
"""背景音乐（BGM）模块。

功能：
  1. 扫描 assets/bgm/ 目录下的 BGM 文件
  2. 按文件名风格自动匹配（--bgm auto）
  3. 用 ffmpeg 混合 BGM 到视频（支持循环、淡入淡出、与配音共存）

文件名规范：
  风格_曲名.mp3  例如：治愈_暖阳.mp3 / 欢快_蜜糖.mp3
  工具通过文件名前缀（风格）做自动匹配

BGM 来源：
  工具不内置音乐文件（版权原因），用户需自行下载放到 assets/bgm/
  下方 BGM_RECOMMENDATIONS 提供小红书热门 BGM 推荐清单（按风格分类）
"""
import os
import re
import json
import logging
import subprocess

import config

logger = logging.getLogger("bgm")


# ===== 小红书热门 BGM 推荐清单 =====
# 按风格分类，每类列出热门曲目（用户可自行搜索下载）
# 文件名建议保存为：风格_曲名.mp3 （如 治愈_起风了.mp3）
BGM_RECOMMENDATIONS = {
    "治愈": [
        "起风了", "晚安", "云烟成雨", "光年之外", "大鱼",
        "世间美好与你环环相扣", "星辰大海", "白月光与朱砂痣",
    ],
    "欢快": [
        "蜜雪冰城甜蜜蜜", "你笑起来真好看", "卡路里", "热爱105°C的你",
        "学猫叫", "沙漠骆驼", "芒种",
    ],
    "情感": [
        "孤勇者", "漠河舞厅", "错位时空", "可可托海的牧羊人",
        "你的答案", "飞云之下", "岁月神偷",
    ],
    "古风": [
        "烟雨行舟", "红昭愿", "芒种", "锦鲤抄", "琵琶行",
        "赤伶", "游山恋", "踏山河",
    ],
    "Vlog": [
        "Sunburst", "Love Story", "Sunday Best", "Perfect",
        "Photograph", "Lemon Tree", "小幸运",
    ],
    "氛围": [
        "星空", "River Flows in You", "卡农", "天空之城",
        "风居住的街道", "神秘园之歌",
    ],
    "美食": [
        "舌尖上的中国", "小尖尖", "甜甜的", "确认过眼神",
        "烧肉粽", "吃货进行曲",
    ],
    "旅行": [
        "蓝莲花", "去大理", "旅行的意义", "曾经的你",
        "平凡之路", "夜空中最亮的星",
    ],
    "美妆": [
        "Try", "Girls", "问题先生", "Dance Monkey",
        "Bad Guy", "亮色系",
    ],
    "卡点": [
        "The Spectre", "Alone", "Faded", "The Race",
        "Pop Stars", "Stronger",
    ],
    "搞笑": [
        "鸡你太美", "砂锅粥", "改革春风吹满地", "好嗨哟",
        "我的滑板鞋", "PPAP",
    ],
    "动感": [
        "Despacito", "Shape of You", "Uptown Funk", "Havana",
        "Señorita", "Sugar",
    ],
    "浪漫": [
        "告白气球", "甜蜜蜜", "月亮代表我的心", "最浪漫的事",
        "因为爱情", "慢慢喜欢你",
    ],
    "励志": [
        "相信自己", "追梦赤子心", "海阔天空", "光辉岁月",
        "不再犹豫", "骄傲的少年",
    ],
    "民谣": [
        "成都", "理想三旬", "南山南", "董小姐",
        "斑马斑马", "傲寒",
    ],
    "电子": [
        "Lean On", "Waves", "Don't You Worry Child",
        "Wake Me Up", "Animals",
    ],
    "说唱": [
        "不用去猜", "飘向北方", "Time", "Lifecycle",
        "我真的需要你",
    ],
}


# ===== BGM 风格 → 关键词映射（用于 --bgm auto 自动匹配）=====
# 笔记标签/标题命中关键词 → 选对应风格的 BGM
BGM_KEYWORDS = {
    "治愈": ["治愈", "暖心", "温柔", "安静", "深夜", "emo", "孤独",
             "陪伴", "感动", "泪目", "温暖"],
    "欢快": ["欢快", "快乐", "开心", "元气", "活力", "阳光", "少女",
             "可爱", "甜", "夏日", "元气满满"],
    "情感": ["情感", "爱情", "失恋", "分手", "思念", "回忆", "故事",
             "遗憾", "心动", "暗恋", "emo"],
    "古风": ["古风", "古韵", "汉服", "国风", "古典", "诗词", "江湖",
             "武侠", "古装"],
    "Vlog": ["vlog", "日常", "记录", "生活", "分享", "一天",
             "大学生", "上班族"],
    "氛围": ["氛围", "质感", "高级感", "大片", "电影感", "意境",
             "空灵", "深邃"],
    "美食": ["美食", "探店", "做饭", "烹饪", "食谱", "早餐", "午餐",
             "晚餐", "甜品", "咖啡", "烘焙", "好吃", "吃货"],
    "旅行": ["旅行", "旅游", "vlog", "打卡", "攻略", "风景", "游记",
             "民宿", "酒店", "出行", "自驾", "徒步", "远方"],
    "美妆": ["美妆", "化妆", "口红", "粉底", "护肤", "穿搭", "ootd",
             "时尚", "少女", "颜值"],
    "卡点": ["卡点", "踩点", "节奏", "混剪", "转场", "快剪",
             "变装", "换装"],
    "搞笑": ["搞笑", "段子", "沙雕", "笑死", "哈哈", "整活", "鬼畜",
             "反转", "吐槽", "东北", "老铁"],
    "动感": ["动感", "舞蹈", "健身", "运动", "燃脂", "燃", "热血",
             "蹦迪", "派对"],
    "浪漫": ["浪漫", "婚礼", "求婚", "情侣", "恋爱", "甜蜜",
             "告白", "纪念日", "七夕"],
    "励志": ["励志", "奋斗", "拼搏", "逆袭", "成长", "坚持",
             "梦想", "努力", "不放弃"],
    "民谣": ["民谣", "吉他", "弹唱", "原创", "独立", "文艺",
             "小众", "诗意"],
    "电子": ["电子", "电音", "edm", "舞曲", "夜店", "dj",
             "remix", "混音"],
    "说唱": ["说唱", "rap", "嘻哈", "hiphop", "flow", "freestyle",
             "battle", "diss"],
}


def scan_bgm_dir(bgm_dir=None):
    """扫描 BGM 目录，返回 [(style, name, filename, full_path), ...]。

    文件名规范：风格_曲名.mp3
      - 风格取自下划线前部分（如 "治愈_起风了.mp3" → 风格="治愈"）
      - 曲名取自下划线后部分（去掉扩展名）
      - 无下划线则风格="未分类"，曲名=文件名（去扩展名）

    支持的扩展名：.mp3 .wav .m4a .aac .ogg .flac
    """
    bgm_dir = bgm_dir or config.BGM.get("dir", os.path.join(config.ASSETS_DIR, "bgm"))
    if not os.path.isdir(bgm_dir):
        return []

    results = []
    exts = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
    for fn in sorted(os.listdir(bgm_dir)):
        low = fn.lower()
        if not low.endswith(exts):
            continue
        full = os.path.join(bgm_dir, fn)
        if not os.path.isfile(full):
            continue
        # 解析风格_曲名
        base = os.path.splitext(fn)[0]
        if "_" in base:
            style, name = base.split("_", 1)
        elif "—" in base:
            style, name = base.split("—", 1)
        elif "-" in base:
            style, name = base.split("-", 1)
        else:
            style, name = "未分类", base
        results.append((style.strip(), name.strip(), fn, full))
    return results


def auto_bgm(tags, title, desc="", available=None):
    """根据笔记标签和标题自动选择 BGM 风格。

    参数:
        tags: 标签列表
        title: 笔记标题
        desc: 笔记描述
        available: 可用 BGM 列表（scan_bgm_dir 返回值）。若提供，只在已有风格中选

    返回:
        (style, bgm_path, matched_keywords)
        style: 匹配到的风格
        bgm_path: 选中的 BGM 文件路径（若有多个同风格随机选一个；无可用则 None）
        matched_keywords: 命中的关键词列表
    """
    tag_text = " ".join(t.lstrip("#") for t in (tags or []))
    full_text = f"{tag_text} {title or ''} {desc or ''}".lower()

    # 可用风格集合（如果提供了 available，优先在已有风格里选）
    available_styles = set()
    if available:
        for style, _, _, _ in available:
            available_styles.add(style)

    if not full_text.strip():
        return _pick_default_bgm(available), None, []

    # 统计每个风格的命中数
    scores = {}
    for style, keywords in BGM_KEYWORDS.items():
        # 如果提供了可用风格，跳过没有素材的风格
        if available_styles and style not in available_styles:
            continue
        matched = [kw for kw in keywords if kw.lower() in full_text]
        if matched:
            scores[style] = (len(matched), matched)

    if not scores:
        # 未命中关键词，返回默认风格
        style = _pick_default_bgm(available)
        logger.info("自动选BGM: %s (默认未命中)", style)
        return style, _pick_bgm_file(style, available), []

    # 取命中最多的
    best_style = max(scores.keys(), key=lambda s: scores[s][0])
    best_count, best_keywords = scores[best_style]
    bgm_path = _pick_bgm_file(best_style, available)
    logger.info("自动选BGM: 风格=%s (命中%d个关键词: %s) 文件=%s",
                best_style, best_count, ",".join(best_keywords[:5]),
                os.path.basename(bgm_path) if bgm_path else "无可用素材")
    return best_style, bgm_path, best_keywords


def _pick_default_bgm(available):
    """选默认风格（优先有素材的：Vlog > 治愈 > 欢快 > 氛围）。"""
    if available:
        available_styles = {s for s, _, _, _ in available}
        for s in ("Vlog", "治愈", "欢快", "氛围"):
            if s in available_styles:
                return s
        # 任意一个有素材的
        return next(iter(available_styles), "Vlog")
    return "Vlog"


def _pick_bgm_file(style, available):
    """从可用 BGM 中选指定风格的文件（有多个取第一个）。"""
    if not available:
        return None
    for s, _, _, full in available:
        if s == style:
            return full
    return None


def pick_bgm(name_or_style, available):
    """按曲名或风格选 BGM 文件。

    参数:
        name_or_style: 曲名（如 "起风了"）或风格（如 "治愈"）
        available: scan_bgm_dir 返回值

    返回:
        (bgm_path, matched_style) 或 (None, None)
    """
    if not available:
        return None, None
    key = name_or_style.strip().lower()
    # 1. 精确匹配风格
    for style, name, fn, full in available:
        if style.lower() == key:
            return full, style
    # 2. 精确匹配曲名
    for style, name, fn, full in available:
        if name.lower() == key:
            return full, style
    # 3. 模糊匹配曲名（包含）
    for style, name, fn, full in available:
        if key in name.lower() or name.lower() in key:
            return full, style
    # 4. 模糊匹配风格（包含）
    for style, name, fn, full in available:
        if key in style.lower() or style.lower() in key:
            return full, style
    # 5. 模糊匹配文件名
    for style, name, fn, full in available:
        if key in fn.lower():
            return full, style
    return None, None


def mix_bgm_into_video(video_path, bgm_path, out_path,
                       bgm_volume=0.15, fade=True, tmp_dir=None):
    """把 BGM 混合到视频音轨。

    - 保留视频原音轨（配音/原声）作为主音轨
    - BGM 循环到视频时长，加淡入淡出，降到 bgm_volume 音量
    - 用 ffmpeg amix 混合（主音轨 1.0 + BGM bgm_volume）

    参数:
        video_path: 输入视频（已有音轨）
        bgm_path: BGM 音频文件
        out_path: 输出视频
        bgm_volume: BGM 音量 0-1（默认 0.15 = 15%）
        fade: 是否给 BGM 加淡入淡出（默认 True）
        tmp_dir: 临时目录
    返回:
        成功返回 out_path，失败返回 None
    """
    from . import dedup_video
    ff = dedup_video._get_ffmpeg()
    if not ff:
        logger.error("未找到 ffmpeg")
        return None
    if not os.path.exists(video_path) or not os.path.exists(bgm_path):
        logger.error("视频或BGM文件不存在: %s / %s", video_path, bgm_path)
        return None

    video_dur = dedup_video.probe_duration(video_path)
    if video_dur <= 0:
        logger.error("无法探测视频时长")
        return None

    import tempfile
    _tmp = tmp_dir or tempfile.gettempdir()
    os.makedirs(_tmp, exist_ok=True)

    # 步骤1：把 BGM 循环+截断到视频时长，并加淡入淡出，输出为临时音频
    bgm_fitted = os.path.join(_tmp, "_bgm_fitted.m4a")
    # stream_loop=-1 无限循环；-t 截断到视频时长
    cmd1 = [ff, "-y", "-loglevel", "error",
            "-stream_loop", "-1", "-i", bgm_path,
            "-t", f"{video_dur:.3f}",
            "-vn", "-ac", "2", "-ar", "44100",
            "-c:a", "aac", "-b:a", "192k"]
    # 加淡入淡出（前0.5s淡入，后1s淡出）
    af = []
    if fade:
        fade_out_start = max(0, video_dur - 1.0)
        af.append(f"afade=t=in:st=0:d=0.5")
        af.append(f"afade=t=out:st={fade_out_start:.3f}:d=1.0")
    af.append(f"volume={bgm_volume}")
    cmd1 += ["-af", ",".join(af), bgm_fitted]

    try:
        logger.info("准备BGM: 循环到 %.2fs, 音量 %.0f%%, 淡入淡出=%s",
                    video_dur, bgm_volume * 100, fade)
        subprocess.run(cmd1, check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as e:
        logger.error("BGM 准备失败: %s", e.stderr.decode("utf-8", "ignore")[-300:])
        return None
    except Exception as e:
        logger.error("BGM 准备异常: %s", e)
        return None

    # 步骤2：混合视频原音轨 + BGM（amix，主音轨权重1，BGM权重1）
    # 用 filter_complex: [0:a]主音轨 [1:a]bgm → amix
    cmd2 = [ff, "-y", "-loglevel", "error",
            "-i", video_path,
            "-i", bgm_fitted,
            "-filter_complex",
            "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
            "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a1];"
            "[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:weights=1 1[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", out_path]

    try:
        logger.info("混合BGM到视频...")
        subprocess.run(cmd2, check=True, capture_output=True, timeout=180)
    except subprocess.CalledProcessError as e:
        logger.error("BGM 混合失败: %s", e.stderr.decode("utf-8", "ignore")[-300:])
        return None
    except Exception as e:
        logger.error("BGM 混合异常: %s", e)
        return None

    # 清理临时文件
    for p in [bgm_fitted]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        logger.info("BGM 混合完成: %s", out_path)
        return out_path
    logger.error("BGM 混合输出文件无效")
    return None


def list_bgm():
    """列出所有可用 BGM + 推荐清单。

    返回 (available, recommendations)
      available: scan_bgm_dir() 结果
      recommendations: BGM_RECOMMENDATIONS
    """
    available = scan_bgm_dir()
    return available, BGM_RECOMMENDATIONS
