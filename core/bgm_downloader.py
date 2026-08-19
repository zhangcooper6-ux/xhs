# -*- coding: utf-8 -*-
"""BGM 下载助手。

从用户提供的免版税音乐直链列表（assets/bgm/_sources.json）批量下载音乐，
自动按"风格_曲名.mp3"规范命名并归类到 assets/bgm/。

为什么需要这个助手：
  - Pixabay Music 等站无官方公开 API，批量爬取违反 ToS 且会被反爬
  - 商业流行歌曲（起风了/孤勇者等）受版权保护，不可自动下载
  - 本助手只下载用户**手动收集的合法直链**，把机械的重命名/归类自动化

工作流程：
  1. 用户浏览 Pixabay Music (https://pixabay.com/music/) 等免版税音乐站
  2. 找到喜欢的曲目，复制下载直链（cdn.pixabay.com/download/audio/...）
  3. 把直链按格式填入 assets/bgm/_sources.json
  4. 运行：python xhs_clone.py --download-bgm
  5. 脚本自动下载、重命名为"风格_曲名.mp3"、放入 assets/bgm/

合法音乐来源（均为免版税可商用，无需署名）：
  - Pixabay Music:  https://pixabay.com/music/       （推荐，曲库大）
  - Bensound:       https://www.bensound.com/         （部分需署名，注意许可）
  - FMA:            https://freemusicarchive.org/     （CC 协议，注意具体条款）
  - YouTube 音频库: https://studio.youtube.com/       （仅限 YouTube 视频用）
  - Musopen:        https://musopen.org/              （公共领域古典乐）
"""
import os
import re
import json
import logging
import requests

import config

logger = logging.getLogger("bgm_downloader")


# 合法免版税音乐来源（用于 --download-bgm --list-sources 时展示）
FREE_MUSIC_SOURCES = {
    "Pixabay Music": {
        "url": "https://pixabay.com/music/",
        "license": "Pixabay Content License（免版税，可商用，无需署名）",
        "note": "推荐。曲库大，按风格/情绪分类。下载页右键复制直链。",
    },
    "Bensound": {
        "url": "https://www.bensound.com/",
        "license": "部分免版税（需署名），部分付费",
        "note": "搜索时筛选 'Royalty-Free'。免费版需在视频描述署名作者。",
    },
    "Free Music Archive (FMA)": {
        "url": "https://freemusicarchive.org/",
        "license": "Creative Commons（各曲不同，注意具体条款）",
        "note": "曲库大但许可复杂，下载前确认每首的 CC 协议。",
    },
    "Musopen": {
        "url": "https://musopen.org/",
        "license": "公共领域（古典乐录音）",
        "note": "适合 古风/氛围/治愈 风格。可按作曲家/乐器筛选。",
    },
    "YouTube 音频库": {
        "url": "https://studio.youtube.com/",
        "license": "YouTube 免版税（仅限 YouTube 视频内使用）",
        "note": "注意：仅限 YouTube 平台使用，不适合小红书发布。",
    },
}


