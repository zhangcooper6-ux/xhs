# -*- coding: utf-8 -*-
"""AI 配音模块（多服务商：edge-tts / 火山引擎 Seed-TTS）。

特点：
  - 支持多 TTS 服务商切换：
      * edge（默认）：edge-tts 微软神经网络合成，免费/无需 API key/26 个中文配音
      * volcengine：火山引擎豆包 Seed-TTS 2.0，自然度更高/支持情感表达/需 API key
  - 支持语速/音调/音量调节
  - 支持替换原音频 或 保留原 BGM 混合配音

配音生成后用 ffmpeg 合成到视频：
  - replace 模式：完全替换原音频
  - mix 模式：原音频降低音量作为 BGM + 配音叠加
"""
import os
import re
import asyncio
import logging
import subprocess
import base64
import json
import uuid
import tempfile
import ssl
import time

logger = logging.getLogger("tts")

# edge-tts 是否可用
try:
    import edge_tts
    _HAS_EDGE_TTS = True
except ImportError:
    _HAS_EDGE_TTS = False

# requests 是否可用（火山引擎 TTS 必需）
try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


# ===== 火山引擎 Seed-TTS 2.0 配置 =====
VOLCENGINE_TTS_API = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
VOLCENGINE_TTS_RESOURCE_ID = "seed-tts-2.0"

# 火山引擎 Seed-TTS 2.0 合法音色（voice_type 列表）
# 文档: https://www.volcengine.com/docs/6561/1257544
# 注意：2.0 音色 ID 命名格式为 *_uranus_bigtts / *_saturn_bigtts / saturn_*_tob
#       旧版 1.0 音色（如 zh_female_qingxin / zh_female_sichuanhua）不能在 2.0 下使用
#       2.0 暂不支持方言音色（方言需切到 seed-tts-1.0）
VOLCENGINE_VOICES = {
    # === 豆包语音合成模型 2.0（通用场景，_uranus_bigtts 后缀）===
    "bd_vivi":      "zh_female_vv_uranus_bigtts",          # Vivi 2.0（通用女声，中英双语）
    "bd_xiaohe":    "zh_female_xiaohe_uranus_bigtts",      # 小何 2.0（通用女声）
    "bd_yunzhou":   "zh_male_m191_uranus_bigtts",          # 云舟 2.0（通用男声）
    "bd_xiaotian":  "zh_male_taocheng_uranus_bigtts",      # 小天 2.0（通用男声）
    # === 精品克隆音色 - 视频配音（_saturn_bigtts 后缀，剪映业务方）===
    "bd_xueayi":    "zh_female_xueayi_saturn_bigtts",      # 儿童绘本（亲子/童音）
    "bd_dayi":      "zh_male_dayi_saturn_bigtts",          # 大壹（视频配音男声）
    "bd_mizai":     "zh_female_mizai_saturn_bigtts",       # 黑猫侦探社咪（悬疑/故事）
    "bd_jitangnv":  "zh_female_jitangnv_saturn_bigtts",    # 鸡汤女（情感治愈）
    "bd_meilinvyou": "zh_female_meilinvyou_saturn_bigtts", # 魅力女友（甜美/情感）
    "bd_liuchang":  "zh_female_santongyongns_saturn_bigtts",  # 流畅女声（通用）
    "bd_ruyayichen": "zh_male_ruyayichen_saturn_bigtts",   # 儒雅逸辰（温润男声）
    # === 精品克隆音色 - 角色扮演（saturn_ 前缀 + _tob 后缀）===
    "bd_keainvsheng":  "saturn_zh_female_keainvsheng_tob",    # 可爱女生
    "bd_tiaopigongzhu": "saturn_zh_female_tiaopigongzhu_tob", # 调皮公主（俏皮）
    "bd_shuanglang":   "saturn_zh_male_shuanglangshaonian_tob",  # 爽朗少年
    "bd_tiancaitongzhuo": "saturn_zh_male_tiancaitongzhuo_tob", # 天才同桌
    "bd_cancan":       "saturn_zh_female_cancan_tob",       # 知性灿灿
}

# 火山引擎音色描述（用于 --list-voices 友好显示）
VOLCENGINE_VOICE_DESCRIPTIONS = {
    "bd_vivi":      "Vivi 2.0（火山·通用女声）中英双语，适合生活/家居/好物",
    "bd_xiaohe":    "小何 2.0（火山·通用女声）自然亲切，适合日常/穿搭",
    "bd_yunzhou":   "云舟 2.0（火山·通用男声）沉稳，适合知识/科普",
    "bd_xiaotian":  "小天 2.0（火山·通用男声）温润，适合情感/读书",
    "bd_xueayi":    "儿童绘本（火山·视频配音）童音，适合亲子/童话",
    "bd_dayi":      "大壹（火山·视频配音）男声，适合解说/种草",
    "bd_mizai":     "黑猫侦探社咪（火山·视频配音）女声，适合悬疑/故事",
    "bd_jitangnv":  "鸡汤女（火山·视频配音）情感治愈，适合励志/情感",
    "bd_meilinvyou": "魅力女友（火山·视频配音）甜美，适合美妆/穿搭",
    "bd_liuchang":  "流畅女声（火山·视频配音）通用，适合生活/好物",
    "bd_ruyayichen": "儒雅逸辰（火山·视频配音）温润男声，适合读书/情感",
    "bd_keainvsheng":  "可爱女生（火山·角色扮演）萌系，适合搞笑/萌宠",
    "bd_tiaopigongzhu": "调皮公主（火山·角色扮演）俏皮，适合段子/剧情",
    "bd_shuanglang":   "爽朗少年（火山·角色扮演）少年音，适合校园/游戏",
    "bd_tiancaitongzhuo": "天才同桌（火山·角色扮演）少年音，适合学习/校园",
    "bd_cancan":       "知性灿灿（火山·角色扮演）知性女声，适合职场/干货",
}


def _has_volcengine():
    """检查火山引擎 TTS 是否可用（依赖 requests 库 + 环境变量配置）"""
    if not _HAS_REQUESTS:
        return False
    return bool(os.getenv("VOLCENGINE_APP_ID") and os.getenv("VOLCENGINE_ACCESS_KEY"))

# ===== ElevenLabs API 配置与常用音色预设 =====
ELEVENLABS_TTS_API = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_S2S_API = "https://api.elevenlabs.io/v1/speech-to-speech"

ELEVENLABS_VOICES = {
    "el_rachel": "21m00Tcm4TlvDq8ikWAM",   # Rachel（温暖自然女声，美妆/好物/生活）
    "el_domi":   "AZnzlk1XvdvUeBnXmlld",   # Domi（强情绪女声，种草/爆款/解说）
    "el_bella":  "EXAVITQu4vr4xnSDxMaL",   # Bella（亲切柔和女声，穿搭/日常）
    "el_antoni": "ErXwobaYiN019PkySvjV",   # Antoni（干货知识男声，解说/科普）
    "el_adam":   "pNInz6obpgDQGcFmaJgB",   # Adam（沉稳男声，情感/剧情）
    "el_elli":   "MF3mGyEYCl7XYWbV9V6O",   # Elli（情感治愈女声，治愈/故事）
}

ELEVENLABS_VOICE_DESCRIPTIONS = {
    "el_rachel": "Rachel（ElevenLabs·自然女声）温暖亲切，适合家居/美妆/好物",
    "el_domi":   "Domi（ElevenLabs·强情绪女声）张力极强，适合短视频解说/爆款种草",
    "el_bella":  "Bella（ElevenLabs·亲切女声）柔和甜美，适合穿搭/日常vlog",
    "el_antoni": "Antoni（ElevenLabs·知识男声）清晰干练，适合科普/干货",
    "el_adam":   "Adam（ElevenLabs·沉稳男声）磁性深沉，适合故事/情感",
    "el_elli":   "Elli（ElevenLabs·治愈女声）情感丰富，适合情感/读书",
}


def _has_elevenlabs(api_key=None):
    """检查 ElevenLabs 服务是否可用（依赖 requests + API Key）"""
    if not _HAS_REQUESTS:
        return False
    key = api_key or os.getenv("ELEVENLABS_API_KEY")
    return bool(key)



# ===== 热门中文配音预设 =====
# 每个 voice 对应 edge-tts 的 ShortName
# 为方便用户选择，提供友好的中文名别名
# 共 26 个：普通话 20（11女 + 7男 + 2方言）+ 粤语 3 + 台腔 3
VOICE_PRESETS = {
    # ====== 普通话 - 女声（11 个）======
    "xiaoxiao":   "zh-CN-XiaoxiaoNeural",        # 晓晓 - 温暖亲切（最热门，适合生活/家居/好物分享）
    "xiaoyi":     "zh-CN-XiaoyiNeural",          # 晓伊 - 活泼可爱（适合美妆/穿搭/萌系）
    "xiaohan":    "zh-CN-XiaohanNeural",         # 晓涵 - 大气温婉（适合情感/故事/治愈）
    "xiaomeng":   "zh-CN-XiaomengNeural",        # 晓梦 - 甜美柔和（适合母婴/育儿/睡前）
    "xiaomo":     "zh-CN-XiaomoNeural",          # 晓墨 - 知性优雅（适合读书/职场/商务）
    "xiaoqiu":    "zh-CN-XiaoqiuNeural",         # 晓秋 - 俏皮活力（适合种草/测评/开箱）
    "xiaorui":    "zh-CN-XiaoruiNeural",         # 晓睿 - 沉稳大气（适合汽车/科技/财经）
    "xiaoshuang": "zh-CN-XiaoshuangNeural",      # 晓双 - 儿童声（适合亲子/童话/玩具）
    "xiaoxuan":   "zh-CN-XiaoxuanNeural",        # 晓萱 - 清新少女（适合旅行/vlog/日常）
    "xiaoyan":    "zh-CN-XiaoyanNeural",         # 晓颜 - 邻家女孩（适合美食/探店）
    "xiaozhen":   "zh-CN-XiaozhenNeural",        # 晓甄 - 御姐范（适合剧情/时尚）
    # ====== 普通话 - 男声（7 个）======
    "yunjian":    "zh-CN-YunjianNeural",         # 云健 - 浑厚有力（适合知识/科普/解说）
    "yunxi":      "zh-CN-YunxiNeural",           # 云希 - 阳光少年（适合潮流/数码）
    "yunxia":     "zh-CN-YunxiaNeural",          # 云夏 - 沉稳大叔（适合历史/故事）
    "yunyang":    "zh-CN-YunyangNeural",         # 云扬 - 新闻播音腔（适合资讯/干货）
    "yunfeng":    "zh-CN-YunfengNeural",         # 云枫 - 温润青年（适合读书/情感/治愈）
    "yunhao":     "zh-CN-YunhaoNeural",          # 云皓 - 阳光学生（适合校园/运动）
    "yunze":      "zh-CN-YunzeNeural",           # 云泽 - 中年儒雅（适合历史/财经/职场）
    # ====== 普通话 - 方言（2 个）======
    "xiaobei":    "zh-CN-liaoning-XiaobeiNeural",  # 晓北 - 东北腔（适合搞笑/接地气）
    "xiaoni":     "zh-CN-shaanxi-XiaoniNeural",    # 晓妮 - 陕西腔（地方特色）
    # ====== 粤语（3 个）======
    "hiugaai":    "zh-HK-HiuGaaiNeural",        # 曉佳 - 粤语女声
    "hiumaan":    "zh-HK-HiuMaanNeural",        # 曉曼 - 粤语女声
    "wanlung":    "zh-HK-WanLungNeural",        # 允龍 - 粤语男声
    # ====== 台湾国语（3 个）======
    "hsiaochen":  "zh-TW-HsiaoChenNeural",      # 曉臻 - 台腔女声
    "hsiaoyu":    "zh-TW-HsiaoYuNeural",        # 曉雨 - 台腔女声
    "yunjhe":     "zh-TW-YunJheNeural",         # 云哲 - 台腔男声
}


