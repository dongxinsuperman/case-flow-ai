"""一级目录（RequirementGroup）承载的 AI Phone functionMap 文件。

多个文件合并成一段文本（按协议交给 AI Phone 的 functionMapContext 单字段），
合并后总字符受 settings.function_map_context_max_chars（默认 8000）限制，超限拒绝上传。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.requirements import RequirementGroup
from app.schemas.function_map import FunctionMapFileOut, FunctionMapStateOut, FunctionMapUploadIn

SEPARATOR = "\n\n"


def merge_files(files: list[dict]) -> str:
    return SEPARATOR.join(str(item.get("content") or "") for item in files)


def _state(group_id: int, files: list[dict], *, overwritten: bool = False) -> FunctionMapStateOut:
    settings = get_settings()
    return FunctionMapStateOut(
        group_id=group_id,
        files=[
            FunctionMapFileOut(
                filename=str(item.get("filename") or ""),
                content=str(item.get("content") or ""),
                char_count=len(str(item.get("content") or "")),
            )
            for item in files
        ],
        total_chars=len(merge_files(files)),
        max_chars=settings.function_map_context_max_chars,
        overwritten=overwritten,
    )


async def _get_group(session: AsyncSession, group_id: int) -> RequirementGroup:
    group = await session.get(RequirementGroup, group_id)
    if group is None:
        raise ValueError("一级目录不存在")
    return group


async def get_function_map(session: AsyncSession, group_id: int) -> FunctionMapStateOut:
    group = await _get_group(session, group_id)
    return _state(group_id, list(group.function_map_files or []))


async def upload_function_map_file(
    session: AsyncSession,
    group_id: int,
    payload: FunctionMapUploadIn,
) -> FunctionMapStateOut:
    filename = (payload.filename or "").strip()
    if not filename:
        raise ValueError("文件名不能为空")
    content = payload.content if isinstance(payload.content, str) else ""

    group = await _get_group(session, group_id)
    files = list(group.function_map_files or [])

    overwritten = False
    next_files: list[dict] = []
    replaced = False
    for item in files:
        if item.get("filename") == filename:
            next_files.append({"filename": filename, "content": content})
            replaced = True
            overwritten = True
        else:
            next_files.append(item)
    if not replaced:
        next_files.append({"filename": filename, "content": content})

    max_chars = get_settings().function_map_context_max_chars
    merged_len = len(merge_files(next_files))
    if merged_len > max_chars:
        raise ValueError(
            f"功能地图合并后共 {merged_len} 字符，超出 {max_chars} 上限，请精简后再上传"
        )

    group.function_map_files = next_files
    await session.commit()
    return _state(group_id, next_files, overwritten=overwritten)


async def delete_function_map_file(
    session: AsyncSession,
    group_id: int,
    filename: str,
) -> FunctionMapStateOut:
    target = (filename or "").strip()
    group = await _get_group(session, group_id)
    files = [item for item in (group.function_map_files or []) if item.get("filename") != target]
    group.function_map_files = files
    await session.commit()
    return _state(group_id, files)