def _sanitize_filename(name):
    """清洗文件名：去掉非法字符。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("._") or "untitled"


def load_sources(sources_path=None):
    """加载 _sources.json。

    格式：
    [
      {
        "url": "https://cdn.pixabay.com/download/audio/xxx.mp3?filename=xxx.mp3",
        "style": "治愈",
        "name": "暖阳",
        "note": "可选备注"
      },
      ...
    ]
    """
    if sources_path is None:
        sources_path = os.path.join(config.ASSETS_DIR, "bgm", "_sources.json")
    if not os.path.exists(sources_path):
        return None, sources_path
    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return None, sources_path
        return data, sources_path
    except (json.JSONDecodeError, OSError) as e:
        logger.error("读取 _sources.json 失败: %s", e)
        return None, sources_path


def download_one(url, save_path, timeout=60):
    """下载单个文件到指定路径。返回 True/False。"""
    if not url or not url.startswith(("http://", "https://")):
        logger.error("无效的 URL: %s", url)
        return False
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    headers = dict(config.HEADERS)
    # 部分音乐站需要 Referer
    if "pixabay.com" in url:
        headers["Referer"] = "https://pixabay.com/"
    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
            if r.status_code != 200:
                logger.error("下载失败 [%d]: %s", r.status_code, url)
                return False
            with open(save_path, "wb") as f:
                total = 0
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
            if total < 1024:
                logger.warning("文件过小 (%d 字节)，可能是错误页: %s", total, url)
                return False
            logger.info("下载完成: %s (%d KB)", os.path.basename(save_path), total // 1024)
            return True
    except requests.RequestException as e:
        logger.error("下载异常 %s: %s", url, e)
        return False


def download_all(bgm_dir=None, sources_path=None, skip_existing=True, dry_run=False):
    """批量下载 _sources.json 中的所有音乐。

    参数:
        bgm_dir: BGM 目标目录（默认 config.BGM.dir）
        sources_path: _sources.json 路径
        skip_existing: 已存在则跳过（默认 True）
        dry_run: 仅打印将执行的下载，不实际下载

    返回:
        (成功数, 跳过数, 失败数)
    """
    bgm_dir = bgm_dir or config.BGM.get("dir", os.path.join(config.ASSETS_DIR, "bgm"))
    os.makedirs(bgm_dir, exist_ok=True)

    sources, sources_path = load_sources(sources_path)
    if sources is None:
        logger.error("未找到 _sources.json 或格式无效: %s", sources_path)
        logger.info("请先创建该文件，格式见 core/bgm_downloader.py 文档")
        return 0, 0, 0

    if not sources:
        logger.warning("_sources.json 为空，无可下载条目")
        return 0, 0, 0

    logger.info("从 %s 读取到 %d 个条目", sources_path, len(sources))
    logger.info("目标目录: %s", bgm_dir)
    if dry_run:
        logger.info("【DRY-RUN 模式】仅预览，不实际下载")
    print()

    ok, skipped, failed = 0, 0, 0
    for i, item in enumerate(sources, 1):
        url = item.get("url", "").strip()
        style = item.get("style", "未分类").strip() or "未分类"
        name = item.get("name", "").strip() or f"track_{i}"
        note = item.get("note", "")

        if not url:
            logger.warning("[%d/%d] 跳过：无 URL  (name=%s)", i, len(sources), name)
            skipped += 1
            continue

        style_clean = _sanitize_filename(style)
        name_clean = _sanitize_filename(name)
        filename = f"{style_clean}_{name_clean}.mp3"
        save_path = os.path.join(bgm_dir, filename)

        prefix = f"[{i}/{len(sources)}]"
        if skip_existing and os.path.exists(save_path) and os.path.getsize(save_path) > 1024:
            logger.info("%s 已存在，跳过: %s", prefix, filename)
            skipped += 1
            continue

        logger.info("%s 下载: [%s] %s%s",
                    prefix, style_clean, name_clean,
                    f"  ({note})" if note else "")

        if dry_run:
            print(f"   → {url}")
            print(f"   → 保存为: {filename}")
            ok += 1
            continue

        if download_one(url, save_path):
            ok += 1
        else:
            failed += 1
            # 删除可能的残留
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except OSError:
                    pass

    print()
    logger.info("=" * 50)
    logger.info("完成: 成功 %d, 跳过 %d, 失败 %d", ok, skipped, failed)
    logger.info("=" * 50)
    return ok, skipped, failed


def print_sources_guide():
    """打印合法免版税音乐来源指南。"""
    print("=" * 60)
    print("合法免版税音乐来源（可商用，无需署名）")
    print("=" * 60)
    print()
    print("请到以下网站浏览音乐，复制下载直链填入 _sources.json")
    print()
    for name, info in FREE_MUSIC_SOURCES.items():
        print(f"【{name}】")
        print(f"  网址: {info['url']}")
        print(f"  许可: {info['license']}")
        print(f"  说明: {info['note']}")
        print()
    print("-" * 60)
    print("推荐工作流（以 Pixabay 为例）：")
    print("  1. 访问 https://pixabay.com/music/")
    print("  2. 按情绪/风格筛选（如 Calm, Happy, Cinematic）")
    print("  3. 试听选中曲目，点下载按钮")
    print("  4. 复制浏览器地址栏的直链（cdn.pixabay.com/download/audio/...）")
    print("  5. 填入 assets/bgm/_sources.json：")
    print('     {"url": "复制的直链", "style": "治愈", "name": "暖阳"}')
    print("  6. 运行: python xhs_clone.py --download-bgm")
    print()
    print("也可以用 --download-bgm --dry-run 预览将下载的文件，不实际下载。")


def create_sources_template(sources_path=None):
    """创建 _sources.json 模板文件（若不存在）。"""
    if sources_path is None:
        sources_path = os.path.join(config.ASSETS_DIR, "bgm", "_sources.json")
    os.makedirs(os.path.dirname(sources_path), exist_ok=True)
    if os.path.exists(sources_path):
        logger.info("_sources.json 已存在，不覆盖: %s", sources_path)
        return False

    template = [
        {
            "url": "https://cdn.pixabay.com/download/audio/2023/xxx.mp3?filename=example.mp3",
            "style": "治愈",
            "name": "示例曲名",
            "note": "这是示例条目，请替换为真实直链"
        },
        {
            "url": "",
            "style": "欢快",
            "name": "你的曲名"
        }
    ]
    try:
        with open(sources_path, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
        logger.info("已创建 _sources.json 模板: %s", sources_path)
        logger.info("请编辑该文件，填入真实下载直链后运行 --download-bgm")
        return True
    except OSError as e:
        logger.error("创建模板失败: %s", e)
        return False
