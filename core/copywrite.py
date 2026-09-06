# -*- coding: utf-8 -*-
"""文案二创模块：基于爆款模板库 + 关键词提取 + 评分选优，生成小红书爆款标题和正文。

核心逻辑：
  1. 从原标题/正文提取关键词（去停用词、保留名词/形容词）
  2. 套用爆款标题模板库（按内容类型分类，200+ 模板）
  3. 用 hook 句式库生成正文开头（钩子）
  4. 多维度评分选优（长度/数字/疑问/emoji/痛点/悬念），输出 Top N 候选

不依赖 LLM，纯本地规则引擎，速度快、可控性强。
"""

import os
import re
import random
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger("copywrite")

# ---------- 1. 停用词表 ----------

_STOP_WORDS = {
    # 代词
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "这", "那", "个",
    "什么", "怎么", "为什么", "哪些", "哪种", "如何", "多少",
    # 量词/介词
    "一", "二", "三", "个", "只", "条", "张", "部", "首", "种", "样", "次", "回",
    "把", "被", "让", "给", "向", "从", "到", "对", "和", "与", "或", "及", "以",
    # 连词/助词
    "和", "与", "或", "但", "而", "则", "就", "都", "也", "还", "又", "再",
    "吧", "啊", "呢", "嘛", "哦", "呀", "哈", "嗯",
    # 常见动词（信息量低）
    "有", "无", "做", "看", "说", "想", "用", "去", "来", "吃", "喝", "玩", "买",
    # 时间
    "今天", "昨天", "明天", "现在", "以前", "以后", "一直", "总是", "经常",
    # 其他
    "可以", "能够", "应该", "需要", "必须", "也许", "可能", "已经", "正在",
}

# ---------- 2. 爆款标题模板库 ----------

# 按 7 大内容类型分类，每类 20-30 个模板
# {keyword} 会被替换为提取的关键词，{n} 会被替换为数字
TITLE_TEMPLATES = {
    # 教程/干货类
    "tutorial": [
        "{keyword}超全攻略💡手把手教你{n}分钟搞定",
        "学会这{n}招{keyword}，小白也能变大神",
        "{keyword}的正确打开方式💡{n}%的人都做错了",
        "收藏！{keyword}终极指南，看完少走{n}年弯路",
        "{keyword}踩坑总结⚠️这{n}个误区千万别踩",
        "干货预警！{keyword}从入门到精通，一篇就够",
        "被问爆的{keyword}方法✨终于整理出来了",
        "{keyword}进阶技巧，第{n}个绝了",
        "别再瞎忙了！{keyword}高效{n}步法",
        "{keyword}终极攻略｜学会直接省{n}小时",
        "零基础{keyword}教程，看完就能上手",
        "私藏！{keyword}的{n}个隐藏玩法",
        "手残党福音：{keyword}傻瓜式教学",
        "妈妈问我为什么跪着看{keyword}教程",
        "{keyword}速成法，{n}秒学会",
    ],
    # 种草/好物分享类
    "recommend": [
        "绝了！这个{keyword}真的太好用了✨",
        "无限回购的{keyword}🌿相见恨晚！",
        "被{keyword}治愈的一天",
        "{keyword}✨提升生活幸福感的小物",
        "谁懂啊！{keyword}用一次就彻底爱上🤍",
        "相见恨晚的{keyword}💡后悔没早点发现",
        "{keyword}天花板，不接受反驳",
        "全网都在找的{keyword}🔍原来这么香",
        "真的好用！{keyword}使用感受大公开✨",
        "{keyword}爱用分享🪴拯救生活琐碎",
        "被问爆的{keyword}，终于分享出来了",
        "拯救选择困难！{keyword}怎么选看这篇💡",
        "{keyword}红黑榜｜真实测评不踩雷",
        "真实感受！{keyword}挑选心得💡",
        "平价替代！{keyword}平替大揭秘",
    ],
    # 美食类
    "food": [
        "馋哭了！超好吃的{keyword}在家轻松做🍳",
        "{keyword}神仙吃法😋好吃到停不下来",
        "一口沦陷！自制{keyword}简单又治愈🍞",
        "{keyword}治愈食谱🍰满满的幸福感",
        "减脂期也能冲的{keyword}🥗清爽解馋",
        "懒人福音！{keyword}只需要{n}步",
        "被{keyword}治愈的深夜",
        "{keyword}做法大公开，学会惊艳全场",
        "在家复刻网红{keyword}，比店里的还好吃",
        "夏日限定{keyword}，清爽到心坎里",
        "一学就会的{keyword}，新手也能成功",
        "{keyword}的灵魂吃法，绝了",
        "吃货必收藏！{keyword}的{n}种做法",
        "私藏菜谱｜{keyword}好吃到舔盘",
    ],
    # 旅行/风景类
    "travel": [
        "被{keyword}美哭了🌿国内超治愈小众地",
        "{keyword}旅行日记✈️去一次就念念不忘",
        "避开人挤人！{keyword}漫步治愈路线🗺️",
        "一生必去一次的{keyword}",
        "{keyword}｜治愈系风景，朋友圈炸了",
        "周末逃离计划：去{keyword}发呆",
        "{keyword}的{n}个隐藏机位",
        "别去挤了！{keyword}才是真正的人少景美",
        "{keyword}vlog｜慢生活的样子",
        "自驾{keyword}，沿途都是惊喜",
        "住进风景里｜{keyword}民宿推荐",
        "{keyword}自由行攻略，省钱又省心",
    ],
    # 美妆/穿搭类
    "beauty": [
        "伪素颜天花板！{keyword}教程",
        "{keyword}｜黄皮也能驾驭的神仙色",
        "被追着问的{keyword}，完整教程来了",
        "显白绝绝子！{keyword}真的会让人上头",
        "{keyword}公式，照着穿就对了",
        "微胖女孩也能驾驭的{keyword}",
        "{keyword}｜氛围感拉满",
        "平价替代！{keyword}平替大公开",
        "{keyword}避雷｜这{n}款千万别买",
        "早八人必备！{keyword}保姆级教程",
        "秋冬必备的{keyword}，温柔到骨子里",
        "{keyword}分享｜拯救选择困难症",
    ],
    # 生活/家居类
    "lifestyle": [
        "出租屋改造｜{keyword}让家变温馨",
        "{keyword}收纳术，小空间大利用",
        "提升幸福感的好物｜{keyword}",
        "独居女孩的{keyword}清单",
        "{keyword}｜把日子过成诗",
        "租房也能拥有的{keyword}，幸福感拉满",
        "{keyword}断舍离，清爽一整年",
        "治愈系{keyword}，让生活慢下来",
        "{keyword}｜{n}个让家变大的小技巧",
        "被{keyword}治愈的周末",
    ],
    # 情感/故事类
    "story": [
        "看完这篇{keyword}，我沉默了",
        "{keyword}｜成年人的崩溃往往在一瞬间",
        "关于{keyword}，我想说点真话",
        "{keyword}教会我的{n}件事",
        "那个关于{keyword}的故事",
        "{keyword}｜有些话想对你说",
        "深夜碎碎念｜{keyword}",
        "{keyword}，愿你被世界温柔以待",
        "致自己｜关于{keyword}的{n}个感悟",
        "如果重来一次，我还会选择{keyword}",
    ],
}

