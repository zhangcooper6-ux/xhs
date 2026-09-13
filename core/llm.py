# -*- coding: utf-8 -*-
"""统一 LLM 客户端：支持豆包/通义千问/OpenAI 三大服务商。

所有服务商都兼容 OpenAI SDK 协议，只需切换 base_url 和 model。
API Key 从环境变量读取，不写死在代码里。

环境变量配置：
  # 豆包（火山引擎）
  setx LLM_PROVIDER "doubao"
  setx LLM_API_KEY "your-ark-api-key"
  setx LLM_MODEL "doubao-pro-32k"        # 可选，默认 doubao-pro-32k

  # 通义千问（阿里云百炼）
  setx LLM_PROVIDER "qwen"
  setx LLM_API_KEY "your-dashscope-api-key"
  setx LLM_MODEL "qwen-plus"             # 可选，默认 qwen-plus

  # OpenAI
  setx LLM_PROVIDER "openai"
  setx LLM_API_KEY "sk-xxx"
  setx LLM_MODEL "gpt-4o-mini"           # 可选，默认 gpt-4o-mini

  # deepseek
  setx LLM_PROVIDER "deepseek"
  setx LLM_API_KEY "sk-e1984a20b5a34ecd8edc4249510b0f08"
  setx LLM_MODEL "deepseek-v4-flash"           # 可选，默认deepseek-v4-flash

特性：
  - 统一接口：chat() 方法三服务商通用
  - 优雅降级：API 不可用时返回 None，调用方回退到本地模板库
  - 重试机制：网络波动自动重试 2 次
  - 成本控制：超时 30s，max_tokens 可配
"""

import os
import json
import time
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("llm")

# ---------- 服务商配置 ----------

PROVIDER_CONFIGS = {
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-pro-32k",
        "env_key": "ARK_API_KEY",        # 豆包优先读 ARK_API_KEY
        "env_key_alt": "LLM_API_KEY",    # 兜底读 LLM_API_KEY
        "name_cn": "豆包(火山引擎)",
        "image_model": "doubao-seedream-3-0-t2i-250415",  # 文生图模型（需在火山引擎控制台开通）
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "env_key": "DASHSCOPE_API_KEY",
        "env_key_alt": "LLM_API_KEY",
        "name_cn": "通义千问(阿里云)",
        "image_model": None,  # 百炼兼容模式暂不支持 wanx 文生图，需用原生 dashscope SDK
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-flash",
        "env_key": "DEEPSEEK_API_KEY",
        "env_key_alt": "LLM_API_KEY",
        "api_key": "sk-e1984a20b5a34ecd8edc4249510b0f08",  # 直接写在这里（环境变量优先，此为兜底）
        "name_cn": "DeepSeek",
        "image_model": None,  # DeepSeek 暂无文生图能力
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "env_key_alt": "LLM_API_KEY",
        "name_cn": "OpenAI",
        "image_model": "dall-e-3",
    },
    "grsai": {
        # GrsAI 聚合 API：国内直连 https://grsai.dakka.com.cn，海外 https://api.grsai.com
        # 文生图走 /v1/draw/completions（流式响应），不是标准 OpenAI Images 协议
        # 对话走 OpenAI 兼容协议 /v1/chat/completions（可用 GPT-4o/Gemini 等）
        "base_url": "https://grsai.dakka.com.cn",
        "default_model": "gpt-4o-mini",
        "env_key": "GRSAI_API_KEY",       # 环境变量名（优先读）
        "env_key_alt": "LLM_API_KEY",     # 兜底读 LLM_API_KEY
        "api_key": "sk-af2c6fc67e5541aea336bd35ee6fbcc0",  # 直接写在这里（环境变量优先，此为兜底）
        "name_cn": "GrsAI(国内直连)",
        "image_model": "gpt-image-2",  # GrsAI 文生图默认模型（0.02元/张），也可选 gpt-image-1.5 / nano-banana 等
        "image_api": "custom",  # 标记走自定义 requests 调用，不用 OpenAI SDK
    },
    "modelscope": {
        # 魔搭 ModelScope API-Inference：完全免费（每日 2000 次，单模型 500 次/日）
        # 文生图走 /v1/images/generations（异步任务模式，需 X-ModelScope-Async-Mode: true）
        # 对话走 OpenAI 兼容协议 /v1/chat/completions
        # 前置：注册魔搭账号 + 绑定阿里云 + 实名认证
        # Token 获取：https://modelscope.cn/my/myaccesstoken
        "base_url": "https://api-inference.modelscope.cn/v1",
        "default_model": "Qwen/Qwen-Image",  # 魔搭默认对话模型
        "env_key": "MODELSCOPE_API_TOKEN",   # 环境变量名（优先读）
        "env_key_alt": "MODELSCOPE_TOKEN",   # 兜底
        "name_cn": "魔搭ModelScope(免费)",
        # 文生图 model ID 必须用 "组织/仓库名" 格式（魔搭 API 要求）
        # 常用：Tongyi-MAI/Z-Image-Turbo（8步极速，中英双语）/ Qwen/Qwen-Image
        "image_model": "Tongyi-MAI/Z-Image-Turbo",
        "image_api": "custom",               # 走自定义 requests 调用（异步任务模式）
    },
}


# 支持 OpenAI Images SDK 协议的服务商（直接用 client.images.generate）
SDK_IMAGE_PROVIDERS = {"doubao", "openai"}
# 走自定义 HTTP 调用的服务商（魔搭 OpenAI 兼容 /v1/images/generations 但响应结构不同）
CUSTOM_IMAGE_PROVIDERS = {"grsai", "modelscope"}


# 魔搭 ModelScope 支持图生图（image-editing）的模型 ID
# 经实测：魔搭 OpenAI 兼容协议的图生图走 /v1/images/generations 端点，
# 用 image 字段传参考图（不是 image_url，也不是 /v1/images/edits 端点）
# 其他魔搭模型（如 Z-Image-Turbo / Qwen-Image）仅支持纯文生图
MODELSCOPE_IMAGE_EDIT_MODELS = {
    "black-forest-labs/flux.2-klein-4b",
    "black-forest-labs/flux.2-klein-9b",
    "black-forest-labs/flux.1-kontinuous-dev",
    "black-forest-labs/flux.1-krea-dev",
}


def _is_modelscope_image_edit_model(model: str) -> bool:
    """判断魔搭模型是否支持图生图（image-editing）。

    参数:
        model: 模型 ID（如 black-forest-labs/FLUX.2-klein-4B）
    返回:
        True=支持图生图，False=纯文生图
    """
    if not model:
        return False
    model_lower = model.strip().lower()
    # 完全匹配
    if model_lower in MODELSCOPE_IMAGE_EDIT_MODELS:
        return True
    # 模糊匹配：模型名包含 flux.2-klein / flux-klein / kontinuous / krea
    for keyword in ("flux.2-klein", "flux2-klein", "flux-2-klein",
                    "kontinuous", "krea-dev"):
        if keyword in model_lower:
            return True
    return False