# 配音描述（用于 --list-voices 友好显示）
VOICE_DESCRIPTIONS = {
    # 普通话 - 女声
    "xiaoxiao":   "晓晓（女·普通话）温暖亲切，最热门，适合生活/家居/好物分享",
    "xiaoyi":     "晓伊（女·普通话）活泼可爱，适合美妆/穿搭/萌系",
    "xiaohan":    "晓涵（女·普通话）大气温婉，适合情感/故事/治愈",
    "xiaomeng":   "晓梦（女·普通话）甜美柔和，适合母婴/育儿/睡前",
    "xiaomo":     "晓墨（女·普通话）知性优雅，适合读书/职场/商务",
    "xiaoqiu":    "晓秋（女·普通话）俏皮活力，适合种草/测评/开箱",
    "xiaorui":    "晓睿（女·普通话）沉稳大气，适合汽车/科技/财经",
    "xiaoshuang": "晓双（儿童声·普通话）童音，适合亲子/童话/玩具",
    "xiaoxuan":   "晓萱（女·普通话）清新少女，适合旅行/vlog/日常",
    "xiaoyan":    "晓颜（女·普通话）邻家女孩，适合美食/探店",
    "xiaozhen":   "晓甄（女·普通话）御姐范，适合剧情/时尚",
    # 普通话 - 男声
    "yunjian":    "云健（男·普通话）浑厚有力，适合知识/科普/解说",
    "yunxi":      "云希（男·普通话）阳光少年，适合潮流/数码",
    "yunxia":     "云夏（男·普通话）沉稳大叔，适合历史/故事",
    "yunyang":    "云扬（男·普通话）新闻播音腔，适合资讯/干货",
    "yunfeng":    "云枫（男·普通话）温润青年，适合读书/情感/治愈",
    "yunhao":     "云皓（男·普通话）阳光学生，适合校园/运动",
    "yunze":      "云泽（男·普通话）中年儒雅，适合历史/财经/职场",
    # 方言
    "xiaobei":    "晓北（女·东北腔）接地气，适合搞笑/段子",
    "xiaoni":     "晓妮（女·陕西腔）地方特色，适合本地内容",
    # 粤语
    "hiugaai":    "曉佳（女·粤语）",
    "hiumaan":    "曉曼（女·粤语）",
    "wanlung":    "允龍（男·粤语）",
    # 台腔
    "hsiaochen":  "曉臻（女·台腔）",
    "hsiaoyu":    "曉雨（女·台腔）",
    "yunjhe":     "云哲（男·台腔）",
}

# 按场景推荐的配音（用于 --list-voices 时附带推荐）
VOICE_RECOMMENDATIONS = {
    "生活家居":     "xiaoxiao",
    "美妆穿搭":     "xiaoyi",
    "母婴育儿":     "xiaomeng",
    "美食探店":     "xiaoyan",
    "知识科普":     "yunjian",
    "潮流数码":     "yunxi",
    "情感治愈":     "xiaohan",
    "读书职场":     "xiaomo",
    "搞笑段子":     "xiaobei",
    "新闻资讯":     "yunyang",
    "历史故事":     "yunxia",
    "种草测评":     "xiaoqiu",
    "旅行vlog":    "xiaoxuan",
    "剧情时尚":     "xiaozhen",
    "亲子童话":     "xiaoshuang",
    "财经科技":     "xiaorui",
    "粤语内容":     "hiugaai",
    "台腔内容":     "hsiaochen",
}


# ===== 自动配音匹配表 =====
# 关键词 → 推荐配音别名
# 用于 --voice auto：根据笔记标签/标题关键词自动选配音
# 命中规则：标签或标题包含任一关键词即匹配该场景
AUTO_VOICE_KEYWORDS = {
    # 美妆穿搭
    "xiaoyi": ["美妆", "化妆", "口红", "粉底", "护肤", "穿搭", "ootd", "搭配",
               "衣服", "裙子", "时尚", "萌", "可爱", "少女"],
    # 母婴育儿
    "xiaomeng": ["母婴", "育儿", "宝宝", "婴儿", "奶粉", "纸尿裤", "孕期",
                 "备孕", "辅食", "亲子", "妈妈", "幼儿"],
    # 美食探店
    "xiaoyan": ["美食", "探店", "餐厅", "吃货", "食谱", "做饭", "烹饪",
                "蛋糕", "甜品", "咖啡", "奶茶", "零食", "早餐", "午餐", "晚餐",
                "烘焙", "菜谱", "好吃"],
    # 知识科普
    "yunjian": ["知识", "科普", "解说", "原理", "科学", "百科", "干货",
                "学习", "教程", "讲解", "分析", "揭秘"],
    # 潮流数码
    "yunxi": ["数码", "手机", "电脑", "耳机", "科技", "潮流", "新品",
              "测评", "开箱", "苹果", "安卓", "平板", "相机", "数码"],
    # 情感治愈
    "xiaohan": ["情感", "治愈", "暖心", "故事", "感悟", "心灵", "鸡汤",
                "励志", "emo", "孤独", "陪伴"],
    # 读书职场
    "xiaomo": ["读书", "书单", "职场", "工作", "面试", "简历", "管理",
               "自律", "成长", "认知", "思维", "效率"],
    # 搞笑段子
    "xiaobei": ["搞笑", "段子", "沙雕", "笑死", "哈哈", "整活", "鬼畜",
                "反转", "吐槽", "东北", "老铁"],
    # 新闻资讯
    "yunyang": ["新闻", "资讯", "热点", "时事", "报道", "最新", "今日",
                "播报", "快讯", "声明"],
    # 历史故事
    "yunxia": ["历史", "古代", "皇帝", "战争", "朝代", "故事", "传奇",
               "人物", "民国", "抗战"],
    # 种草测评
    "xiaoqiu": ["种草", "好物", "推荐", "测评", "开箱", "拔草", "神器",
                "必买", "囤货", "宝藏"],
    # 旅行vlog
    "xiaoxuan": ["旅行", "旅游", "vlog", "打卡", "攻略", "风景", "游记",
                 "民宿", "酒店", "出行", "自驾", "徒步"],
    # 剧情时尚
    "xiaozhen": ["剧情", "短剧", "时尚", "大片", "御姐", "高级感",
                 "质感", "氛围感"],
    # 亲子童话
    "xiaoshuang": ["童话", "故事", "绘本", "儿童", "小孩", "小朋友",
                   "动画", "睡前故事"],
    # 财经科技
    "xiaorui": ["财经", "股票", "基金", "理财", "投资", "经济", "金融",
                "科技", "汽车", "新能源", "芯片", "ai", "人工智能"],
    # 生活家居（默认兜底）
    "xiaoxiao": ["生活", "家居", "收纳", "清洁", "好物", "日用", "厨房",
                 "卫浴", "卧室", "装修", "租房", "神器", "分享"],
}


