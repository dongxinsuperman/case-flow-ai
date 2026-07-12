from __future__ import annotations

from pydantic import BaseModel


class FunctionMapFileOut(BaseModel):
    filename: str
    content: str
    char_count: int


class FunctionMapStateOut(BaseModel):
    group_id: int
    files: list[FunctionMapFileOut]
    total_chars: int
    max_chars: int
    overwritten: bool = False


class FunctionMapUploadIn(BaseModel):
    filename: str
    content: str
