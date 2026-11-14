"""Bounded lesson controls shared by Live and regular conversation tools."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LessonControlArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    command: Literal['start_teaching','finish_teaching','finish_and_save','review','rename','update_step','insert_step','remove_step','reorder_steps','save','start_guidance','next','previous','repeat','stop']
    skill_id: str | None = Field(default=None, max_length=64)
    revision: int | None = Field(default=None, ge=0, description='Current lesson revision from execution/context; required for edits and save so stale requests cannot overwrite newer changes.')
    title: str | None = Field(default=None, min_length=1, max_length=160)
    step_number: int | None = Field(default=None, ge=1, le=31)
    instruction: str | None = Field(default=None, min_length=1, max_length=800)
    detector_prompt: str | None = Field(default=None, min_length=1, max_length=1500, description='Required when changing an instruction: describe the NEW visible completion condition; detector returns JSON state="done" or "not_done". Do not reuse an obsolete completion test.')
    tip: str | None = Field(default=None, max_length=400)
    warning: str | None = Field(default=None, max_length=400)
    order: list[int] | None = Field(default=None, min_length=1, max_length=30, description='Permutation of ALL current one-based step numbers.')


class LessonStepSnapshot(BaseModel):
    model_config = ConfigDict(extra='ignore')
    number: int = Field(ge=1,le=30)
    instruction: str = Field(max_length=800)
    tip: str = Field(default='',max_length=400)
    warning: str = Field(default='',max_length=400)


class LessonSnapshot(BaseModel):
    model_config = ConfigDict(extra='ignore')
    status: str = Field(max_length=40)
    title: str = Field(max_length=160)
    revision: int = Field(ge=0)
    step: int = Field(default=0,ge=0,le=30)
    skill_id: str | None = Field(default=None,max_length=64)
    steps: list[LessonStepSnapshot] = Field(default_factory=list,max_length=30)


DESCRIPTION = ('Distinguish user learning from user teaching AI: asking to learn/practice must use start_guidance, never start_teaching. First read list_skills to select a matching saved skill_id. A correction such as wanting to learn must change your action immediately. '
    'Vietnamese roles: "tôi dạy bạn", "để tôi chỉ bạn", "học cách tôi làm" mean the USER teaches AI (start_teaching); "dạy tôi", "chỉ tôi", "hướng dẫn tôi" mean AI coaches the USER (start_guidance). Do not route by the word "dạy" alone. '
    'Control teaching, draft review and learned-skill practice IN THE CURRENT CAMERA. '
    'start_teaching records spoken demonstration; finish_teaching compiles a draft for review, it DOES NOT save. '
    'When the user says "dạy xong rồi, lưu lại đi", "lưu bài này" or otherwise explicitly asks to finish AND save, call finish_and_save. This already IS confirmation: compile and save in one action, without asking again. While compiling, finish_and_save/save queues saving when compilation succeeds. "Xong bước một" only describes a step and must not end the lesson. A negation such as "chưa lưu", "chỉ cho xem lại" must use finish_teaching/review instead. '
    'Use review to show steps; skill_id loads a saved lesson as a NEW editable copy. '
    'Use rename/update_step/insert_step/remove_step/reorder_steps for explicitly requested adjustments; provide current revision. '
    'New steps and instruction changes require a matching detector_prompt for the new visible completion condition. '
    'Never invent observed steps or silently delete them. save persists only after explicit user confirmation; provide revision. '
    'start_guidance uses a saved skill_id; next/previous/repeat control practice. stop cancels without saving. '
    'No manual editor or navigation required. Wait for actual client execution; review/compiling is not saved, and loading is not ready.')

VOICE_LESSON_INSTRUCTIONS = (
    "Phân biệt vai trò theo chủ ngữ và ngữ cảnh, không chỉ theo từ 'dạy': 'tôi dạy bạn', 'để tôi chỉ bạn', 'học cách tôi làm' là người dùng dạy AI, gọi lesson_control start_teaching ngay và lắng nghe làm mẫu; 'dạy tôi', 'bạn chỉ tôi', 'hướng dẫn tôi' là người dùng cần được hướng dẫn, tìm bài đã lưu rồi start_guidance. "
    "Khi đang ghi bài, các câu mô tả thao tác là nội dung bài dạy; 'xong bước một' không kết thúc bài. 'Dạy xong rồi, lưu lại đi', 'lưu bài này' là xác nhận lưu đầy đủ: gọi finish_and_save một lần để kết thúc, biên soạn và lưu, không bắt xác nhận lần nữa. Nếu đang biên soạn thì ghi nhận yêu cầu lưu, chờ kết quả thực; không nói đã lưu trước status=saved. "
    "Chỉ 'dạy xong', 'cho xem lại', 'chưa lưu' thì finish_teaching tạo bản nháp để xem, không tự lưu. Khi đang xem bản nháp, 'lưu đi', 'ok lưu' dùng save với revision hiện tại. Không bịa revision: nếu chưa biết, gọi review để lấy trạng thái. Nếu chỉ nói 'dạy pha cà phê' mà vai trò chưa rõ, hỏi một câu ai sẽ làm mẫu."
)