class LLMClient:
    """统一 LLM 客户端（懒加载，首次调用时才初始化 SDK）。"""

    def __init__(self, provider: str = None, api_key: str = None,
                 model: str = None, base_url: str = None):
        """
        参数:
            provider: 服务商 doubao/qwen/openai（None=从环境变量读取）
            api_key: API Key（None=从环境变量读取）
            model: 模型名（None=用服务商默认）
            base_url: API 地址（None=用服务商默认）
        """
        self.provider = provider or os.getenv("LLM_PROVIDER", "").lower()
        if not self.provider:
            # 默认豆包
            self.provider = "doubao"

        cfg = PROVIDER_CONFIGS.get(self.provider)
        if not cfg:
            logger.error("未知 LLM 服务商: %s（支持: doubao/qwen/openai）", self.provider)
            self._available = False
            return

        self.base_url = base_url or cfg["base_url"]
        self.model = model or os.getenv("LLM_MODEL") or cfg["default_model"]

        # 读取 API Key 优先级：构造参数 > 环境变量(专用) > 环境变量(通用) > 配置文件写死
        self.api_key = (api_key
                        or os.getenv(cfg["env_key"])
                        or os.getenv(cfg["env_key_alt"])
                        or cfg.get("api_key"))

        if not self.api_key:
            logger.warning(
                "LLM API Key 未配置（服务商=%s）。请设置环境变量 %s 或 %s",
                cfg["name_cn"], cfg["env_key"], cfg["env_key_alt"]
            )
            self._available = False
        else:
            self._available = True
            logger.info("LLM 客户端就绪: %s 模型=%s", cfg["name_cn"], self.model)

        self._client = None

    @property
    def available(self) -> bool:
        """是否可用（API Key 已配置且 SDK 已安装）"""
        if not self._available:
            return False
        if self._client is None:
            self._init_client()
        return self._client is not None

    def _init_client(self):
        """懒加载初始化 OpenAI SDK。"""
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60.0,   # 客户端默认 60s（推理模型可能较慢）
                max_retries=2,
            )
        except ImportError:
            logger.warning(
                "openai SDK 未安装，LLM 不可用。请运行: pip install openai"
            )
            self._client = None
        except Exception as e:
            logger.warning("LLM 客户端初始化失败: %s", e)
            self._client = None

    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.9, max_tokens: int = 1000,
             timeout: int = 60, allow_reasoning_fallback: bool = True) -> Optional[str]:
        """调用 LLM 对话接口。

        参数:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
            temperature: 温度（0=确定,1=随机，文案生成建议 0.9）
            max_tokens: 最大输出 token 数
            timeout: 超时秒数
            allow_reasoning_fallback: 推理模型 content 为空时是否降级用 reasoning_content 兜底。
                思考过程字数远超原文，对"输出长度需接近原文"的任务（如字幕纠错）应设为 False。
        返回:
            LLM 回复文本，失败返回 None
        """
        if not self.available:
            return None

        # 推理模型（如 deepseek-v4-flash）会先用 reasoning_content 思考，再输出 content
        # 若 max_tokens 太小，token 全用在 reasoning 上，content 会是空的（finish_reason=length）
        # 检测推理模型并自动放大 max_tokens（至少 32000，保证 reasoning 后仍有空间输出正式答案）
        # 经验值：reasoning_content 常达 3000-6000 token，复杂任务可能突破 8000，需 32000
        original_max_tokens = max_tokens
        if self._is_reasoning_model() and max_tokens < 32000:
            max_tokens = 32000

        # 推理模型需要先思考再输出，耗时更长，自动放大超时
        original_timeout = timeout
        if self._is_reasoning_model() and timeout < 120:
            timeout = 120

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            choice = resp.choices[0]
            content = (choice.message.content or "").strip()
            # 兼容推理模型：content 为空时降级用 reasoning_content
            # 注意：reasoning_content 是思考过程，不是正式答案，仅在 content 空时兜底
            if not content:
                reasoning = getattr(choice.message, "reasoning_content", None)
                if reasoning and reasoning.strip() and allow_reasoning_fallback:
                    finish_reason = getattr(choice, "finish_reason", "")
                    logger.warning(
                        "LLM 返回 content 为空（model=%s finish_reason=%s max_tokens=%d→%d），"
                        "推理模型可能仍因 token 不足导致未输出正式答案，"
                        "降级使用 reasoning_content（建议改用更轻量的推理模型如 deepseek-v4-flash）",
                        self.model, finish_reason, original_max_tokens, max_tokens)
                    return reasoning.strip()
                logger.warning("LLM 返回空 content（model=%s finish_reason=%s allow_reasoning_fallback=%s），"
                               "推理模型未输出正式答案",
                               self.model, getattr(choice, "finish_reason", "unknown"),
                               allow_reasoning_fallback)
                return None
            return content
        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                logger.warning("LLM 调用超时（model=%s timeout=%ds）: %s",
                               self.model, timeout, e)
            else:
                logger.warning("LLM 调用失败（model=%s）: %s", self.model, e)
            return None

    def _is_reasoning_model(self) -> bool:
        """判断当前模型是否为推理模型（会产出 reasoning_content）。

        推理模型特征：deepseek-v4-flash / deepseek-v4-pro / deepseek-r1 / o1 / o3 等。
        这些模型会先用 reasoning_content 思考，再输出 content，需要更大 max_tokens。
        注意：DeepSeek 目前仅提供推理模型（v4-flash 轻量 / v4-pro 重量），无非推理模型可选。
        """
        m = (self.model or "").lower()
        reasoning_keywords = ("v4-flash", "v4-pro", "r1", "o1", "o3", "reasoner", "thinking")
        return any(kw in m for kw in reasoning_keywords)

    def chat_with_retry(self, messages: List[Dict[str, str]],
                        retries: int = 2, **kwargs) -> Optional[str]:
        """带重试的对话调用。超时类错误重试间隔更长。"""
        last_err = None
        for i in range(retries + 1):
            result = self.chat(messages, **kwargs)
            if result:
                return result
            if i < retries:
                wait = 2.0 * (i + 1)  # 递增等待：2s, 4s
                time.sleep(wait)
        return None

    def get_image_model(self) -> Optional[str]:
        """获取当前服务商的文生图模型名（None=该服务商不支持文生图）。

        优先级：用户显式指定的 image_model > 服务商配置默认值。
        set_image_client() 会把用户传入的 model 存到 self._explicit_image_model。
        """
        # 优先返回用户显式指定的文生图模型（--image-llm-model）
        if getattr(self, "_explicit_image_model", None):
            return self._explicit_image_model
        cfg = PROVIDER_CONFIGS.get(self.provider, {})
        return cfg.get("image_model")

    def generate_image(self, prompt: str, size: str = "1024x1024",
                       model: str = None) -> Optional[str]:
        """调用文生图模型，返回图片 URL（失败返回 None）。

        参数:
            prompt: 图像生成提示词（建议英文，效果更好）
            size: 图像尺寸，如 "1024x1024" / "1024x1792" / "1792x1024"
            model: 指定模型（None=用服务商默认 image_model）
        返回:
            图片 URL 字符串，失败返回 None
        """
        if not self.available:
            return None

        image_model = model or self.get_image_model()
        if not image_model:
            cfg = PROVIDER_CONFIGS.get(self.provider, {})
            logger.warning("当前服务商 %s 暂不支持文生图（image_model 未配置）",
                           cfg.get("name_cn", self.provider))
            logger.warning("支持文生图的服务商: 豆包(doubao-seedream) / OpenAI(dall-e-3) / GrsAI(sora-image等) / 魔搭(Z-Image-Turbo,免费)")
            return None

        # 分支：GrsAI / 魔搭 走自定义 HTTP 调用（非 OpenAI SDK 协议）
        if self.provider in CUSTOM_IMAGE_PROVIDERS:
            if self.provider == "modelscope":
                return self._generate_image_modelscope(prompt, size, image_model)
            return self._generate_image_grsai(prompt, size, image_model)

        # 默认走 OpenAI Images SDK 协议（豆包/OpenAI）
        try:
            resp = self._client.images.generate(
                model=image_model,
                prompt=prompt,
                n=1,
                size=size,
                response_format="url",
            )
            if resp.data and resp.data[0].url:
                url = resp.data[0].url
                logger.info("文生图成功: model=%s size=%s url=%s...",
                            image_model, size, url[:80])
                return url
            logger.warning("文生图返回空数据")
            return None
        except Exception as e:
            logger.warning("文生图调用失败: %s", e)
            return None

    def _generate_image_grsai(self, prompt: str, size: str = "1024x1024",
                              model: str = "sora-image") -> Optional[str]:
        """GrsAI 文生图（异步任务模式：提交任务 + 轮询结果）。

        GrsAI 接口流程：
            1. POST /v1/draw/completions（加 webHook:"-1" 立即返回 task_id）
               响应: {"code":0, "data":{"id":"task_id", ...}}
            2. POST /v1/draw/result + {"id": task_id}（轮询，每 3 秒）
               响应: {"code":0, "data":{"status":"succeeded", "results":[{"url":"..."}]}}

        参数:
            prompt: 图像生成提示词
            size: OpenAI 风格尺寸（1024x1024 等），内部转换成 GrsAI 比例格式
            model: GrsAI 模型名（sora-image / gpt-image-1.5 / nano-banana 等）
        返回:
            图片 URL，失败返回 None
        """
        return self._grsai_submit_and_poll(prompt, size, model, image_url=None)

    def _grsai_submit_and_poll(self, prompt: str, size: str,
                                model: str, image_url: str = None,
                                _is_retry: bool = False) -> Optional[str]:
        """GrsAI 生图通用方法：提交任务 + 轮询结果（文生图/图生图统一用异步模式）。

        异步模式比流式模式更稳定：
        - 流式模式（stream=True）对图生图支持不稳定，可能流结束但拿不到 URL
        - 异步模式（webHook:"-1" + 轮询 /v1/draw/result）文生图/图生图都稳定

        参数:
            prompt: 图像生成提示词
            size: OpenAI 风格尺寸（1024x1024 等）
            model: GrsAI 模型名
            image_url: 参考图 URL（图生图模式，None=文生图）
        返回:
            图片 URL，失败返回 None
        """
        try:
            import requests
        except ImportError:
            logger.warning("requests 未安装，GrsAI 生图不可用")
            return None

        import time

        size_ratio = self._size_to_grsai_ratio(size)
        mode_label = "图生图" if image_url else "文生图"

        url = f"{self.base_url}/v1/draw/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size_ratio,
            "variants": 1,
            "webHook": "-1",  # 异步模式：立即返回 task_id，不流式推送
        }
        if image_url:
            payload["image_url"] = image_url  # 图生图参考图

        logger.info("GrsAI %s: model=%s size=%s(%s)%s prompt=%s...",
                    mode_label, model, size, size_ratio,
                    " ref=" + image_url[:50] + "..." if image_url else "",
                    prompt[:60])

        # 第1步：提交任务，获取 task_id
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code != 200:
                try:
                    err = resp.json()
                    logger.warning("GrsAI %s提交失败: %s", mode_label, err)
                except Exception:
                    logger.warning("GrsAI %s提交失败: HTTP %s %s",
                                   mode_label, resp.status_code, resp.text[:200])
                return None

            resp_data = resp.json()
            # 响应格式: {"code":0, "data":{"id":"xxx", ...}}
            if resp_data.get("code") != 0:
                logger.warning("GrsAI %s提交业务错误: %s",
                               mode_label, resp_data.get("message") or resp_data)
                return None

            task_id = resp_data.get("data", {}).get("id")
            if not task_id:
                # 兜底：某些模型可能直接返回 url（流式模式兼容）
                direct_url = (resp_data.get("data", {}).get("url")
                              or resp_data.get("url"))
                if direct_url:
                    logger.info("GrsAI %s直接返回 URL: %s...", mode_label, direct_url[:80])
                    return direct_url
                logger.warning("GrsAI %s未获取到 task_id: %s", mode_label, resp_data)
                return None

            logger.info("GrsAI %s任务已提交: task_id=%s", mode_label, task_id)
        except requests.exceptions.Timeout:
            logger.warning("GrsAI %s提交超时", mode_label)
            return None
        except Exception as e:
            logger.warning("GrsAI %s提交异常: %s", mode_label, e)
            return None

        # 第2步：轮询任务结果（每 3 秒，最多 60 次 = 180 秒）
        result_url = f"{self.base_url}/v1/draw/result"
        max_polls = 60
        poll_interval = 3  # 秒

        for i in range(max_polls):
            time.sleep(poll_interval)
            try:
                r = requests.post(result_url, headers=headers,
                                  json={"id": task_id}, timeout=15)
                if r.status_code != 200:
                    logger.debug("GrsAI %s轮询 HTTP %s", mode_label, r.status_code)
                    continue

                r_data = r.json()
                if r_data.get("code") != 0:
                    logger.debug("GrsAI %s轮询 code=%s", mode_label, r_data.get("code"))
                    continue

                data = r_data.get("data", {})
                status = data.get("status", "")

                if status == "succeeded":
                    # 成功：从 results 数组取图片 URL
                    results = data.get("results", [])
                    if results and results[0].get("url"):
                        img_url = results[0]["url"]
                        logger.info("GrsAI %s成功: %s...", mode_label, img_url[:80])
                        return img_url
                    # 兜底：直接在 data 里的 url 字段
                    img_url = data.get("url") or data.get("image_url")
                    if img_url:
                        logger.info("GrsAI %s成功: %s...", mode_label, img_url[:80])
                        return img_url
                    logger.warning("GrsAI %s成功但未获取到 URL: %s", mode_label, data)
                    return None

                if status in ("failed", "error"):
                    reason = data.get("failure_reason") or data.get("message") or "未知"
                    logger.warning("GrsAI %s失败: %s", mode_label, reason)
                    # 内容审核失败：尝试用简化 prompt 重试一次（去掉指令式措辞）
                    if ("moderation" in reason.lower() and image_url
                            and not _is_retry):
                        # 提取 prompt 中的描述部分，去掉指令式前缀（Based on... / keep the... 等）
                        import re as _re
                        simplified = _re.sub(
                            r'^(Based on|Keep|Place|Put|According to)[^.]*\.\s*',
                            '', prompt, flags=_re.IGNORECASE)
                        simplified = _re.sub(
                            r'\b(keep the EXACT same|preserve|maintain|identical)\b[^.]*\.?\s*',
                            '', simplified, flags=_re.IGNORECASE)
                        simplified = simplified.strip() or prompt
                        if simplified != prompt:
                            logger.info("GrsAI 内容审核失败，用简化 prompt 重试: %s...",
                                        simplified[:80])
                            return self._grsai_submit_and_poll(
                                simplified, size, model, image_url=image_url,
                                _is_retry=True)
                    return None

                # 进行中：processing / pending / queued 等
                progress = data.get("progress")
                if progress is not None:
                    logger.debug("GrsAI %s进度: %s%% (第%d次轮询)",
                                 mode_label, progress, i + 1)
                else:
                    logger.debug("GrsAI %s状态: %s (第%d次轮询)",
                                 mode_label, status, i + 1)

            except requests.exceptions.Timeout:
                logger.debug("GrsAI %s轮询超时，重试", mode_label)
                continue
            except Exception as e:
                logger.debug("GrsAI %s轮询异常: %s", mode_label, e)
                continue

        logger.warning("GrsAI %s超时（%d 秒未完成）", mode_label, max_polls * poll_interval)
        return None

    def generate_image_edit(self, prompt: str, image_url: str,
                            size: str = "1024x1024", model: str = None) -> Optional[str]:
        """调用图生图模型（基于参考图重新生成），返回新图 URL（失败返回 None）。

        图生图（image-to-image）：传入参考图 URL + LLM 优化的提示词，
        从不同视角/光线/风格重新生成图片，保留主体但增强视觉冲击力。

        参数:
            prompt: 图像生成提示词（建议英文，含视角/光线/风格指令）
            image_url: 参考图 URL（原图 URL，必须是公网可访问的 URL）
            size: 图像尺寸，如 "1024x1024" / "1024x1792" / "1792x1024"
            model: 指定模型（None=用服务商默认 image_model）
        返回:
            新图片 URL 字符串，失败返回 None
        """
        if not self.available:
            return None

        image_model = model or self.get_image_model()
        if not image_model:
            cfg = PROVIDER_CONFIGS.get(self.provider, {})
            logger.warning("当前服务商 %s 暂不支持图生图（image_model 未配置）",
                           cfg.get("name_cn", self.provider))
            logger.warning("支持图生图的服务商: 豆包(doubao-seedream) / OpenAI(dall-e-3) / GrsAI(nano-banana-pro 等) / 魔搭(FLUX.2-klein-4B)")
            return None

        if not image_url:
            logger.warning("图生图缺少参考图 URL，将退回文生图模式")
            return self.generate_image(prompt, size=size, model=model)

        # 分支：GrsAI / 魔搭 走自定义 HTTP 调用
        if self.provider in CUSTOM_IMAGE_PROVIDERS:
            if self.provider == "modelscope":
                # 魔搭 ModelScope 图生图能力依模型而定：
                # - FLUX.2-klein-4B / FLUX.2-klein-9B：原生支持图生图（image-editing）
                # - Z-Image-Turbo / Qwen-Image：纯文生图，不支持图生图
                if _is_modelscope_image_edit_model(image_model):
                    logger.info("魔搭图生图: %s 原生支持图生图，调用 /v1/images/generations + image 字段",
                                image_model)
                    return self._generate_image_edit_modelscope(
                        prompt, image_url, size, image_model)
                # 纯文生图模型回退
                logger.warning(
                    "魔搭 ModelScope 的 %s 是纯文生图模型，不支持图生图（image-to-image）。",
                    image_model)
                logger.warning(
                    "已自动回退到文生图模式（产品可能会跑偏）。"
                    "要实现真正的图生图（原产品保真），可选方案：")
                logger.warning(
                    "  1) 魔搭图生图模型: --image-llm-model black-forest-labs/FLUX.2-klein-4B")
                logger.warning(
                    "  2) GrsAI: --image-llm-provider grsai --image-llm-model nano-banana-pro")
                logger.warning(
                    "  3) 豆包: --image-llm-provider doubao（通过 prompt 拼接参考图）")
                logger.warning("本次回退到文生图模式继续执行...")
                return self._generate_image_modelscope(prompt, size, image_model)
            return self._generate_image_edit_grsai(prompt, image_url, size, image_model)

        # 默认走 OpenAI Images Edit SDK 协议（豆包/OpenAI）
        # 注意：OpenAI Images API 的 edit 端点要求 image 是文件而非 URL，
        # 因此对 URL 形式的参考图，我们退回到 generate_image 并在 prompt 里嵌入 URL 描述
        # （豆包 Seedream 支持在 prompt 中描述原图）
        try:
            # 部分服务商（如豆包）通过 images.generate + prompt 描述参考图来实现图生图
            # 这里用 prompt 拼接方式实现，避免 SDK 限制
            enhanced_prompt = f"Based on reference image at {image_url}. {prompt}"
            logger.info("图生图（prompt 拼接模式）: model=%s size=%s ref=%s...",
                        image_model, size, image_url[:60])
            resp = self._client.images.generate(
                model=image_model,
                prompt=enhanced_prompt,
                n=1,
                size=size,
                response_format="url",
            )
            if resp.data and resp.data[0].url:
                url = resp.data[0].url
                logger.info("图生图成功: model=%s url=%s...", image_model, url[:80])
                return url
            logger.warning("图生图返回空数据")
            return None
        except Exception as e:
            logger.warning("图生图调用失败: %s", e)
            return None

    def _generate_image_modelscope(self, prompt: str, size: str = "1024x1024",
                                    model: str = "Z-Image-Turbo") -> Optional[str]:
        """魔搭 ModelScope 文生图（异步任务模式）。

        魔搭 AIGC 模型强制要求异步调用：
            1. POST /v1/images/generations（带 X-ModelScope-Async-Mode: true）→ 返回 task_id
            2. GET /v1/tasks/{task_id}（带 X-ModelScope-Task-Type: image_generation）轮询
            3. task_status == "SUCCEED" 时，从 output_images[0] 取图片 URL

        文档: https://www.modelscope.cn/docs/model-service/API-Inference/intro
        限制: 每日 2000 次（单模型 500 次/日），需实名认证

        参数:
            prompt: 图像生成提示词（建议英文，效果更好）
            size: 图像尺寸，如 "1024x1024" / "1328x1328"
                  Z-Image-Turbo 支持 [512x512, 2048x2048]
            model: 魔搭模型 ID（如 Z-Image-Turbo / Qwen/Qwen-Image）
        返回:
            图片 URL 字符串，失败返回 None
        """
        try:
            import requests
            import time as _time
        except ImportError:
            logger.warning("requests 未安装，魔搭文生图不可用")
            return None

        # 魔搭支持的尺寸格式：WxH
        size_clean = size.strip().lower().replace(" ", "")

        # 魔搭 API 要求 model ID 用「组织/仓库名」格式（如 Tongyi-MAI/Z-Image-Turbo）
        # 用户常见误填：Z-Image-Turbo / z_image_turbo / Comfy-Org/z_image_turbo
        # 自动纠正常见短名 → 正确 model ID
        MODEL_ALIAS = {
            "z-image-turbo": "Tongyi-MAI/Z-Image-Turbo",
            "z_image_turbo": "Tongyi-MAI/Z-Image-Turbo",
            "qwen-image": "Qwen/Qwen-Image",
            "qwen_image": "Qwen/Qwen-Image",
        }
        model_id = model.strip()
        # 去掉常见的错误组织前缀
        for bad_prefix in ("Comfy-Org/", "comfy-org/"):
            if model_id.lower().startswith(bad_prefix):
                model_id = model_id[len(bad_prefix):]
        # 短名补全为完整 model ID
        if model_id and "/" not in model_id and model_id.lower() in MODEL_ALIAS:
            model_id = MODEL_ALIAS[model_id.lower()]
            logger.info("魔搭文生图: 模型名自动纠错 → %s", model_id)

        # 异步模式必须用 https://api-inference.modelscope.cn/ 作为 base（去掉 /v1）
        base = self.base_url.rstrip("/").removesuffix("/v1")
        submit_url = f"{base}/v1/images/generations"

        common_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_id,
            "prompt": prompt,
            "size": size_clean,
        }

        try:
            logger.info("魔搭文生图(异步): model=%s size=%s prompt=%s...",
                        model_id, size_clean, prompt[:60])

            # 1. 提交任务（X-ModelScope-Async-Mode: true 触发异步模式）
            resp = requests.post(
                submit_url,
                headers={**common_headers, "X-ModelScope-Async-Mode": "true"},
                json=payload,
                timeout=30,
            )
            if resp.status_code != 200:
                try:
                    err_data = resp.json()
                    err_msg = (err_data.get("errors", {}).get("message")
                               or err_data.get("message") or str(err_data))
                except Exception:
                    err_msg = resp.text[:300]
                logger.warning("魔搭文生图提交任务失败 HTTP %d: %s",
                               resp.status_code, err_msg)
                return None

            task_id = resp.json().get("task_id")
            if not task_id:
                logger.warning("魔搭文生图: 提交任务未返回 task_id, resp=%s",
                               resp.text[:300])
                return None
            logger.info("魔搭文生图任务已提交: task_id=%s", task_id)

            # 2. 轮询任务状态（最多 120 秒，每 5 秒一次）
            poll_url = f"{base}/v1/tasks/{task_id}"
            poll_headers = {**common_headers,
                            "X-ModelScope-Task-Type": "image_generation"}
            max_attempts = 24
            for i in range(max_attempts):
                _time.sleep(5)
                try:
                    r = requests.get(poll_url, headers=poll_headers, timeout=15)
                except requests.exceptions.Timeout:
                    logger.debug("魔搭文生图轮询超时，重试 (attempt %d/%d)",
                                 i + 1, max_attempts)
                    continue
                if r.status_code != 200:
                    logger.debug("魔搭文生图轮询 HTTP %d (attempt %d/%d)",
                                 r.status_code, i + 1, max_attempts)
                    continue
                data = r.json()
                status = data.get("task_status", "")
                if status == "SUCCEED":
                    images = data.get("output_images") or []
                    if images:
                        logger.info("魔搭文生图成功: task_id=%s url=%s...",
                                    task_id, images[0][:80])
                        return images[0]
                    logger.warning("魔搭文生图: 任务成功但 output_images 为空, data=%s",
                                   json.dumps(data, ensure_ascii=False)[:300])
                    return None
                if status == "FAILED":
                    logger.warning("魔搭文生图任务失败: %s",
                                   json.dumps(data, ensure_ascii=False)[:300])
                    return None
                # 其他状态（PENDING/RUNNING）继续轮询
                logger.debug("魔搭文生图任务状态: %s (attempt %d/%d)",
                             status, i + 1, max_attempts)

            logger.warning("魔搭文生图: 任务轮询超时（%d 秒），task_id=%s",
                           max_attempts * 5, task_id)
            return None

        except requests.exceptions.Timeout:
            logger.warning("魔搭文生图: 请求超时，可能模型排队中，请稍后重试")
            return None
        except Exception as e:
            logger.warning("魔搭文生图调用失败: %s", e)
            return None

    def _generate_image_edit_modelscope(self, prompt: str, image_url: str,
                                         size: str = "1024x1024",
                                         model: str = "black-forest-labs/FLUX.2-klein-4B") -> Optional[str]:
        """魔搭 ModelScope 图生图（异步任务模式，基于参考图编辑）。

        用于支持图像编辑的模型（如 FLUX.2-klein-4B）。

        经实测，魔搭 OpenAI 兼容协议的图生图走文生图端点，用 image 字段传参考图：
            1. POST /v1/images/generations（带 X-ModelScope-Async-Mode: true）→ 返回 task_id
               payload: {model, prompt, image, size}   ← 注意字段名是 image，不是 image_url
            2. GET /v1/tasks/{task_id}（带 X-ModelScope-Task-Type: image_generation）轮询
            3. task_status == "SUCCEED" 时，从 output_images[0] 取图片 URL

        实测结论（2026-08）:
            - /v1/images/edits 端点返回 404（魔搭未实现该端点）
            - /v1/images/generations + image_url 字段返回 400（字段名不对）
            - /v1/images/generations + image 字段返回 200 ✅（正确方式）
            - image 字段支持公网 URL 和 data:image/...;base64,... 两种形式

        参数:
            prompt: 图像编辑提示词（描述如何修改参考图）
            image_url: 参考图 URL（公网可访问）或 data URL（base64）
            size: 图像尺寸，如 "1024x1024"
            model: 魔搭图生图模型 ID（如 black-forest-labs/FLUX.2-klein-4B）
        返回:
            新图片 URL 字符串，失败返回 None
        """
        try:
            import requests
            import time as _time
        except ImportError:
            logger.warning("requests 未安装，魔搭图生图不可用")
            return None

        size_clean = size.strip().lower().replace(" ", "")
        model_id = model.strip()

        # 异步模式 base url
        base = self.base_url.rstrip("/").removesuffix("/v1")
        # 关键：魔搭图生图走 /v1/images/generations 端点（非 /v1/images/edits）
        submit_url = f"{base}/v1/images/generations"

        common_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 关键：字段名是 image（不是 image_url），与文生图共用 /generations 端点
        payload = {
            "model": model_id,
            "prompt": prompt,
            "image": image_url,
            "size": size_clean,
        }

        try:
            logger.info("魔搭图生图(异步): model=%s size=%s image=%s... prompt=%s...",
                        model_id, size_clean, image_url[:60], prompt[:60])

            # 1. 提交任务
            resp = requests.post(
                submit_url,
                headers={**common_headers, "X-ModelScope-Async-Mode": "true"},
                json=payload,
                timeout=30,
            )
            if resp.status_code != 200:
                try:
                    err_data = resp.json()
                    err_msg = (err_data.get("errors", {}).get("message")
                               or err_data.get("message") or str(err_data))
                except Exception:
                    err_msg = resp.text[:300]
                logger.warning("魔搭图生图提交任务失败 HTTP %d: %s",
                               resp.status_code, err_msg)
                return None

            task_id = resp.json().get("task_id")
            if not task_id:
                logger.warning("魔搭图生图: 提交任务未返回 task_id, resp=%s",
                               resp.text[:300])
                return None
            logger.info("魔搭图生图任务已提交: task_id=%s", task_id)

            # 2. 轮询任务状态（最多 120 秒，每 5 秒一次）
            poll_url = f"{base}/v1/tasks/{task_id}"
            poll_headers = {**common_headers,
                            "X-ModelScope-Task-Type": "image_generation"}
            max_attempts = 24
            for i in range(max_attempts):
                _time.sleep(5)
                try:
                    r = requests.get(poll_url, headers=poll_headers, timeout=15)
                except requests.exceptions.Timeout:
                    logger.debug("魔搭图生图轮询超时，重试 (attempt %d/%d)",
                                 i + 1, max_attempts)
                    continue
                if r.status_code != 200:
                    logger.debug("魔搭图生图轮询 HTTP %d (attempt %d/%d)",
                                 r.status_code, i + 1, max_attempts)
                    continue
                data = r.json()
                status = data.get("task_status", "")
                if status == "SUCCEED":
                    images = data.get("output_images") or []
                    if images:
                        logger.info("魔搭图生图成功: task_id=%s url=%s...",
                                    task_id, images[0][:80])
                        return images[0]
                    logger.warning("魔搭图生图: 任务成功但 output_images 为空, data=%s",
                                   json.dumps(data, ensure_ascii=False)[:300])
                    return None
                if status == "FAILED":
                    logger.warning("魔搭图生图任务失败: %s",
                                   json.dumps(data, ensure_ascii=False)[:300])
                    return None
                logger.debug("魔搭图生图任务状态: %s (attempt %d/%d)",
                             status, i + 1, max_attempts)

            logger.warning("魔搭图生图: 任务轮询超时（%d 秒），task_id=%s",
                           max_attempts * 5, task_id)
            return None

        except requests.exceptions.Timeout:
            logger.warning("魔搭图生图: 请求超时，可能模型排队中，请稍后重试")
            return None
        except Exception as e:
            logger.warning("魔搭图生图调用失败: %s", e)
            return None

    def _generate_image_edit_grsai(self, prompt: str, image_url: str,
                                    size: str = "1024x1024",
                                    model: str = "nano-banana-pro") -> Optional[str]:
        """GrsAI 图生图（基于参考图重新生成，异步任务模式）。

        复用 _grsai_submit_and_poll() 的异步提交+轮询逻辑，传入 image_url 触发图生图。

        GrsAI 的 /v1/draw/completions 接口支持 image_url 参数（参考图），
        配合 nano-banana / nano-banana-pro / gpt-image-1.5 等模型可做图生图：
        - 保留原图主体（产品/人物/场景）
        - 根据 prompt 从不同视角/光线/构图/风格重新生成
        - 适合基于原封面做视觉增强二创

        参数:
            prompt: 图像生成提示词（含视角/光线/风格指令）
            image_url: 参考图 URL（公网可访问）
            size: OpenAI 风格尺寸（1024x1024 等），内部转换成 GrsAI 比例
            model: GrsAI 模型名（推荐 nano-banana-pro，图生图效果最好）
        返回:
            新图片 URL，失败返回 None
        """
        return self._grsai_submit_and_poll(prompt, size, model, image_url=image_url)

    @staticmethod
    def _size_to_grsai_ratio(size: str) -> str:
        """把 OpenAI 风格尺寸（1024x1024）转换成 GrsAI 比例格式（1:1）。

        GrsAI 支持的比例：auto / 1:1 / 3:2 / 2:3
        """
        if not size or size == "auto":
            return "auto"
        try:
            w, h = size.lower().split("x")
            w, h = int(w), int(h)
        except Exception:
            return "auto"
        ratio = w / h
        # GrsAI 只支持 1:1 / 3:2 / 2:3，找最接近的
        if abs(ratio - 1.0) < 0.1:
            return "1:1"
        if abs(ratio - 3/2) < 0.15:  # 1.5 横图
            return "3:2"
        if abs(ratio - 2/3) < 0.15:  # 0.667 竖图
            return "2:3"
        # 兜底：方图
        return "1:1"


