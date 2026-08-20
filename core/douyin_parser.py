# -*- coding: utf-8 -*-
"""抖音视频解析与下载。

支持输入：
  - 抖音分享文本（含短链 https://v.douyin.com/xxx/）
  - 抖音网页 URL（https://www.douyin.com/video/xxx）

输出：与 xhs_parser.NoteData 兼容的对象（note_type="video"）

实现思路（多策略回退）：
  策略1: 短链 → 302 重定向拿到真实 URL → 解析 video_id
  策略2: 用移动端 UA 请求分享页，从 HTML 里正则提取视频 URL（抖音对移动端 UA 较宽松）
  策略3: 网页 URL 用桌面 UA + Referer 请求，从 RENDER_DATA 提取

抖音反爬较严，网页版对未登录请求返回空壳。
移动端 UA + 短链是目前最稳定的免登录方案。
"""
import os
import re
import json
import time
import logging
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import config
from .xhs_parser import NoteData

logger = logging.getLogger("douyin_parser")


# 抖音 URL 模式
DOUYIN_PATTERNS = [
    re.compile(r"v\.douyin\.com/([A-Za-z0-9_-]+)/"),   # 短链（支持 _ 和 -）
    re.compile(r"douyin\.com/video/(\d+)"),             # 网页 URL
    re.compile(r"douyin\.com/discover\?modal_id=(\d+)"),
    re.compile(r"iesdouyin\.com/share/video/(\d+)"),    # iesdouyin 分享页
]

# 移动端 UA（抖音对移动端 UA 较宽松，能返回带视频的 HTML）
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

# 桌面 UA
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def is_douyin_input(text):
    """判断输入是否为抖音链接。"""
    return "douyin.com" in text or "iesdouyin.com" in text


def extract_video_id(text):
    """从输入文本提取抖音视频 ID。

    短链需要先跟随重定向拿到真实 URL。
    返回: video_id 或 None
    """
    text = text.strip()
    # 网页 URL 直接正则
    for pat in DOUYIN_PATTERNS[1:]:
        m = pat.search(text)
        if m:
            return m.group(1)
    # 短链需要重定向
    m = re.search(r"https?://v\.douyin\.com/[A-Za-z0-9_-]+/", text)
    if m:
        short_url = m.group(0)
        try:
            r = requests.head(short_url, allow_redirects=True, timeout=10,
                              headers={"User-Agent": MOBILE_UA})
            final_url = r.url
            logger.info("短链重定向: %s", final_url)
            for pat in DOUYIN_PATTERNS[1:]:
                m2 = pat.search(final_url)
                if m2:
                    return m2.group(1)
        except Exception as e:
            logger.warning("短链重定向失败: %s", e)
    return None


def _resolve_short_url(short_url):
    """解析短链，返回最终 URL 和 video_id。"""
    try:
        r = requests.head(short_url, allow_redirects=True, timeout=10,
                          headers={"User-Agent": MOBILE_UA})
        final_url = r.url
        for pat in DOUYIN_PATTERNS[1:]:
            m = pat.search(final_url)
            if m:
                return final_url, m.group(1)
    except Exception as e:
        logger.warning("解析短链失败: %s", e)
    return None, None


