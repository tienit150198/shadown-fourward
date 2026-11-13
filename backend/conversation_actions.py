"""One-shot acknowledgements for browser-executed lesson tools in streamed chat."""
import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from lesson_actions import LessonSnapshot

router=APIRouter()


class SavedLesson(BaseModel):
    id: str = Field(max_length=64)
    name: str | None = Field(default=None,max_length=160)
    steps: int = Field(ge=1,le=30)


class LessonExecution(BaseModel):
    model_config=ConfigDict(extra='ignore')
    mode: str = Field(default='chat',max_length=40)
    status: str = Field(max_length=40)
    message: str = Field(default='',max_length=1000)
    error: str | None = Field(default=None,max_length=1000)
    lesson_state: LessonSnapshot | None = None
    saved_skill: SavedLesson | None = None


class ActionResult(BaseModel):
    token: UUID
    execution: LessonExecution


def pending(request):
    if not hasattr(request.app.state,'lesson_actions'):
        request.app.state.lesson_actions={}
    return request.app.state.lesson_actions


def create_action(request):
    actions=pending(request)
    if len(actions)>=128:
        raise HTTPException(503,'Máy chủ đang xử lý nhiều tác vụ. Hãy thử lại sau.')
    token=str(uuid4())
    actions[token]=asyncio.get_running_loop().create_future()
    return token


async def wait_action(request,token):
    future=pending(request)[token]
    try:
        return await asyncio.wait_for(future,90)
    except asyncio.TimeoutError:
        return {'status':'unknown','error':'Client execution was not confirmed. Do not claim edited or saved. Ask the user to check current lesson state.'}
    finally:
        pending(request).pop(token,None)


@router.post('/api/conversation/action-result')
async def action_result(payload:ActionResult,request:Request):
    future=pending(request).get(str(payload.token))
    if future is None or future.done():
        raise HTTPException(410,'Tác vụ đã hết hạn hoặc đã được xác nhận.')
    future.set_result(payload.execution.model_dump(exclude_none=True))
    return {'ok':True}
