# -*- coding: utf-8 -*-
"""小红书笔记解析与下载。

支持笔记类型：
  - video      视频
  - normal     图文（含 Live 图，通过 live_photo 字段识别）

输入：笔记 URL 或 note_id
输出：NoteData 对象（含标题、正文、标签、素材本地路径）
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

logger = logging.getLogger("xhs_parser")


class NoteData:
    """解析后的笔记数据。"""

    def __init__(self, note_id, note_type, title, desc, tags, media_items, raw=None):
        self.note_id = note_id              # 笔记 ID
        self.note_type = note_type          # "video" | "image"
        self.title = title                  # 标题
        self.desc = desc                    # 正文
        self.tags = tags                    # 标签列表
        self.media_items = media_items      # 媒体项列表（dict）
        self.raw = raw                      # 原始 note dict

    @property
    def is_live(self):
        """是否包含 Live 图。"""
        return any(m.get("live_photo") for m in self.media_items)

    def __repr__(self):
        return (f"<NoteData id={self.note_id} type={self.note_type} "
                f"live={self.is_live} media={len(self.media_items)}>")


# 笔记 URL 提取 ID 的几种模式
URL_PATTERNS = [
    re.compile(r"xiaohongshu\.com/explore/([a-zA-Z0-9]+)"),
    re.compile(r"xiaohongshu\.com/discovery/item/([a-zA-Z0-9]+)"),
    re.compile(r"xiaohongshu\.com/note/([a-zA-Z0-9]+)"),
    re.compile(r"xiaohongshu\.com/[^/]*[?#].*?([a-zA-Z0-9]{24})"),
    re.compile(r"^([a-zA-Z0-9]{24})$"),  # 纯 ID
]


def extract_note_id(text):
    """从 URL 或文本中提取笔记 ID。"""
    text = text.strip()
    for pat in URL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def extract_note_id_and_token(text):
    """从 URL 或文本中提取笔记 ID 和 xsec_token。

    新版小红书分享链接带 xsec_token（鉴权令牌），访问笔记必须带上。
    返回: (note_id, xsec_token, xsec_source)
    """
    text = text.strip()
    note_id = extract_note_id(text)
    if not note_id:
        return None, None, None

    # 提取 xsec_token
    xsec_token = None
    m = re.search(r"[?&]xsec_token=([^&\s]+)", text)
    if m:
        xsec_token = m.group(1)

    # 提取 xsec_source
    xsec_source = None
    m = re.search(r"[?&]xsec_source=([^&\s]+)", text)
    if m:
        xsec_source = m.group(1)

    return note_id, xsec_token, xsec_source


def _fetch_page(note_id, xsec_token=None, xsec_source=None):
    """请求笔记页面 HTML。

    若提供了 xsec_token，使用带 token 的完整 URL（新版小红书必需）。
    否则使用 /explore/<id> 裸链接（旧版/未登录可能拿不到数据）。
    """
    if xsec_token:
        url = (f"https://www.xiaohongshu.com/discovery/item/{note_id}"
               f"?xsec_token={xsec_token}"
               f"&xsec_source={xsec_source or 'pc_share'}")
        logger.info("使用带 xsec_token 的分享链接")
    else:
        url = f"https://www.xiaohongshu.com/explore/{note_id}"

    sess = requests.Session()
    sess.headers.update(config.HEADERS)
    # 访问首页拿 cookie，降低被拦概率
    try:
        sess.get("https://www.xiaohongshu.com/", timeout=config.REQUEST_TIMEOUT)
    except Exception:
        pass
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = sess.get(url, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 200 and resp.text:
                # 调试：保存 HTML 便于排查
                _save_debug_html(note_id, resp.text)
                return resp.text
            logger.warning("请求 %s 状态码 %s", url, resp.status_code)
        except requests.RequestException as e:
            logger.warning("请求异常(%d/%d): %s", attempt + 1, config.MAX_RETRIES, e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"无法获取笔记页面: {url}")


def _save_debug_html(note_id, html):
    """保存 HTML 副本，便于解析失败时排查。"""
    try:
        import os
        debug_dir = os.path.join(config.DOWNLOAD_DIR, note_id)
        os.makedirs(debug_dir, exist_ok=True)
        debug_path = os.path.join(debug_dir, "_page.html")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass


def _parse_initial_state(html):
    """从 HTML 中解析 window.__INITIAL_STATE__。"""
    # 匹配 window.__INITIAL_STATE__={...}
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
                  html, re.DOTALL)
    if not m:
        # 兜底：到 ; 之前
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});",
                      html, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    # 处理 undefined -> null
    raw = re.sub(r"\bundefined\b", "null", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("解析 __INITIAL_STATE__ 失败: %s", e)
        return None


def _extract_note_from_state(state, note_id):
    """从 state 中提取指定 note_id 的 note 对象。

    尝试多种路径以兼容不同版本的小红书页面结构。
    """
    if not state:
        return None

    # 路径1: state.note.noteDetailMap[note_id].note
    note_map = None
    try:
        note_map = state["note"]["noteDetailMap"]
    except (KeyError, TypeError):
        pass

    if note_map:
        # 精确匹配
        if note_id in note_map:
            n = note_map[note_id].get("note")
            if n:
                return n
        # 取第一个非空
        for k, v in note_map.items():
            n = v.get("note") if isinstance(v, dict) else None
            if n:
                logger.debug("从 noteDetailMap 取到笔记 key=%s", k)
                return n

    # 路径2: state.note.noteDetailMap[note_id] 直接就是 note
    if note_map and note_id in note_map and isinstance(note_map[note_id], dict):
        if "type" in note_map[note_id] or "imageList" in note_map[note_id]:
            return note_map[note_id]

    # 路径3: 直接在 state.note 下
    try:
        n = state["note"].get("note") if isinstance(state.get("note"), dict) else None
        if n:
            return n
    except (KeyError, TypeError):
        pass

    # 路径4: state.note.firstNote
    try:
        n = state["note"].get("firstNote")
        if n:
            return n
    except (KeyError, TypeError):
        pass

    # 路径5: 在整个 state 里递归找包含 imageList 或 video 字段的 dict
    found = _deep_find_note(state)
    if found:
        logger.debug("通过递归搜索找到笔记数据")
        return found

    return None


def _deep_find_note(obj, depth=0):
    """递归查找包含 imageList 或 video 字段的 dict（兜底）。"""
    if depth > 6 or not isinstance(obj, dict):
        return None
    if ("imageList" in obj or "video" in obj) and "type" in obj:
        # 确认是 note 对象
        if obj.get("type") in ("video", "normal"):
            return obj
    for v in obj.values():
        if isinstance(v, dict):
            r = _deep_find_note(v, depth + 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    r = _deep_find_note(item, depth + 1)
                    if r:
                        return r
    return None


def _pick_stream_url(streams):
    """从 media.stream 字典中选最佳视频 URL。

    streams 结构: {fmt: [stream_obj, ...], ...}  (fmt: h264/h265/h266/av1)
    stream_obj 字段为 camelCase: masterUrl, backupUrls
    """
    if not isinstance(streams, dict):
        return None
    # 优先 h264（兼容性最好）
    for fmt in ("h264", "h265", "h266", "av1"):
        arr = streams.get(fmt)
        if isinstance(arr, list) and arr:
            s = arr[0]
            if not isinstance(s, dict):
                continue
            url = s.get("masterUrl") or s.get("master_url")
            if not url:
                backups = s.get("backupUrls") or s.get("backup_urls")
                if isinstance(backups, list) and backups:
                    url = backups[0]
            if url:
                return url
    # 兜底：任意格式
    for fmt, arr in streams.items():
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            url = arr[0].get("masterUrl") or arr[0].get("master_url")
            if not url:
                backups = arr[0].get("backupUrls") or arr[0].get("backup_urls")
                if isinstance(backups, list) and backups:
                    url = backups[0]
            if url:
                return url
    return None


def _pick_image_url(img):
    """从 imageList 项中取图片 URL。"""
    if not isinstance(img, dict):
        return None
    url = img.get("urlDefault") or img.get("url")
    if not url:
        info = img.get("infoList")
        if isinstance(info, list) and info and isinstance(info[0], dict):
            url = info[0].get("url")
    return url


def _pick_live_video_url(img):
    """从 imageList 项中取 Live 图的视频 URL。

    新版结构: img.stream = {fmt: [stream_obj,...], ...}  (与视频 stream 相同)
    旧版结构: img.livePhoto = {"url": ...} 或 img.livePhotoUrl
    """
    if not isinstance(img, dict):
        return None
    # 新版: img.stream 含格式列表
    stream = img.get("stream")
    if isinstance(stream, dict) and stream:
        url = _pick_stream_url(stream)
        if url:
            return url
    # livePhoto 为 dict
    lp = img.get("livePhoto")
    if isinstance(lp, dict):
        u = lp.get("url") or lp.get("masterUrl")
        if u:
            return u
    # 旧版字段
    for k in ("livePhotoUrl", "live_photo_url"):
        u = img.get(k)
        if u:
            return u
    return None


def _build_media_items(note):
    """从 note 对象构建媒体项列表。

    每项格式：
      图片: {"kind": "image", "url": "...", "live_photo": None}
      Live: {"kind": "image", "url": "...", "live_photo": {"url": "...", "duration": ...}}
      视频: {"kind": "video", "url": "...", "cover": "...", "duration": ...}
    """
    items = []
    note_type = note.get("type", "normal")

    if note_type == "video":
        media = note.get("video") or {}
        streams = (media.get("media") or {}).get("stream") or {}
        best_url = _pick_stream_url(streams)
        # 封面：优先 imageList[0].urlDefault
        cover = None
        il = note.get("imageList") or []
        if il and isinstance(il[0], dict):
            cover = _pick_image_url(il[0])
        duration = (media.get("capa") or {}).get("duration", 0)
        items.append({
            "kind": "video",
            "url": best_url,
            "cover": cover,
            "duration": duration,
        })
    else:
        # 图文 / Live
        image_list = note.get("imageList") or []
        for img in image_list:
            url = _pick_image_url(img)
            live = None
            live_url = _pick_live_video_url(img)
            if live_url:
                # 判断是否真为 Live 图（livePhoto 字段为真值或存在 stream）
                is_live = bool(img.get("livePhoto")) or bool(img.get("stream"))
                if is_live:
                    live = {"url": live_url, "duration": 3.0}
            items.append({
                "kind": "image",
                "url": url,
                "live_photo": live,
            })
    return [it for it in items if it.get("url")]


def _download_file(sess, url, save_path):
    """下载文件到指定路径（带重试与 SSL 降级）。"""
    if not url:
        return False
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    for attempt in range(config.MAX_RETRIES):
        try:
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            resp = sess.get(url, timeout=60, stream=True, headers=config.HEADERS)
            if resp.status_code == 200:
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                return True
            logger.warning("下载失败 %s -> %s", url, resp.status_code)
        except Exception as e:
            logger.warning("下载异常(%d/%d): %s，尝试 SSL 降级请求...", attempt + 1, config.MAX_RETRIES, e)
            try:
                resp = sess.get(url, timeout=60, stream=True, headers=config.HEADERS, verify=False)
                if resp.status_code == 200:
                    with open(save_path, "wb") as f:
                        for chunk in resp.iter_content(8192):
                            if chunk:
                                f.write(chunk)
                    logger.info("SSL 降级下载成功: %s", save_path)
                    return True
            except Exception as e2:
                logger.warning("SSL 降级下载(%d/%d) 仍失败: %s", attempt + 1, config.MAX_RETRIES, e2)
        time.sleep(1)
    return False


def _guess_ext(url, kind):
    """根据 URL 和类型猜扩展名。"""
    low = (url or "").lower().split("?")[0]
    if kind == "video":
        return ".mp4"
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if ext in low:
            return ext
    return ".jpg"


def parse_note(note_input):
    """解析笔记并下载素材。

    参数:
        note_input: 笔记 URL 或 note_id
    返回:
        NoteData
    """
    config.ensure_dirs()
    note_id, xsec_token, xsec_source = extract_note_id_and_token(note_input)
    if not note_id:
        raise ValueError(f"无法识别笔记 ID: {note_input}")
    logger.info("笔记 ID: %s  xsec_token: %s", note_id, "有" if xsec_token else "无")

    html = _fetch_page(note_id, xsec_token, xsec_source)
    state = _parse_initial_state(html)
    if not state:
        # 保存 HTML 便于排查
        debug_path = os.path.join(config.DOWNLOAD_DIR, note_id, "_page.html")
        raise RuntimeError(
            f"无法解析页面 __INITIAL_STATE__，可能需要登录或被反爬拦截。"
            f"HTML 已保存到 {debug_path}，可检查是否需要 cookie。")

    note = _extract_note_from_state(state, note_id)
    if not note:
        debug_path = os.path.join(config.DOWNLOAD_DIR, note_id, "_page.html")
        raise RuntimeError(
            f"未在页面中找到笔记 {note_id} 的数据。"
            f"HTML 已保存到 {debug_path}，可检查页面结构。")

    note_type = note.get("type", "normal")
    # 统一类型：video 或 image
    norm_type = "video" if note_type == "video" else "image"

    title = (note.get("title") or "").strip()
    desc = (note.get("desc") or "").strip()
    # 提取标签：优先从 tagList 字段，其次从正文 #...#
    tags = []
    tag_list = note.get("tagList") or []
    if isinstance(tag_list, list):
        for t in tag_list:
            if isinstance(t, dict):
                tags.append(t.get("name") or t.get("id") or "")
            elif isinstance(t, str):
                tags.append(t)
    tags = [t for t in tags if t]
    if not tags:
        tags = re.findall(r"#([^#\s]+?)#", desc)
    # 交互数据
    interact = note.get("interactInfo") or {}

    media_items = _build_media_items(note)
    logger.info("笔记类型=%s, 媒体数=%d, Live=%s", norm_type, len(media_items),
                any(m.get("live_photo") for m in media_items))

    # 下载素材
    sess = requests.Session()
    sess.headers.update(config.HEADERS)

    dl_dir = os.path.join(config.DOWNLOAD_DIR, note_id)
    os.makedirs(dl_dir, exist_ok=True)

    for idx, item in enumerate(media_items):
        ext = _guess_ext(item["url"], item["kind"])
        if item["kind"] == "video":
            local = os.path.join(dl_dir, f"video_{idx}{ext}")
        else:
            local = os.path.join(dl_dir, f"image_{idx}{ext}")
            if item["live_photo"]:
                # 静态图用 jpg，live 视频用 mp4
                local = os.path.join(dl_dir, f"image_{idx}.jpg")
                live_url = item["live_photo"]["url"]
                live_local = os.path.join(dl_dir, f"live_{idx}.mp4")
                logger.info("下载 Live 图视频 %d: %s", idx, live_url)
                if _download_file(sess, live_url, live_local):
                    item["live_photo"]["local"] = live_local
                else:
                    item["live_photo"] = None

        logger.info("下载 %s %d: %s", item["kind"], idx, item["url"])
        if _download_file(sess, item["url"], local):
            item["local"] = local
        else:
            item["local"] = None
            logger.error("下载失败: %s", item["url"])

        # 视频笔记：下载原视频封面（优先网络下载；网络失败则从本地视频截取抽帧作为封面兜底）
        if item["kind"] == "video":
            cover_local = os.path.join(dl_dir, "cover_original.jpg")
            cover_ok = False
            if item.get("cover"):
                logger.info("下载原视频封面 %d: %s", idx, item["cover"])
                if _download_file(sess, item["cover"], cover_local):
                    item["cover_local"] = cover_local
                    logger.info("原封面下载完成: %s", cover_local)
                    cover_ok = True
                else:
                    logger.warning("原封面下载失败: %s", item["cover"])

            if not cover_ok and item.get("local") and os.path.exists(item["local"]):
                try:
                    from core import cover_gen
                    logger.info("从本地已下载视频抽帧作为封面兜底...")
                    if cover_gen.extract_video_frame(item["local"], cover_local, time_ratio=0.3):
                        item["cover_local"] = cover_local
                        logger.info("视频抽帧生成封面成功: %s", cover_local)
                except Exception as e:
                    logger.warning("视频抽帧生成封面失败: %s", e)

    # 保存文本内容
    text_path = os.path.join(dl_dir, "content.json")
    content = {
        "note_id": note_id,
        "type": norm_type,
        "is_live": any(m.get("live_photo") for m in media_items),
        "title": title,
        "desc": desc,
        "tags": tags,
        "interact": interact,
    }
    with open(text_path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)

    return NoteData(
        note_id=note_id,
        note_type=norm_type,
        title=title,
        desc=desc,
        tags=tags,
        media_items=media_items,
        raw=note,
    )