def auto_voice(tags, title, desc="", provider="edge"):
    """根据笔记标签和标题自动选择最合适的配音。

    匹配规则：
      1. 把 tags + title + desc 拼成文本
      2. 遍历 AUTO_VOICE_KEYWORDS，统计每个配音别名命中的关键词数
      3. 取命中数最多的配音；命中数相同取优先级靠前的
      4. 全部未命中 → 返回默认音色（edge=xiaoxiao, volcengine=bd_vivi）

    参数:
        tags: 标签列表，如 ["美食", "探店"]
        title: 笔记标题
        desc: 笔记描述（可选）
        provider: "edge" 或 "volcengine"，决定返回的别名命名空间
    返回:
        (voice_alias, matched_scene, matched_keywords)
        voice_alias: 配音别名（edge 风格或 bd_ 风格，由 provider 决定）
        matched_scene: 匹配到的场景描述（用于日志）
        matched_keywords: 命中的关键词列表
    """
    # edge 别名 → volcengine Seed-TTS 2.0 别名映射（按场景匹配）
    # 注意：2.0 不支持方言，方言场景回退到通用女声
    # 火山引擎音色需在控制台「语音合成」→「应用管理」→「音色管理」中开通
    EDGE_TO_ELEVEN = {
        "xiaoxiao":   "el_rachel",
        "xiaoyi":     "el_bella",
        "xiaomeng":   "el_elli",
        "xiaoyan":    "el_rachel",
        "yunjian":    "el_antoni",
        "yunxi":      "el_adam",
        "xiaohan":    "el_elli",
        "xiaomo":     "el_antoni",
        "xiaobei":    "el_domi",
        "xiaoqiu":    "el_domi",
        "xiaoxuan":   "el_bella",
        "xiaozhen":   "el_domi",
    }
    EDGE_TO_VOLC = {
        "xiaoxiao":   "bd_vivi",         # 生活家居 → Vivi 2.0（通用女声）
        "xiaoyi":     "bd_meilinvyou",   # 美妆穿搭 → 魅力女友（甜美）
        "xiaomeng":   "bd_xueayi",       # 母婴育儿 → 儿童绘本（童音）
        "xiaoyan":    "bd_liuchang",     # 美食探店 → 流畅女声
        "yunjian":    "bd_yunzhou",      # 知识科普 → 云舟 2.0（沉稳男声）
        "yunxi":      "bd_xiaotian",     # 潮流数码 → 小天 2.0（通用男声）
        "xiaohan":    "bd_jitangnv",     # 情感治愈 → 鸡汤女
        "xiaomo":     "bd_ruyayichen",   # 读书职场 → 儒雅逸辰
        "xiaobei":    "bd_tiaopigongzhu", # 搞笑段子 → 调皮公主
        "yunyang":    "bd_yunzhou",      # 新闻资讯 → 云舟 2.0
        "yunxia":     "bd_ruyayichen",   # 历史故事 → 儒雅逸辰
        "xiaoqiu":    "bd_meilinvyou",   # 种草测评 → 魅力女友
        "xiaoxuan":   "bd_xiaohe",       # 旅行vlog → 小何 2.0
        "xiaozhen":   "bd_meilinvyou",   # 剧情时尚 → 魅力女友
        "xiaoshuang": "bd_xueayi",       # 亲子童话 → 儿童绘本
        "xiaorui":    "bd_cancan",       # 财经科技 → 知性灿灿
    }
    default_alias = "el_rachel" if provider == "elevenlabs" else ("bd_vivi" if provider == "volcengine" else "xiaoxiao")

    # 拼接文本（标签去掉 # 前缀）
    tag_text = " ".join(t.lstrip("#") for t in (tags or []))
    full_text = f"{tag_text} {title or ''} {desc or ''}".lower()

    if not full_text.strip():
        return default_alias, "默认(空文本)", []

    # 统计每个配音的命中数
    scores = {}  # alias -> (count, matched_keywords)
    for alias, keywords in AUTO_VOICE_KEYWORDS.items():
        matched = [kw for kw in keywords if kw.lower() in full_text]
        if matched:
            scores[alias] = (len(matched), matched)

    if not scores:
        return default_alias, "默认(未命中)", []

    # 取命中数最多的（命中数相同则保留字典顺序，即 AUTO_VOICE_KEYWORDS 的定义顺序）
    best_alias = max(scores.keys(), key=lambda a: scores[a][0])
    best_count, best_keywords = scores[best_alias]
    scene = VOICE_DESCRIPTIONS.get(best_alias, best_alias)

    # 按 provider 转换别名
    if provider == "elevenlabs":
        best_alias = EDGE_TO_ELEVEN.get(best_alias, "el_rachel")
        scene = ELEVENLABS_VOICE_DESCRIPTIONS.get(best_alias, scene)
    elif provider == "volcengine":
        best_alias = EDGE_TO_VOLC.get(best_alias, "bd_vivi")
        scene = VOLCENGINE_VOICE_DESCRIPTIONS.get(best_alias, scene)

    logger.info("自动选配音[%s]: %s (命中%d个关键词: %s) - %s",
                provider, best_alias, best_count, ",".join(best_keywords[:5]), scene)
    return best_alias, scene, best_keywords


def list_voices(provider="edge"):
    """列出所有可用配音。返回 [(alias, voice_name, description), ...]

    参数:
        provider: "edge" / "volcengine" / "elevenlabs"
    """
    if provider == "elevenlabs":
        return [(alias, ELEVENLABS_VOICES[alias],
                 ELEVENLABS_VOICE_DESCRIPTIONS.get(alias, alias))
                for alias in ELEVENLABS_VOICES]
    if provider == "volcengine":
        return [(alias, VOLCENGINE_VOICES[alias],
                 VOLCENGINE_VOICE_DESCRIPTIONS.get(alias, alias))
                for alias in VOLCENGINE_VOICES]
    return [(alias, VOICE_PRESETS[alias], VOICE_DESCRIPTIONS.get(alias, alias))
            for alias in VOICE_PRESETS]


def list_all_voices():
    """列出所有服务商的所有配音（合并 edge + volcengine）。"""
    return list_voices("edge") + list_voices("volcengine") + list_voices("elevenlabs")


def print_voices():
    """以友好的分组格式打印所有可用配音（用于 CLI --list-voices）。"""
    edge_groups = [
        ("普通话 - 女声", [
            ("xiaoxiao",   "晓晓",   "温暖亲切",     "生活/家居/好物分享"),
            ("xiaoyi",     "晓伊",   "活泼可爱",     "美妆/穿搭/萌系"),
            ("xiaohan",    "晓涵",   "大气温婉",     "情感/故事/治愈"),
            ("xiaomeng",   "晓梦",   "甜美柔和",     "母婴/育儿/睡前"),
            ("xiaomo",     "晓墨",   "知性优雅",     "读书/职场/商务"),
            ("xiaoqiu",    "晓秋",   "俏皮活力",     "种草/测评/开箱"),
            ("xiaorui",    "晓睿",   "沉稳大气",     "汽车/科技/财经"),
            ("xiaoshuang", "晓双",   "儿童声",       "亲子/童话/玩具"),
            ("xiaoxuan",   "晓萱",   "清新少女",     "旅行/vlog/日常"),
            ("xiaoyan",    "晓颜",   "邻家女孩",     "美食/探店"),
            ("xiaozhen",   "晓甄",   "御姐范",       "剧情/时尚"),
        ]),
        ("普通话 - 男声", [
            ("yunjian", "云健", "浑厚有力",   "知识/科普/解说"),
            ("yunxi",   "云希", "阳光少年",   "潮流/数码"),
            ("yunxia",  "云夏", "沉稳大叔",   "历史/故事"),
            ("yunyang", "云扬", "新闻播音腔", "资讯/干货"),
            ("yunfeng", "云枫", "温润青年",   "读书/情感/治愈"),
            ("yunhao",  "云皓", "阳光学生",   "校园/运动"),
            ("yunze",   "云泽", "中年儒雅",   "历史/财经/职场"),
        ]),
        ("普通话 - 方言", [
            ("xiaobei", "晓北", "东北腔", "搞笑/接地气"),
            ("xiaoni",  "晓妮", "陕西腔", "本地特色"),
        ]),
        ("粤语", [
            ("hiugaai", "曉佳", "女声", "粤语内容"),
            ("hiumaan", "曉曼", "女声", "粤语内容"),
            ("wanlung", "允龍", "男声", "粤语内容"),
        ]),
        ("台湾国语", [
            ("hsiaochen", "曉臻", "女声", "台腔内容"),
            ("hsiaoyu",   "曉雨", "女声", "台腔内容"),
            ("yunjhe",    "云哲", "男声", "台腔内容"),
        ]),
    ]
    volcengine_groups = [
        ("火山引擎 - 豆包2.0 通用场景", [
            ("bd_vivi",     "Vivi 2.0",   "通用女声", "生活/家居/好物（中英双语）"),
            ("bd_xiaohe",   "小何 2.0",   "通用女声", "日常/穿搭/生活"),
            ("bd_yunzhou",  "云舟 2.0",   "通用男声", "知识/科普/解说"),
            ("bd_xiaotian", "小天 2.0",   "通用男声", "情感/读书"),
        ]),
        ("火山引擎 - 精品克隆·视频配音", [
            ("bd_xueayi",     "儿童绘本",    "童音",   "亲子/童话"),
            ("bd_dayi",       "大壹",        "男声",   "解说/种草"),
            ("bd_mizai",      "黑猫侦探社咪", "女声",   "悬疑/故事"),
            ("bd_jitangnv",   "鸡汤女",      "治愈系", "情感/励志"),
            ("bd_meilinvyou", "魅力女友",    "甜美",   "美妆/穿搭"),
            ("bd_liuchang",   "流畅女声",    "通用",   "生活/好物"),
            ("bd_ruyayichen", "儒雅逸辰",    "温润",   "读书/情感"),
        ]),
        ("火山引擎 - 精品克隆·角色扮演", [
            ("bd_keainvsheng",     "可爱女生",   "萌系",   "搞笑/萌宠"),
            ("bd_tiaopigongzhu",   "调皮公主",   "俏皮",   "段子/剧情"),
            ("bd_shuanglang",      "爽朗少年",   "少年音", "校园/游戏"),
            ("bd_tiancaitongzhuo", "天才同桌",   "少年音", "学习/校园"),
            ("bd_cancan",          "知性灿灿",   "知性",   "职场/干货"),
        ]),
    ]
    lines = []
    lines.append("=" * 70)
    lines.append(f"【服务商 1】edge-tts（免费，无需 API key）共 {len(VOICE_PRESETS)} 个中文配音")
    lines.append("=" * 70)
    for group_name, voices in edge_groups:
        lines.append("")
        lines.append(f"【{group_name}】")
        lines.append(f"  {'别名':<14}{'中文名':<8}{'音色':<14}{'推荐场景'}")
        lines.append(f"  {'-'*14}{'-'*8}{'-'*14}{'-'*20}")
        for alias, cn, tone, scene in voices:
            lines.append(f"  {alias:<14}{cn:<8}{tone:<14}{scene}")
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"【服务商 2】火山引擎 Seed-TTS 2.0（自然度更高，需 API key）"
                 f"共 {len(VOLCENGINE_VOICES)} 个音色")
    lines.append("  环境变量: VOLCENGINE_APP_ID / VOLCENGINE_ACCESS_KEY")
    lines.append("=" * 70)
    for group_name, voices in volcengine_groups:
        lines.append("")
        lines.append(f"【{group_name}】")
        lines.append(f"  {'别名':<22}{'中文名':<10}{'音色':<10}{'推荐场景'}")
        lines.append(f"  {'-'*22}{'-'*10}{'-'*10}{'-'*20}")
        for alias, cn, tone, scene in voices:
            lines.append(f"  {alias:<22}{cn:<10}{tone:<10}{scene}")
    lines.append("")
    lines.append("=" * 70)
    lines.append("用法: python xhs_clone.py <URL> --voice <别名>                          (默认 edge)")
    lines.append("      python xhs_clone.py <URL> --tts-provider volcengine --voice bd_vivi")
    lines.append("示例: python xhs_clone.py <URL> --voice xiaoxiao")
    lines.append("      python xhs_clone.py <URL> --voice yunjian --voice-rate -8%")
    lines.append("      python xhs_clone.py <URL> --tts-provider volcengine --voice bd_vivi")
    lines.append("注意: Seed-TTS 2.0 不支持方言，方言需改用 1.0 (set VOLCENGINE_TTS_RESOURCE_ID=seed-tts-1.0)")
    lines.append("=" * 70)
    return "\n".join(lines)


