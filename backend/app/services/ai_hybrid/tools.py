from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.schemas.ai_api import AIAPIDirectRunIn
from app.services.ai_api.direct import run_direct_aiapi
from app.services.ai_hybrid import child_wait
from app.services.ai_hybrid import function_map_ctx
from app.services.ai_hybrid import report_observer
from app.services.ai_hybrid.schemas import HybridToolInput, HybridToolResult
from app.services.executor_platforms import executor_callback_base_url, normalize_executor_platform


class HybridTool(Protocol):
    name: str

    async def run(self, inp: HybridToolInput, settings: Any) -> HybridToolResult:
        ...


class AIAPITool:
    name = "ai_api"

    async def run(self, inp: HybridToolInput, settings: Any) -> HybridToolResult:
        title, preconditions, steps, expected = _structured_fields(
            inp.raw or {}, inp.input, "AI Hybrid API 子任务"
        )
        if not steps.strip():
            return HybridToolResult(
                tool=self.name,
                status="needs_human",
                reason="missing_steps",
                raw={
                    "submitted": False,
                    "message": (
                        "ai_api 缺少 steps（请求要素）：请说清方法(GET/POST…)、接口名或路径、"
                        "必要参数/请求体，以及要从响应里拿什么。"
                    ),
                },
            )
        if not expected.strip():
            return HybridToolResult(
                tool=self.name,
                status="needs_human",
                reason="missing_expected",
                raw={
                    "submitted": False,
                    "message": (
                        "ai_api 缺少可验证锚点 expected：子 ai_api 无锚点会拒发请求。"
                        "请在 expected 给这一片一个最小可验证锚点（探查/造数如「返回 2xx 且响应含 uid」；断言写真实通过标准）。"
                    ),
                },
            )
        payload = AIAPIDirectRunIn(
            title=title,
            preconditions=preconditions,
            steps_text=steps,
            expected_result=expected,
            function_map_context=inp.function_map_context,
            submission_name="AI Hybrid API 子任务",
            return_report_html=False,
        )
        try:
            result = await run_direct_aiapi(payload)
        except Exception as exc:
            return HybridToolResult(
                tool=self.name,
                status="failed",
                reason=f"ai_api_error: {exc}",
                raw={"error": str(exc)},
            )
        return HybridToolResult(
            tool=self.name,
            status="success" if result.status == "success" else "failed",
            reason=result.status_reason,
            report_url=result.report_url,
            raw=result.model_dump(mode="json"),
        )


class ReportReaderTool:
    name = "report_reader"

    async def run(self, inp: HybridToolInput, settings: Any) -> HybridToolResult:
        raw = inp.raw or {}
        report_url = str(raw.get("report_url") or "")
        executor = str(raw.get("executor") or "")
        mode = _reader_mode(raw.get("mode") or raw.get("action") or raw.get("depth"))
        if not report_url:
            return HybridToolResult(tool=self.name, status="needs_human", reason="missing_report_url")

        index = await report_observer.fetch_index(report_url, executor=executor)
        if not index.get("available"):
            return HybridToolResult(
                tool=self.name,
                status="failed",
                reason=str(index.get("error") or "report_unreadable"),
                report_url=report_url,
                raw={k: v for k, v in index.items() if k != "blocks"},
            )

        if mode == "image":
            img_no = _first(raw, ("image", "imgNo", "image_no", "index"))
            if img_no is None:
                observation = report_observer.outline(index)
                observation["hint"] = "image 模式需指定 imgNo（见 toc 里的 image 块）。"
            else:
                observation = await report_observer.read_image(index, img_no)
        elif mode == "search":
            observation = report_observer.search(index, str(_first(raw, ("query", "q", "keyword")) or ""))
        elif mode == "read":
            observation = report_observer.read(
                index,
                _first(raw, ("from", "from_block", "start")),
                _first(raw, ("to", "to_block", "end")),
            )
        else:
            observation = report_observer.outline(index)

        available = not observation.get("error")
        return HybridToolResult(
            tool=self.name,
            status="success" if available else "failed",
            reason="read_ok" if available else str(observation.get("error")),
            report_url=report_url,
            raw=observation,
        )


