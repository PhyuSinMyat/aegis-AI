from typing import List
from pydantic import BaseModel, ConfigDict, Field


class StudySession(BaseModel):
    model_config = ConfigDict(extra='ignore')

    day:     str = ''
    start:   str = ''
    end:     str = ''
    subject: str = ''
    topic:   str = ''
    type:    str = 'study'   # "study" | "pre_reading"


class StudyPlanResult(BaseModel):
    model_config = ConfigDict(extra='ignore')

    sessions: List[StudySession] = Field(default_factory=list)