def resolve_voice(voice_input, provider="edge"):
    """解析用户输入的 voice（别名或完整 ShortName / voice_type / Voice ID）。

    参数:
        voice_input: 用户输入的配音别名或 ID
        provider: "edge" / "volcengine" / "elevenlabs"
    返回:
        edge: ShortName（如 "zh-CN-XiaoxiaoNeural"）
        volcengine: voice_type（如 "zh_female_vv_uranus_bigtts"）
        elevenlabs: Voice ID（如 "21m00Tcm4TlvDq8ikWAM"）
    """
    if provider == "elevenlabs":
        return _resolve_elevenlabs_voice(voice_input)
    if provider == "volcengine":
        return _resolve_volcengine_voice(voice_input)
    return _resolve_edge_voice(voice_input)


def _resolve_elevenlabs_voice(voice_input):
    """解析 ElevenLabs 的 Voice ID。"""
    default_voice = ELEVENLABS_VOICES["el_rachel"]
    if not voice_input:
        return default_voice
    if voice_input in ELEVENLABS_VOICES:
        return ELEVENLABS_VOICES[voice_input]
    if len(voice_input) >= 15 or not voice_input.startswith("el_"):
        return voice_input
    for alias, vid in ELEVENLABS_VOICES.items():
        if voice_input.lower() in alias.lower():
            return vid
    logger.warning("未找到 ElevenLabs 音色 '%s'，使用默认 Rachel", voice_input)
    return default_voice


def _resolve_edge_voice(voice_input):
    """解析 edge-tts 的 voice。"""
    if not voice_input:
        return VOICE_PRESETS["xiaoxiao"]  # 默认晓晓
    # 别名
    if voice_input in VOICE_PRESETS:
        return VOICE_PRESETS[voice_input]
    # 直接传 ShortName
    if "Neural" in voice_input:
        return voice_input
    # 尝试模糊匹配
    for alias, name in VOICE_PRESETS.items():
        if voice_input.lower() in alias.lower() or voice_input.lower() in name.lower():
            return name
    logger.warning("未找到 edge 配音 '%s'，使用默认晓晓", voice_input)
    return VOICE_PRESETS["xiaoxiao"]


def _resolve_volcengine_voice(voice_input):
    """解析火山引擎的 voice_type。"""
    default_voice = VOLCENGINE_VOICES["bd_vivi"]  # 默认 Vivi 2.0（通用女声）
    if not voice_input:
        return default_voice
    # 别名（bd_ 前缀）
    if voice_input in VOLCENGINE_VOICES:
        return VOLCENGINE_VOICES[voice_input]
    # 直接传 voice_type（如 zh_female_xxx_uranus_bigtts / saturn_xxx_tob）
    if voice_input.startswith("zh_") or voice_input.startswith("saturn_"):
        return voice_input
    # 兼容旧版 1.0 别名（如 bd_xiaoyan/bd_sichuan 等）→ 统一回退到 bd_vivi
    legacy_aliases = {
        "bd_xiaoyan", "bd_xiaomei", "bd_xiaoxia", "bd_xiaoqian", "bd_xinling",
        "bd_tianxin", "bd_zhisheng", "bd_qingqing", "bd_linjia", "bd_yueyue",
        "bd_yunhao", "bd_yunyang", "bd_yunfeng", "bd_yunjian", "bd_laohao",
        "bd_xiaobai", "bd_lele", "bd_qingxinvsheng", "bd_qingxinnansheng",
        "bd_qiaopi", "bd_wenrou", "bd_yueyu", "bd_sichuan", "bd_dongbei",
    }
    if voice_input in legacy_aliases:
        logger.warning(
            "火山引擎音色 '%s' 是旧版 Seed-TTS 1.0 别名，当前使用 2.0，已自动回退到 bd_vivi。"
            "如需使用 1.0 音色，请设置环境变量 VOLCENGINE_TTS_RESOURCE_ID=seed-tts-1.0",
            voice_input)
        return default_voice
    # 尝试模糊匹配
    for alias, name in VOLCENGINE_VOICES.items():
        if voice_input.lower() in alias.lower() or voice_input.lower() in name.lower():
            return name
    logger.warning("未找到火山引擎配音 '%s'，使用默认 Vivi 2.0", voice_input)
    return default_voice


def clean_text_for_tts(text):
    """清理文本以提升配音自然度。

    - 去除 #话题标签（避免被读成"井号xxx"）
    - 去除 URL
    - 去除 emoji（部分会被读成描述词，影响自然度）
    - 折叠多余空白
    - 整理标点（多个感叹号/问号折叠为一个，避免语气过激）
    """
    if not text:
        return ""
    import re
    # 1. 去 URL
    text = re.sub(r"https?://\S+", "", text)
    # 2. 去 #标签#  和  #标签（无尾井号）
    text = re.sub(r"#[^#\s]{1,30}#?", "", text)
    # 3. 去 @用户
    text = re.sub(r"@[A-Za-z0-9_\-\u4e00-\u9fa5]{1,30}", "", text)
    # 4. 去 emoji（只匹配真正的 emoji 区块，避免误删中文）
    #    中文在 \u4e00-\u9fa5，不在下列任何 emoji 区块内
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # 表情 Emoticons
        "\U0001F300-\U0001F5FF"  # 符号/象形 Misc Symbols and Pictographs
        "\U0001F680-\U0001F6FF"  # 交通/地图 Transport and Map
        "\U0001F1E0-\U0001F1FF"  # 旗帜 Flags
        "\U0001F900-\U0001F9FF"  # 补充符号 Supplemental Symbols
        "\U0001FA70-\U0001FAFF"  # 扩展符号 Extended-A
        "\U00002600-\U000026FF"  # 杂项符号 Misc Symbols (☀☃等)
        "\U00002700-\U000027BF"  # 装饰符号 Dingbats
        "\U0001F700-\U0001F77F"  # 炼金符号
        "\U0001F780-\U0001F7FF"  # 几何形状扩展
        "\U0001F800-\U0001F8FF"  # 补充箭头-C
        "\U0001F900-\U0001F9FF"  # 补充符号和象形文字
        "]+", flags=re.UNICODE)
    text = emoji_pattern.sub("", text)
    # 5. 折叠多个相同标点为一个（！！！→！，？？？→？）
    #    注意包含全角！？和半角!?
    text = re.sub(r"([!！?？\.。,，])\1+", r"\1", text)
    # 6. 折叠多余空白
    text = re.sub(r"\s+", " ", text).strip()
    # 7. 若无结尾标点，加个句号（避免最后一句语气突兀）
    if text and text[-1] not in "。.！!？?…":
        text += "。"
    return text


def generate_tts(text, out_path, voice="xiaoxiao", rate="+0%", pitch="+0Hz",
                 volume="+0%", clean=True, provider="edge", api_key=None):
    """生成配音音频（支持多服务商）。

    参数:
        text: 配音文本
        out_path: 输出 mp3 路径
        voice: 配音别名或 ShortName（edge）/ voice_type（volcengine）
        rate: 语速，如 "+10%"（加快）/ "-10%"（减慢）/ "+0%"（正常）
        pitch: 音调，如 "+5Hz"（高）/ "-5Hz"（低）/ "+0Hz"（正常）
        volume: 音量，如 "+20%"（大）/ "-20%"（小）/ "+0%"（正常）
        clean: 是否清理文本（去标签/emoji/URL，默认 True 提升自然度）
        provider: TTS 服务商: "edge"（默认）/ "volcengine"（火山引擎 Seed-TTS 2.0）
    返回:
        out_path（成功）或 None（失败）
    """
    if provider == "elevenlabs":
        return _generate_elevenlabs_tts(text, out_path, voice, rate, pitch, volume, clean, api_key=api_key)
    if provider == "volcengine":
        return _generate_volcengine_tts(text, out_path, voice, rate, pitch, volume, clean)
    return _generate_edge_tts(text, out_path, voice, rate, pitch, volume, clean)


def _generate_edge_tts(text, out_path, voice, rate, pitch, volume, clean):
    """用 edge-tts 生成配音音频。"""
    if not _HAS_EDGE_TTS:
        logger.error("edge-tts 未安装，请运行: pip install edge-tts")
        return None
    if not text or not text.strip():
        logger.warning("配音文本为空，跳过 TTS")
        return None

    if clean:
        text = clean_text_for_tts(text)
    if not text.strip():
        logger.warning("清理后配音文本为空，跳过 TTS")
        return None

    voice_name = _resolve_edge_voice(voice)
    logger.info("[edge-tts] 生成配音: voice=%s rate=%s pitch=%s", voice_name, rate, pitch)
    logger.info("文本: %s", text[:80] + "..." if len(text) > 80 else text)

    try:
        async def _gen():
            communicate = edge_tts.Communicate(text, voice_name,
                                               rate=rate, pitch=pitch, volume=volume)
            await communicate.save(out_path)

        asyncio.run(_gen())
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info("配音生成完成: %s (%d bytes)",
                        out_path, os.path.getsize(out_path))
            return out_path
        logger.error("配音生成失败：文件为空")
        return None
    except Exception as e:
        logger.error("配音生成异常: %s", e)
        return None




def _generate_elevenlabs_tts(text, out_path, voice, rate="+0%", pitch="+0Hz",
                            volume="+0%", clean=True, model_id=None, api_key=None):
    """用 ElevenLabs API 生成超高质量配音音频。"""
    if not _HAS_REQUESTS:
        logger.error("requests 未安装，ElevenLabs 需要: pip install requests")
        return None
    api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        logger.error("ElevenLabs 未配置 API Key！请设置环境变量 ELEVENLABS_API_KEY 或 --elevenlabs-api-key")
        return None
    if not text or not text.strip():
        logger.warning("配音文本为空，跳过 TTS")
        return None

    if clean:
        text = clean_text_for_tts(text)
    if not text.strip():
        logger.warning("清理后文本为空，跳过 TTS")
        return None

    voice_id = _resolve_elevenlabs_voice(voice)
    model_id = model_id or os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")

    url = f"{ELEVENLABS_TTS_API}/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.2,
            "use_speaker_boost": True
        }
    }

    logger.info("[ElevenLabs] 生成超真实配音: voice=%s (%s), model=%s", voice, voice_id, model_id)
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code == 200:
            with open(out_path, "wb") as f:
                f.write(r.content)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("ElevenLabs 配音生成完成: %s (%d bytes)", out_path, os.path.getsize(out_path))
                return out_path
        else:
            logger.error("ElevenLabs TTS 请求失败 (%d): %s", r.status_code, r.text)
            return None
    except Exception as e:
        logger.error("ElevenLabs TTS 异常: %s", e)
        return None