class FunctionMapTool:
    """供 Hybrid 主脑渐进式读取挂载 Map；只用于设备绑定，不替子执行器解释业务操作。"""

    name = "function_map"

    async def run(self, inp: HybridToolInput, settings: Any) -> HybridToolResult:
        raw = inp.raw or {}
        wanted = raw.get("asset_id") or raw.get("id") or raw.get("title")
        maps = inp.function_maps
        context = inp.function_map_context or ""
        if not str(wanted or "").strip():
            return HybridToolResult(
                tool=self.name,
                status="success",
                reason="catalog",
                raw={"catalog": function_map_ctx.build_catalog(maps, context)},
            )
        block = function_map_ctx.read_block(maps, context, str(wanted))
        if block is None:
            return HybridToolResult(
                tool=self.name,
                status="needs_human",
                reason="function_map_not_found",
                raw={
                    "requested": str(wanted),
                    "catalog": function_map_ctx.build_catalog(maps, context),
                },
            )
        return HybridToolResult(
            tool=self.name,
            status="success",
            reason="read_ok",
            raw={
                "asset_id": block.get("asset_id"),
                "title": block.get("title"),
                "targets": block.get("targets"),
                "content": block.get("content"),
            },
        )


@dataclass(frozen=True)
class PlatformDecision:
    platform: str
    source: str
    reason: str


