"""Evidence Q&A endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...assistant import answer_question
from ...schemas import AssistantAnswer, AssistantQuery
from ..deps import get_db

router = APIRouter(tags=["assistant"])


@router.post("/assistant/query", response_model=AssistantAnswer)
def query(payload: AssistantQuery, db: Session = Depends(get_db)):
    return answer_question(
        db,
        question=payload.question,
        analysis_id=payload.analysis_id,
        candidate_id=payload.candidate_id,
    )