def _fetch_with_mobile_ua(url):
    """用移动端 UA 请求页面（抖音对移动端 UA 较宽松）。"""
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.douyin.com/",
    }
    r = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def _fetch_with_desktop_ua(url):
    """用桌面 UA 请求页面。"""
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.douyin.com/",
    }
    r = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def _extract_render_data(html):
    """从分享页 HTML 提取 RENDER_DATA 或 _ROUTER_DATA。"""
    # 方案1: <script id="RENDER_DATA" type="application/json">...</script>
    m = re.search(r'<script id="RENDER_DATA"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            from urllib.parse import unquote
            return json.loads(unquote(m.group(1)))
        except Exception:
            pass
    # 方案2: window._ROUTER_DATA = {...}（m.douyin.com / iesdouyin.com 使用）
    # 注意：JSON 中可能包含嵌套的 }，需要用非贪婪匹配到 </script> 前结束
    m = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>', html, re.DOTALL)
    if not m:
        m = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
    if not m:
        m = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception as e:
            logger.debug("_ROUTER_DATA JSON 解析失败: %s", e)
    return None


def _fetch_m_douyin_share(video_id):
    """请求 m.douyin.com 移动端分享页，返回 (html, router_data)。

    m.douyin.com/share/video/{id} 对未登录用户友好，SSR 返回带 _ROUTER_DATA 的 HTML。
    这是 2026 年最稳定的免登录解析方案。
    """
    url = f"https://m.douyin.com/share/video/{video_id}"
    html = _fetch_with_mobile_ua(url)
    data = _extract_render_data(html)
    return html, data


def _extract_item_from_router_data(data):
    """从 _ROUTER_DATA 中提取视频 item（item_list[0]）。

    数据路径：loaderData["video_(id)/page"].videoInfoRes.item_list[0]
    注意 loaderData 的 key 可能是 "video_(id)/page" 或类似格式，需遍历。
    """
    if not data:
        return None

    loader_data = data.get("loaderData") or {}
    # 遍历 loaderData 的所有 key 找 videoInfoRes
    for key, val in loader_data.items():
        if not isinstance(val, dict):
            continue
        video_info_res = val.get("videoInfoRes") or {}
        item_list = video_info_res.get("item_list") or []
        if item_list:
            return item_list[0]
    return None


def _pick_video_url_no_wm(item):
    """从 item 中提取无水印视频 URL。

    play_addr.url_list[0] 默认是 playwm（带水印），替换为 play 得到无水印版本。
    """
    video = item.get("video") or {}

    # 优先从 bit_rate 列表找无水印 URL（某些端点会返回）
    bit_rate = video.get("bit_rate") or video.get("bitRate") or []
    if bit_rate and isinstance(bit_rate, list):
        for br in bit_rate:
            play = br.get("playAddr") or br.get("play_addr") or {}
            urls = play.get("url_list") or []
            if urls:
                url = urls[0]
                # playwm → play 去水印
                if "playwm" in url:
                    url = url.replace("playwm", "play")
                return url

    # 回退到 play_addr
    play_addr = video.get("playAddr") or video.get("play_addr") or {}
    urls = play_addr.get("url_list") or []
    if urls:
        url = urls[0]
        # playwm → play 去水印
        if "playwm" in url:
            url = url.replace("playwm", "play")
        return url

    # download_addr
    download_addr = video.get("downloadAddr") or video.get("download_addr") or {}
    urls = download_addr.get("url_list") or []
    if urls:
        return urls[0]

    return None


def _extract_meta_from_item(item):
    """从 item 中提取标题、描述、标签、作者等元数据。"""
    desc = (item.get("desc") or "").strip()
    title = desc.split("\n")[0][:40] if desc else ""
    tags = []
    text_extra = item.get("textExtra") or item.get("text_extra") or []
    for te in text_extra:
        if isinstance(te, dict):
            hashtag = te.get("hashtagName") or te.get("hashtag_name")
            if hashtag:
                tags.append(hashtag)
    author = item.get("author") or {}
    author_name = author.get("nickname") or ""
    return title, desc, tags, author_name


# ---------- Playwright 策略：绕过 byted_acrawler JS 校验 ----------
#
# 2026 年抖音 www.douyin.com 对未登录请求返回 byted_acrawler 混淆 JS，
# 执行后生成 __ac_signature 签名 cookie 再 reload 才能拿到真实页面。
# 纯 requests 无法执行 JS，需要用无头浏览器执行校验流程。
# m.douyin.com / iesdouyin.com 虽不触发 JS 校验，但只返回页面骨架，
# 不再 SSR videoInfoRes，因此也拿不到视频数据。
# playwright 是当前最稳定的免登录解析方案。

_PLAYWRIGHT_DISABLED = False


def _fetch_with_ttwid_api(video_id):
    """使用 ByteDance 官方访客 ttwid 凭证 + 网页 Web 详情 API 提取视频数据。

    这是 2026 年最快、最稳定的免登录纯 HTTP 解析方案。
    """
    try:
        sess = requests.Session()
        sess.trust_env = False
        headers = {
            "User-Agent": DESKTOP_UA,
            "Referer": "https://www.douyin.com/",
            "Accept": "application/json, text/plain, */*"
        }
        # 1. 注册获取 ttwid 访客 Cookie
        tt_res = sess.post(
            "https://ttwid.bytedance.com/ttwid/union/register/",
            json={"region": "cn", "aid": 1768, "needFp": True, "fp": "verify_123456789", "service": "www.douyin.com"},
            headers=headers,
            timeout=5
        )
        ttwid_val = sess.cookies.get("ttwid") or (tt_res.cookies.get("ttwid") if hasattr(tt_res, "cookies") else None)
        if ttwid_val:
            headers["Cookie"] = f"ttwid={ttwid_val}; msToken=1234567890abcdef;"

        # 2. 请求网页 aweme detail 接口
        detail_url = (
            f"https://www.douyin.com/aweme/v1/web/aweme/detail/?"
            f"aweme_id={video_id}&device_platform=webapp&aid=6383&channel=channel_pc_web"
        )
        r = sess.get(detail_url, headers=headers, timeout=10)
        if r.status_code == 200 and r.text:
            data = r.json()
            aweme = data.get("aweme_detail")
            if aweme:
                logger.info("策略0 (ttwid Web API): 提取成功")
                return aweme
    except Exception as e:
        logger.warning("ttwid Web API 策略失败: %s", e)
    return None


def _fetch_with_playwright(video_id, timeout_ms=45000, max_retries=2):
    """用 playwright 无头浏览器访问 www.douyin.com/video/{id}，
    自动执行 byted_acrawler JS 校验，返回真实页面 HTML。

    需要先安装: pip install playwright && playwright install chromium
    返回: HTML 字符串；playwright 不可用或超时返回 None
    """
    global _PLAYWRIGHT_DISABLED
    if _PLAYWRIGHT_DISABLED:
        logger.debug("Playwright 环境已被禁用，跳过策略")
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("playwright 未安装，跳过无头浏览器策略")
        return None

    url = f"https://www.douyin.com/video/{video_id}"

    for attempt in range(1, max_retries + 1):
        logger.info("playwright 策略: 访问 %s (attempt %d/%d)", url, attempt, max_retries)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=MOBILE_UA,
                    viewport={"width": 390, "height": 844},
                    is_mobile=True,
                )
                page = context.new_page()
                # 用 domcontentloaded 快速完成导航，不等所有网络请求
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if response and response.status != 200:
                    logger.warning("playwright 返回非 200: %s", response.status)
                    browser.close()
                    continue
                # 等待 _ROUTER_DATA 出现（JS 校验 + reload 后会写入）
                try:
                    page.wait_for_function(
                        "document.documentElement.innerHTML.includes('_ROUTER_DATA')",
                        timeout=30000,
                    )
                except Exception:
                    logger.debug("等待 _ROUTER_DATA 超时，额外等待 5s")
                    page.wait_for_timeout(5000)
                html = page.content()
                browser.close()
                if "_ROUTER_DATA" in html:
                    logger.info("playwright 页面大小: %d bytes (含 _ROUTER_DATA)", len(html))
                    return html
                logger.warning("playwright 页面无 _ROUTER_DATA (size=%d)", len(html))
        except (PermissionError, OSError) as e:
            logger.warning("Playwright 系统权限拒绝 (%s)，禁用 Playwright 策略: %s", type(e).__name__, e)
            _PLAYWRIGHT_DISABLED = True
            break
        except Exception as e:
            logger.warning("playwright 策略失败 (attempt %d): %s", attempt, e)
            try:
                browser.close()
            except Exception:
                pass
    return None