class ExternalExecutorTool:
    name = ""
    executor_key = ""
    label = ""
    default_platform = ""
    resource_kind = "资源"
    entry_hint = ""
    # 仅 AI Phone 支持显式锁定一台设备；Web 等执行器保留原有按端资源池逻辑。
    supports_device_lock = False
    platform_labels: dict[str, str] = {}
    platform_aliases: dict[str, tuple[str, ...]] = {}

    @staticmethod
    def _missing_required(preconditions: str, steps: str, expected: str) -> str:
        if not preconditions.strip():
            return "preconditions"
        if not steps.strip():
            return "steps"
        if not expected.strip():
            return "expected"
        return ""

    def _missing_message(self, field: str) -> str:
        if field == "preconditions":
            hint = f"（{self.entry_hint}）" if self.entry_hint else ""
            return f"{self.label} 缺少 preconditions（前置条件）{hint}，不提交子任务；请补齐后重试。"
        if field == "steps":
            return f"{self.label} 缺少 steps（操作步骤），不提交子任务；请补齐后重试。"
        return f"{self.label} 缺少 expected（预期结果），不提交子任务；请补齐后重试。"

    async def run(self, inp: HybridToolInput, settings: Any) -> HybridToolResult:
        title, preconditions, steps, expected = _structured_fields(
            inp.raw or {}, inp.input, f"{self.label} 子任务"
        )
        missing = self._missing_required(preconditions, steps, expected)
        if missing:
            return HybridToolResult(
                tool=self.name,
                status="needs_human",
                reason=f"missing_{missing}",
                raw={"submitted": False, "message": self._missing_message(missing)},
            )

        base_url = self._base_url(settings)
        if not base_url:
            return HybridToolResult(tool=self.name, status="failed", reason="executor_base_url_missing")

        try:
            callback_base = executor_callback_base_url(settings, self.executor_key, self.label)
        except Exception as exc:
            return HybridToolResult(tool=self.name, status="failed", reason=f"callback_base_error: {exc}")

        platform_decision = self._platform_decision(inp)
        requested_alias = self._requested_device_alias(inp) if self.supports_device_lock else ""
        if requested_alias:
            resource_decision = await self._locked_resource_decision(
                requested_alias, platform_decision, settings
            )
        else:
            resource_decision = await self._resource_decision(platform_decision)
        if resource_decision.get("blocked"):
            return HybridToolResult(
                tool=self.name,
                status="needs_human",
                reason=str(resource_decision.get("reason") or "resource_unavailable"),
                raw={
                    "submitted": False,
                    "execution_strategy": platform_decision.__dict__,
                    "resource": resource_decision,
                },
            )

        token = uuid.uuid4().hex
        event = child_wait.register(token, base_url)
        callback_url = f"{callback_base}/api/v1/aihybrid/child-callback/{token}"
        platform = resource_decision.get("platform") or platform_decision.platform
        device_alias_pools = resource_decision.get("device_alias_pools")
        item = {
            "caseId": f"aihybrid-{token[:8]}",
            "caseName": title,
            "runContent": _render_run_content(title, preconditions, steps, expected),
            "platforms": [platform],
        }
        if device_alias_pools:
            item["deviceAliasPools"] = device_alias_pools
        payload: dict[str, Any] = {
            "submissionName": f"AI Hybrid {self.label}",
            "callbackUrl": callback_url,
            "cacheMode": "off",
            "retryMax": 0,
            "items": [item],
        }
        if inp.function_map_context:
            payload["functionMapContext"] = inp.function_map_context

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(f"{base_url.rstrip('/')}/api/submissions", json=payload)
                response.raise_for_status()
                submitted = response.json()
        except asyncio.CancelledError:
            # 提交是否已到达子服务不追踪；立即丢弃 Hybrid 本地等待句柄。
            child_wait.forget(token)
            raise
        except Exception as exc:
            child_wait.forget(token)
            return HybridToolResult(
                tool=self.name,
                status="failed",
                reason=f"child_submit_failed: {_executor_error_text(self.label, exc)}",
                raw={
                    "request": payload,
                    "error": _executor_error_payload(exc),
                    "submitted": False,
                    "execution_strategy": platform_decision.__dict__,
                    "resource": resource_decision,
                },
            )

        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=max(1, int(getattr(settings, "hybrid_max_wall_seconds", 1800) or 1800)),
            )
            result = child_wait.take_result(token) or {}
        except asyncio.TimeoutError:
            child_wait.forget(token)
            return HybridToolResult(
                tool=self.name,
                status="failed",
                reason="child_callback_timeout",
                raw={"request": payload, "submitted": submitted},
            )
        except asyncio.CancelledError:
            # 只结束 Hybrid 自身的等待；已提交的外部子任务不取消、不查询。
            child_wait.forget(token)
            raise

        state = str(result.get("state") or "").lower()
        status = "success" if state in {"success", "passed", "pass"} else "failed"
        reason = str(result.get("statusReason") or result.get("status_reason") or state or "child_finished")
        return HybridToolResult(
            tool=self.name,
            status=status,
            reason=reason,
            report_url=result.get("reportUrl") or result.get("report_url"),
            summary_report_url=result.get("summaryReportUrl") or result.get("summary_report_url"),
            raw={
                "request": payload,
                "submitted": submitted,
                "callback": result,
                "execution_strategy": platform_decision.__dict__,
                "resource": resource_decision,
            },
        )

    def _base_url(self, settings: Any) -> str:
        raise NotImplementedError

    def _platform_decision(self, inp: HybridToolInput) -> PlatformDecision:
        text = self._inference_text(inp)
        explicit = self._platform_from_explicit_raw(inp.raw, text)
        if explicit:
            return PlatformDecision(
                platform=explicit,
                source="explicit",
                reason=f"tool_input 明确指定执行平台：{self._platform_label(explicit)}",
            )
        inferred = self._platform_from_execution_context(text)
        if inferred:
            return PlatformDecision(
                platform=inferred,
                source="explicit",
                reason=f"用例文本明确指定执行环境：{self._platform_label(inferred)}",
            )
        return PlatformDecision(
            platform=self.default_platform,
            source="default",
            reason=f"用例未明确指定执行环境，按默认策略选择 {self._platform_label(self.default_platform)}",
        )

    def _inference_text(self, inp: HybridToolInput) -> str:
        raw = inp.raw or {}
        parts = [
            str(raw.get(key) or "")
            for key in ("title", "preconditions", "steps", "expected", "goal", "run_content")
        ]
        parts.append(str(inp.input or ""))
        return "\n".join(part for part in parts if part)

    def _platform_from_explicit_raw(self, raw: dict[str, Any], text: str) -> str:
        for key in ("executor_platform", "target_platform"):
            platform = self._normalize_platform(raw.get(key))
            if platform:
                return platform
        raw_platform = self._normalize_platform(raw.get("platform") or raw.get("browser"))
        if raw_platform and self._text_has_platform_constraint(text, raw_platform):
            return raw_platform
        return ""

    def _platform_from_execution_context(self, text: str) -> str:
        for platform in self._platform_order():
            if self._text_has_platform_constraint(text, platform):
                return platform
        return ""

    def _text_has_platform_constraint(self, text: str, platform: str) -> bool:
        compact = _compact_text(text)
        if not compact:
            return False
        terms = self.platform_aliases.get(platform, ())
        for term in terms:
            escaped = re.escape(term.lower())
            if re.search(rf"(在|用|使用|通过|基于|指定|选择|限定|拿).{{0,16}}{escaped}", compact):
                return True
            if re.search(
                rf"{escaped}.{{0,12}}(设备|手机|端|环境|平台|真机|浏览器).{{0,16}}(执行|运行|跑|打开|访问|测试|验证)",
                compact,
            ):
                return True
            if re.search(
                rf"(执行|运行|跑|打开|访问|测试|验证).{{0,16}}(在|用|使用|通过|基于).{{0,12}}{escaped}",
                compact,
            ):
                return True
            if re.search(rf"(打开|启动).{{0,12}}{escaped}", compact):
                return True
            if _term_is_assertion_target(compact, term):
                continue
            if re.search(rf"(验证|测试|检查|回归).{{0,10}}{escaped}.{{0,10}}(端|设备|浏览器|环境|平台|真机)", compact):
                return True
            if re.search(
                rf"{escaped}.{{0,10}}(端|设备|浏览器|环境|平台|真机).{{0,18}}(登录|下单|支付|打开|访问|流程|功能|页面|场景|兼容|回归)",
                compact,
            ):
                return True
        return False

    def _normalize_platform(self, value: Any) -> str:
        text = normalize_executor_platform(value, self.executor_key)
        if text in self.platform_aliases:
            return text
        lowered = str(value or "").strip().lower()
        for platform, aliases in self.platform_aliases.items():
            if lowered in aliases:
                return platform
        return ""

    def _platform_order(self) -> tuple[str, ...]:
        return tuple(self.platform_aliases.keys())

    def _platform_label(self, platform: str) -> str:
        return self.platform_labels.get(platform, platform or "默认平台")

    async def _resource_decision(self, platform_decision: PlatformDecision) -> dict[str, Any]:
        try:
            result = await self._list_devices()
        except Exception as exc:
            return {
                "blocked": True,
                "reason": "resource_unavailable",
                "message": f"当前无法获取{self.resource_kind}资源：{exc}",
                "platform": platform_decision.platform,
                "platform_label": self._platform_label(platform_decision.platform),
                "source": "unavailable",
                "error": str(exc),
                "devices": [],
            }

        devices = list(getattr(result, "devices", []) or [])
        summarized = [_summarize_device(device) for device in devices]
        if getattr(result, "source", "") == "unavailable" and not devices:
            error = str(getattr(result, "error", "") or "")
            suffix = f"：{error}" if error else ""
            return {
                "blocked": True,
                "reason": "resource_unavailable",
                "message": f"当前无法获取{self.resource_kind}资源{suffix}",
                "platform": platform_decision.platform,
                "platform_label": self._platform_label(platform_decision.platform),
                "source": getattr(result, "source", "unavailable"),
                "error": getattr(result, "error", None),
                "devices": summarized,
            }

        matched = [device for device in devices if _device_platform(device) == platform_decision.platform]
        if not matched:
            return {
                "blocked": True,
                "reason": "resource_not_available",
                "message": f"当前没有可用 {self._platform_label(platform_decision.platform)} {self.resource_kind}，未提交任务。",
                "platform": platform_decision.platform,
                "platform_label": self._platform_label(platform_decision.platform),
                "source": getattr(result, "source", ""),
                "error": getattr(result, "error", None),
                "devices": summarized,
            }

        idle = [device for device in matched if not _is_busy_device(device)]
        selected = idle or matched
        aliases = [_device_alias(device) for device in selected if _device_alias(device)]
        state = "idle" if idle else "queued"
        return {
            "blocked": False,
            "reason": "resource_selected",
            "message": (
                f"已选择 {self._platform_label(platform_decision.platform)} {self.resource_kind}。"
                if idle
                else f"{self._platform_label(platform_decision.platform)} {self.resource_kind}均占用，任务将排队。"
            ),
            "platform": platform_decision.platform,
            "platform_label": self._platform_label(platform_decision.platform),
            "source": getattr(result, "source", ""),
            "state": state,
            "device_alias_pools": {platform_decision.platform: aliases},
            "devices": [_summarize_device(device) for device in selected],
        }

    def _requested_device_alias(self, inp: HybridToolInput) -> str:
        raw = inp.raw or {}
        return str(raw.get("device_alias") or raw.get("deviceAlias") or "").strip()

    async def _locked_resource_decision(
        self,
        device_alias: str,
        platform_decision: PlatformDecision,
        settings: Any,
    ) -> dict[str, Any]:
        """硬锁目标设备：不进行模糊匹配、设备替换或隐式回退。"""
        interval = max(1, int(getattr(settings, "hybrid_device_wait_interval_seconds", 180) or 180))
        max_attempts = max(1, int(getattr(settings, "hybrid_device_wait_max_attempts", 3) or 3))
        wall = int(getattr(settings, "hybrid_max_wall_seconds", 1800) or 0)
        if wall > 0 and interval * (max_attempts - 1) > wall:
            max_attempts = max(1, wall // interval + 1)
        last_device: dict[str, Any] | None = None
        for attempt in range(max_attempts):
            try:
                result = await self._list_devices()
            except Exception as exc:
                return self._lock_blocked(
                    "resource_unavailable",
                    f"当前无法获取{self.resource_kind}资源：{exc}",
                    device_alias,
                    platform_decision,
                )
            devices = list(getattr(result, "devices", []) or [])
            if getattr(result, "source", "") == "unavailable" and not devices:
                return self._lock_blocked(
                    "resource_unavailable",
                    f"当前无法获取{self.resource_kind}资源，未提交任务。",
                    device_alias,
                    platform_decision,
                )
            matched = [device for device in devices if _device_identity_matches(device, device_alias)]
            if not matched:
                return self._lock_blocked(
                    "device_not_available",
                    f"指定{self.resource_kind}「{device_alias}」当前不在线或不存在，未提交任务（不改派其他设备）。",
                    device_alias,
                    platform_decision,
                    devices=[_summarize_device(device) for device in devices],
                )
            if len(matched) > 1:
                return self._lock_blocked(
                    "device_alias_ambiguous",
                    f"指定{self.resource_kind}「{device_alias}」命中多台，无法唯一锁定，未提交任务。",
                    device_alias,
                    platform_decision,
                    devices=[_summarize_device(device) for device in matched],
                )
            device = matched[0]
            last_device = device
            platform = _device_platform(device)
            if not platform:
                return self._lock_blocked(
                    "device_platform_unknown",
                    f"设备「{device_alias}」缺少平台信息，无法确认其平台，未提交任务。",
                    device_alias,
                    platform_decision,
                    devices=[_summarize_device(device)],
                )
            if platform_decision.source == "explicit" and platform != platform_decision.platform:
                return self._lock_blocked(
                    "device_platform_conflict",
                    (
                        f"用例语义指定平台 {self._platform_label(platform_decision.platform)}，"
                        f"但设备「{device_alias}」实际为 {self._platform_label(platform)}，证据冲突，未提交任务。"
                    ),
                    device_alias,
                    platform_decision,
                    devices=[_summarize_device(device)],
                )
            alias = _device_alias(device) or device_alias
            if not _is_busy_device(device):
                return {
                    "blocked": False,
                    "reason": "device_locked",
                    "message": f"已锁定指定{self.resource_kind}「{alias}」。",
                    "platform": platform,
                    "platform_label": self._platform_label(platform),
                    "source": getattr(result, "source", ""),
                    "state": "locked",
                    "device_alias": alias,
                    "device_alias_pools": {platform: [alias]},
                    "devices": [_summarize_device(device)],
                    "wait_attempts": attempt,
                }
            if attempt < max_attempts - 1:
                await asyncio.sleep(interval)
        return self._lock_blocked(
            "device_busy_timeout",
            f"指定{self.resource_kind}「{device_alias}」持续被占用，等待后仍不空闲，未提交任务（不改派其他设备）。",
            device_alias,
            platform_decision,
            devices=[_summarize_device(last_device)] if last_device else [],
        )

    def _lock_blocked(
        self,
        reason: str,
        message: str,
        device_alias: str,
        platform_decision: PlatformDecision,
        devices: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "blocked": True,
            "reason": reason,
            "message": message,
            "platform": platform_decision.platform,
            "platform_label": self._platform_label(platform_decision.platform),
            "source": "device_lock",
            "device_alias": device_alias,
            "devices": devices or [],
        }

    async def _list_devices(self) -> Any:
        raise NotImplementedError


class AIPhoneTool(ExternalExecutorTool):
    name = "ai_phone"
    executor_key = "ai_phone"
    label = "AI Phone"
    default_platform = "android"
    resource_kind = "手机"
    supports_device_lock = True
    entry_hint = "第一句须为固定冷启动话术「关闭 App「【目标App名】」（杀进程）后重新打开 App「【目标App名】」」"
    platform_labels = {"android": "Android", "ios": "iOS", "harmony": "Harmony"}
    platform_aliases = {
        "android": ("android", "安卓"),
        "ios": ("ios", "iphone", "苹果"),
        "harmony": ("harmony", "鸿蒙"),
    }

    def _base_url(self, settings: Any) -> str:
        return str(settings.aiphone_base_url or "")

    async def _list_devices(self) -> Any:
        from app.services import executions

        return await executions.list_aiphone_devices()


class AIWebTool(ExternalExecutorTool):
    name = "ai_web"
    executor_key = "ai_web"
    label = "AI Web"
    default_platform = "chrome"
    resource_kind = "浏览器槽"
    entry_hint = "第一句须为「打开【业务平台名】平台」"
    platform_labels = {"chrome": "Chrome", "safari": "Safari", "firefox": "Firefox"}
    platform_aliases = {
        "chrome": ("chrome", "chromium", "谷歌浏览器", "谷歌"),
        "safari": ("safari", "webkit"),
        "firefox": ("firefox",),
    }

    def _base_url(self, settings: Any) -> str:
        return str(settings.aiweb_base_url or "")

    async def _list_devices(self) -> Any:
        from app.services import executions

        return await executions.list_aiweb_devices()


def tool_registry() -> dict[str, HybridTool]:
    return {
        "ai_api": AIAPITool(),
        "ai_phone": AIPhoneTool(),
        "ai_web": AIWebTool(),
        "report_reader": ReportReaderTool(),
        "function_map": FunctionMapTool(),
    }


def _reader_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"outline", "read", "search", "image"}:
        return mode
    # 兼容旧口径：facts/full -> read；vision -> image；其余默认先看 outline。
    if mode in {"facts", "full", "text"}:
        return "read"
    if mode in {"vision", "screenshot", "img"}:
        return "image"
    return "outline"


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _structured_fields(
    raw: dict[str, Any], fallback_text: str, fallback_title: str
) -> tuple[str, str, str, str]:
    """从工具入参解析四段结构（标题/前置/步骤/预期）；缺字段时按自然语言文本兜底。"""
    title = str(raw.get("title") or raw.get("goal") or "").strip()
    preconditions = str(raw.get("preconditions") or raw.get("precondition") or "").strip()
    steps = str(raw.get("steps") or "").strip()
    expected = str(raw.get("expected") or raw.get("expected_result") or "").strip()
    if not (title or preconditions or steps or expected):
        steps = str(fallback_text or "").strip()
    if not title:
        title = _title_from_text(steps or fallback_text, fallback_title)
    return title, preconditions, steps, expected


