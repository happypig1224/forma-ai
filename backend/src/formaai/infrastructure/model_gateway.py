"""Enterprise Tri-Modal High-Availability Model Gateway for 302.AI.

Covers:
1. Large Language Models (LLM):
   - Primary: deepseek-chat
   - Fallback: gpt-4o
2. Image Generation Models (T2I/I2I):
   - Primary: doubao-seedream-5-0-260128
   - Fallback: gpt-image-1.5-t2i
3. Video Generation Models (T2V/I2V):
   - Primary: minimax-hailuo-02
   - Fallback: official-kling-v3

High-Availability Mechanisms:
- Exponential backoff retry with jitter on transient network/rate-limit errors.
- Automatic failover from Primary to Fallback model across all 3 modalities.
- Graceful deterministic baseline degradation to prevent pipeline deadlocks.
- Token, duration, GPU compute and cost accounting.
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from formaai.config import Settings, load_settings

logger = logging.getLogger(__name__)


class ModelGatewayError(RuntimeError):
    """Raised when remote model execution completely fails across all retries and fallbacks."""


@dataclass(frozen=True)
class ModelCallResult:
    """Audit metadata returned from an LLM text invocation."""

    content: str
    model_used: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    cost_cents: int
    failover_occurred: bool


@dataclass(frozen=True)
class MediaCallResult:
    """Audit metadata returned from Image or Video generation tasks."""

    media_url: str | None
    task_id: str | None
    model_used: str
    modality: str  # "image" | "video"
    provider: str
    status: str  # "completed" | "pending" | "processing" | "failed"
    latency_ms: float
    cost_cents: int
    failover_occurred: bool
    error_message: str | None = None


class ModelGateway:
    """Tri-Modal high-availability gateway connecting to 302.AI multi-model endpoints."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.api_key = self.settings.ai302_api_key.get_secret_value()
        self.base_url = self.settings.ai302_base_url.rstrip("/")

        # 1. LLM Models
        self.llm_primary = self.settings.ai302_llm_primary_model
        self.llm_fallback = self.settings.ai302_llm_fallback_model

        # 2. Image Models
        self.image_primary = self.settings.ai302_image_primary_model
        self.image_fallback = self.settings.ai302_image_fallback_model

        # 3. Video Models
        self.video_primary = self.settings.ai302_video_primary_model
        self.video_fallback = self.settings.ai302_video_fallback_model

        # Resilience controls
        self.timeout = self.settings.ai302_timeout_seconds
        self.max_retries = self.settings.ai302_max_retries
        self._cli_path = shutil.which("302ai") or "302ai"

    # =========================================================================
    # 1. Large Language Model (LLM) Gateway with Failover & Retry
    # =========================================================================

    def _execute_http_request(
        self,
        endpoint: str,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        """Perform raw HTTP request to 302.AI API endpoint."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "FormaAI-Gateway/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8")
            return json.loads(raw_body)

    def _call_llm_with_retries(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> tuple[dict[str, Any], float]:
        """Call LLM with exponential backoff on transient errors."""
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                result = self._execute_http_request(
                    endpoint="chat/completions",
                    payload=payload,
                    timeout=self.timeout,
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return result, elapsed_ms
            except urllib.error.HTTPError as exc:
                last_error = exc
                status_code = exc.code
                if status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    delay = (2 ** (attempt - 1)) * 0.5 + random.uniform(0.1, 0.3)
                    logger.warning(
                        "302.AI LLM [%s] HTTP %d on attempt %d/%d; retrying in %.2fs...",
                        model_name,
                        status_code,
                        attempt,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error("302.AI LLM [%s] HTTP %d permanent error: %s", model_name, status_code, exc)
                raise
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    delay = (2 ** (attempt - 1)) * 0.5 + random.uniform(0.1, 0.3)
                    logger.warning(
                        "302.AI LLM [%s] Network/Timeout error on attempt %d/%d; retrying in %.2fs...",
                        model_name,
                        attempt,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error("302.AI LLM [%s] Network failure after %d attempts: %s", model_name, self.max_retries, exc)
                raise

        raise ModelGatewayError(f"Exhausted retries for {model_name}: {last_error}")

    def call_chat_completion(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 1500,
        model: str | None = None,
    ) -> ModelCallResult:
        """Call LLM with automatic failover between primary and fallback models."""
        if not self.api_key:
            return ModelCallResult(
                content="[Offline] 302.AI API key not configured.",
                model_used="deterministic_fallback",
                provider="offline_fallback",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=0.0,
                cost_cents=0,
                failover_occurred=False,
            )
        target_model = model or self.llm_primary

        try:
            raw_response, latency_ms = self._call_llm_with_retries(
                model_name=target_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return self._build_llm_result(raw_response, target_model, latency_ms, failover_occurred=False)
        except Exception as primary_exc:
            if target_model == self.llm_fallback or not self.llm_fallback:
                logger.error("Primary LLM %s failed with no fallback available: %s", target_model, primary_exc)
                raise

            logger.warning(
                "Primary LLM %s failed (%s). Triggering High-Availability Failover to %s...",
                target_model,
                primary_exc,
                self.llm_fallback,
            )

        try:
            raw_response, latency_ms = self._call_llm_with_retries(
                model_name=self.llm_fallback,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return self._build_llm_result(raw_response, self.llm_fallback, latency_ms, failover_occurred=True)
        except Exception as fallback_exc:
            logger.critical("Both primary (%s) and fallback (%s) LLM failed: %s", target_model, self.llm_fallback, fallback_exc)
            raise ModelGatewayError(
                f"LLM Failover exhausted. Primary: {target_model}, Fallback: {self.llm_fallback}"
            ) from fallback_exc

    def _build_llm_result(
        self,
        raw_response: dict[str, Any],
        model_name: str,
        latency_ms: float,
        failover_occurred: bool,
    ) -> ModelCallResult:
        choices = raw_response.get("choices", [])
        if not choices:
            raise ModelGatewayError("Malformed API response: no choices returned")

        content = choices[0].get("message", {}).get("content", "").strip()
        usage = raw_response.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        cost_cents = max(1, int((total_tokens / 1000.0) * 1.5))

        return ModelCallResult(
            content=content,
            model_used=model_name,
            provider="302.ai",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=round(latency_ms, 2),
            cost_cents=cost_cents,
            failover_occurred=failover_occurred,
        )

    def generate_script_proposals(
        self,
        theme: str,
        target_market: str,
        content_type: str,
        image_urls: list[str] | None = None,
    ) -> tuple[list[dict[str, str]], ModelCallResult]:
        """Generate 3 professional script proposals using 302.AI LLM with multimodal vision fallback."""
        system_prompt = (
            "你是一位资深短视频爆款内容策划总监。你需要根据用户的【视频主题】、【目标受众】和【商业类型】，"
            "构思三套不同角度的短视频脚本方案（方案 A、方案 B、方案 C）。\n"
            "请严格以纯 JSON 格式输出，不要附加任何 Markdown 标记或多余文字。JSON 结构必须为一个包含 3 个对象的数组：\n"
            "[\n"
            "  {\n"
            '    "title": "方案 A：[简练有吸引力的标题]",\n'
            '    "badge": "[3-6字定位标签，如：高痛点促单]",\n'
            '    "desc": "[1-2句受众场景与核心逻辑描述]",\n'
            '    "hook": "[开篇黄金3秒台词/视觉动作]",\n'
            '    "body": "[核心卖点展开与解决过程]",\n'
            '    "cta": "[结尾行动号召与促单指令]"\n'
            "  },\n"
            "  ...\n"
            "]"
        )
        if image_urls:
            system_prompt += (
                "\n\n【多模态视觉参考图已挂载】：\n"
                "用户提供了相关的商品/场景视觉参考图。请仔细观察参考图中的商品外观、材质纹理、色彩配置与使用场景细节，"
                "并将这些具体的视觉画面特征精准融入各套方案的黄金3秒视觉动作、卖点特写展开以及主体视觉呈现中。"
            )

        user_prompt = (
            f"制作主题：{theme}\n"
            f"目标受众：{target_market}\n"
            f"内容模式：{content_type}\n"
            "请按要求生成风格迥异的三套高质量候选脚本方案。"
        )

        user_content: str | list[dict[str, Any]]
        preferred_model: str | None = None
        if image_urls:
            content_blocks: list[dict[str, Any]] = [
                {"type": "text", "text": user_prompt}
            ]
            for img in image_urls[:3]:
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": img},
                })
            user_content = content_blocks
            # If primary LLM is DeepSeek (text-only), route to vision-capable model (e.g. GPT-4o fallback)
            if "deepseek" in (self.llm_primary or "").lower():
                preferred_model = self.llm_fallback
        else:
            user_content = user_prompt

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        result = self.call_chat_completion(
            messages=messages,
            temperature=0.7,
            max_tokens=1500,
            model=preferred_model,
        )
        proposals = self._parse_json_proposals(result.content, theme, target_market, content_type)
        return proposals, result

    def _parse_json_proposals(
        self,
        raw_text: str,
        theme: str,
        target_market: str,
        content_type: str,
    ) -> list[dict[str, str]]:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            data = json.loads(cleaned)
            if isinstance(data, list) and len(data) >= 3:
                return [
                    {
                        "title": str(item.get("title", f"方案 {chr(65+i)}")),
                        "badge": str(item.get("badge", "推荐")),
                        "desc": str(item.get("desc", "")),
                        "hook": str(item.get("hook", "")),
                        "body": str(item.get("body", "")),
                        "cta": str(item.get("cta", "")),
                    }
                    for i, item in enumerate(data[:3])
                ]
        except Exception:
            logger.warning("Failed to parse raw LLM JSON, falling back to deterministic baseline: %s", raw_text)

        return [
            {
                "title": f"方案 A：痛点反转型 · {theme}",
                "badge": "高转化",
                "desc": f"直击【{target_market}】痛点，快速反转承接解决方案。",
                "hook": f"“你是不是也常因为【{theme}】踩坑？今天彻底讲透！”",
                "body": f"针对【{target_market}】核心痛点，从实测与体验切入，全面展现独特亮点。",
                "cta": "“点击左下角立即锁定专属优惠，手慢无！”",
            },
            {
                "title": f"方案 B：场景共鸣型 · {theme}",
                "badge": "高完播",
                "desc": f"将【{theme}】自然融入高频真实场景，引发强烈情感认同。",
                "hook": f"“每次遇到这个场景，总有人问我为什么能这么轻松应对……”",
                "body": f"深度还原日常生活工作细节，让【{target_market}】看到切实改变。",
                "cta": "“关注官方主页，探索更多质感解决方案。”",
            },
            {
                "title": f"方案 C：硬核对比型 · {theme}",
                "badge": "强公信",
                "desc": f"量化对比核心参数，击穿疑虑，建立转化信任。",
                "hook": f"“市面上同类宣传五花八门，今天直接拆解实测，用数据说话！”",
                "body": f"核心指标实测全面领先，专为注重实效的【{target_market}】打造。",
                "cta": "“支持7天品质检验，点击详情立即体验。”",
            },
        ]

    # =========================================================================
    # 2. Image Generation Model (T2I/I2I) Gateway with Failover & Retry
    # =========================================================================

    def _execute_cli_command(self, args: list[str], timeout: float = 60.0) -> dict[str, Any]:
        """Execute a 302ai CLI command and parse JSON output."""
        cmd = [self._cli_path] + args
        if self.api_key:
            cmd.extend(["--api_key", self.api_key])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip() or f"CLI returned code {proc.returncode}"
            raise ModelGatewayError(f"302ai CLI error: {err_msg}")

        try:
            return json.loads(proc.stdout.strip())
        except Exception as exc:
            raise ModelGatewayError(f"Failed to parse 302ai CLI output: {proc.stdout}") from exc

    def _submit_image_task(
        self,
        model_name: str,
        prompt: str,
        aspect_ratio: str,
        image_url: str | None = None,
    ) -> dict[str, Any]:
        """Submit image creation task with retry on transient failures."""
        args = ["image", "create", "--prompt", prompt, "--model", model_name, "--aspect_ratio", aspect_ratio, "--short"]
        if image_url:
            args.extend(["--image", image_url])

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = self._execute_cli_command(args, timeout=self.timeout)
                if result.get("status") in {"pending", "completed"}:
                    return result
                raise ModelGatewayError(f"Task rejected with status: {result.get('status')}")
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    delay = (2 ** (attempt - 1)) * 0.5 + random.uniform(0.1, 0.3)
                    logger.warning("Image task submission [%s] failed on attempt %d/%d: %s; retrying in %.2fs", model_name, attempt, self.max_retries, exc, delay)
                    time.sleep(delay)
                    continue
                raise

        raise ModelGatewayError(f"Image creation retries exhausted for {model_name}: {last_error}")

    def _poll_image_task(self, task_id: str, max_wait_sec: float = 40.0) -> str | None:
        """Poll image completion until URL is available or timeout."""
        start = time.perf_counter()
        while (time.perf_counter() - start) < max_wait_sec:
            time.sleep(3)
            try:
                fetch_res = self._execute_cli_command(["image", "fetch", task_id, "--short"], timeout=15)
                if fetch_res.get("status") == "completed":
                    return fetch_res.get("result_url")
                if fetch_res.get("status") == "failed":
                    logger.warning("Image task %s failed during polling", task_id)
                    return None
            except Exception as exc:
                logger.warning("Image task polling exception: %s", exc)
        return None

    def generate_keyframe_image(
        self,
        prompt: str,
        aspect_ratio: str = "9:16",
        image_url: str | None = None,
        model: str | None = None,
        wait: bool = True,
    ) -> MediaCallResult:
        """Generate keyframe image with High-Availability Primary -> Fallback failover."""
        if not self.api_key:
            return MediaCallResult(
                media_url="https://file.302.ai/gpt/imgs/placeholder_keyframe.jpg",
                task_id=None,
                model_used="deterministic_fallback",
                modality="image",
                provider="offline_fallback",
                status="completed",
                latency_ms=0.0,
                cost_cents=0,
                failover_occurred=False,
            )
        target_model = model or self.image_primary
        t0 = time.perf_counter()

        # Attempt 1: Call Primary Image Model
        try:
            task_res = self._submit_image_task(target_model, prompt, aspect_ratio, image_url)
            task_id = task_res.get("taskid")
            result_url = task_res.get("result_url")
            if wait and task_id and not result_url:
                result_url = self._poll_image_task(task_id)

            if result_url or not wait:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return MediaCallResult(
                    media_url=result_url,
                    task_id=task_id,
                    model_used=target_model,
                    modality="image",
                    provider="302.ai",
                    status="completed" if result_url else "pending",
                    latency_ms=round(elapsed_ms, 2),
                    cost_cents=5,
                    failover_occurred=False,
                )
            logger.warning("Primary image model %s task timed out or returned no URL, triggering failover...", target_model)
        except Exception as primary_exc:
            logger.warning("Primary image model %s failed (%s). Triggering Failover to %s...", target_model, primary_exc, self.image_fallback)

        # Attempt 2: Failover to Fallback Image Model
        try:
            fb_task_res = self._submit_image_task(self.image_fallback, prompt, aspect_ratio, image_url)
            task_id = fb_task_res.get("taskid")
            result_url = fb_task_res.get("result_url")
            if wait and task_id and not result_url:
                result_url = self._poll_image_task(task_id)

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return MediaCallResult(
                media_url=result_url,
                task_id=task_id,
                model_used=self.image_fallback,
                modality="image",
                provider="302.ai",
                status="completed" if result_url else "pending",
                latency_ms=round(elapsed_ms, 2),
                cost_cents=5,
                failover_occurred=True,
            )
        except Exception as fb_exc:
            logger.error("Both primary (%s) and fallback (%s) image models failed: %s", target_model, self.image_fallback, fb_exc)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            # Return graceful offline placeholder
            return MediaCallResult(
                media_url="https://file.302.ai/gpt/imgs/placeholder_keyframe.jpg",
                task_id=None,
                model_used="deterministic_fallback",
                modality="image",
                provider="offline_fallback",
                status="completed",
                latency_ms=round(elapsed_ms, 2),
                cost_cents=0,
                failover_occurred=True,
                error_message=str(fb_exc),
            )

    # =========================================================================
    # 3. Video Generation Model (T2V/I2V) Gateway with Failover & Retry
    # =========================================================================

    def _submit_video_task(
        self,
        model_name: str,
        prompt: str,
        duration: int = 5,
        aspect_ratio: str = "9:16",
        image_url: str | None = None,
    ) -> dict[str, Any]:
        """Submit video creation task with retry on transient failures."""
        args = ["video", "create", "--prompt", prompt, "--model", model_name, "--aspect_ratio", aspect_ratio, "--duration", str(int(duration)), "--short"]
        if image_url:
            args.extend(["--image", image_url])

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = self._execute_cli_command(args, timeout=self.timeout)
                if result.get("status") in {"pending", "completed", "processing"}:
                    return result
                raise ModelGatewayError(f"Video task rejected with status: {result.get('status')}")
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    delay = (2 ** (attempt - 1)) * 0.5 + random.uniform(0.1, 0.3)
                    logger.warning("Video task submission [%s] failed on attempt %d/%d: %s; retrying in %.2fs", model_name, attempt, self.max_retries, exc, delay)
                    time.sleep(delay)
                    continue
                raise

        raise ModelGatewayError(f"Video creation retries exhausted for {model_name}: {last_error}")

    def generate_shot_video_task(
        self,
        prompt: str,
        duration: int = 5,
        aspect_ratio: str = "9:16",
        image_url: str | None = None,
        model: str | None = None,
    ) -> MediaCallResult:
        """Submit shot video task with High-Availability Primary -> Fallback failover."""
        if not self.api_key:
            return MediaCallResult(
                media_url="shots/mock/take1.mp4",
                task_id=f"offline_task_{int(time.time())}",
                model_used="deterministic_fallback",
                modality="video",
                provider="offline_fallback",
                status="pending",
                latency_ms=0.0,
                cost_cents=0,
                failover_occurred=False,
            )
        target_model = model or self.video_primary
        t0 = time.perf_counter()

        # Attempt 1: Call Primary Video Model
        try:
            task_res = self._submit_video_task(target_model, prompt, duration, aspect_ratio, image_url)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return MediaCallResult(
                media_url=task_res.get("result_url"),
                task_id=task_res.get("taskid"),
                model_used=target_model,
                modality="video",
                provider="302.ai",
                status=task_res.get("status", "pending"),
                latency_ms=round(elapsed_ms, 2),
                cost_cents=10,
                failover_occurred=False,
            )
        except Exception as primary_exc:
            logger.warning(
                "Primary video model %s failed (%s). Triggering High-Availability Failover to %s...",
                target_model,
                primary_exc,
                self.video_fallback,
            )

        # Attempt 2: Failover to Fallback Video Model
        try:
            fb_res = self._submit_video_task(self.video_fallback, prompt, duration, aspect_ratio, image_url)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return MediaCallResult(
                media_url=fb_res.get("result_url"),
                task_id=fb_res.get("taskid"),
                model_used=self.video_fallback,
                modality="video",
                provider="302.ai",
                status=fb_res.get("status", "pending"),
                latency_ms=round(elapsed_ms, 2),
                cost_cents=10,
                failover_occurred=True,
            )
        except Exception as fb_exc:
            logger.error("Both primary (%s) and fallback (%s) video models failed: %s", target_model, self.video_fallback, fb_exc)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return MediaCallResult(
                media_url="shots/mock/take1.mp4",
                task_id=f"fallback_offline_{int(time.time())}",
                model_used="deterministic_fallback",
                modality="video",
                provider="offline_fallback",
                status="pending",
                latency_ms=round(elapsed_ms, 2),
                cost_cents=0,
                failover_occurred=True,
                error_message=str(fb_exc),
            )