def _fetch_and_download_with_playwright(video_id, downloads=None, timeout_ms=60000):
    global _PLAYWRIGHT_DISABLED
    if _PLAYWRIGHT_DISABLED:
        return None, {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("playwright 未安装，跳过无头浏览器策略")
        return None, {}

    url = f"https://www.douyin.com/video/{video_id}"
    logger.info("playwright 统一会话: 访问 %s", url)
    html = None
    download_results = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=MOBILE_UA,
                viewport={"width": 390, "height": 844},
                is_mobile=True,
            )
            page = context.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            if response and response.status != 200:
                logger.warning("playwright 返回非 200: %s", response.status)
                browser.close()
                return None, {}
            # 等待 JS 校验完成 + reload 后页面稳定
            page.wait_for_timeout(5000)
            html = page.content()
            logger.info("playwright 页面大小: %d bytes", len(html))

            # 在同一 context 内下载文件（共享 cookie + TLS 指纹正常）
            if downloads:
                cookies = context.cookies()
                logger.info("浏览器 cookie 数: %d", len(cookies))
                for dl_url, dl_path in downloads:
                    try:
                        r = context.request.get(dl_url, timeout=30000)
                        if r.ok:
                            body = r.body()
                            with open(dl_path, "wb") as f:
                                f.write(body)
                            download_results[dl_path] = True
                            logger.info("playwright 下载完成: %s (%d bytes)",
                                        dl_path, len(body))
                        else:
                            download_results[dl_path] = False
                            logger.warning("playwright 下载失败: %s status=%s",
                                           dl_path, r.status)
                    except Exception as e:
                        download_results[dl_path] = False
                        logger.warning("playwright 下载异常 %s: %s", dl_path, e)

            browser.close()
    except (PermissionError, OSError) as e:
        logger.warning("Playwright 系统权限拒绝 (%s)，禁用 Playwright 策略: %s", type(e).__name__, e)
        _PLAYWRIGHT_DISABLED = True
    except Exception as e:
        logger.warning("playwright 统一会话失败: %s", e)
    return html, download_results