def _render_run_content(title: str, preconditions: str, steps: str, expected: str) -> str:
    """渲染成子执行器认识的标准 runContent（与正式 case 一致）。"""
    return "\n\n".join(
        [
            f"测试标题：{title}",
            f"前置条件：{preconditions}",
            f"操作步骤：{steps}",
            f"预期结果：{expected}",
        ]
    )


def _title_from_text(text: str, fallback: str) -> str:
    for line in str(text or "").splitlines():
        clean = line.strip(" #：:\t")
        if clean:
            return clean[:80]
    return fallback


def _device_alias(device: dict[str, Any]) -> str:
    return str(device.get("alias") or device.get("serial") or "").strip()


def _device_identity_matches(device: dict[str, Any], wanted: str) -> bool:
    """仅精确匹配 alias 或 serial；不按相似名称猜测。"""
    target = str(wanted or "").strip().lower()
    if not target:
        return False
    return target in {
        str(device.get("alias") or "").strip().lower(),
        str(device.get("serial") or "").strip().lower(),
    }


def _device_platform(device: dict[str, Any]) -> str:
    return str(device.get("platform") or "").strip().lower()


def _is_busy_device(device: dict[str, Any]) -> bool:
    return str(device.get("occupancy") or "").strip().lower() == "busy"