# ---------- 单例 ----------

_client_instance: Optional[LLMClient] = None

# 生图 prompt 专用客户端（用于 --ai-cover 时分离 prompt 生成与文生图）
# 例如: --llm-provider grsai --ai-cover --prompt-llm-provider deepseek
# 文生图用 grsai 的 sora-image，生图 prompt 用 deepseek-v4-flash（更便宜）
_prompt_client_instance: Optional[LLMClient] = None
_prompt_client_configured: bool = False  # 是否显式配置过 prompt 客户端

# 文生图/图生图专用客户端（用于 --ai-cover 时分离图片生成与对话/prompt）
# 例如: --llm-provider deepseek --ai-cover --image-llm-provider grsai
# 二创文案用 deepseek-v4-flash，文生图/图生图用 grsai 的 nano-banana-pro
_image_client_instance: Optional[LLMClient] = None
_image_client_configured: bool = False  # 是否显式配置过 image 客户端


def get_client() -> LLMClient:
    """获取全局 LLM 客户端单例。"""
    global _client_instance
    if _client_instance is None:
        _client_instance = LLMClient()
    return _client_instance


def set_prompt_client(provider: str = None, model: str = None, api_key: str = None) -> bool:
    """配置生图 prompt 专用 LLM 客户端。

    用于 --ai-cover 时将"生成生图 prompt"和"文生图"分离到不同服务商。
    例如用 deepseek 生成 prompt（便宜），用 grsai 的 sora-image 生图（支持文生图）。

    参数:
        provider: 服务商名（doubao/qwen/deepseek/openai/grsai）
        model: 模型名（None=用服务商默认）
        api_key: API Key（None=从环境变量读取）
    返回:
        True=客户端可用，False=不可用
    """
    global _prompt_client_instance, _prompt_client_configured
    _prompt_client_configured = True
    _prompt_client_instance = LLMClient(provider=provider, api_key=api_key, model=model)
    if _prompt_client_instance.available:
        logger.info("Prompt 专用 LLM 客户端就绪: provider=%s model=%s",
                    _prompt_client_instance.provider, _prompt_client_instance.model)
        return True
    logger.warning("Prompt 专用 LLM 客户端不可用，将回退到主客户端（%s）",
                   provider or "默认")
    return False