def _download_with_playwright(url, save_path, timeout_ms=30000):
    """单独用 playwright 下载文件（需要独立启动浏览器，较慢）。

    优先使用 _fetch_and_download_with_playwright 在同一会话内下载。
    此函数仅在已经通过其他方式拿到 HTML 后，单独下载文件时使用。

    返回: 成功 True / 失败 False
    """
    global _PLAYWRIGHT_DISABLED
    if _PLAYWRIGHT_DISABLED:
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # 先访问 douyin.com 拿 cookie，再下载 CDN 资源
            context = browser.new_context(
                user_agent=MOBILE_UA,
                viewport={"width": 390, "height": 844},
                is_mobile=True,
            )
            page = context.new_page()
            try:
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded",
                          timeout=timeout_ms)
                page.wait_for_timeout(3000)
            except Exception:
                pass
            r = context.request.get(url, timeout=timeout_ms)
            if r.ok:
                body = r.body()
                with open(save_path, "wb") as f:
                    f.write(body)
                browser.close()
                logger.info("playwright 下载完成: %s (%d bytes)", save_path, len(body))
                return True
            browser.close()
            logger.warning("playwright 下载失败: status=%s", r.status)
            return False
    except (PermissionError, OSError) as e:
        logger.warning("Playwright 系统权限拒绝 (%s)，禁用 Playwright 策略: %s", type(e).__name__, e)
        _PLAYWRIGHT_DISABLED = True
        return False
    except Exception as e:
        logger.warning("playwright 下载异常: %s", e)
        return False


def _pick_cover_url_from_item(item):
    """从 item 中提取视频封面 URL。

    抖音 item 结构（多层级）：
      video.cover.url_list[0]      原始封面（最高清）
      video.originCover.url_list[0] 原始封面（备选）
      video.dynamicCover.url_list[0] 动态封面
      video.animatedCover.url_list[0] 动图封面
    优先静态封面，用于后续 cover_original.jpg 保存与底图合成。
    """
    video = item.get("video") or {}
    # 优先级：cover > originCover
    for key in ("cover", "originCover"):
        cover_obj = video.get(key) or {}
        urls = cover_obj.get("url_list") or []
        if urls:
            url = urls[0]
            if url.startswith("http"):
                return url
    # 兜底：动态封面（降级方案，仍是有效图片 URL）
    for key in ("dynamicCover", "animatedCover"):
        cover_obj = video.get(key) or {}
        urls = cover_obj.get("url_list") or []
        if urls:
            url = urls[0]
            if url.startswith("http"):
                return url
    return None