def _summarize_device(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "alias": _device_alias(device),
        "serial": str(device.get("serial") or ""),
        "platform": _device_platform(device),
        "occupancy": str(device.get("occupancy") or "idle"),
        "model": str(device.get("model") or ""),
        "os_version": str(device.get("osVersion") or device.get("os_version") or ""),
    }


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _executor_error_text(label: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail: Any
        try:
            body = exc.response.json()
            detail = body.get("detail") if isinstance(body, dict) else body
        except Exception:
            detail = exc.response.text
        if isinstance(detail, dict):
            reason = detail.get("rejectReason") or detail.get("reason") or status
            message = detail.get("rejectDetail") or detail.get("message") or detail
            return f"{label} 拒绝提交：{reason}，{message}"
        if detail:
            return f"HTTP {status}，{detail}"
        return f"HTTP {status}"
    return str(exc)


def _executor_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, httpx.HTTPStatusError):
        payload: dict[str, Any] = {
            "type": "HTTPStatusError",
            "status_code": exc.response.status_code,
            "text": exc.response.text,
        }
        try:
            payload["json"] = exc.response.json()
        except Exception:
            pass
        return payload
    return {"type": type(exc).__name__, "message": str(exc)}


def _term_is_assertion_target(compact: str, term: str) -> bool:
    lowered = term.lower()
    index = compact.find(lowered)
    if index < 0:
        return False
    before = compact[max(0, index - 16) : index]
    after = compact[index : index + 16]
    window = before + after
    if any(marker in window for marker in ("是否", "是不是", "是否为", "是否是", "是否属于", "是否包含")):
        return True
    escaped = re.escape(lowered)
    return bool(re.search(rf"(判断|校验|确认|识别|检查|验证).{{0,16}}(为|是|属于|包含).{{0,6}}{escaped}", compact))