def speech_to_speech_elevenlabs(input_audio_path, out_path, voice="el_rachel",
                                model_id="eleven_multilingual_sts_v2", api_key=None):
    """ElevenLabs Speech-to-Speech (S2S) 音频变声二创。

    完全继承原视频中说话人的情感起伏、语速节奏、语气助词与笑声停顿，
    并替换为目标音色（如 Rachel, Domi 或自定义 Voice ID）。
    """
    if not _HAS_REQUESTS:
        logger.error("requests 未安装，ElevenLabs S2S 需要: pip install requests")
        return None
    api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        logger.error("ElevenLabs 未配置 API Key！无法执行 S2S 变声")
        return None
    if not os.path.exists(input_audio_path):
        logger.error("S2S 输入音频不存在: %s", input_audio_path)
        return None

    voice_id = _resolve_elevenlabs_voice(voice)
    url = f"{ELEVENLABS_S2S_API}/{voice_id}"
    headers = {
        "xi-api-key": api_key,
    }

    voice_settings = json.dumps({
        "stability": 0.45,
        "similarity_boost": 0.8,
        "style": 0.3,
        "use_speaker_boost": True
    })

    data = {
        "model_id": model_id,
        "voice_settings": voice_settings
    }

    logger.info("[ElevenLabs S2S] 启动 Speech-to-Speech 变声: voice=%s (%s), model=%s", voice, voice_id, model_id)
    try:
        with open(input_audio_path, "rb") as audio_f:
            files = {"audio": (os.path.basename(input_audio_path), audio_f, "audio/mpeg")}
            r = requests.post(url, headers=headers, data=data, files=files, timeout=120)

        if r.status_code == 200:
            with open(out_path, "wb") as f:
                f.write(r.content)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("ElevenLabs S2S 变声完成: %s (%d bytes)", out_path, os.path.getsize(out_path))
                return out_path
        else:
            logger.error("ElevenLabs S2S 请求失败 (%d): %s", r.status_code, r.text)
            return None
    except Exception as e:
        logger.error("ElevenLabs S2S 异常: %s", e)
        return None


def _parse_percent(value_str, default=0):
    """解析 "+10%" / "-5%" 等百分比字符串为整数（如 10 / -5）。

    edge-tts 用百分比字符串，火山引擎用整数百分比。
    """
    if not value_str:
        return default
    s = str(value_str).strip().replace("%", "").replace("+", "")
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def _parse_pitch_hz(value_str, default=0):
    """解析 "+5Hz" / "-3Hz" 等音调字符串为整数（如 5 / -3）。

    edge-tts 用 Hz 字符串，火山引擎用整数半音/音调值。
    """
    if not value_str:
        return default
    s = str(value_str).strip().replace("Hz", "").replace("+", "")
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def _generate_volcengine_tts(text, out_path, voice, rate, pitch, volume, clean):
    """用火山引擎 Seed-TTS 2.0 生成配音音频。

    API 文档: https://www.volcengine.com/docs/6561/1598757
    端点: https://openspeech.bytedance.com/api/v3/tts/unidirectional
    资源 ID: seed-tts-2.0
    认证: Header 携带 X-Api-App-Id / X-Api-Access-Key / X-Api-Resource-Id
    响应: HTTP Chunked 流式 JSON，每个 chunk 含 base64 音频数据
    """
    if not _HAS_REQUESTS:
        logger.error("requests 未安装，火山引擎 TTS 需要: pip install requests")
        return None
    if not text or not text.strip():
        logger.warning("配音文本为空，跳过 TTS")
        return None

    if clean:
        text = clean_text_for_tts(text)
    if not text.strip():
        logger.warning("清理后配音文本为空，跳过 TTS")
        return None

    app_id = os.getenv("VOLCENGINE_APP_ID")
    access_key = os.getenv("VOLCENGINE_ACCESS_KEY")
    if not app_id or not access_key:
        logger.error("火山引擎 TTS 未配置：请设置环境变量 VOLCENGINE_APP_ID 和 VOLCENGINE_ACCESS_KEY")
        logger.error("获取方式: 火山引擎控制台 → 语音合成 → 应用管理 → APP ID / Access Token")
        return None

    # 允许通过环境变量覆盖 resource_id（如使用 seed-tts-1.0）
    resource_id = os.getenv("VOLCENGINE_TTS_RESOURCE_ID", VOLCENGINE_TTS_RESOURCE_ID)

    voice_type = _resolve_volcengine_voice(voice)
    # V3 API 字段名：speech_rate / loudness_rate（范围均为 [-50, 100]，0=正常）
    # Seed-TTS 2.0 不支持 pitch 参数，忽略 pitch 输入
    speech_rate = _parse_percent(rate, 0)
    loudness_rate = _parse_percent(volume, 0)

    logger.info("[火山引擎] 生成配音: voice=%s speech_rate=%d loudness_rate=%d",
                voice_type, speech_rate, loudness_rate)
    logger.info("文本: %s", text[:80] + "..." if len(text) > 80 else text)

    headers = {
        "X-Api-App-Id": app_id,
        "X-Api-Access-Key": access_key,
        "X-Api-Resource-Id": resource_id,
        "Content-Type": "application/json",
    }

    # V3 HTTP Chunked 单向流式 payload
    # 文档: https://www.volcengine.com/docs/6561/1598757
    # 注意：speaker 必须是字符串（voice_type），不能是对象；否则服务端报
    #       "cannot unmarshal object into Go struct field TTSReqParams.req_params.speaker of type string"
    payload = {
        "user": {"uid": "xhs_clone_" + uuid.uuid4().hex[:8]},
        "req_params": {
            "text": text,
            "speaker": voice_type,
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": speech_rate,
                "loudness_rate": loudness_rate,
            },
        },
    }

    # SSL 兼容适配器：解决 SSLEOFError（中间设备/代理在 TLS 握手时中断连接）
    # 使用 urllib3 的 SSLAdapter 降级 TLS 配置，提高兼容性
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.ssl_ import create_urllib3_context
        _SSL_AVAILABLE = True
    except ImportError:
        _SSL_AVAILABLE = False

    def _build_ssl_session():
        """构建带 SSL 兼容配置的 requests.Session"""
        s = requests.Session()
        if _SSL_AVAILABLE:
            try:
                # 降低 TLS 最低版本到 1.0，兼容旧代理/防火墙
                ctx = create_urllib3_context()
                ctx.minimum_version = ssl.TLSVersion.TLSv1
                ctx.maximum_version = ssl.TLSVersion.TLSv1_2  # 避免 TLS 1.3 兼容性问题
                # 不强制验证证书链完整性（部分代理自签证书）
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                class SSLAdapter(HTTPAdapter):
                    def init_poolmanager(self, *args, **kwargs):
                        kwargs["ssl_context"] = ctx
                        return super().init_poolmanager(*args, **kwargs)

                s.mount("https://", SSLAdapter())
            except Exception as e:
                logger.debug("SSL 适配器配置失败，使用默认配置: %s", e)
        return s

    # 带重试的请求（针对 SSL/连接错误，最多重试 2 次，间隔 2s/4s）
    response = None
    last_err = None
    for attempt in range(3):
        try:
            session = _build_ssl_session()
            response = session.post(
                VOLCENGINE_TTS_API,
                headers=headers,
                json=payload,
                stream=True,
                timeout=(15, 120),  # (连接超时, 读取超时)
                verify=False,  # 禁用证书验证，避免代理自签证书问题
            )
            response.raise_for_status()
            break  # 请求成功，退出重试
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError) as e:
            last_err = e
            if attempt < 2:
                wait = 2.0 * (attempt + 1)  # 2s, 4s
                logger.warning("火山引擎 TTS 连接失败（第%d次），%ds 后重试: %s",
                               attempt + 1, wait, str(e)[:200])
                time.sleep(wait)
            else:
                logger.error("火山引擎 TTS 连接失败（已重试 3 次）: %s", e)
        except requests.exceptions.HTTPError:
            raise  # HTTP 错误码不重试，交给外层处理
        except requests.exceptions.Timeout:
            raise  # 超时不重试，交给外层处理

    if response is None:
        # 所有重试均失败
        logger.error("火山引擎 TTS 连接失败（已重试 3 次均失败）: %s", last_err)
        logger.error("可能原因:\n"
                     "  1. 网络代理/VPN 拦截了 HTTPS 连接（尝试关闭代理或换网络）\n"
                     "  2. 防火墙/安全软件拦截了 openspeech.bytedance.com\n"
                     "  3. TLS 握手被中间设备中断（SSLEOFError）\n"
                     "  建议:\n"
                     "  - 关闭 VPN/代理后重试\n"
                     "  - 或改用 --tts-provider edge（免费、无需联网火山引擎）")
        return None

    try:
        audio_data = b""
        chunks_received = 0
        # HTTP Chunked 流式响应：逐行读取 JSON
        for line in response.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.debug("跳过无法解析的 chunk: %s", e)
                continue

            # 状态检查
            # 火山引擎 TTS v3 响应格式：
            #   - 成功音频块: {"code":0/20000000, "message":""/"OK", "data":"<base64音频>"}
            #   - 错误响应:   {"code":<非0错误码>, "message":"<错误描述>", "data":null}
            # 判断逻辑：有 data 字段 = 成功音频块；无 data 且 message 非空 = 错误
            audio_b64 = chunk.get("data")
            msg = chunk.get("message", "")
            code = chunk.get("code")

            if audio_b64:
                # 有音频数据 = 成功块，提取并累加
                try:
                    audio_data += base64.b64decode(audio_b64)
                    chunks_received += 1
                except Exception as e:
                    logger.debug("base64 解码失败: %s", e)
            elif msg and msg != "OK":
                # 无音频数据且 message 非空（非 "OK"）= 错误响应
                chunk_str = json.dumps(chunk, ensure_ascii=False)[:500]
                logger.error("火山引擎 TTS 返回错误: code=%s message=%s",
                             code, msg)
                logger.error("完整响应 chunk（截断）: %s", chunk_str)
                # 针对常见错误给出修复建议
                if "resource ID is mismatched" in msg or "mismatched" in msg:
                    logger.error(
                        "音色与资源不匹配。请到火山引擎控制台「语音合成」→「应用管理」→对应应用"
                        "（App ID=%s）→「音色管理」中，确认已添加当前音色: %s\n"
                        "  - 资源 ID: %s\n"
                        "  - 若未开通，请添加该音色后再试\n"
                        "  - 或改用 --voice bd_vivi（Vivi 2.0，通用女声，控制台默认开通）\n"
                        "  - 注意: Seed-TTS 2.0 不支持方言，方言需改用 seed-tts-1.0\n"
                        "  - 也可改用 --tts-provider edge（免费、无需开通音色）",
                        app_id, voice_type, resource_id)
                elif "app_id" in msg.lower() or "access" in msg.lower() or "auth" in msg.lower():
                    logger.error(
                        "鉴权失败。请检查:\n"
                        "  - 火山引擎 App ID: %s\n"
                        "  - Access Token 是否正确\n"
                        "  - 控制台是否已开通「语音合成」服务并创建应用",
                        app_id)
                return None
            # else: 无 data 且 message 为空/"OK" = 状态/元数据块，跳过

            # 检查是否结束（sequence=-1 表示最后一个 chunk）
            sequence = chunk.get("sequence")
            if sequence == -1:
                break

        if not audio_data:
            logger.error("火山引擎 TTS 未返回音频数据（共收到 %d 个 chunk）", chunks_received)
            return None

        # 写入输出文件
        with open(out_path, "wb") as f:
            f.write(audio_data)

        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info("配音生成完成: %s (%d bytes, %d chunks)",
                        out_path, os.path.getsize(out_path), chunks_received)
            return out_path
        logger.error("配音生成失败：文件为空")
        return None
    except requests.exceptions.HTTPError as e:
        resp = e.response
        body = resp.text[:300] if resp is not None else ""
        logger.error("火山引擎 TTS HTTP 错误 %s: %s",
                     resp.status_code if resp is not None else "?", body)
        if resp is not None and resp.status_code == 401:
            logger.error("认证失败：请检查 VOLCENGINE_APP_ID 和 VOLCENGINE_ACCESS_KEY 是否正确")
        return None
    except requests.exceptions.Timeout:
        logger.error("火山引擎 TTS 请求超时")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error("火山引擎 TTS 连接失败: %s", e)
        return None
    except Exception as e:
        logger.error("火山引擎 TTS 生成异常: %s", e)
        return None


