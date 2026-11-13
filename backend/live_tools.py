"""Bounded server-side function bridge for the teammate's direct Gemini Live transport."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from conversation import MAX_TOOL_RESULT, TOOL_LABELS, ConversationTurn
from conversation_tools import execute, tool_schemas
from tryon_selection import plan_tryon
from wardrobe_api import _store

TASK_INTENTS = ('tryon_live', 'teach_skill', 'guide_skill', 'guide_coffee', 'guide_cupware', 'guide_folding', 'guide_cooking', 'guide_cleaning', 'guide_plants')


class CameraArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    intent: Literal['tryon_live','teach_skill','guide_skill','guide_coffee','guide_cupware','guide_folding','guide_cooking','guide_cleaning','guide_plants'] = 'guide_skill'
    request: str = Field(min_length=1, max_length=500)
    command: Literal['route_request','finish_teaching','cancel_task','continue_task','switch_task'] = 'route_request'
    item_ids: list[str] = Field(default_factory=list, max_length=3)
    skill_id: str | None = Field(default=None, max_length=80)


def declarations() -> list[dict]:
    # Live already sees the shared camera; avoid a redundant second vision-model call.
    result = [{'name':entry['function']['name'], 'description':entry['function']['description'],
               'parametersJsonSchema':entry['function']['parameters'], 'behavior':'BLOCKING'}
              for entry in tool_schemas() if entry['function']['name'] != 'inspect_image']
    for declaration in result:
        if declaration['name'] == 'showcase_outfits':
            declaration['description'] = 'Display recent outfit suggestions as real garment image cards, with your introduction before them. Call after suggest_outfits. After showing cards, give a brief spoken summary; do not repeat the entire introduction or garment list, and do not add standalone try-on actions. The cards already offer try-on controls.'
    result.append({'name':'camera_task', 'behavior':'BLOCKING',
        'description':'Execute an explicitly requested shared-camera task: guidance, recording a lesson, fitting selected wardrobe clothes, or finishing/cancelling the current task. Call only when the user requests a camera operation; ordinary chat, wardrobe reads and outfit suggestions use other tools. finish_teaching means the ENTIRE lesson is finished, not one step. Preserve ongoing teaching; do not switch until the user finishes or cancels. Use actual wardrobe IDs for fitting. Camera execution happens in the client; wait for context.execution before claiming success.',
        'parametersJsonSchema':CameraArgs.model_json_schema()})
    return result


@dataclass
class ToolSession:
    context: dict = field(default_factory=dict)
    expires: float = field(default_factory=lambda:time.monotonic()+300)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    results: dict = field(default_factory=dict)


class LiveToolStore:
    def __init__(self, maximum=128):
        self.maximum = maximum
        self.sessions: dict[str, ToolSession] = {}

    def clean(self):
        now=time.monotonic()
        for key,value in list(self.sessions.items()):
            if value.expires < now and not value.lock.locked():
                del self.sessions[key]

    def create(self, city):
        self.clean()
        if len(self.sessions) >= self.maximum:
            raise HTTPException(503, 'Đang có nhiều phiên trò chuyện. Bạn thử lại sau nhé.')
        key=str(uuid4())
        self.sessions[key]=ToolSession(context={'configured_city':city})
        return key

    def get(self, key):
        self.clean()
        session=self.sessions.get(str(key))
        if not session or session.expires < time.monotonic():
            raise HTTPException(410, 'Phiên công cụ đã hết hạn. Hãy mở lại cuộc trò chuyện.')
        return session


def sessions(request):
    if not hasattr(request.app.state,'live_tool_sessions'):
        request.app.state.live_tool_sessions=LiveToolStore()
    return request.app.state.live_tool_sessions


class LiveToolRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_id: UUID
    call_id: Annotated[str,StringConstraints(min_length=1,max_length=160)]
    name: Annotated[str,StringConstraints(min_length=1,max_length=80)]
    arguments: dict[str,Any] = Field(default_factory=dict)
    current_task: Literal['idle','chat','guiding','teaching','compiling','tryon'] = 'idle'
    task_title: str = Field(default='',max_length=200)
    selected_item_ids: list[Annotated[str,StringConstraints(min_length=1,max_length=100)]] = Field(default_factory=list,max_length=3)
    history: list[ConversationTurn] = Field(default_factory=list,max_length=6)


async def camera_decision(args: CameraArgs, payload: LiveToolRequest, request):
    from gemini_live import LABELS, shadow_context
    context,actions={},[]
    if args.command in ('route_request','switch_task'):
        if args.intent.startswith('guide_'):
            context,actions=await shadow_context(args.intent,args.request,request)
            if args.skill_id:
                skill=request.app.state.skill_store.get_dict(args.skill_id)
                keys=('id','instruction','tip','warning','on_enter_speech')
                context['lesson']={'name':skill['display_name'], 'learned_from':skill.get('learned_from'),
                    'steps':[{key:step[key] for key in keys if key in step} for step in skill.get('steps',[])[:20]]}
        elif args.intent=='tryon_live' and payload.current_task not in ('teaching','compiling'):
            context['tryon_plan']=await plan_tryon(request.app.state.provider,_store(request),args.request,
                selected_ids=payload.selected_item_ids,requested_ids=args.item_ids,history=[turn.model_dump() for turn in payload.history])
    # No fake classifier score: this is a validated model function request, not a Decisions prediction.
    return {'intent':args.intent,'label':LABELS[args.intent],'camera_command':args.command,
            'context':context,'actions':actions,'source':'tool_call'}


async def run(payload: LiveToolRequest, request: Request) -> dict:
    session=sessions(request).get(payload.session_id)
    arguments=json.dumps(payload.arguments,ensure_ascii=False,sort_keys=True)
    if len(arguments)>16000:
        raise HTTPException(422,'Đối số công cụ quá dài.')
    fingerprint=(payload.name,arguments)
    async with session.lock:
        if session.expires < time.monotonic():
            raise HTTPException(410,'Phiên công cụ đã hết hạn. Hãy mở lại cuộc trò chuyện.')
        if payload.call_id in session.results:
            previous,result=session.results[payload.call_id]
            if previous!=fingerprint:
                raise HTTPException(409,'Mã gọi công cụ đã được dùng cho yêu cầu khác.')
            return result
        if len(session.results)>=60:
            raise HTTPException(429,'Phiên đã đạt giới hạn gọi công cụ. Hãy bắt đầu phiên mới.')
        context=copy.deepcopy(session.context)
        events=[]
        try:
            async with asyncio.timeout(90):
                if payload.name=='camera_task':
                    args=CameraArgs.model_validate(payload.arguments)
                    decision=await camera_decision(args,payload,request)
                    result={'camera_decision':decision}
                else:
                    result,action=await execute(payload.name,arguments,request=request,context=context,image=None)
                    if not result.get('error') and result.get('outfits') and payload.name in ('showcase_outfits','open_screen'):
                        if payload.name=='showcase_outfits':
                            events.append({'type':'text','text':result['introduction']})
                        events.append({'type':'outfits','outfits':result['outfits'][:3]})
                    if action:
                        events.append({'type':'action',**action})
        except ValidationError:
            result={'error':'Invalid tool arguments. Check the function schema.'}
        except Exception:
            result={'error':'Tool failed or timed out. Do not invent a result.'}
        if len(json.dumps(result,ensure_ascii=False))>MAX_TOOL_RESULT:
            result,events={'error':'Tool result too large. Ask a narrower question.'},[]
        if await request.is_disconnected():
            response={'result':{'error':'Tool request was cancelled.'},'events':[]}
        else:
            session.context=context
            response={'result':result,'events':events}
        session.results[payload.call_id]=(fingerprint,response)
        return response
