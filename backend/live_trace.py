"""Optional text-only diagnostics for browser-to-Gemini Live sessions."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from live_tools import sessions

router = APIRouter()
logger = logging.getLogger('uvicorn.error')


def enabled() -> bool:
    return os.getenv('SHADOW_LIVE_TRACE', '').lower() in {'1', 'true', 'on'}


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seq: int = Field(ge=1, le=100000)
    at_ms: int = Field(ge=0, le=4102444800000)
    turn: int = Field(0, ge=0, le=100000)
    kind: Literal['transcript', 'state', 'task_queued', 'task_sent', 'tool_start', 'tool_result', 'error', 'stop']
    role: Literal['user', 'assistant'] | None = None
    text: str = Field('', max_length=4000)
    name: str = Field('', max_length=80)
    status: str = Field('', max_length=40)
    guide_session_id: UUID | None = None
    step: int | None = Field(None, ge=0, le=1000)


class TraceBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: UUID
    events: list[TraceEvent] = Field(min_length=1, max_length=8)


def redact(text: str) -> str:
    # Log text only, with common credential formats removed even if spoken/pasted.
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|uk-[A-Za-z0-9_-]{12,}|AQ\.[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]+)\b', '[redacted]', text)
    return re.sub(r'(?i)(bearer\s+|(?:access_token|api[_-]?key|token)\s*[=:]\s*)[^\s&"<>]+', r'\1[redacted]', text)


@router.post('/api/live/trace')
async def receive_trace(payload: TraceBatch, request: Request) -> dict:
    if not enabled():
        return {'accepted': 0, 'enabled': False}
    session = sessions(request).get(payload.session_id)
    # A short-lived, issued session is required; no public log-reading endpoint.
    accepted = 0
    for event in payload.events:
        if event.seq in session.trace_seen:
            continue
        if len(session.trace_seen) >= 1000:
            raise HTTPException(429, 'Đã đạt giới hạn log của phiên.')
        record = event.model_dump(mode='json', exclude_none=True)
        record['text'] = redact(record['text'])
        record['name'] = redact(record['name'])
        record['status'] = redact(record['status'])
        line = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        size = len(line.encode('utf-8'))
        if session.trace_bytes + size > 512000:
            raise HTTPException(429, 'Đã đạt giới hạn log của phiên.')
        session.trace_seen.add(event.seq)
        session.trace_bytes += size
        logger.info('SKI live_trace source=browser session=%s event=%s', payload.session_id, line)
        accepted += 1
    return {'accepted': accepted, 'enabled': True}