def get_prompt_client() -> LLMClient:
    """获取生图 prompt 专用客户端。

    如已通过 set_prompt_client() 显式配置，返回专用客户端；
    否则回退到主客户端 get_client()（向后兼容）。
    """
    if _prompt_client_configured and _prompt_client_instance is not None:
        return _prompt_client_instance
    return get_client()


def set_image_client(provider: str = None, model: str = None, api_key: str = None) -> bool:
    """配置文生图/图生图专用 LLM 客户端。

    用于 --ai-cover 时将"图片生成"与"对话/prompt 生成"分离到不同服务商。
    例如: --llm-provider deepseek --image-llm-provider grsai
         二创文案用 deepseek-v4-flash，文生图/图生图用 grsai nano-banana-pro（支持图生图）。

    参数:
        provider: 服务商名（doubao/openai/grsai，需支持文生图）
        model: 文生图模型名（None=用服务商默认）
        api_key: API Key（None=从环境变量读取）
    返回:
        True=客户端可用且支持文生图，False=不可用
    """
    global _image_client_instance, _image_client_configured
    _image_client_configured = True
    _image_client_instance = LLMClient(provider=provider, api_key=api_key, model=model)
    # 记录用户显式指定的文生图模型，供 get_image_model() 优先返回
    # 避免日志显示配置字典里的默认模型而非用户实际传入的模型
    _image_client_instance._explicit_image_model = model
    if _image_client_instance.available:
        img_model = _image_client_instance.get_image_model()
        if img_model:
            logger.info("Image 专用 LLM 客户端就绪: provider=%s image_model=%s",
                        _image_client_instance.provider, img_model)
            return True
        logger.warning("Image 专用客户端 provider=%s 不支持文生图（image_model 未配置），将回退到主客户端",
                       provider or "默认")
    else:
        logger.warning("Image 专用 LLM 客户端不可用，将回退到主客户端（%s）",
                       provider or "默认")
    return False


def get_image_client() -> LLMClient:
    """获取文生图/图生图专用客户端。

    如已通过 set_image_client() 显式配置且支持文生图，返回专用客户端；
    否则回退到主客户端 get_client()（向后兼容）。

    这样设计的好处：
    - --llm-provider deepseek --image-llm-provider grsai → 文生图走 grsai，对话走 deepseek
    - 不指定 --image-llm-provider 时，文生图跟随 --llm-provider（原行为）
    """
    if _image_client_configured and _image_client_instance is not None:
        img_model = _image_client_instance.get_image_model()
        if img_model:
            return _image_client_instance
    return get_client()


# ===== 图片生成备用客户端（负载均衡/故障转移）=====
_image_fallback_client: Optional[LLMClient] = None
_image_fallback_configured: bool = False


def set_image_fallback_client(provider: str = None, model: str = None,
                              api_key: str = None) -> bool:
    """配置文生图/图生图备用客户端（主客户端失败时自动切换）。

    典型场景：主用 GrsAI（gpt-image-2），失败时切换到魔搭（Z-Image-Turbo，免费）。
    触发切换的条件：主客户端返回 None（包括内容审核 output_moderation 拦截）。

    参数:
        provider: 备用服务商名（如 modelscope）
        model: 备用模型名
        api_key: 备用 API Key（魔搭用 token，None=从环境变量读取）
    返回:
        True=备用客户端可用，False=不可用
    """
    global _image_fallback_client, _image_fallback_configured
    _image_fallback_configured = True
    _image_fallback_client = LLMClient(provider=provider, api_key=api_key, model=model)
    _image_fallback_client._explicit_image_model = model
    if _image_fallback_client.available:
        img_model = _image_fallback_client.get_image_model()
        if img_model:
            logger.info("Image 备用 LLM 客户端就绪: provider=%s image_model=%s",
                        _image_fallback_client.provider, img_model)
            return True
        logger.warning("Image 备用客户端 provider=%s 不支持文生图，备用机制不生效",
                       provider or "默认")
    else:
        logger.warning("Image 备用 LLM 客户端不可用（provider=%s），备用机制不生效",
                       provider or "默认")
    return False


def get_image_fallback_client() -> Optional[LLMClient]:
    """获取文生图备用客户端。未配置或不可用返回 None。"""
    if not _image_fallback_configured:
        return None
    if _image_fallback_client is None or not _image_fallback_client.available:
        return None
    if not _image_fallback_client.get_image_model():
        return None
    return _image_fallback_client


def is_available() -> bool:
    """检查 LLM 是否可用（API Key 已配置且 SDK 已安装）。"""
    return get_client().available


# ---------- 业务封装：文案生成 ----------

SYSTEM_PROMPT = """你是小红书爆款文案专家，深谙小红书调性和爆款规律。

你的文案特点：
1. 标题：15-25 字，含钩子词（绝了/天花板/相见恨晚/被问爆/保姆级等）
2. 开头：有强 hook，引发好奇或共鸣
3. 结尾：引导互动（点赞/收藏/关注）
4. 标签：5-8 个，含品类词+情绪词+长尾词
5. 调性：真实、有温度、有情绪，像朋友分享而非广告

输出要求：严格按指定格式输出，不要多余解释。"""


def llm_generate_titles(original_title: str, original_desc: str,
                         tags: List[str], content_type: str = None,
                         n: int = 5) -> List[str]:
    """用 LLM 生成爆款标题候选。

    返回: 标题列表，失败返回空列表（调用方应回退到模板库）
    """
    client = get_client()
    if not client.available:
        return []

    type_hint = f"\n内容类型: {content_type}" if content_type else ""
    tags_str = " ".join(f"#{t}" for t in tags) if tags else "无"

    prompt = f"""请把以下小红书笔记改写成 {n} 个爆款标题候选。

原标题: {original_title}
正文: {original_desc}
标签: {tags_str}{type_hint}

2026 小红书高赞爆款标题核心法则（高曝光、高互动、拒绝老土与干瘪陈旧）：
1. 【潮感语感】彻底丢弃"保姆级/小白必看/从入门到精通/吐血整理"等古早陈旧套路。使用当下年轻博主最火的潮感网感句式：
   - 痛点反常识："以为是智商税…用完真香了📦"、"建议所有搞不好XX的人都去试试💡"
   - 偷感/被问爆："偷感很重的XX技巧🤫"、"被闺蜜连环催更的XX整理法✨"
   - 场景代入/松弛感："出租屋第N年，靠它住出高级感🏡"、"衣柜整理瓶颈期？换这个直接爽翻👗"
   - 情绪认同："求求大数据推给需要的姐妹🤍"、"这届XX已经next level了！"
2. 【契合Emoji】必须根据赛道自然融入 1~2 个高质感 Emoji（收纳👗/📦/✨、好物💡/🤍/🏷️、日常🪴/☕、美食🍳/🍞）。
3. 【黄金字数】字数严格控制在 14~20 字（过短无信息量，>20 字手机双列排版会被截断丢词）。
4. 【视觉与心理抓手】直击核心爽点（“省出一半空间”、“治愈强迫症”、“高级感拉满”），拒绝无聊平淡。
5. 只输出标题，每行一个，不要序号、不要引号、不要额外解释。

输出:"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = client.chat_with_retry(messages, temperature=0.9, max_tokens=500)
    if not result:
        return []

    # 解析输出（每行一个标题）
    titles = []
    for line in result.split("\n"):
        line = line.strip()
        # 去掉可能的序号前缀 "1. " "2. " 等
        line = line.lstrip("0123456789.、-）()） ")
        # 去掉引号
        line = line.strip("\"'""''")
        # 过滤超长标题（硬性 <= 21 字）和过短标题
        if line and 5 <= len(line) <= 21:
            titles.append(line)
        elif line and len(line) > 21:
            logger.warning("LLM 标题超长已丢弃 (%d 字): %s", len(line), line)

    logger.info("LLM 生成 %d 个标题候选 (已过滤 >21 字)", len(titles))
    return titles[:n]


def llm_generate_body(original_title: str, original_desc: str,
                       tags: List[str], content_type: str = None) -> Optional[str]:
    """用 LLM 生成爆款正文。

    返回: 正文文本，失败返回 None
    """
    client = get_client()
    if not client.available:
        return None

    type_hint = f"\n内容类型: {content_type}" if content_type else ""
    tags_str = " ".join(f"#{t}" for t in tags) if tags else "无"

    prompt = f"""请把以下小红书笔记改写成爆款正文。