def _trim_tts_to_speech(tts_path: str, tmp_dir: str) -> str:
    """去除 TTS 音频尾部的静音/杂音，只保留语音部分。

    用 ffmpeg silencedetect 探测静音段，只截断**尾部静音**（静音持续到音频结尾），
    不截断句间停顿。判断条件：静音段的 end 接近音频总时长（end >= tts_dur - 0.5）。

    返回: 截断后的音频路径（可能等于 tts_path，也可能是一个新文件）。
    """
    from . import dedup_video
    ff = dedup_video._get_ffmpeg()
    if not ff:
        return tts_path

    try:
        # silencedetect: 检测 >=1.0s 低于 -35dB 的静音段
        r = subprocess.run(
            [ff, "-i", tts_path, "-af",
             "silencedetect=noise=-35dB:d=1.0", "-f", "null", "-"],
            capture_output=True, text=True, errors="ignore", timeout=30)
        stderr = r.stderr

        # 解析 silence_start 和 silence_end（成对出现）
        segments = []  # [(start, end), ...]
        cur_start = None
        for line in stderr.splitlines():
            m = re.search(r"silence_start: ([\d.]+)", line)
            if m:
                cur_start = float(m.group(1))
            m = re.search(r"silence_end: ([\d.]+)", line)
            if m and cur_start is not None:
                segments.append((cur_start, float(m.group(1))))
                cur_start = None

        tts_dur = probe_audio_duration(tts_path)
        if tts_dur <= 0 or not segments:
            return tts_path

        # 只找尾部静音：静音段的 end 接近音频总时长（差值 < 0.5s）
        # 这样句间停顿（后面还有语音）不会被误判
        trailing_silence = None
        for s_start, s_end in reversed(segments):
            if s_end >= tts_dur - 0.5:
                # 这段静音持续到音频结尾 → 尾部静音
                trailing_silence = (s_start, s_end)
                break

        if trailing_silence is None:
            # 没有尾部静音，语音贯穿到结尾，不需要截断
            logger.debug("TTS 无尾部静音，无需截断 (dur=%.1fs, 静音段=%d)", tts_dur, len(segments))
            return tts_path

        # 截断位置 = 尾部静音起点 + 0.3s 缓冲（保留一点尾音）
        speech_end = trailing_silence[0]
        cut_at = speech_end + 0.3
        if cut_at >= tts_dur - 0.2:
            # 尾部静音很短，不值得截断
            return tts_path

        trimmed_path = os.path.join(tmp_dir, "voiceover_trimmed.mp3")
        cmd = [ff, "-y", "-loglevel", "error",
               "-i", tts_path, "-t", f"{cut_at:.3f}",
               "-c:a", "libmp3lame", "-b:a", "128k", trimmed_path]
        r2 = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=30)
        if r2.returncode == 0 and os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 0:
            trimmed_dur = probe_audio_duration(trimmed_path)
            logger.info("TTS 尾部截断: %.1fs → %.1fs (尾部静音起于 %.1fs, 截断位置 %.1fs)",
                        tts_dur, trimmed_dur, speech_end, cut_at)
            # 删除原始 TTS 文件
            try:
                os.remove(tts_path)
            except OSError:
                pass
            return trimmed_path
        else:
            logger.warning("TTS 截断失败，保留原始文件: %s",
                           r2.stderr[-200:] if r2.stderr else "unknown")
            return tts_path
    except Exception as e:
        logger.warning("TTS 尾部截断异常: %s，保留原始文件", e)
        return tts_path