def _find_video_url_in_html(html):
    """从 HTML 里正则提取视频 URL（回退方案）。"""
    patterns = [
        r'"(https://[^"]*douyinvod[^"]*\.mp4[^"]*)"',
        r'"(https://[^"]*bytecdntp[^"]*\.mp4[^"]*)"',
        r'"(https://[^"]*byteimg[^"]*\.mp4[^"]*)"',
        r'"(https://[^"]*\.mp4[^"]*video[^"]*)"',
        r'src="(https://[^"]*\.mp4[^"]*)"',
        r'"playApi":\s*"(https://[^"]+)"',
        r'"playAddr":\s*\{[^}]*"src":\s*"(https://[^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            url = m.group(1).replace("\\u002F", "/").replace("\\/", "/")
            # 过滤掉明显不是视频的 URL
            if ".mp4" in url or "video" in url or "playApi" in pat:
                return url
    return None


def _extract_aweme_detail(data):
    """从 render data 里找到 aweme 详情。"""
    if not data:
        return None

    def _search(obj):
        if isinstance(obj, dict):
            if "awemeId" in obj and "video" in obj:
                return obj
            if "aweme_id" in obj and "video" in obj:
                return obj
            for v in obj.values():
                r = _search(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = _search(v)
                if r:
                    return r
        return None
    return _search(data)


def _pick_video_url(aweme):
    """从 aweme 详情里挑出无水印视频 URL。"""
    video = aweme.get("video") or {}
    # 优先 playApi（无水印 dy 流）
    play_api = video.get("playApi")
    if play_api and play_api.startswith("http"):
        return play_api
    # bitRate 列表
    bit_rate = video.get("bitRate") or video.get("bit_rate") or []
    if bit_rate and isinstance(bit_rate, list):
        for br in bit_rate:
            play = br.get("playAddr") or br.get("play_addr") or {}
            urls = play.get("url_list") or []
            if urls:
                return urls[0]
    # playAddr
    play_addr = video.get("playAddr") or video.get("play_addr") or {}
    urls = play_addr.get("url_list") or []
    if urls:
        return urls[0]
    # downloadAddr
    download_addr = video.get("downloadAddr") or video.get("download_addr") or {}
    urls = download_addr.get("url_list") or []
    if urls:
        return urls[0]
    return None


def _extract_frame_fallback(video_path, out_path, time_ratio=0.3):
    """从本地视频抽取一帧作为封面兜底。"""
    try:
        from core import cover_gen
        res = cover_gen.extract_video_frame(video_path, out_path, time_ratio=time_ratio)
        if res and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return True
    except Exception as e:
        logger.warning("从视频抽帧生成封面失败: %s", e)
    return False


def _download_file(sess, url, save_path):
    """下载文件（带重试）。"""
    headers = {
        "User-Agent": MOBILE_UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    for attempt in range(config.MAX_RETRIES):
        try:
            r = sess.get(url, stream=True, timeout=config.REQUEST_TIMEOUT, headers=headers)
            r.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            logger.warning("下载尝试 %d/%d 失败: %s", attempt + 1, config.MAX_RETRIES, e)
            time.sleep(1)
    return False


def _guess_ext(url, default=".mp4"):
    """从 URL 猜测扩展名。"""
    m = re.search(r"\.(mp4|webm|mov|jpg|png)(\?|$)", url, re.IGNORECASE)
    return "." + m.group(1).lower() if m else default


def parse_note(note_input):
    """解析抖音视频并下载。

    参数:
        note_input: 抖音 URL / 短链 / 分享文本
    返回:
        NoteData（note_type="video"）

    解析策略（按优先级）：
      0. playwright 无头浏览器 → 绕过 byted_acrawler JS 校验 → _ROUTER_DATA（最稳定，2026 年推荐）
      1. m.douyin.com 移动端分享页 → _ROUTER_DATA（requests 回退，2026 年后只返回骨架）
      2. www.douyin.com + 移动端 UA → RENDER_DATA（旧方案，可能被反爬）
      3. iesdouyin.com 分享页 → _ROUTER_DATA / HTML 正则（备用）
      4. HTML 正则提取视频 URL（兜底）
    """
    config.ensure_dirs()
    text = note_input.strip()

    # 提取 video_id（短链会自动重定向）
    video_id = extract_video_id(text)
    if not video_id:
        raise ValueError(f"无法识别抖音视频 ID: {note_input}")
    logger.info("抖音视频 ID: %s", video_id)

    title = ""
    desc = ""
    tags = []
    video_url = None
    cover_url = None  # 原视频封面 URL
    html = ""

    # === 策略0: ByteDance 官方访客 ttwid + Web API（0.3 秒超高速纯 HTTP 免登录解析）===
    try:
        logger.info("策略0: ttwid 官方访客 Web API 解析")
        aweme = _fetch_with_ttwid_api(video_id)
        if aweme:
            desc = (aweme.get("desc") or "").strip()
            title = desc.split("\n")[0][:40] if desc else ""
            text_extra = aweme.get("text_extra") or aweme.get("textExtra") or []
            tags = [te.get("hashtag_name") or te.get("hashtagName") for te in text_extra if isinstance(te, dict) and (te.get("hashtag_name") or te.get("hashtagName"))]
            video_url = _pick_video_url(aweme)
            cover_url = _pick_cover_url_from_item(aweme)
            if video_url:
                logger.info("ttwid Web API 成功提取视频 URL")
            if cover_url:
                logger.info("ttwid Web API 成功提取封面 URL")
    except Exception as e:
        logger.warning("策略0失败: %s", e)

    # === 策略1: playwright 无头浏览器（备用）===
    if not video_url:
        try:
            logger.info("策略1: playwright 无头浏览器")
            pw_html = _fetch_with_playwright(video_id)
            if pw_html:
                html = pw_html
                router_data = _extract_render_data(html)
                if router_data:
                    item = _extract_item_from_router_data(router_data)
                    if item:
                        logger.info("playwright 提取到视频 item")
                        title, desc, tags, _ = _extract_meta_from_item(item)
                        video_url = _pick_video_url_no_wm(item)
                        cover_url = _pick_cover_url_from_item(item)
                        if video_url:
                            logger.info("提取到无水印视频 URL（playwm→play 去水印）")
                        if cover_url:
                            logger.info("提取到原视频封面 URL")
                    else:
                        logger.warning("playwright _ROUTER_DATA 中未找到 item")
                else:
                    logger.warning("playwright 页面未包含 _ROUTER_DATA")
        except Exception as e:
            logger.warning("策略1失败: %s", e)

    # === 策略1: m.douyin.com 移动端分享页（requests 回退）===
    if not video_url:
        try:
            logger.info("策略1: m.douyin.com 移动端分享页")
            html, router_data = _fetch_m_douyin_share(video_id)
            logger.info("m.douyin.com 页面大小: %d bytes", len(html))
            if router_data:
                item = _extract_item_from_router_data(router_data)
                if item:
                    logger.info("从 _ROUTER_DATA 提取到视频 item")
                    title, desc, tags, author = _extract_meta_from_item(item)
                    video_url = _pick_video_url_no_wm(item)
                    cover_url = _pick_cover_url_from_item(item)
                    if video_url:
                        logger.info("提取到无水印视频 URL（playwm→play 去水印）")
                    if cover_url:
                        logger.info("提取到原视频封面 URL")
                else:
                    logger.warning("_ROUTER_DATA 中未找到 item_list")
            else:
                logger.warning("m.douyin.com 页面未包含 _ROUTER_DATA")
        except Exception as e:
            logger.warning("策略1失败: %s", e)

    # === 策略2: www.douyin.com + 移动端 UA → RENDER_DATA ===
    if not video_url:
        try:
            url = f"https://www.douyin.com/video/{video_id}"
            logger.info("策略2: www.douyin.com 移动端 UA")
            html = _fetch_with_mobile_ua(url)
            logger.info("分享页大小: %d bytes", len(html))
            data = _extract_render_data(html)
            if data:
                aweme = _extract_aweme_detail(data)
                if aweme:
                    logger.info("从 RENDER_DATA 提取到 aweme 详情")
                    if not desc:
                        desc = (aweme.get("desc") or "").strip()
                    if not title:
                        title = desc.split("\n")[0][:40] if desc else ""
                    if not tags:
                        text_extra = aweme.get("textExtra") or aweme.get("text_extra") or []
                        for te in text_extra:
                            if isinstance(te, dict) and te.get("hashtagName"):
                                tags.append(te["hashtagName"])
                    video_url = _pick_video_url_no_wm(aweme)
                    if not cover_url:
                        cover_url = _pick_cover_url_from_item(aweme)
        except Exception as e:
            logger.warning("策略2失败: %s", e)

    # === 策略3: iesdouyin 分享页 ===
    if not video_url:
        try:
            share_url = f"https://www.iesdouyin.com/share/video/{video_id}"
            logger.info("策略3: iesdouyin 分享页")
            html2 = _fetch_with_mobile_ua(share_url)
            logger.info("iesdouyin 页面大小: %d bytes", len(html2))
            # 先尝试 _ROUTER_DATA
            data = _extract_render_data(html2)
            if data:
                item = _extract_item_from_router_data(data)
                if item:
                    logger.info("从 iesdouyin _ROUTER_DATA 提取到 item")
                    if not desc:
                        title, desc, tags, _ = _extract_meta_from_item(item)
                    video_url = _pick_video_url_no_wm(item)
                    if not cover_url:
                        cover_url = _pick_cover_url_from_item(item)
            # 回退到 HTML 正则
            if not video_url:
                video_url = _find_video_url_in_html(html2)
            if not video_url and not html:
                html = html2
        except Exception as e:
            logger.warning("策略3失败: %s", e)

    # === 策略4: HTML 正则提取（兜底）===
    if not video_url and html:
        logger.info("策略4: 从 HTML 正则提取视频 URL")
        video_url = _find_video_url_in_html(html)

    if not video_url:
        # 保存 HTML 便于排查
        debug_dir = os.path.join(config.DOWNLOAD_DIR, f"dy_{video_id}")
        os.makedirs(debug_dir, exist_ok=True)
        debug_path = os.path.join(debug_dir, "_page.html")
        all_html = html or (html2 if 'html2' in locals() else "")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(all_html)
        raise RuntimeError(
            f"未能从抖音页面提取视频 URL（抖音 2026 年加强了反爬，免登录解析已不稳定）。\n"
            f"    HTML 已保存到 {debug_path}\n"
            f"    推荐方案（最稳定）：\n"
            f"      1. 用抖音 App 或微信小程序（如\"小青去水印\"）下载无水印视频到本地\n"
            f"      2. 直接用本地文件路径作为输入：\n"
            f"         python xhs_clone.py \"下载的视频.mp4\" --subtitle-style bold_yellow --watermark \"@你的小红书号\"\n"
            f"      3. 工具会自动完成：去重 → 小红书风格增强 → 字幕抹除+重渲染 → 二创叠加 → 剪映草稿"
        )

    # 回退：标题/描述用 video_id
    if not title:
        title = f"抖音视频_{video_id[:8]}"
    if not desc:
        desc = title

    logger.info("标题: %s", title)
    logger.info("视频 URL: %s", video_url[:100] + "..." if len(video_url) > 100 else video_url)

    # 下载视频
    sess = requests.Session()
    dl_dir = os.path.join(config.DOWNLOAD_DIR, f"dy_{video_id}")
    os.makedirs(dl_dir, exist_ok=True)

    ext = _guess_ext(video_url)
    local_path = os.path.join(dl_dir, f"video_0{ext}")
    logger.info("下载视频到: %s", local_path)
    if not _download_file(sess, video_url, local_path):
        # requests 失败（抖音 CDN TLS 指纹拦截）→ 回退 playwright 下载
        logger.info("requests 下载失败，尝试 playwright 下载视频")
        if _PLAYWRIGHT_DISABLED or not _download_with_playwright(video_url, local_path):
            raise RuntimeError(f"视频下载失败: {video_url}")
    logger.info("下载完成: %s (%d bytes)", local_path, os.path.getsize(local_path))

    # 下载原视频封面（优先尝试 playwright；因抖音图片 CDN 反爬拦截 requests，不可用时秒级使用已下载视频抽帧）
    cover_local = os.path.join(dl_dir, "cover_original.jpg")
    cover_downloaded = False

    if cover_url:
        logger.info("尝试下载原视频封面到: %s", cover_local)
        if _download_file(sess, cover_url, cover_local):
            logger.info("原封面下载完成(HTTP): %s (%d bytes)",
                        cover_local, os.path.getsize(cover_local))
            cover_downloaded = True
        elif not _PLAYWRIGHT_DISABLED and _download_with_playwright(cover_url, cover_local):
            logger.info("原封面下载完成(playwright): %s (%d bytes)",
                        cover_local, os.path.getsize(cover_local))
            cover_downloaded = True

    # 兜底保障：若 Playwright 未获取成功，直接使用本地已下载视频秒级抽帧生成封面（无延时，防告警）
    if not cover_downloaded or not os.path.exists(cover_local) or os.path.getsize(cover_local) == 0:
        logger.info("使用已下载的本地视频抽帧生成封面: %s", local_path)
        if _extract_frame_fallback(local_path, cover_local, time_ratio=0.3):
            logger.info("视频抽帧生成封面成功: %s (%d bytes)",
                        cover_local, os.path.getsize(cover_local))
        else:
            logger.warning("视频抽帧生成封面失败，跳过原封面保存")
            cover_local = None

    # 构造 NoteData 兼容对象
    note_id = f"dy_{video_id}"
    media_items = [{
        "kind": "video",
        "url": video_url,
        "local": local_path,
        "cover": cover_url,
        "cover_local": cover_local,
    }]
    note = NoteData(
        note_id=note_id,
        note_type="video",
        title=title,
        desc=desc,
        tags=tags,
        media_items=media_items,
        raw={"video_id": video_id, "source": "douyin"},
    )
    return note