原标题: {original_title}
原正文: {original_desc}
标签: {tags_str}{type_hint}

要求:
1. 开头有强 hook（用"姐妹们！"/"被问了好多次"/"先收藏再看"等钩子句式）
2. 保留原内容核心信息，但用更口语化、有温度的方式表达
3. 结尾引导互动（点赞/收藏/关注）
4. 总字数 150-300 字
5. 不要用 emoji 符号过多（最多 2-3 个）
6. 最后单独一行输出 5-8 个标签（用 # 开头，空格分隔）

输出:"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = client.chat_with_retry(messages, temperature=0.9, max_tokens=800)
    if not result:
        return None

    logger.info("LLM 生成正文 (%d 字)", len(result))
    return result


def llm_generate_cover_title(original_title: str, original_desc: str,
                              subtitle_text: str = None, content_type: str = None) -> Optional[str]:
    """用 LLM 生成封面标题（结合视频字幕内容，更贴切）。

    参数:
        subtitle_text: whisper 识别的字幕文本（如有，让封面标题更贴合视频内容）
    返回: 封面标题（6-12 字），失败返回 None
    """
    client = get_client()
    if not client.available:
        return None

    subtitle_hint = ""
    if subtitle_text and len(subtitle_text) > 10:
        # 截取前 200 字字幕作为参考
        subtitle_hint = f"\n视频字幕（参考）: {subtitle_text[:200]}"

    type_hint = f"\n内容类型: {content_type}" if content_type else ""

    prompt = f"""为小红书视频封面提炼一个高点击率的超强视觉短标题。

原标题: {original_title}
正文: {original_desc}{type_hint}{subtitle_hint}

封面标题准则:
1. 6-11 个字（简短有力，一眼抓住视线，如“拯救凌乱衣柜👗”、“厚外套这样收纳✨”）
2. 根据具体赛道自然带上 1 个契合主题的爆款 emoji（如 👗/✨/💡/📦/🏠/🪴/🍳/☕）
3. 必须包含核心利益点或痛点反差（如“衣柜瞬间多出一半”、“告别塞爆杂乱”）
4. 适合大字报封面视觉，拒绝冗长废话
5. 只输出标题内容，不要带标点符号，不要加多余解释
6. 不要带任何前缀标签，直接输出标题内容

输出:"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = client.chat_with_retry(messages, temperature=0.9, max_tokens=50)
    if not result:
        return None

    # 清理：去掉换行、引号、标点
    title = result.strip().split("\n")[0].strip()
    title = title.strip("\"'""''")
    # 去掉句末标点
    title = title.rstrip("。！？!?.，,；;")

    # 清洗掉 LLM 可能输出的风格前缀（如"对比式：""情绪式："等）
    import re
    title = re.sub(r'^[（(]?\s*(对比|情绪|清单|场景|产品|钩子|主角|特写|卡片|疑问|感叹|数字|悬念)(式|类|型|风|风格)?\s*[：:)]\s*', '', title)
    title = re.sub(r'^[（(]?\s*[A-Za-z]+\s*[:：]\s*', '', title)  # 英文前缀如 "Style:"
    title = title.strip("\"'""'' ")

    if len(title) < 2:
        return None

    logger.info("LLM 生成封面标题: %s", title)
    return title


# ---------- 口播二创改写 ----------

# 二创风格预设（每种风格对应不同的改写指令）
REWRITE_STYLES = {
    # words_per_second: 该风格配音的中文语速基准（字/秒）
    #   - 影响改写目标字数计算，避免配音时长与视频不匹配
    #   - 默认 3.5；语速慢的风格（gentle/knowledge）调低，语速快的（passion/concise）调高
    "casual": {
        "name_cn": "口语化",
        "desc": "更自然口语，像朋友分享",
        "words_per_second": 3.5,  # 默认基准
        "instruction": (
            "用更自然、口语化的方式改写，像朋友之间分享好物一样亲切自然。"
            "保留原意和所有关键信息（产品名、卖点、价格等），但表达更接地气。"
            "可以适当加语气词（啊/呢/啦/哦），但不要过多。"
        ),
    },
    "professional": {
        "name_cn": "专业带货",
        "desc": "专业自信，突出卖点",
        "words_per_second": 3.7,  # 专业主播节奏略快
        "instruction": (
            "用专业带货主播的口吻改写，自信、有说服力。"
            "保留所有关键信息（产品名、卖点、价格、优惠等），"
            "突出产品优势和购买理由，语气专业但不生硬。"
        ),
    },
    "passion": {
        "name_cn": "激情推销",
        "desc": "高能量激情，催促下单",
        "words_per_second": 4.0,  # 激情推销语速较快
        "instruction": (
            "用激情推销的口吻改写，能量感强、节奏快。"
            "保留所有关键信息，加强语气词和感叹句，"
            "营造紧迫感和抢购氛围（如\"真的绝了\"\"手慢无\"\"必须冲\"）。"
            "但不要过度夸张，保持真实感。"
        ),
    },
    "gentle": {
        "name_cn": "温柔种草",
        "desc": "温柔细腻，慢慢安利",
        "words_per_second": 3.0,  # 温柔细腻节奏舒缓
        "instruction": (
            "用温柔细腻的种草口吻改写，像闺蜜推荐好物。"
            "保留所有关键信息，语气柔和、节奏舒缓，"
            "多用感受性描述（如\"真的会很治愈\"\"用起来超舒服\"），"
            "不要硬推销，而是分享体验感。"
        ),
    },
    "knowledge": {
        "name_cn": "知识科普",
        "desc": "理性讲解，原理+好处",
        "words_per_second": 3.3,  # 科普讲解略慢，便于理解
        "instruction": (
            "用知识科普的口吻改写，理性、有逻辑。"
            "保留所有关键信息，并适当补充原理解释或使用场景说明，"
            "让用户理解\"为什么好\"而不仅是\"买它\"。"
            "语气专业客观，避免过度营销感。"
        ),
    },
    "humor": {
        "name_cn": "幽默段子",
        "desc": "搞笑段子手风格",
        "words_per_second": 3.8,  # 段子节奏偏快
        "instruction": (
            "用幽默段子的口吻改写，有梗、有趣、有记忆点。"
            "保留所有关键信息，但用搞笑或自嘲的方式表达，"
            "可以加网络热梗或流行语，但不要喧宾夺主。"
            "让观众笑着听完并记住产品。"
        ),
    },
    "concise": {
        "name_cn": "精简干练",
        "desc": "删减冗余，直击要点",
        "words_per_second": 4.2,  # 精简干练快节奏
        "instruction": (
            "用精简干练的口吻改写，删除所有冗余和重复。"
            "保留所有关键信息（产品名、核心卖点、价格），"
            "每句话都要有信息量，不废话。"
            "适合短视频快节奏场景。"
        ),
    },
    "viral": {
        "name_cn": "爆款脚本",
        "desc": "参考小红书爆款脚本结构改写",
        "words_per_second": 3.5,  # 默认基准，按赛道脚本结构动态适配
        "instruction": (
            "参考小红书爆款脚本结构改写，让文案符合爆款逻辑。"
            "根据原视频内容所属赛道，从项目 assets/hot_scripts/ 目录加载对应的爆款脚本模板，"
            "选择最匹配的脚本结构（如痛点共鸣型/对比测评型/教程型等）作为改写骨架，"
            "套用其开头hook、中段展开、结尾引导的节奏。"
            "保留原视频所有关键信息，但按爆款脚本的结构重新组织表达顺序和节奏。"
            "开头3秒必须出现强 hook（痛点/悬念/数字/反差），中段紧凑不拖沓，结尾有明确引导或情绪升华。"
        ),
    },
}


# ---------- 爆款脚本库加载（viral 风格专用） ----------

# 项目根目录（core/llm.py → 上一级是项目根）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOT_SCRIPTS_DIR = os.path.join(_PROJECT_ROOT, "assets", "hot_scripts")

# 赛道关键词映射：原视频文本/标题中出现这些关键词 → 匹配到对应赛道脚本库
# 按赛道优先级排序，匹配度高的赛道在前
TRACK_KEYWORDS = {
    "mother_baby": ["宝宝", "婴儿", "辅食", "孕期", "孕妈", "月子", "奶粉", "纸尿裤",
                    "儿童", "小孩", "幼儿", "母婴", "宝妈", "妈妈带", "育儿", "儿保"],
    "beauty_makeup": ["护肤", "精华", "面霜", "面膜", "彩妆", "口红", "粉底", "遮瑕",
                      "防晒", "美白", "抗老", "毛孔", "痘", "敏感肌", "成分", "烟酰胺",
                      "玻尿酸", "上脸", "妆容", "化妆"],
    "food_cooking": ["做法", "食谱", "菜谱", "教程", "食材", "调料", "烹饪", "电饭锅",
                     "烤箱", "炒", "煮", "蒸", "炖", "烤箱", "美食", "零食", "速食",
                     "山姆", "盒马", "烘焙", "面包"],
    "digital_review": ["手机", "电脑", "笔记本", "耳机", "相机", "平板", "智能", "数码",
                       "跑分", "帧率", "参数", "测评", "拆解", "i7", "i5", "RTX",
                       "苹果", "华为", "小米", "OPPO", "vivo"],
    "outfit_fashion": ["穿搭", "搭配", "衬衫", "裤子", "阔腿裤", "裙子", "外套", "羽绒服",
                       "鞋", "包", "配饰", "显瘦", "显高", "微胖", "小个子", "高腰",
                       "版型", "材质", "面料"],
    "knowledge_tutorial": ["教程", "学会", "Excel", "Word", "PS", "Python", "学习",
                           "方法论", "思维", "效率", "职场", "技能", "入门", "零基础"],
    "home_goods": ["家居", "收纳", "沥水", "置物", "厨房", "卫浴", "小家电", "感应",
                   "磁吸", "密封", "租房", "出租屋", "氛围灯", "夜灯", "摆件", "好物"],
    "lifestyle_vlog": ["独居", "周末", "日常", "vlog", "生活记录", "治愈", "幸福感",
                       "仪式感", "出租屋", "改造", "情侣", "家庭"],
}


def _detect_track(text: str) -> str:
    """从原视频文本中识别所属赛道（用于 viral 风格自动匹配爆款脚本）。

    参数:
        text: 原口播文本或视频标题/描述
    返回:
        赛道 ID（如 home_goods），无匹配返回 "home_goods" 作为默认（最通用赛道）
    """
    if not text:
        return "home_goods"
    text_lower = text.lower()
    scores = {}
    for track, keywords in TRACK_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[track] = score
    if not scores:
        return "home_goods"  # 默认家居赛道（最通用）
    # 取得分最高的赛道
    return max(scores.items(), key=lambda x: x[1])[0]


def _load_hot_scripts(track: str) -> Optional[Dict[str, Any]]:
    """加载指定赛道的爆款脚本库。

    参数:
        track: 赛道 ID（如 home_goods）
    返回:
        脚本库字典（含 track/name_cn/scripts），加载失败返回 None
    """
    json_path = os.path.join(HOT_SCRIPTS_DIR, f"{track}.json")
    if not os.path.exists(json_path):
        logger.warning("爆款脚本库不存在: %s（viral 风格回退到通用改写）", json_path)
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载爆款脚本库失败: %s（%s）", json_path, e)
        return None


def _pick_best_script(scripts: List[Dict[str, Any]], original_text: str) -> Optional[Dict[str, Any]]:
    """从赛道脚本列表中挑选最匹配原视频内容的脚本结构。

    匹配逻辑：用原视频文本与每个脚本的 applicable_scenes + name 做关键词匹配，
    得分最高的脚本被选中。无匹配时返回第一个脚本作为默认。

    参数:
        scripts: 赛道下的脚本列表
        original_text: 原视频口播文本
    返回:
        最匹配的脚本字典
    """
    if not scripts:
        return None
    if not original_text:
        return scripts[0]
    text_lower = original_text.lower()
    best_score = -1
    best_script = scripts[0]
    for script in scripts:
        # 匹配 applicable_scenes 关键词
        scenes = script.get("applicable_scenes", []) or []
        name = script.get("name", "")
        score = 0
        for scene in scenes:
            if scene and scene in text_lower:
                score += 2
        # 脚本名关键词匹配（如"测评"出现在原文中→选"对比测评型"）
        for kw in ("测评", "对比", "教程", "清单", "合集", "经验", "改造", "避雷", "避坑"):
            if kw in name and kw in text_lower:
                score += 3
        if score > best_score:
            best_score = score
            best_script = script
    return best_script


def llm_rewrite_speech(original_text: str, style: str = "casual",
                       content_type: str = None,
                       target_duration: float = None,
                       track: str = None) -> Optional[str]:
    """用 LLM 对原口播文本做二创改写（保持语义不偏离）。

    参数:
        original_text: 原口播文本（通常来自 whisper 识别）
        style: 改写风格（casual/professional/passion/gentle/knowledge/humor/concise/viral）
            viral=参考小红书爆款脚本结构改写，需配合 track 参数或自动识别赛道
        content_type: 内容类型提示（如\"带货\"\"教程\"\"测评\"），可选
        target_duration: 目标配音时长（秒，通常=原视频时长）。
            仅作为参考信息告知 LLM，字数约束始终以原文长度为主基准
            （改写后字数控制在原文 ±10% 以内，保持原节奏）。
            配音时长与视频的匹配交给 tts.py 的 atempo/silenceremove 分级处理。
        track: 内容赛道 ID（viral 风格专用，如 home_goods/beauty_makeup/food_cooking/
            mother_baby/digital_review/knowledge_tutorial/lifestyle_vlog/outfit_fashion）。
            None=自动从原文识别赛道。非 viral 风格忽略此参数。
    返回:
        改写后的文本，失败返回 None（调用方应回退到原文本）
    """
    client = get_client()
    if not client.available:
        return None

    if not original_text or not original_text.strip():
        logger.warning("口播二创: 原文本为空，跳过")
        return None

    style_cfg = REWRITE_STYLES.get(style)
    if not style_cfg:
        logger.warning("未知二创风格: %s（可用: %s），回退到 casual",
                       style, "/".join(REWRITE_STYLES.keys()))
        style_cfg = REWRITE_STYLES["casual"]
        style = "casual"

    type_hint = f"\n内容类型: {content_type}" if content_type else ""

    # viral 风格专属：加载赛道爆款脚本结构作为改写骨架
    # 失败时降级到 casual（不影响主流程），保证鲁棒性
    script_ref_block = ""
    if style == "viral":
        # 赛道识别：用户指定 > 自动识别
        detected_track = track or _detect_track(original_text)
        scripts_lib = _load_hot_scripts(detected_track)
        if scripts_lib:
            track_name_cn = scripts_lib.get("name_cn", detected_track)
            scripts_list = scripts_lib.get("scripts", []) or []
            picked = _pick_best_script(scripts_list, original_text)
            if picked:
                structure_str = "\n".join(f"  - {s}" for s in picked.get("structure", []))
                techniques_str = "\n".join(f"  - {t}" for t in picked.get("key_techniques", []))
                hooks_str = " / ".join(picked.get("hook_templates", [])[:3])
                example = picked.get("example", "")
                script_ref_block = f"""

