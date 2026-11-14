"""Bounded server-side function bridge for the teammate's direct Gemini Live transport."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4
from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from lesson_actions import LessonSnapshot
from conversation import MAX_TOOL_RESULT, TOOL_LABELS, ConversationTurn
from conversation_tools import execute, tool_schemas
from tryon_selection import plan_tryon
from wardrobe_api import _store
from styling_context import StylingLocation, contextual_request
from wardrobe_preferences import CatalogGender, wardrobe_gender

TASK_INTENTS = ('tryon_live', 'teach_skill', 'guide_skill', 'guide_coffee', 'guide_cupware', 'guide_folding', 'guide_cooking', 'guide_cleaning', 'guide_plants')


class CameraArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    intent: Literal['tryon_live','teach_skill','guide_skill','guide_coffee','guide_cupware','guide_folding','guide_cooking','guide_cleaning','guide_plants'] = 'guide_skill'
    request: str = Field(min_length=1, max_length=500)
    command: Literal['route_request','finish_teaching','cancel_task','continue_task','switch_task'] = 'route_request'
    item_ids: list[str] = Field(default_factory=list, max_length=3, description='Exact saved wardrobe IDs for the requested fitting. Use IDs from the outfit you just showed when the user accepts it. Leave empty only when you need wardrobe resolution. Xmax accepts at most three supported garments.')
    skill_id: str | None = Field(default=None, max_length=80)
    gender: CatalogGender | None = Field(default=None, description='Explicitly requested clothing audience (men/women include unisex). Omit to keep the current preference or profile. Never infer from camera; all removes filtering only when requested.')


class LiveViewArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    view: Literal['original','tryon','dismiss_outfits','show_outfits']


def declarations() -> list[dict]:
    # Live already sees the shared camera; avoid a redundant second vision-model call.
    result = [{'name':entry['function']['name'], 'description':entry['function']['description'],
               'parametersJsonSchema':entry['function']['parameters'], 'behavior':'BLOCKING'}
              for entry in tool_schemas() if entry['function']['name'] not in ('inspect_image','open_screen','start_tryon')]
    for declaration in result:
        if declaration['name'] == 'showcase_outfits':
            declaration['description'] = 'Show exactly ONE outfit at a time that YOU choose from actual list_wardrobe results in an overlay ON THE LIVE VIDEO, not chat cards. Pass exact item IDs in chosen layer order, titles and explanations. Consider the conversation, retained weather, preferences and taught rules. This replaces the current suggestion. Do not showcase several alternatives or call repeatedly in the same turn. On a request for another outfit choose a new one and replace the old suggestion. Speak a short natural invitation after displaying. This only presents choices; if the user already requested fitting, also call camera_task intent=tryon_live with the chosen IDs. A spoken confirmation such as "try that one" is enough; never require clicking a button.'
        elif declaration['name'] == 'open_screen':
            declaration['description'] = 'Offer navigation ONLY when the user explicitly asks to leave the live camera for a saved-resource or separate image-generation screen. Does not execute anything. For live fitting, guidance, teaching, cancellation or clothing changes use camera_task instead. Never offer a navigation button instead of executing an explicit voice request.'
            declaration['parametersJsonSchema']['properties']['screen']['enum'] = ['wardrobe','home','stylist','image-studio','tryon-photo']
        elif declaration['name'] == 'list_wardrobe':
            declaration['description'] += ' Reuse items already read in this live session; fetch more pages or refresh only when necessary. Choose outfits yourself and show them with showcase_outfits. Camera frames do not represent saved wardrobe data.'
        elif declaration['name'] == 'get_weather':
            declaration['description'] += ' Reuse the existing forecast for the same city/date; do not fetch for every spoken turn.'
        elif declaration['name'] == 'get_skill':
            declaration['description'] += ' To start spoken guidance in this camera, use camera_task with skill_id; no navigation or click required.'
    result.append({'name':'set_live_view','behavior':'BLOCKING',
        'description':'Control the live video UI in response to speech: show original camera, show the active try-on result, dismiss the outfit overlay, or reopen the latest suggestions. Switching original/tryon preserves the running fitting session; to stop fitting use camera_task cancel_task. Wait for actual client execution before claiming success.',
        'parametersJsonSchema':LiveViewArgs.model_json_schema()})
    camera_schema=CameraArgs.model_json_schema()
    camera_schema['properties']['intent']['enum']=['tryon_live']
    camera_schema['properties']['intent']['default']='tryon_live'
    camera_schema['properties']['command']['enum']=['route_request','cancel_task','continue_task','switch_task']
    camera_schema['properties'].pop('skill_id',None)
    result.append({'name':'camera_task','behavior':'BLOCKING',
        'description':'Execute explicitly requested live fitting, clothing changes or cancellation in this shared camera. Ordinary chat and suggestions use their own tools. Teaching, draft review and guided practice use lesson_control instead. Use actual wardrobe IDs and wait for context.execution before claiming success.',
        'parametersJsonSchema':camera_schema})
    return result


@dataclass
class ToolSession:
    context: dict = field(default_factory=dict)
    expires: float = field(default_factory=lambda:time.monotonic()+300)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    results: dict = field(default_factory=dict)
    trace_seen: set[int] = field(default_factory=set)
    trace_bytes: int = 0


class LiveToolStore:
    def __init__(self, maximum=128, directory: Path | None = None):
        self.maximum = maximum
        self.sessions: dict[str, ToolSession] = {}
        self.directory = directory
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self, key):
        if self.directory is None:
            return
        key = str(UUID(str(key)))
        session = self.sessions[key]
        value = {'expires': time.time() + max(0, session.expires - time.monotonic()),
                 'context': session.context, 'results': session.results}
        target = self.directory / (key + '.json')
        temporary = target.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temporary, target)

    def clean(self):
        now=time.monotonic()
        for key,value in list(self.sessions.items()):
            if value.expires < now and not value.lock.locked():
                del self.sessions[key]
                if self.directory is not None:
                    (self.directory / (key + '.json')).unlink(missing_ok=True)
        if self.directory is not None:
            for path in self.directory.glob('*.json'):
                try:
                    if json.loads(path.read_text())['expires'] <= time.time():
                        path.unlink(missing_ok=True)
                except (ValueError, KeyError):
                    path.unlink(missing_ok=True)

    def create(self, city):
        self.clean()
        stored = {path.stem for path in self.directory.glob('*.json')} if self.directory else set()
        if len(stored | self.sessions.keys()) >= self.maximum:
            raise HTTPException(503, 'Đang có nhiều phiên trò chuyện. Bạn thử lại sau nhé.')
        key=str(uuid4())
        self.sessions[key]=ToolSession(context={'configured_city':city})
        self.save(key)
        return key

    def get(self, key):
        self.clean()
        key = str(UUID(str(key)))
        session=self.sessions.get(key)
        if session is None and self.directory is not None:
            path = self.directory / (key + '.json')
            if path.exists():
                value = json.loads(path.read_text())
                remaining = value['expires'] - time.time()
                if remaining > 0:
                    session = ToolSession(context=value['context'], expires=time.monotonic() + min(300, remaining),
                        results={call: (tuple(row[0]), row[1]) for call, row in value['results'].items()})
                    self.sessions[key] = session
        if not session or session.expires < time.monotonic():
            raise HTTPException(410, 'Phiên công cụ đã hết hạn. Hãy mở lại cuộc trò chuyện.')
        return session


def sessions(request):
    if not hasattr(request.app.state,'live_tool_sessions'):
        request.app.state.live_tool_sessions=LiveToolStore(directory=Path(_store(request).root) / '.live-tools')
    return request.app.state.live_tool_sessions


class LiveToolRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_id: UUID
    call_id: Annotated[str,StringConstraints(min_length=1,max_length=160)]
    name: Annotated[str,StringConstraints(min_length=1,max_length=80)]
    arguments: dict[str,Any] = Field(default_factory=dict)
    location: StylingLocation | None = None
    request_text: str = Field(default='', max_length=12000)
    lesson_state: LessonSnapshot | None = None
    current_task: Literal['idle','chat','guiding','teaching','compiling','reviewing','tryon'] = 'idle'
    task_title: str = Field(default='',max_length=200)
    selected_item_ids: list[Annotated[str,StringConstraints(min_length=1,max_length=100)]] = Field(default_factory=list,max_length=3)
    suggested_item_ids: list[Annotated[str,StringConstraints(min_length=1,max_length=100)]] = Field(default_factory=list,max_length=20)
    history: list[ConversationTurn] = Field(default_factory=list,max_length=6)


async def camera_decision(args: CameraArgs, payload: LiveToolRequest, request, tool_context=None):
    from gemini_live import LABELS, shadow_context
    context,actions={},[]
    if args.command in ('route_request','switch_task'):
        if args.intent.startswith('guide_'):
            context,actions=await shadow_context(args.intent,args.request,request)
            if args.skill_id:
                skill=request.app.state.skill_store.get_dict(args.skill_id)
                keys=('id','instruction','tip','warning','on_enter_speech')
                context['lesson']={**skill,'name':skill['display_name'], 'learned_from':skill.get('learned_from'),
                    'steps':[{key:step[key] for key in keys if key in step} for step in skill.get('steps',[])[:20]]}
        elif args.intent=='tryon_live' and payload.current_task not in ('teaching','compiling'):
            context['tryon_plan']=await plan_tryon(request.app.state.provider,_store(request),args.request,
                selected_ids=payload.selected_item_ids or payload.suggested_item_ids,requested_ids=args.item_ids,history=[turn.model_dump() for turn in payload.history],
                gender=wardrobe_gender(tool_context if tool_context is not None else {}, args.gender))
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
        context['lesson_state'] = payload.lesson_state.model_dump() if payload.lesson_state else None
        if 'location' in payload.model_fields_set:
            location = payload.location.model_dump() if payload.location else None
            if location != context.get('location'):
                context.pop('weather', None)
            if location:
                context['location'] = location
            else:
                context.pop('location', None)
        if payload.name == 'get_weather':
            context['styling_request'] = contextual_request(payload.request_text,
                [turn.model_dump() for turn in payload.history])
        events=[]
        try:
            async with asyncio.timeout(90):
                if payload.name=='set_live_view':
                    result={'live_control':LiveViewArgs.model_validate(payload.arguments).model_dump()}
                elif payload.name=='open_screen':
                    result={'error':'Navigation is deprecated. Use wardrobe/skill tools to read data, showcase_outfits to display choices, or camera_task to execute in this camera.'}
                elif payload.name=='camera_task':
                    args=CameraArgs.model_validate(payload.arguments)
                    decision=await camera_decision(args,payload,request,context)
                    result={'camera_decision':decision}
                else:
                    result,action=await execute(payload.name,arguments,request=request,context=context,image=None)
                    if not result.get('error') and result.get('outfits') and payload.name in ('showcase_outfits','open_screen'):
                        if payload.name=='showcase_outfits':
                            events.append({'type':'live_outfit_intro','text':result['introduction']})
                        events.append({'type':'outfits','outfits':result['outfits'][:3]})
                    if action and payload.name!='lesson_control':
                        events.append({'type':'action',**action})
        except ValidationError:
            result={'error':'Invalid tool arguments. Check the function schema.'}
        except Exception:
            result={'error':'Tool failed or timed out. Do not invent a result.'}
        if payload.name == 'list_wardrobe' and result.get('display') is True and not result.get('error'):
            events.append({'type': 'wardrobe', 'items': result['items'], 'total': result['total'], 'gender_filter': result['gender_filter']})
        if payload.name=='showcase_outfits' and not result.get('error'):
            result['message']='Outfits are displayed over the live video. Give a short spoken invitation. If fitting was requested, call camera_task with the chosen IDs now; a voice confirmation is sufficient, no button required.'
        if len(json.dumps(result,ensure_ascii=False))>MAX_TOOL_RESULT:
            result,events={'error':'Tool result too large. Ask a narrower question.'},[]
        if await request.is_disconnected():
            response={'result':{'error':'Tool request was cancelled.'},'events':[]}
        else:
            session.context=context
            response={'result':result,'events':events}
        session.results[payload.call_id]=(fingerprint,response)
        sessions(request).save(payload.session_id)
        return response
