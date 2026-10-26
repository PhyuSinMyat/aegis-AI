from typing import List
from pydantic import BaseModel, ConfigDict, Field


# -----------------------------
# Base model (STRICT)
# extra='forbid' means Pydantic raises an error if the LLM
# returns any field not defined here — catches hallucinated fields.
# -----------------------------
class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


# -----------------------------
# MODULE
# db_module_code and db_module_name are matched from the user_modules
# table by the pipeline after extraction. The LLM always outputs "" for these.
# -----------------------------
class ExtractedModule(StrictBaseModel):
    module_code: str = ''
    module_name: str = ''
    module_alias: str = ''
    source_file: str = ''
    db_module_code: str = ''
    db_module_name: str = ''


# -----------------------------
# CLASS SESSIONS
# -----------------------------
class ExtractedClassSession(StrictBaseModel):
    module_code: str = ''
    module_name: str = ''
    module_alias: str = ''
    day: str = ''
    start_time: str = ''
    end_time: str = ''
    week_pattern: str = ''
    session_type: str = ''
    class_group: str = ''
    location: str = ''
    delivery_mode: str = ''
    instructor: str = ''
    source_file: str = ''
    source_page: str = ''


# -----------------------------
# MODULE SCHEDULE
# weekly_topics removed — topic is captured in the activities field.
# -----------------------------
class ExtractedModuleSchedule(StrictBaseModel):
    module_code: str = ''
    module_name: str = ''
    week_number: str = ''
    activities: str = ''
    hours: str = ''
    mode: str = ''
    source_file: str = ''
    source_page: str = ''


# -----------------------------
# ASSESSMENTS
# milestone_type merged from old milestones table.
# Values: Release, Submission, Consultation, Checkpoint, Other
# Leave "" when the assessment is not a milestone event.
# -----------------------------
class ExtractedAssessment(StrictBaseModel):
    module_code: str = ''
    module_name: str = ''
    title: str = ''
    assessment_type: str = ''
    milestone_type: str = ''
    week_number: str = ''
    due_date: str = ''
    weightage: str = ''
    topic_scope: str = ''
    duration: str = ''
    source_file: str = ''
    source_page: str = ''


# -----------------------------
# SPECIAL WEEKS
# -----------------------------
class ExtractedSpecialWeek(StrictBaseModel):
    module_code: str = ''
    module_name: str = ''
    week_number: str = ''
    label: str = ''
    source_file: str = ''
    source_page: str = ''


# -----------------------------
# FINAL RESULT
# Removed: weekly_topics, milestones, assumptions
# Added:   assessments.milestone_type, modules.db_module_code,
#          modules.db_module_name, remarks
# -----------------------------
class ExtractionResult(StrictBaseModel):
    modules: List[ExtractedModule] = Field(default_factory=list)
    class_sessions: List[ExtractedClassSession] = Field(default_factory=list)
    module_schedule: List[ExtractedModuleSchedule] = Field(default_factory=list)
    assessments: List[ExtractedAssessment] = Field(default_factory=list)
    special_weeks: List[ExtractedSpecialWeek] = Field(default_factory=list)
    remarks: List[str] = Field(default_factory=list)