【参考爆款脚本结构 - {track_name_cn}/{picked.get("name", "")}】
请严格按以下脚本结构作为改写骨架，把原视频内容套进这个结构：

脚本节奏：
{structure_str}

开头 hook 模板（参考句式，按原视频内容改写）：
  {hooks_str}

关键技巧（必须落实）：
{techniques_str}

爆款示例文案（参考表达方式，不要直接照抄）：
  {example}

【viral 风格额外要求】
- 开头3秒必须出现强 hook（痛点/悬念/数字/反差），不要废话铺垫
- 严格按脚本结构的"开头/中段/高潮/结尾"四段式组织内容
- 套用关键技巧中的写法（如"以前…现在…"对比、"第一步…"分点等）
- 结尾按脚本结构要求给出明确引导（购买/收藏/避坑等）
- 爆款示例仅作表达参考，必须基于原视频真实内容改写，不能编造信息
"""
                logger.info("口播二创 viral: 赛道=%s 脚本结构=%s",
                            track_name_cn, picked.get("name", ""))
            else:
                logger.warning("口播二创 viral: 赛道=%s 脚本列表为空，降级到通用改写",
                               track_name_cn)
        else:
            logger.warning("口播二创 viral: 赛道=%s 脚本库加载失败，降级到通用改写",
                           detected_track)

    # 字数约束：以原文长度为主基准（改写后字数接近原文，保持原节奏）
    # 目标时长作为参考信息，配音时长匹配交给 tts.py 的 atempo/silenceremove 分级处理
    orig_len = len(original_text)
    min_chars = round(orig_len * 0.9)
    max_chars = round(orig_len * 1.1)
    if target_duration and target_duration > 0:
        wps = style_cfg.get("words_per_second", 3.5)
        duration_ref_chars = round(target_duration * wps)
        length_constraint = (
            f"4. 改写后字数严格控制在 {min_chars}-{max_chars} 字之间"
            f"（原文 {orig_len} 字，必须接近原文长度，±10% 以内）。"
            f"参考信息：原视频约 {target_duration:.0f} 秒，{style_cfg['name_cn']}风格语速 "
            f"{wps} 字/秒对应 {duration_ref_chars} 字，但字数仍以原文长度为基准，"
            f"不要为了贴合视频时长而大幅增减字数"
        )
        logger.info("口播二创: 风格=%s 原文%d字(目标范围%d-%d) "
                    "视频时长=%.1fs 按语速%.1f字/秒应=%d字(仅参考)",
                    style_cfg["name_cn"], orig_len, min_chars, max_chars,
                    target_duration, wps, duration_ref_chars)
    else:
        length_constraint = (
            f"4. 改写后字数严格控制在 {min_chars}-{max_chars} 字之间"
            f"（原文 {orig_len} 字，必须接近原文长度，±10% 以内）"
        )
        logger.info("口播二创: 风格=%s 原文%d字(目标范围%d-%d)",
                    style_cfg["name_cn"], orig_len, min_chars, max_chars)

    prompt = f"""请改写以下视频口播文案，用于二创配音。

【原口播文案】
{original_text}

【改写要求】
{style_cfg["instruction"]}
{script_ref_block}

【硬性约束】
1. 必须保留原视频的所有关键信息：产品名/品牌/功能/卖点/价格/优惠/使用方法等
2. 语义不能偏离原意，不能凭空添加原视频没有的信息或卖点
3. 不能编造虚假功效或夸大宣传
{length_constraint}
5. 不要加 emoji、不要加 #标签、不要加 @用户
6. 直接输出改写后的文案，不要加任何解释或前缀
{type_hint}