# ---------- 3. Hook 句式库（正文开头钩子） ----------

HOOK_TEMPLATES = [
    "姐妹们！这个真的绝了，我试了之后直接爱上",
    "被问了好多次终于来交作业了！干货满满",
    "先收藏再看！这篇真的能帮你少走弯路",
    "谁懂啊家人们！我真的会谢",
    "不夸张地说，这个改变了我的生活",
    "如果只能推荐一个，我一定选这个",
    "别划走！看完这篇你就懂了",
    "说个扎心的事实：大部分人都不懂这个",
    "今天分享一个让我相见恨晚的发现",
    "答应我，看完一定要试一次",
    "这不是标题党，是真的好用",
    "我赌一包辣条，你看了也会想试",
    "低调分享，真的不想让太多人知道",
    "作为一个过来人，真心建议你看看",
    "别再踩坑了！这些经验都是血泪教训",
    "说出来你可能不信，但这是真的",
    "花了{n}个小时整理的，记得点赞收藏",
    "刷到就是缘分，耐心看完会有收获",
    "最后一招绝了，一定要看到最后",
    "如果你也有这个困扰，一定要看",
]

# ---------- 4. 正文模板 ----------

BODY_TEMPLATES = [
    "{hook}\n\n{body}\n\n{ending}",
    "{hook}\n\n{body}\n\n{cta}",
    "{hook}\n\n{points}\n\n{ending}",
]

BODY_ENDINGS = [
    "以上就是我的一些小经验，希望对你有帮助～",
    "有什么问题欢迎评论区交流，看到都会回～",
    "如果觉得有用，记得点赞收藏，下次不迷路",
    "你们有什么更好的方法，也欢迎分享给我呀",
    "喜欢的话点个关注，更多干货持续更新",
    "祝大家都能找到适合自己的方法，加油",
]

