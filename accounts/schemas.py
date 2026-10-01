from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    _model_used: str = PrivateAttr(default='')
    _fallback_used: bool = PrivateAttr(default=False)

class Correction(StrictModel):
    section: str
    priority: Literal['high', 'medium', 'low']
    issue: str
    why: str
    correction: str
    evidence: str
    suggested_wording: str

class Requirement(StrictModel):
    skill: str
    kind: Literal['skill', 'education', 'experience']
    optional: bool
    requirement_quote: str
    resume_quote: str
    demonstrated: bool
    explanation: str
    demonstration: str

class Review(StrictModel):
    corrections: list[Correction] = Field(max_length=25)
    requirements: list[Requirement] = Field(max_length=50)

class Topic(StrictModel):
    id: str
    title: str
    requirement: str = ''
    requirement_id: str = ''
    topic_kind: Literal['target', 'foundation', 'unrelated'] = 'target'
    foundation_skill: str = ''
    prerequisites: list[str]
    objectives: str
    learning_hours: int = Field(ge=1, le=80)
    practice_hours: int = Field(ge=1, le=120)
    revision_hours: int = Field(ge=1, le=20)
    assessment_hours: int = Field(ge=1, le=20)
    exercise: str
    project: str
    checkpoint: str

class TopicPlan(StrictModel):
    topics: list[Topic] = Field(min_length=1, max_length=30)