【改写输出】"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    # 改写需要较长输出，max_tokens 按原文长度估算
    # 推理模型（如 deepseek-v4-flash）需要额外 token 做 reasoning，留足空间
    max_tokens = min(32000, max(2000, len(original_text) * 4))
    result = client.chat_with_retry(messages, temperature=0.85, max_tokens=max_tokens)
    if not result:
        return None

    # 清理：去掉可能的引号包裹和前后缀说明
    text = result.strip()
    text = text.strip("\"'""''")
    # 去掉可能的前缀（如"改写后："）
    for prefix in ("改写后：", "改写后:", "改写：", "改写:", "输出：", "输出:", "结果：", "结果:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    if len(text) < 10:
        logger.warning("口播二创: 改写结果过短，可能异常，跳过")
        return None

    # 自检：改写后明显超长（>原文 1.3 倍）→ 自动精简到原文长度
    # 阈值说明：1.3 倍以内的轻微超长交给 tts.py 的 atempo/silenceremove 分级处理，
    #           避免每次都触发额外 LLM 调用（推理模型单次调用 1-2 分钟，代价过高）；
    #           只有明显超长（如 LLM 失控输出 1.5 倍以上）才用 LLM 精简。
    if len(text) > orig_len * 1.3:
        logger.warning("口播二创: 改写后 %d 字超过原文 %d 字的 1.3 倍，触发自动精简",
                       len(text), orig_len)
        condensed = llm_condense_speech(text, orig_len)
        if condensed and condensed.strip() and len(condensed) < len(text):
            logger.info("口播二创自动精简: %d 字 → %d 字", len(text), len(condensed))
            text = condensed
        else:
            logger.warning("口播二创自动精简失败，保留原改写结果")
    elif len(text) > max_chars:
        # 轻微超长（原文 1.1-1.3 倍）：不调 LLM，交给 tts.py 分级处理（atempo 轻微加速）
        logger.info("口播二创: 改写后 %d 字略超原文 %d 字（1.1-1.3 倍），"
                    "交给配音阶段 atempo 处理，跳过 LLM 精简", len(text), orig_len)

    logger.info("LLM 口播二创完成: 风格=%s 原文%d字 → 改写%d字",
                style_cfg["name_cn"], len(original_text), len(text))
    return text


def llm_condense_speech(text: str, target_chars: int) -> Optional[str]:
    """用 LLM 精简口播文案到目标字数（保留关键信息，压缩冗余表达）。

    用于配音时长超出视频时长时的文本压缩，避免用 atempo 加速导致人声不自然。

    参数:
        text: 待精简的文本
        target_chars: 目标字数（精简到该字数左右，允许 ±10%）
    返回:
        精简后的文本，失败返回 None
    """
    client = get_client()
    if not client.available:
        return None

    if not text or not text.strip() or target_chars <= 0:
        return None

    min_chars = round(target_chars * 0.9)
    max_chars = round(target_chars * 1.1)

    prompt = f"""请精简以下口播文案，用于配音时长压缩。

【原文案】
{text}

【精简要求】
1. 精简后字数严格控制在 {min_chars}-{max_chars} 字（目标 {target_chars} 字）
2. 必须保留所有关键信息：产品名/品牌/功能/卖点/价格/优惠/使用方法等
3. 语义不能偏离原意，只能删减冗余表达、重复内容、过长的铺垫
4. 不能编造新信息，不能添加原视频没有的卖点
5. 保持口播的自然语感，不要变成书面语
6. 不要加 emoji、不要加 #标签、不要加 @用户
7. 直接输出精简后的文案，不要加任何解释或前缀

【精简输出】"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    max_tokens = min(32000, max(2000, len(text) * 4))
    result = client.chat_with_retry(messages, temperature=0.7, max_tokens=max_tokens)
    if not result:
        return None

    condensed = result.strip()
    condensed = condensed.strip("\"'""''")
    for prefix in ("精简后：", "精简后:", "精简：", "精简:", "输出：", "输出:", "结果：", "结果:"):
        if condensed.startswith(prefix):
            condensed = condensed[len(prefix):].strip()

    if len(condensed) < 10:
        logger.warning("文本精简: 结果过短，可能异常，跳过")
        return None

    logger.info("LLM 文本精简: %d 字 → %d 字（目标 %d）", len(text), len(condensed), target_chars)
    return condensed


# ---------- 字幕文本纠错（减少 whisper 识别错别字）----------

def llm_fix_subtitle_text(original_text: str, content_type: str = None) -> Optional[str]:
    """用 LLM 对 whisper 识别的字幕文本做错别字纠错（不改语义、不改口播风格）。

    与二创改写的区别：
        - 二创改写（llm_rewrite_speech）：改变表达风格，字数控制在原文 ±10% 以内
        - 纠错（本函数）：只修正明显错别字/同音字误识别，保留原句式和口语感

    参数:
        original_text: whisper 识别的原始字幕文本
        content_type: 内容类型提示（如"带货"/"教程"），可选
    返回:
        纠错后的文本，失败返回 None（调用方应回退到原文本）
    """
    client = get_client()
    if not client.available:
        logger.warning("字幕纠错: LLM 客户端不可用")
        return None

    if not original_text or not original_text.strip():
        logger.warning("字幕纠错: 原文为空")
        return None

    # 文本太短不值得调 LLM
    if len(original_text) < 15:
        logger.info("字幕纠错: 原文太短（%d字<15），跳过纠错", len(original_text))
        return None

    type_hint = f"\n内容类型参考: {content_type}" if content_type else ""

    prompt = f"""请修正以下语音识别（ASR）文本中的错别字。

【识别原文】
{original_text}

【纠错要求】
1. 只修正明显的错别字、同音字误识别、专有名词错误
2. 保持原句式和口语化风格，不要润色、不要改写、不要增删内容
3. 保留所有语气词（啊/呢/啦/哦/哈等）
4. 保留原标点符号的位置和类型
5. 如果原文没有错别字，原样返回
6. 专业名词常见误识别参考：品牌名/产品名/成分名/地名等
{type_hint}

【硬性约束】
- 不能改变原意
- 不能添加原文没有的信息
- 不能删除原文的信息点（除非是明显的语气词重复如"那个那个"）
- 字数与原文相差不超过 ±10%

【输出】
只输出纠错后的文本，不要加任何解释或前缀。"""

    messages = [
        {"role": "system", "content": "你是语音识别结果纠错专家，只修正错别字不改变原意。"},
        {"role": "user", "content": prompt},
    ]

    # 纠错用低温度保证稳定性
    # 推理模型（如 deepseek-v4-flash）需要额外 token 做 reasoning，max_tokens 留足空间
    max_tokens = min(32000, max(2000, len(original_text) * 4))
    # 字幕纠错要求输出长度接近原文，禁用 reasoning_content 兜底
    # （思考过程会复述 prompt + 分析，字数远超原文，触发字数差异保护被回退）
    # 推理模型若 content 为空会直接返回 None，由外层回退到原文本
    result = client.chat_with_retry(messages, temperature=0.2, max_tokens=max_tokens,
                                    allow_reasoning_fallback=False)
    if not result:
        if client._is_reasoning_model():
            logger.warning("字幕纠错: 推理模型 %s 未输出正式答案（reasoning_content 占满 token），"
                           "建议切换到更轻量的 deepseek-v4-flash（若已是 v4-flash 请检查 max_tokens/网络）",
                           client.model)
        else:
            logger.warning("字幕纠错: LLM 返回空（provider=%s model=%s），可能 API Key 未配置/额度不足",
                           client.provider, client.model)
        return None

    text = result.strip().strip("\"'""''")
    # 去掉可能的前缀
    for prefix in ("纠错后：", "纠错后:", "修正后：", "修正后:", "输出：", "输出:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    # 字数差异过大可能跑偏了，拒绝结果
    if original_text and abs(len(text) - len(original_text)) > len(original_text) * 0.15:
        logger.warning("字幕纠错: 字数差异过大（原%d→改%d），可能异常，回退到原文本",
                       len(original_text), len(text))
        return None

    if len(text) < 5:
        logger.warning("字幕纠错: 纠错后文本过短（%d字<5），回退到原文本", len(text))
        return None

    # 统计修改了多少字
    diff_count = sum(1 for a, b in zip(original_text, text) if a != b)
    if diff_count == 0 and len(original_text) == len(text):
        logger.info("字幕纠错: 未发现错别字，原样保留")
    else:
        logger.info("字幕纠错完成: 原文%d字 → 纠错%d字（约 %d 处修改）",
                    len(original_text), len(text), diff_count)
    return text


# ---------- AI 生图封面 ----------

# AI 封面风格预设（每种风格对应不同的生图 prompt 模板和 PIL 后处理方式）
AI_COVER_STYLES = {
    "product_hero": {
        "name_cn": "产品主角",
        "desc": "杂志大片感产品特写 + 高级感背景 + 底部爽点文案 + 信任标签",
        "prompt_tmpl": (
            "High-end magazine product photography, {subject} hero shot centered, "
            "editorial fashion style, soft studio lighting with rim light, "
            "elegant marble or silk fabric background, subtle bokeh, "
            "luxury commercial aesthetic, dewy fresh texture, "
            "shallow depth of field, color grading in warm cream and rose gold tones, "
            "xiaohongshu aesthetic, 3:4 vertical composition, no text in image"
        ),
        "hook_examples": ["3秒提亮 真的绝了", "一抹就亮 不踩雷", "用一次就爱上", "空瓶回购N次", "贵妇级体验"],
    },
    "before_after": {
        "name_cn": "前后对比",
        "desc": "真实对比分屏 + 数据感标签 + 顶部钩子 + 底部天数徽章",
        "prompt_tmpl": (
            "Before and after comparison split screen photography, "
            "left half shows {subject} before use (dull textured), "
            "right half shows after use (glowing radiant), "
            "clear visual transformation, clinical clean background, "
            "soft diffused lighting, realistic skin or surface texture detail, "
            "professional skincare ad style, "
            "xiaohongshu aesthetic, 3:4 vertical composition, no text in image"
        ),
        "hook_examples": ["7天实测 真实对比", "肉眼可见的变化", "第3天就惊了", "坚持28天 效果绝了", "原相机直出"],
    },
    "hook_closeup": {
        "name_cn": "钩子特写",
        "desc": "极致视觉冲击特写 + 大字钩子 + 悬念感氛围 + 紧迫感标签",
        "prompt_tmpl": (
            "Extreme macro close-up photography of {subject}, "
            "dramatic cinematic lighting with strong rim light, "
            "ultra detailed texture, water droplets or cream texture, "
            "high saturation vibrant colors, shallow depth of field, "
            "visual shock impact, sense of mystery and discovery, "
            "dark background with spotlight, hero product reveal moment, "
            "xiaohongshu eye-catching style, 3:4 vertical composition, no text in image"
        ),
        "hook_examples": ["99%的人不知道", "被问爆的宝藏", "相见恨晚", "后悔没早买", "私藏好物曝光"],
    },
    "scene_lifestyle": {
        "name_cn": "场景生活",
        "desc": "氛围感使用场景 + 暖光仪式感 + 底部品牌标签 + 生活感文案",
        "prompt_tmpl": (
            "Aesthetic lifestyle scene photography, {subject} in natural cozy usage scenario, "
            "warm golden hour sunlight, morning routine or desk setup, "
            "fresh flowers, ceramic cup, linen texture, "
            "minimalist Scandinavian interior, soft film grain, "
            "hygge cozy atmosphere, instagram worthy flat lay, "
            "pastel color palette, xiaohongshu aesthetic, "
            "3:4 vertical composition, no text in image"
        ),
        "hook_examples": ["日常好物分享", "提升幸福感的小物", "生活仪式感", "治愈系好物", "一个人也要精致"],
    },
    "list_infographic": {
        "name_cn": "清单信息图",
        "desc": "莫兰迪配色信息图 + 数字步骤层次 + 收藏引导标签",
        "prompt_tmpl": (
            "Minimalist infographic background design, "
            "soft Morandi color palette (sage green, dusty pink, warm beige), "
            "clean white space with subtle paper texture, "
            "{subject} themed flat icons and step illustrations, "
            "geometric shapes and line art, "
            "scandinavian editorial layout, "
            "xiaohongshu tutorial aesthetic, "
            "3:4 vertical composition, no text in image"
        ),
        "hook_examples": ["3步搞定 简单又快", "保姆级教程 一学就会", "一看就懂的干货", "新手必看指南", "超实用小技巧"],
    },
    "emotion_card": {
        "name_cn": "情绪卡片",
        "desc": "梦幻渐变背景 + 氛围光效 + 超大字情绪文案 + 精致排版",
        "prompt_tmpl": (
            "Dreamy aesthetic background, soft gradient from {subject} themed colors, "
            "ethereal light leaks and bokeh, delicate floral or botanical elements, "
            "watercolor texture, warm golden hour lighting, "
            "frosted glass effect, lots of negative space, "
            "cinematic soft focus, romantic atmosphere, "
            "xiaohongshu aesthetic, 3:4 vertical composition, no text in image"
        ),
        "hook_examples": ["真的会很治愈", "一整个爱住了", "谁懂啊太香了", "被狠狠拿捏了", "氛围感拉满"],
    },
    "pain_scenario": {
        "name_cn": "场景痛点",
        "desc": "真实场景痛点展示 + 爽感文案 + 强对比排版（痛点放大→爽点反转）",
        "prompt_tmpl": (
            "Realistic scene photography showing a frustrating daily pain point with {subject}, "
            "messy cluttered problem scenario, slightly desaturated cool tones, "
            "natural lighting with slight shadow, authentic unposed moment, "
            "relatable everyday problem visualization, "
            "items scattered or in disarray, expression of difficulty, "
            "documentary style candid photography, "
            "xiaohongshu aesthetic, 3:4 vertical composition, no text in image"
        ),
        "hook_examples": ["被折磨疯了 终于解决", "原来一直都做错了", "相见恨晚的神器", "后悔没早买 真香", "一招搞定 爽翻天"],
    },
}


def llm_generate_image_prompt(style: str, original_title: str,
                               original_desc: str, tags: List[str] = None,
                               content_type: str = None,
                               image_mode: str = "t2i") -> Optional[str]:
    """用 LLM 生成英文生图/图生图 prompt（基于笔记内容，更精准）。

    使用"生图 prompt 专用客户端"（get_prompt_client），可与文生图客户端分离。
    例如: --llm-provider grsai --prompt-llm-provider deepseek
         文生图用 grsai，生成 prompt 用 deepseek（更便宜）。

    参数:
        style: AI 封面风格名（见 AI_COVER_STYLES）
        original_title/desc/tags: 笔记原内容
        content_type: 内容类型提示
        image_mode: 图片生成模式
            "t2i" = 文生图（默认，从零生成）
            "i2i" = 图生图（基于原封面重新生成，prompt 含视角/光线重构指令）
    返回:
        英文 prompt，失败返回 None（调用方应回退到模板）
    """
    client = get_prompt_client()
    if not client.available:
        return None

    style_cfg = AI_COVER_STYLES.get(style, {})
    tmpl = style_cfg.get("prompt_tmpl", "")

    tags_str = " ".join(f"#{t}" for t in tags[:5]) if tags else "无"

    # 根据图片生成模式（文生图/图生图）生成不同的 prompt 指令
    if image_mode == "i2i":
        # 图生图模式：基于原封面重新生成，prompt 含视角/光线/构图重构指令
        mode_instruction = """你正在为"图生图"模式生成 prompt（基于原封面图重新生成新图）。

要求:
1. 输出一段 40-100 词的英文 prompt，描述如何从**不同视角/光线/构图/风格**重新呈现原图主体
2. 必须保留原图的核心主体（产品/人物/场景），但要从全新角度呈现
3. 可调整的维度（至少选 2 个）：
   - 视角：俯视/仰视/侧视/45度斜角/微距特写/广角远景
   - 光线：自然柔光/逆光剪影/侧光立体感/暖色调氛围光/冷色调科技感
   - 构图：居中对称/三分法则/对角线/留白极简/填充式特写
   - 风格：电影感/日系清新/复古胶片/未来科技/手绘插画/像素风
4. 不要在 prompt 里写中文文字
5. 不要描述具体文字内容（文字由后续 PIL 叠加）
6. 结尾固定加 "3:4 vertical composition, high quality, xiaohongshu cover style, reimagined from reference"
7. 只输出 prompt 文本，不要加任何解释或前缀"""
    else:
        # 文生图模式（默认）：从零生成
        mode_instruction = """要求:
1. 输出一段 30-80 词的英文 prompt，描述画面主体、场景、光线、构图、色调
2. 必须贴合笔记内容（产品名/主题/场景），不能凭空编造
3. 不要在 prompt 里写中文文字（生图模型对英文支持更好）
4. 不要描述具体文字内容（文字由后续 PIL 叠加）
5. 结尾固定加 "3:4 vertical composition, high quality, xiaohongshu cover style"
6. 只输出 prompt 文本，不要加任何解释或前缀"""

    mode_label = "图生图" if image_mode == "i2i" else "文生图"

    # 各风格专属审美引导（针对小红书年轻女性审美 + 心理学原理）
    style_aesthetic = ""
    if style == "emotion_card":
        style_aesthetic = """
emotion_card 审美要求（重要）:
- 背景: 梦幻渐变（如黎明粉/薰衣草紫/鼠尾草绿/奶油白/蜜桃橙），不要纯色
- 光效: 柔和光晕、丁达尔光、镜头眩光、散景光斑
- 元素: 点缀精致花卉/植物叶片/水彩纹理/泡泡/星光粒子
- 质感: 磨砂玻璃、水彩晕染、胶片颗粒
- 氛围: 浪漫、治愈、氛围感、电影感、golden hour 暖光
- 构图: 大面积留白（文字区域），主体偏置一侧或底部
- 色调: 低饱和暖色系或莫兰迪色系，避免高对比刺眼配色
- 不要: 生成文字、logo、人脸特写、杂乱元素"""
    elif style == "product_hero":
        style_aesthetic = """
product_hero 审美要求（重要）:
- 风格: 高端杂志大片感（Vogue/时尚芭莎商业摄影风格）
- 光线: 柔和 studio 光 + 轮廓光(rim light)，突出产品质感
- 背景: 大理石/丝绸/绒布等高级材质，柔美散景
- 色调: 奶油白/玫瑰金/香槟色暖色调，奢华但不俗气
- 质感: 产品表面有露水/光泽/细腻纹理，新鲜水润感
- 构图: 产品居中略偏下，上方留白给文字
- 不要: 生成文字、logo、杂乱背景、廉价感配色"""
    elif style == "before_after":
        style_aesthetic = """
before_after 审美要求（重要）:
- 风格: 专业护肤/美妆广告级前后对比
- 对比: 左侧暗沉粗糙，右侧透亮光滑，差异要明显但真实
- 光线: 左侧偏冷暗，右侧偏暖亮，强化对比感
- 背景: 干净简洁，纯色或淡色，不要干扰主体
- 质感: 真实皮肤/物体纹理细节，不要过度磨皮
- 构图: 严格左右对称分屏，中间留细缝
- 不要: 生成文字、夸张失真、左右角度不一致"""
    elif style == "hook_closeup":
        style_aesthetic = """
hook_closeup 审美要求（重要）:
- 风格: 戏剧性视觉冲击，电影预告片级张力
- 光线: 强烈侧光/逆光 + 暗背景聚光灯，营造神秘感
- 质感: 超精细微距纹理（水珠/膏体/粉末/光泽）
- 色彩: 高饱和但不过曝，色彩浓郁有层次
- 氛围: 悬念感、发现感、宝藏揭晓时刻
- 构图: 主体充满画面，局部特写，上方/下方留文字空间
- 不要: 生成文字、全黑无细节、平淡光线"""
    elif style == "scene_lifestyle":
        style_aesthetic = """
scene_lifestyle 审美要求（重要）:
- 风格: ins 风氛围感生活场景，仪式感满满
- 光线: 暖阳侧光/golden hour，柔和有温度
- 场景: 北欧极简 interior、木桌、陶瓷、亚麻、鲜花
- 色调: 莫兰迪/奶油/燕麦色系，温暖治愈
- 质感: 胶片颗粒、柔焦、轻微漏光
- 构图: flat lay 俯拍或 45 度生活角度，留白给文字
- 不要: 生成文字、杂乱场景、冷色调、塑料感"""
    elif style == "list_infographic":
        style_aesthetic = """
list_infographic 审美要求（重要）:
- 风格: 莫兰迪配色极简信息图，清爽专业
- 背景: 米白/浅灰底，带细腻纸张纹理
- 色彩: 鼠尾草绿/灰粉/燕麦色等莫兰迪色系
- 元素: 扁平图标、线条插画、几何形状
- 构图: 留白充足，层次清晰，有网格感
- 质感: 纸质质感、微立体阴影
- 不要: 生成文字、高饱和色、复杂插画、3D 渲染"""
    elif style == "pain_scenario":
        style_aesthetic = """
pain_scenario 审美要求（重要）:
- 风格: 真实场景痛点纪实摄影，引发强烈共鸣
- 场景: 日常使用中遇到麻烦的真实瞬间（如物品杂乱、难以清洁、空间拥挤）
- 光线: 自然光，略带冷调阴影，营造"困扰"氛围
- 色调: 略微降低饱和度（去饱和 20%），冷色调强化"痛点感"
- 质感: 真实生活质感，物品有使用痕迹，不要摆拍感
- 情绪: 让人一看就"对对对我也这样"的共鸣感
- 构图: 痛点主体居中偏下，上方留白给爽文文案
- 不要: 生成文字、过度美化、明亮暖色、摆拍精致感"""

    prompt = f"""请根据以下小红书笔记内容，生成一段英文 {mode_label} prompt（用于 AI 绘画生成封面底图）。

笔记标题: {original_title}
笔记正文: {original_desc[:300]}
标签: {tags_str}
内容类型: {content_type or "未指定"}
封面风格: {style} - {style_cfg.get('name_cn', '')} ({style_cfg.get('desc', '')})

风格模板参考:
{tmpl}
{style_aesthetic}

{mode_instruction}

输出:"""

    messages = [
        {"role": "system", "content": "你是 AI 绘画 prompt 工程师，擅长生成商业级封面图 prompt。"},
        {"role": "user", "content": prompt},
    ]

    result = client.chat_with_retry(messages, temperature=0.8, max_tokens=400)
    if not result:
        return None

    text = result.strip().strip("\"'""''")
    # 去掉可能的 "Prompt:" 前缀
    for prefix in ("Prompt:", "prompt:", "Prompt：", "prompt："):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    if len(text) < 15:
        return None

    logger.info("LLM 生成生图 prompt [%s]: %s...", style, text[:80])
    return text


def llm_generate_cover_hook(style: str, original_title: str,
                             original_desc: str, tags: List[str] = None,
                             content_type: str = None) -> Optional[str]:
    """用 LLM 生成封面钩子文案（叠加在生图上的短文案）。

    使用"生图 prompt 专用客户端"（get_prompt_client），与生图 prompt 共用同一客户端，
    便于把"封面相关文案生成"统一剥离到便宜的对话模型（如 deepseek）。

    返回: 钩子文案（6-12 字），失败返回 None（调用方回退到模板示例）
    """
    client = get_prompt_client()
    if not client.available:
        return None

    style_cfg = AI_COVER_STYLES.get(style, {})
    examples = " / ".join(style_cfg.get("hook_examples", []))

    tags_str = " ".join(f"#{t}" for t in tags[:5]) if tags else "无"

    # pain_scenario 风格专属钩子引导：强调"痛点反转→爽感"
    style_hook_hint = ""
    if style == "pain_scenario":
        style_hook_hint = """
场景痛点风格专属要求:
- 文案要体现"痛点被解决后的爽感反转"（如：被折磨疯了→终于解决）
- 用反差词强化反转（终于/原来/相见恨晚/后悔没早买/真香/一招搞定）
- 可以用略带夸张的爽文语气（爽翻天/绝绝子/人间值得）
- 不要用平淡描述，要有情绪张力和反转感"""

    prompt = f"""为小红书视频封面生成一句钩子文案（叠加在图片上的短文案）。

笔记标题: {original_title}
笔记正文: {original_desc[:200]}
标签: {tags_str}
封面风格: {style} - {style_cfg.get('name_cn', '')}
参考示例: {examples}

要求:
1. 6-12 个字，短小精悍，一眼抓住注意力
2. 含情绪点或悬念（绝了/天花板/被问爆/相见恨晚/保姆级等钩子词）
3. 适合 {style_cfg.get('name_cn', '')} 风格封面
4. 不要标点符号（封面文字不需要句号逗号）
5. 只输出一句文案，不要解释
6. 不要带任何风格标签前缀（如"对比式:""情绪式:"等），直接输出文案内容
{style_hook_hint}
输出:"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = client.chat_with_retry(messages, temperature=0.9, max_tokens=50)
    if not result:
        return None

    text = result.strip().split("\n")[0].strip().strip("\"'""''")
    text = text.rstrip("。！？!?.，,；;")

    # 清洗掉 LLM 可能输出的风格前缀（如"对比式：""情绪式："等）
    import re
    text = re.sub(r'^[（(]?\s*(对比|情绪|清单|场景|产品|钩子|主角|特写|卡片|疑问|感叹|数字|悬念)(式|类|型|风|风格)?\s*[：:)]\s*', '', text)
    text = re.sub(r'^[（(]?\s*[A-Za-z]+\s*[:：]\s*', '', text)  # 英文前缀如 "Style:"
    text = text.strip("\"'""'' ")

    if len(text) < 2:
        return None

    logger.info("LLM 生成封面钩子 [%s]: %s", style, text)
    return text


def llm_generate_image(prompt: str, size: str = "1024x1024",
                       model: str = None) -> Optional[str]:
    """业务封装：调用文生图模型，返回图片 URL。

    使用 image 专用客户端（get_image_client），可与主对话客户端分离。
    例如: --llm-provider deepseek --image-llm-provider grsai
         对话用 deepseek-v4-flash，文生图用 grsai sora-image。

    支持故障转移：主客户端失败（如内容审核拦截）时自动切换到备用客户端。
    通过 set_image_fallback_client() 配置备用客户端。

    参数:
        prompt: 英文生图 prompt
        size: 图像尺寸（豆包支持 1024x1024 / 960x1280 / 720x1080 等；
              OpenAI DALL-E 3 支持 1024x1024 / 1792x1024 / 1024x1792）
        model: 指定模型（None=用服务商默认）
    返回:
        图片 URL，失败返回 None
    """
    client = get_image_client()
    if not client.available:
        logger.warning("Image LLM 不可用，无法调用文生图（需配置 API Key）")
        return None
    url = client.generate_image(prompt, size=size, model=model)
    if url:
        return url

    # 主客户端失败 → 尝试备用客户端
    fallback = get_image_fallback_client()
    if fallback is not None and fallback is not client:
        logger.warning("主文生图客户端(%s)失败，切换到备用客户端(%s)",
                       client.provider, fallback.provider)
        # 备用客户端用其默认模型（避免主客户端的模型名不适用）
        url = fallback.generate_image(prompt, size=size, model=None)
        if url:
            logger.info("备用文生图客户端(%s)成功",
                        fallback.provider)
            return url
        logger.warning("备用文生图客户端(%s)也失败", fallback.provider)
    return None


def llm_generate_image_edit(prompt: str, image_url: str,
                             size: str = "1024x1024",
                             model: str = None) -> Optional[str]:
    """业务封装：调用图生图模型（基于参考图重新生成），返回新图 URL。

    使用 image 专用客户端（get_image_client），与文生图共用客户端。

    图生图模式：传入参考图 URL + LLM 优化的提示词，
    从不同视角/光线/构图/风格重新生成图片，保留主体但增强视觉冲击力。
    适合基于原封面做视觉增强二创。

    支持故障转移：主客户端失败（如内容审核拦截）时自动切换到备用客户端。
    注意：若备用客户端不支持图生图（如魔搭 Z-Image-Turbo 是纯文生图），
    会自动降级为文生图模式（从 prompt 提取描述）。

    参数:
        prompt: 英文图生图 prompt（含视角/光线/风格指令）
        image_url: 参考图 URL（原图 URL，必须公网可访问）
        size: 图像尺寸
        model: 指定模型（None=用服务商默认，推荐 nano-banana-pro 图生图效果最好）
    返回:
        新图片 URL，失败返回 None
    """
    client = get_image_client()
    if not client.available:
        logger.warning("Image LLM 不可用，无法调用图生图（需配置 API Key）")
        return None
    url = client.generate_image_edit(prompt, image_url, size=size, model=model)
    if url:
        return url

    # 主客户端失败 → 尝试备用客户端
    fallback = get_image_fallback_client()
    if fallback is not None and fallback is not client:
        logger.warning("主图生图客户端(%s)失败，切换到备用客户端(%s)",
                       client.provider, fallback.provider)
        fb_model = fallback.get_image_model()
        # 检查备用客户端是否支持图生图
        # 魔搭纯文生图模型（Z-Image-Turbo / Qwen-Image）不支持图生图，降级为文生图
        if (fallback.provider == "modelscope"
                and not _is_modelscope_image_edit_model(fb_model)):
            logger.info("备用客户端(魔搭 %s)不支持图生图，降级为文生图模式",
                        fb_model)
            # 从 i2i prompt 中提取描述部分（去掉 "Based on reference..." 前缀）
            import re as _re
            simplified = _re.sub(
                r'^(Based on|Keep|Place|Put|According to)[^.]*\.\s*',
                '', prompt, flags=_re.IGNORECASE)
            simplified = simplified.strip() or prompt
            url = fallback.generate_image(simplified, size=size, model=None)
        else:
            url = fallback.generate_image_edit(prompt, image_url, size=size, model=None)
        if url:
            logger.info("备用图生图客户端(%s)成功", fallback.provider)
            return url
        logger.warning("备用图生图客户端(%s)也失败", fallback.provider)
    return None


def is_image_generation_available() -> bool:
    """检查当前 LLM 服务商是否支持文生图/图生图。"""
    client = get_image_client()
    if not client.available:
        return False
    return client.get_image_model() is not None