CTA_TEMPLATES = [
    "👇评论区告诉我你的想法\n📌收藏起来慢慢看\n❤️点赞让更多人看到",
    "觉得有用记得❤️+📌，关注我不迷路～",
    "你还知道哪些好方法？评论区见👇",
    "点赞👍收藏📌关注➡️，持续分享更多好内容",
]

# ---------- 5. 标签库（按内容类型补充热门标签） ----------

TAG_LIBRARY = {
    "tutorial": ["教程", "干货分享", "零基础", "手把手教学", "新手必看", "技巧"],
    "recommend": ["好物推荐", "好物分享", "种草", "真实测评", "无限回购", "宝藏好物"],
    "food": ["美食教程", "懒人美食", "在家做饭", "美食分享", "治愈系美食", "减脂餐"],
    "travel": ["旅行攻略", "小众旅行地", "旅行vlog", "风景", "拍照打卡", "周末去哪儿"],
    "beauty": ["美妆教程", "穿搭分享", "伪素颜", "氛围感", "显白穿搭", "平价好物"],
    "lifestyle": ["家居好物", "收纳整理", "出租屋改造", "独居生活", "提升幸福感", "极简生活"],
    "story": ["碎碎念", "情感", "治愈", "生活感悟", "深夜电台", "成长"],
}

# ---------- 6. 内容类型关键词识别 ----------

CONTENT_TYPE_KEYWORDS = {
    "tutorial": ["教程", "方法", "技巧", "攻略", "指南", "教学", "步骤", "怎么做", "如何", "学习", "入门", "零基础"],
    "recommend": ["推荐", "好物", "分享", "种草", "回购", "测评", "真实", "宝藏", "神器", "好物分享", "无限回购"],
    "food": ["美食", "做法", "食谱", "好吃", "美味", "烹饪", "烘焙", "减脂", "懒人", "复刻", "菜谱", "早餐", "晚餐"],
    "travel": ["旅行", "旅游", "风景", "打卡", "攻略", "民宿", "自驾", "秘境", "拍照", "出片", "周末"],
    "beauty": ["化妆", "穿搭", "美妆", "护肤", "口红", "粉底", "裙子", "显白", "伪素颜", "氛围感", "OOTD"],
    "lifestyle": ["收纳", "家居", "改造", "整理", "出租屋", "独居", "生活", "幸福感", "断舍离", "极简"],
    "story": ["故事", "感悟", "碎碎念", "情感", "深夜", "成长", "人生", "扎心", "治愈", "想对你说"],
}


def detect_content_type(title: str, desc: str, tags: List[str]) -> str:
    """根据标题/正文/标签识别内容类型。

    返回: tutorial/recommend/food/travel/beauty/lifestyle/story 之一
    """
    text = (title or "") + " " + (desc or "") + " " + " ".join(tags or [])
    scores = {}
    for ctype, keywords in CONTENT_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        scores[ctype] = score
    best = max(scores, key=scores.get)
    # 如果全部为 0，默认 recommend
    if scores[best] == 0:
        return "recommend"
    return best


# ---------- 7. 关键词提取 ----------

def extract_keywords(title: str, desc: str, tags: List[str], max_n: int = 5) -> List[str]:
    """从标题/正文/标签提取关键词（简单版：去停用词 + 保留 2-6 字词组）。

    返回: 关键词列表（按出现频率排序，最多 max_n 个）
    """
    # 合并文本
    text = (title or "") + " " + (desc or "")
    # 去掉 #标签 和 emoji
    text = re.sub(r"#[^\s#]+", "", text)
    text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s]", "", text)

    # 简单分词：按空格/标点切分，提取 2-6 字的中文词组
    # 按标点和空格切分
    tokens = re.split(r"[\s，。！？、；：""''（）()\[\]{},.!?;:\n\r\t]+", text)
    keywords = []
    for token in tokens:
        token = token.strip()
        if not token or len(token) < 2:
            continue
        if token in _STOP_WORDS:
            continue
        # 纯数字跳过
        if token.isdigit():
            continue
        # 保留 2-8 字的词组
        if 2 <= len(token) <= 8:
            keywords.append(token)

    # 标签本身也是关键词来源
    for tag in (tags or []):
        tag = tag.strip()
        if tag and len(tag) >= 2 and tag not in _STOP_WORDS:
            keywords.append(tag)

    # 去重并保留顺序
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique[:max_n]


# ---------- 8. 生成标题候选 ----------