def probe_audio_duration(audio_path):
    """探测音频时长（秒）。

    edge-tts 生成的是 VBR MP3，format=duration 可能返回 N/A，
    所以多路径兜底：format → stream → ffmpeg 解析。
    """
    from . import dedup_video
    ffprobe = dedup_video._get_ffprobe()

    # 路径1: format=duration（对 CBR/有元数据的文件有效）
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10)
        val = r.stdout.strip()
        if val and val != "N/A":
            return float(val)
    except Exception:
        pass

    # 路径2: stream=duration（部分音频时长在流里）
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10)
        val = r.stdout.strip()
        if val and val != "N/A":
            return float(val)
    except Exception:
        pass

    # 路径3: 用 ffmpeg 解码整段获取时长（最慢但最准，适合 VBR MP3）
    try:
        ffmpeg = dedup_video._get_ffmpeg()
        r = subprocess.run(
            [ffmpeg, "-i", audio_path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=30)
        # ffmpeg 的 stderr 里有 "time=00:00:12.34" 格式的时间
        import re
        times = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", r.stderr or "")
        if times:
            h, m, s = times[-1]
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass

    logger.warning("无法探测音频时长: %s", audio_path)
    return 0.0


def _compress_silence(audio_path, out_path, min_silence=0.3, leave_silence=0.1):
    """压缩句间静音停顿，保留人声原速。

    用 silenceremove 滤镜去除超过 min_silence 秒的静音段，只保留 leave_silence 秒。
    这样可以压缩 10-20% 的时长，且人声语速完全不变，听感自然。
    失败时返回原路径（调用方回退到纯 atempo）。
    """
    from . import dedup_video
    ffmpeg = dedup_video._get_ffmpeg()

    # silenceremove: 检测低于 -40dB 的静音段，超过 0.3s 的部分只保留 0.1s
    # leave_silence 是较新 ffmpeg 版本才支持的参数，用 try 探测兼容性
    filter_str = (f"silenceremove=stop_periods=-1:"
                  f"stop_duration={min_silence}:"
                  f"stop_threshold=-40dB:"
                  f"leave_silence={leave_silence}")

    cmd = [ffmpeg, "-y", "-i", audio_path, "-af", filter_str,
           "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                           errors="ignore", timeout=120)
    except subprocess.TimeoutExpired:
        logger.warning("静音压缩超时，跳过（回退到纯 atempo）")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return None

    if r.returncode != 0 or not os.path.exists(out_path):
        # leave_silence 不被旧版 ffmpeg 支持，回退到不带该参数的写法
        logger.debug("silenceremove(leave_silence) 失败，尝试兼容写法...")
        filter_str_compat = (f"silenceremove=stop_periods=-1:"
                             f"stop_duration={min_silence}:"
                             f"stop_threshold=-40dB")
        cmd_compat = [ffmpeg, "-y", "-i", audio_path, "-af", filter_str_compat,
                      "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", out_path]
        try:
            r2 = subprocess.run(cmd_compat, capture_output=True, encoding="utf-8",
                                errors="ignore", timeout=120)
        except subprocess.TimeoutExpired:
            logger.warning("静音压缩(兼容)超时，跳过")
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            return None
        if r2.returncode != 0 or not os.path.exists(out_path):
            logger.warning("静音压缩失败，跳过（回退到纯 atempo）: %s",
                           r2.stderr[-200:] if r2.stderr else "")
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            return None

    out_dur = probe_audio_duration(out_path)
    if out_dur <= 0:
        logger.warning("静音压缩后文件损坏，跳过")
        try:
            os.remove(out_path)
        except OSError:
            pass
        return None

    return out_path


def fit_audio_to_duration(audio_path, target_duration, out_path):
    """用分级策略调整音频时长以匹配视频时长。

    分级策略（ratio = 配音时长 / 视频时长）：
      - ratio < 0.3:  配音远短于视频 → 原速 + 静音填充
      - ratio > 3.0:  配音远长于视频 → 截断
      - 0.3 ≤ ratio < 0.98:  配音偏短 → atempo 慢放
      - 0.98 ≤ ratio ≤ 1.02: 差距<2% → 不调整
      - 1.02 < ratio ≤ 1.10:  超长<10% → 温和 atempo（听感几乎无变化）
      - 1.10 < ratio ≤ 1.25:  超长10-25% → silenceremove 去停顿 + 温和 atempo
      - 1.25 < ratio ≤ 1.50:  超长25-50% → silenceremove + atempo(≤1.3)
      - 1.50 < ratio ≤ 3.0:   超长>50% → silenceremove + atempo(≤1.5) + 截断兜底
    """
    from . import dedup_video
    ffmpeg = dedup_video._get_ffmpeg()
    audio_dur = probe_audio_duration(audio_path)
    logger.info("配音时长匹配: audio=%.2fs target=%.2fs", audio_dur, target_duration)
    if audio_dur <= 0 or target_duration <= 0:
        logger.warning("时长探测失败，跳过配音时长调整 (audio=%s, target=%s)", audio_dur, target_duration)
        return audio_path

    ratio = audio_dur / target_duration
    if abs(ratio - 1.0) < 0.02:  # 差距 < 2% 不调整
        logger.info("配音时长与视频差距<2%%，无需调整")
        return audio_path

    # === 极端比例保护 ===
    if ratio < 0.3:
        logger.warning("配音时长 %.2fs 远短于视频 %.2fs (ratio=%.3f)，"
                       "拉伸会让人声变成杂音。改为：原速播放 + 尾部静音填充",
                       audio_dur, target_duration, ratio)
        return _pad_audio_with_silence(audio_path, target_duration, out_path)
    if ratio > 3.0:
        logger.warning("配音时长 %.2fs 远长于视频 %.2fs (ratio=%.3f)，"
                       "压缩会让人声变尖锐。改为：截断到视频时长",
                       audio_dur, target_duration, ratio)
        return _trim_audio_to_duration(audio_path, target_duration, out_path)

    # === 分级策略 ===
    # 配音超长时（ratio > 1.10）：先 silenceremove 压缩句间停顿（人声原速不变）
    work_path = audio_path
    silence_compressed = False
    if ratio > 1.10:
        logger.info("配音超长 %.0f%%，先压缩句间停顿（人声原速不变）...",
                    (ratio - 1) * 100)
        silenced_path = os.path.join(os.path.dirname(out_path),
                                     "_silence_compressed.mp3")
        sr = _compress_silence(work_path, silenced_path)
        if sr:
            work_path = sr
            silence_compressed = True
            new_dur = probe_audio_duration(work_path)
            new_ratio = new_dur / target_duration if target_duration > 0 else ratio
            logger.info("静音压缩后: %.2fs → %.2fs (ratio %.3f → %.3f)",
                        audio_dur, new_dur, ratio, new_ratio)
            # 压缩后可能已接近目标（句间停顿多的情况）
            if abs(new_ratio - 1.0) < 0.02:
                logger.info("静音压缩后已接近目标时长，无需 atempo")
                # 重命名到 out_path
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                    os.rename(work_path, out_path)
                except OSError:
                    pass
                return out_path
            ratio = new_ratio  # 用压缩后的 ratio 继续计算 atempo

    # 计算 atempo（配音超长时限制上限，避免人声变尖锐）
    # ratio > 1 表示需要加速（atempo > 1）
    atempo_val = ratio  # 默认按实际 ratio
    if ratio > 1.0:
        # 超长档位限制 atempo 上限
        original_ratio = audio_dur / target_duration
        if original_ratio <= 1.25:
            atempo_cap = 1.15   # 超长 10-25%：温和
        elif original_ratio <= 1.50:
            atempo_cap = 1.30   # 超长 25-50%：中等
        else:
            atempo_cap = 1.50   # 超长 > 50%：激进但不超过 1.5
        atempo_val = min(ratio, atempo_cap)
        if atempo_val < ratio:
            logger.info("atempo 限制在 %.3f（实际需要 %.3f），剩余通过截断兜底",
                        atempo_val, ratio)

    # atempo 范围 0.5-2.0，超出用多个串联
    atempos = []
    remaining = atempo_val
    while remaining > 2.0:
        atempos.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        atempos.append(0.5)
        remaining /= 0.5
    atempos.append(remaining)
    atempo_filter = ",".join(f"atempo={v:.4f}" for v in atempos)

    strategy = "silenceremove+atempo" if silence_compressed else "atempo"
    logger.info("调整配音时长: %.2fs -> %.2fs (ratio=%.3f, %s=%s)",
                audio_dur, target_duration, ratio, strategy, atempo_filter)

    cmd = [ffmpeg, "-y", "-i", work_path, "-filter:a", atempo_filter,
           "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                           errors="ignore", timeout=120)
    except subprocess.TimeoutExpired:
        logger.error("调整配音时长超时（120s），回退到原始音频")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return audio_path

    if r.returncode != 0 or not os.path.exists(out_path):
        logger.error("调整配音时长失败: %s", r.stderr[-300:] if r.stderr else "")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return audio_path

    # 验证输出文件有效
    out_dur = probe_audio_duration(out_path)
    if out_dur <= 0:
        logger.error("调整后的配音文件无法解码（损坏），回退到原始音频: %s", out_path)
        try:
            os.remove(out_path)
        except OSError:
            pass
        return audio_path

    # 如果 atempo 被限制上限后仍超长，截断到目标时长
    if out_dur > target_duration * 1.05:
        logger.info("atempo 后仍超长 (%.2fs > %.2fs)，截断到目标时长",
                    out_dur, target_duration)
        trim_tmp = os.path.join(os.path.dirname(out_path), "_trim_tmp.mp3")
        trim_result = _trim_audio_to_duration(out_path, target_duration, trim_tmp)
        if trim_result and trim_result != out_path and os.path.exists(trim_result):
            try:
                os.remove(out_path)
            except OSError:
                pass
            try:
                os.rename(trim_result, out_path)
            except OSError:
                pass
        out_dur = probe_audio_duration(out_path)

    # 清理静音压缩的临时文件
    if silence_compressed and work_path != audio_path:
        try:
            if os.path.exists(work_path):
                os.remove(work_path)
        except OSError:
            pass

    if abs(out_dur - target_duration) / target_duration > 0.15:
        logger.warning("调整后配音时长 %.2fs 与目标 %.2fs 偏差过大，但仍使用",
                       out_dur, target_duration)

    logger.info("配音时长调整完成: %.2fs -> %.2fs", audio_dur, out_dur)
    return out_path


def _pad_audio_with_silence(audio_path, target_duration, out_path):
    """用原速音频 + 尾部静音填充到目标时长。

    用于配音远短于视频的场景：保持人声正常语速，剩余部分静音。
    避免用 atempo 极端慢放（ratio<0.3）导致人声变成低频杂音。

    使用 anullsrc 生成静音 + concat 拼接，避免 apad 在某些 ffmpeg 构建下
    循环原音频而非补静音。
    """
    from . import dedup_video
    ffmpeg = dedup_video._get_ffmpeg()
    audio_dur = probe_audio_duration(audio_path)
    if audio_dur <= 0:
        return audio_path

    pad_dur = max(0.0, target_duration - audio_dur)
    if pad_dur < 0.1:
        # 差距很小，直接复制原文件
        try:
            import shutil
            shutil.copyfile(audio_path, out_path)
            return out_path
        except Exception:
            return audio_path

    # 用 anullsrc 生成 pad_dur 秒静音，与原音频 concat 拼接
    # 避免 apad 在某些 ffmpeg 版本下循环原音频而非补静音
    cmd = [ffmpeg, "-y",
           "-i", audio_path,
           "-f", "lavfi", "-t", f"{pad_dur:.3f}",
           "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
           "-filter_complex",
           "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
           "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a1];"
           "[a0][a1]concat=n=2:v=0:a=1[aout]",
           "-map", "[aout]",
           "-ar", "44100", "-ac", "2", "-b:a", "192k",
           out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                           errors="ignore", timeout=120)
    except subprocess.TimeoutExpired:
        logger.error("静音填充超时，回退到原始音频")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return audio_path

    if r.returncode != 0 or not os.path.exists(out_path):
        logger.error("静音填充失败: %s", r.stderr[-300:] if r.stderr else "")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return audio_path

    out_dur = probe_audio_duration(out_path)
    if out_dur <= 0:
        logger.error("静音填充后文件损坏，回退到原始音频")
        try:
            os.remove(out_path)
        except OSError:
            pass
        return audio_path

    logger.info("静音填充完成: %.2fs 原音 + %.2fs 静音 = %.2fs",
                audio_dur, pad_dur, out_dur)
    return out_path


def _trim_audio_to_duration(audio_path, target_duration, out_path):
    """截断音频到目标时长（配音远长于视频时使用）。

    避免用 atempo 极端加速（ratio>3.0）导致人声变尖锐像花栗鼠。
    """
    from . import dedup_video
    ffmpeg = dedup_video._get_ffmpeg()

    cmd = [ffmpeg, "-y", "-i", audio_path,
           "-t", f"{target_duration:.3f}",
           "-ar", "44100", "-ac", "2", "-b:a", "192k",
           "-c:a", "libmp3lame",
           out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                           errors="ignore", timeout=120)
    except subprocess.TimeoutExpired:
        logger.error("音频截断超时，回退到原始音频")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return audio_path

    if r.returncode != 0 or not os.path.exists(out_path):
        logger.error("音频截断失败: %s", r.stderr[-300:] if r.stderr else "")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return audio_path

    out_dur = probe_audio_duration(out_path)
    if out_dur <= 0:
        logger.error("截断后文件损坏，回退到原始音频")
        try:
            os.remove(out_path)
        except OSError:
            pass
        return audio_path

    logger.info("音频截断完成: -> %.2fs", out_dur)
    return out_path


def replace_video_audio(video_path, new_audio_path, out_path,
                        keep_bg_audio=False, bg_volume=0.15):
    """替换视频的音频轨道。

    参数:
        video_path: 原视频
        new_audio_path: 新配音音频
        out_path: 输出视频路径
        keep_bg_audio: True=保留原音频作为BGM（降低音量混合）；False=完全替换
        bg_volume: BGM 音量（0-1，仅 keep_bg_audio=True 时有效）
    返回:
        out_path（成功）或 None（失败）
    """
    from . import dedup_video
    ffmpeg = dedup_video._get_ffmpeg()

    if keep_bg_audio:
        # 混合：原音频降低音量 + 配音叠加
        # [0:a]原音频 volume 降音 → [bg]
        # [1:a]配音 → [voice]
        # [bg][voice]amix → [mixed]
        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", new_audio_path,
            "-filter_complex",
            f"[0:a]volume={bg_volume}[bg];"
            f"[1:a]volume=1.0[voice];"
            f"[bg][voice]amix=inputs=2:duration=first:dropout_transition=0[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            out_path,
        ]
        logger.info("混合音频: 原BGM音量=%.0f%% + 配音", bg_volume * 100)
    else:
        # 完全替换：以视频时长为准，TTS 短则补静音、长则截断
        # 避免 -shortest 在 TTS 短时截短视频画面，或 TTS 长时尾部杂音覆盖全程
        video_dur = dedup_video.probe_duration(video_path)
        if video_dur > 0:
            # 探测 TTS 实际时长：短则用 anullsrc 生成静音 + concat 拼接；长则 atrim 截断
            # 注意：不用 apad（某些 ffmpeg 构建会循环原音频而非补静音，导致尾部重复）
            # 注意：不用 silenceremove/areverse 去除尾部杂音，会误伤语音末尾停顿
            tts_dur = probe_audio_duration(new_audio_path)
            if tts_dur > 0 and tts_dur > video_dur:
                # TTS 比视频长：截断到视频时长（保留开头语音，丢弃尾部）
                cmd = [
                    ffmpeg, "-y",
                    "-i", video_path,
                    "-i", new_audio_path,
                    "-filter_complex",
                    f"[1:a]atrim=0:{video_dur:.3f},asetpts=PTS-STARTPTS[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    out_path,
                ]
                logger.info("完全替换原音频 (TTS %.1fs > 视频 %.1fs, 截断)",
                            tts_dur, video_dur)
            elif tts_dur > 0 and (video_dur - tts_dur) < 0.1:
                # TTS 与视频几乎等长（差距 <0.1s）：直接用 TTS
                cmd = [
                    ffmpeg, "-y",
                    "-i", video_path,
                    "-i", new_audio_path,
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    out_path,
                ]
                logger.info("完全替换原音频 (TTS %.1fs ≈ 视频 %.1fs, 不补静音)",
                            tts_dur, video_dur)
            else:
                # TTS 比视频短：用 anullsrc 生成静音 + concat 拼接
                # 避免 apad 在某些 ffmpeg 版本下循环原音频而非补静音
                silence_dur = max(0.1, video_dur - (tts_dur if tts_dur > 0 else 0))
                cmd = [
                    ffmpeg, "-y",
                    "-i", video_path,
                    "-i", new_audio_path,
                    "-f", "lavfi", "-t", f"{silence_dur:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-filter_complex",
                    "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
                    "[2:a]aformat=sample_rates=44100:channel_layouts=stereo[a1];"
                    "[a0][a1]concat=n=2:v=0:a=1[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    out_path,
                ]
                logger.info("完全替换原音频 (TTS %.1fs + 静音 %.1fs = 视频 %.1fs)",
                            tts_dur if tts_dur > 0 else 0, silence_dur, video_dur)
        else:
            # 探测失败回退到 -shortest
            cmd = [
                ffmpeg, "-y",
                "-i", video_path,
                "-i", new_audio_path,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                out_path,
            ]
            logger.info("完全替换原音频 (shortest 回退)")

    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore")
    if r.returncode == 0 and os.path.exists(out_path):
        logger.info("音频替换完成: %s", out_path)
        return out_path
    logger.error("音频替换失败: %s", r.stderr[-500:] if r.stderr else "unknown")
    return None


def apply_voiceover(video_path, text, out_path, voice="xiaoxiao",
                    rate="+0%", pitch="+0Hz", volume="+0%",
                    keep_bg_audio=False, bg_volume=0.15,
                    fit_duration=True, tmp_dir=None, provider="edge",
                    condenser=None, use_s2s=False, elevenlabs_api_key=None):
    """完整配音流程：生成配音 → 调整时长 → 替换/混合视频音频。

    参数:
        video_path: 输入视频
        text: 配音文本
        out_path: 输出视频
        voice: 配音别名或 ShortName（edge）/ voice_type（volcengine）
        rate/pitch/volume: TTS 参数（edge 用百分比/HZ 字符串，volcengine 自动转换）
        keep_bg_audio: 是否保留原音频作为 BGM
        bg_volume: BGM 音量
        fit_duration: 是否调整配音时长以匹配视频
        tmp_dir: 临时文件目录
        provider: TTS 服务商: "edge"（默认）/ "volcengine"（火山引擎 Seed-TTS 2.0）
        condenser: 文本精简回调函数 condenser(text, target_chars) -> str。
            当配音超长 ≥ 25% 时调用，精简文本后重新 TTS，比纯 atempo 加速更自然。
            传入 None 则超长时仅用音频分级策略（silenceremove+atempo）。
    返回:
        out_path（成功）或 None（失败）
    """
    # 服务商可用性检查
    if provider == "elevenlabs":
        if not _HAS_REQUESTS:
            logger.error("requests 未安装，ElevenLabs 需要: pip install requests")
            return None
        key = elevenlabs_api_key or os.getenv("ELEVENLABS_API_KEY")
        if not key:
            logger.error("ElevenLabs 未配置 API Key！请设置环境变量 ELEVENLABS_API_KEY 或 --elevenlabs-api-key")
            return None
    elif provider == "volcengine":
        if not _HAS_REQUESTS:
            logger.error("requests 未安装，火山引擎 TTS 需要: pip install requests")
            return None
        if not (os.getenv("VOLCENGINE_APP_ID") and os.getenv("VOLCENGINE_ACCESS_KEY")):
            logger.error("火山引擎 TTS 未配置：请设置环境变量 VOLCENGINE_APP_ID 和 VOLCENGINE_ACCESS_KEY")
            return None
    else:
        if not _HAS_EDGE_TTS:
            logger.error("edge-tts 未安装，请运行: pip install edge-tts")
            return None

    if not text or not text.strip():
        logger.warning("配音文本为空，跳过")
        return None

    _tmp = tmp_dir or getattr(config, 'TEMP_DIR', tempfile.gettempdir())
    os.makedirs(_tmp, exist_ok=True)

    # 1. 生成配音 (支持 S2S 变声 或 标准 TTS)
    tts_path = os.path.join(_tmp, "voiceover_raw.mp3")
    s2s_done = False
    if use_s2s or (provider == "elevenlabs" and use_s2s):
        raw_audio_path = os.path.join(_tmp, "video_audio_extracted.mp3")
        from . import dedup_video
        ffmpeg = dedup_video._get_ffmpeg()
        cmd = [ffmpeg, "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", raw_audio_path]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=60)
            if res.returncode == 0 and os.path.exists(raw_audio_path) and os.path.getsize(raw_audio_path) > 0:
                s2s_res = speech_to_speech_elevenlabs(raw_audio_path, tts_path, voice=voice, api_key=elevenlabs_api_key)
                if s2s_res:
                    s2s_done = True
                    logger.info("Speech-to-Speech 变声成功！完全保留原视频的情感与语气起伏！")
        except Exception as e:
            logger.warning("提取视频原声音频进行 S2S 变声异常: %s", e)

    if not s2s_done:
        if not generate_tts(text, tts_path, voice, rate, pitch, volume,
                            clean=True, provider=provider, api_key=elevenlabs_api_key):
            return None

    # 1.5 去除 TTS 尾部静音/杂音：探测语音实际结束点，截断尾部
    # 火山引擎/edge-tts 生成的 MP3 尾部常有编码残留/低电平杂音，
    # 这些杂音在 apad 补静音后会保留，听起来像"循环杂音/重复音频"
    tts_path = _trim_tts_to_speech(tts_path, _tmp)

    # 2. 调整配音时长以匹配视频
    if fit_duration:
        from . import dedup_video
        video_dur = dedup_video.probe_duration(video_path)

        # 方案3分级：超长 ≥ 25%（ratio > 1.25）且有 condenser → 精简文本 + 重 TTS
        tts_dur = probe_audio_duration(tts_path)
        if (condenser and video_dur > 0 and tts_dur > 0
                and tts_dur > video_dur * 1.25):
            over_ratio = tts_dur / video_dur
            # 目标字数 = 当前字数 × (视频时长 / 配音时长)，留 5% 余量
            target_chars = max(20, round(len(text) * video_dur / tts_dur * 0.95))
            logger.warning("配音超长 %.0f%%（%.1fs vs %.1fs），触发文本精简重 TTS "
                           "（%d 字 → 目标 %d 字）",
                           (over_ratio - 1) * 100, tts_dur, video_dur,
                           len(text), target_chars)
            try:
                condensed = condenser(text, target_chars)
            except Exception as e:
                logger.warning("文本精简回调异常: %s，回退到音频分级策略", e)
                condensed = None

            if condensed and condensed.strip() and len(condensed) < len(text):
                logger.info("文本精简成功: %d 字 → %d 字，重新生成 TTS",
                            len(text), len(condensed))
                tts_path2 = os.path.join(_tmp, "voiceover_raw_condensed.mp3")
                if generate_tts(condensed, tts_path2, voice, rate, pitch, volume,
                                clean=True, provider=provider):
                    # 用精简后的 TTS 替换
                    try:
                        os.remove(tts_path)
                    except OSError:
                        pass
                    tts_path = tts_path2
                    tts_dur2 = probe_audio_duration(tts_path)
                    logger.info("精简后 TTS 时长: %.1fs（原 %.1fs，目标 %.1fs）",
                                tts_dur2, tts_dur, video_dur)
                else:
                    logger.warning("精简后 TTS 生成失败，保留原 TTS")
            else:
                logger.warning("文本精简未返回有效结果，回退到音频分级策略")

        # 音频分级策略（silenceremove + atempo 分档）
        fitted_path = os.path.join(_tmp, "voiceover_fitted.mp3")
        tts_path = fit_audio_to_duration(tts_path, video_dur, fitted_path)

    # 3. 替换/混合视频音频
    result = replace_video_audio(video_path, tts_path, out_path,
                                 keep_bg_audio, bg_volume)

    # 清理临时文件（无论成功与否都清理，最终输出是 out_path 视频）
    for p in [os.path.join(_tmp, "voiceover_raw.mp3"),
              os.path.join(_tmp, "voiceover_trimmed.mp3"),
              os.path.join(_tmp, "voiceover_raw_condensed.mp3"),
              os.path.join(_tmp, "voiceover_fitted.mp3")]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    return result