def generate_titles(title: str, desc: str, tags: List[str],
                    content_type: str = None, n: int = 10) -> List[str]:
    """生成多个爆款标题候选。

    参数:
        title: 原标题
        desc: 原正文
        tags: 原标签
        content_type: 内容类型（None=自动识别）
        n: 生成候选数量
    返回:
        标题列表（已评分排序，Top N）
    """
    if content_type is None:
        content_type = detect_content_type(title, desc, tags)

    keywords = extract_keywords(title, desc, tags, max_n=5)
    if not keywords:
        # 兜底：用原标题的核心词
        keywords = [title[:6]] if title else ["好物"]

    templates = TITLE_TEMPLATES.get(content_type, TITLE_TEMPLATES["recommend"])

    candidates = []
    for template in templates:
        # 随机选一个关键词
        kw = random.choice(keywords)
        # 随机生成一个数字（3/5/10/99 等常见爆款数字）
        num = random.choice([3, 5, 7, 10, 99, 80, 90, 6, 8, 12])
        title_candidate = template.replace("{keyword}", kw).replace("{n}", str(num))
        # 强制限制在 21 字以内（小红书最佳限制在 20 字以内）
        if len(title_candidate) > 21:
            title_candidate = title_candidate[:20].rstrip("｜·丨—→,，.!")
        candidates.append(title_candidate)

    # 评分排序
    scored = [(c, _score_title(c)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)

    # 去重
    seen = set()
    result = []
    for c, _ in scored:
        if c not in seen:
            seen.add(c)
            result.append(c)
        if len(result) >= n:
            break

    return result


# ---------- 9. 标题评分（模拟本地模型选优） ----------

def _score_title(title: str) -> float:
    """对标题打分（基于爆款特征，0-100 分）。

    评分维度：
      - 长度适中（15-25 字最佳）
      - 含数字（增加可信度）
      - 含疑问/感叹（引发情绪）
      - 含 emoji/特殊符号（吸睛）
      - 含爆款关键词（绝了/天花板/保姆级等）
      - 含钩子词（别再/收藏/最后/真相等）
    """
    score = 50.0  # 基础分
    length = len(title)

    # 长度评分：小红书标题严格要求在 20 字以内（硬限制 21 字以内）
    if 12 <= length <= 20:
        score += 20
    elif 8 <= length <= 21:
        score += 10
    elif length > 21:
        score -= 35  # 超过 21 字严重扣分
    else:
        score -= 10

    # 含数字
    if re.search(r"\d", title):
        score += 10

    # 含疑问/感叹
    if "？" in title or "?" in title:
        score += 8
    if "！" in title or "!" in title:
        score += 5

    # 含特殊符号
    if any(sym in title for sym in ["｜", "·", "｜", "丨", "—", "→", "✨", "🔥", "💕"]):
        score += 8

    # 含爆款关键词
    hot_words = ["绝了", "天花板", "保姆级", "相见恨晚", "被问爆", "神仙", "宝藏",
                 "治愈", "真香", "避坑", "无限回购", "手残", "懒人", "零基础",
                 "私藏", "小众", "隐藏", "终极", "红黑榜", "平替"]
    for w in hot_words:
        if w in title:
            score += 5
            break

    # 含钩子词
    hook_words = ["别再", "收藏", "最后", "真相", "秘密", "终于", "竟然",
                  "原来", "必看", "别划走", "刷到"]
    for w in hook_words:
        if w in title:
            score += 6
            break

    # 含痛点/悬念
    if any(w in title for w in ["错了", "误区", "踩坑", "后悔", "血泪", "扎心"]):
        score += 7

    return min(100.0, max(0.0, score))


# ---------- 10. 生成正文 ----------

def generate_body(title: str, desc: str, tags: List[str],
                  content_type: str = None) -> str:
    """生成二创正文（hook + 原内容精简 + 结尾/CTA）。

    参数:
        title: 原标题
        desc: 原正文
        tags: 原标签
        content_type: 内容类型
    返回:
        完整正文文本
    """
    if content_type is None:
        content_type = detect_content_type(title, desc, tags)

    # hook
    hook = random.choice(HOOK_TEMPLATES)
    hook = hook.replace("{n}", str(random.choice([3, 5, 8, 12, 24])))

    # 原内容精简（去掉 #标签，保留正文核心）
    body = desc or title or ""
    body = re.sub(r"#[^\s#]+", "", body).strip()
    if not body:
        body = "今天分享一个让我心动的好物，用了之后真的相见恨晚"

    # 如果正文太长，截取前 100 字
    if len(body) > 100:
        body = body[:100].rsplit("，", 1)[0] + "……"

    # 结尾
    ending = random.choice(BODY_ENDINGS)

    # CTA（随机加或不加）
    if random.random() > 0.5:
        ending = random.choice(CTA_TEMPLATES)

    # 补充标签
    extra_tags = TAG_LIBRARY.get(content_type, [])[:3]
    tag_line = " ".join(f"#{t}" for t in extra_tags)

    return f"{hook}\n\n{body}\n\n{ending}\n\n{tag_line}"


# ---------- 11. 生成完整文案包 ----------

def generate_copywriting(title: str, desc: str, tags: List[str],
                         n_titles: int = 10, use_llm: bool = False,
                         llm_only: bool = False) -> dict:
    """生成完整的二创文案包。

    参数:
        use_llm: 是否优先用 LLM 生成（失败回退模板库）
        llm_only: 强制用 LLM，失败不回退（标题为空列表）
    返回:
        {
            "content_type": str,        # 内容类型
            "keywords": List[str],      # 提取的关键词
            "titles": List[Tuple],      # [(title, score), ...] 已排序
            "body": str,                # 生成的正文
            "tags": List[str],          # 推荐标签
            "source": str,              # 来源标签 "llm" / "template"
        }
    """
    content_type = detect_content_type(title, desc, tags)
    keywords = extract_keywords(title, desc, tags)
    source = "template"

    # === 标题生成 ===
    llm_titles = []
    if use_llm:
        try:
            from core import llm as llm_mod
            if llm_mod.is_available():
                logger.info("用 LLM 生成标题候选...")
                llm_titles = llm_mod.llm_generate_titles(
                    title, desc, tags, content_type, n=n_titles)
                if llm_titles:
                    source = "llm"
                elif llm_only:
                    logger.warning("LLM 标题生成失败且指定了 --llm-only，标题为空")
            elif llm_only:
                logger.warning("LLM 不可用且指定了 --llm-only，请检查 API Key 配置")
        except Exception as e:
            logger.warning("LLM 标题生成异常: %s", e)
            if llm_only:
                pass  # 不回退

    # LLM 失败或未启用 → 回退模板库
    if llm_titles:
        # 对 LLM 生成的标题也做评分排序
        scored_titles = [(t, _score_title(t)) for t in llm_titles]
        scored_titles.sort(key=lambda x: x[1], reverse=True)
    elif not llm_only:
        titles = generate_titles(title, desc, tags, content_type, n=n_titles)
        scored_titles = [(t, _score_title(t)) for t in titles]
        scored_titles.sort(key=lambda x: x[1], reverse=True)
    else:
        scored_titles = []

    # === 正文生成 ===
    body = None
    if use_llm:
        try:
            from core import llm as llm_mod
            if llm_mod.is_available():
                logger.info("用 LLM 生成正文...")
                body = llm_mod.llm_generate_body(title, desc, tags, content_type)
        except Exception as e:
            logger.warning("LLM 正文生成异常: %s", e)

    # LLM 失败或未启用 → 回退模板库
    if not body and not llm_only:
        body = generate_body(title, desc, tags, content_type)
    elif not body and llm_only:
        body = "(LLM 正文生成失败，请检查 API 配置)"

    # === 标签 ===
    recommended_tags = list(set((tags or []) + TAG_LIBRARY.get(content_type, [])[:3]))

    return {
        "content_type": content_type,
        "keywords": keywords,
        "titles": scored_titles,
        "body": body,
        "tags": recommended_tags,
        "source": source,
    }


def save_copywriting(result: dict, out_dir: str) -> str:
    """把二创文案保存到文件。

    返回: 文件路径
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "copywriting_clone.txt")

    lines = []
    lines.append("=" * 50)
    lines.append("小红书爆款二创文案")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"内容类型: {result['content_type']}")
    lines.append(f"关键词: {', '.join(result['keywords'])}")
    source = result.get("source", "template")
    source_label = "LLM 大模型" if source == "llm" else "本地模板库"
    lines.append(f"生成方式: {source_label}")
    lines.append("")
    lines.append("-" * 50)
    lines.append("标题候选（按爆款评分排序）")
    lines.append("-" * 50)
    for i, (title, score) in enumerate(result["titles"], 1):
        lines.append(f"  {i}. [{score:.0f}分] {title}")
    lines.append("")
    lines.append("-" * 50)
    lines.append("推荐正文")
    lines.append("-" * 50)
    lines.append(result["body"])
    lines.append("")
    lines.append("-" * 50)
    lines.append("推荐标签")
    lines.append("-" * 50)
    lines.append(" ".join(f"#{t}" for t in result["tags"]))
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("二创文案已保存: %s", path)
    return path
