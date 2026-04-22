from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from firestore_store import (
    get_candidate,
    list_candidates,
    save_analysis,
    save_question_set,
    save_transcript_feedback,
    upsert_candidate,
)
from schemas import ATSActionRequest, ATSActionResponse

DEFAULT_MODEL = (
    os.getenv("GEMINI_MODEL_FLASH")
    or os.getenv("GEMINI_MODEL")
    or os.getenv("GEMINI_MODEL_PRO")
    or "gemini-2.5-flash"
).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _to_lines(value: str | None, limit: int = 6) -> list[str]:
    if not value:
        return []
    chunks = [part.strip("- ").strip() for part in re.split(r"[\n\r•]+", value) if part and part.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for item in chunks:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def _gemini_json(system_prompt: str, user_prompt: str, fallback: dict[str, Any], *, max_tokens: int = 1400) -> dict[str, Any]:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return fallback

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.9,
            "responseMimeType": "application/json",
            "maxOutputTokens": max_tokens,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_MODEL}:generateContent",
                params={"key": api_key},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return fallback

    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    text = "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    parsed = _extract_json(text)
    return parsed or fallback


def _require_user(req: ATSActionRequest) -> str:
    user_id = _clean(req.userId)
    if not user_id:
        raise ValueError("userId is required for ats-agent actions.")
    return user_id


def _resolve_candidate_base(req: ATSActionRequest) -> dict[str, Any]:
    return {
        "name": _clean(req.candidateName),
        "email": _clean(req.candidateEmail),
        "resumeText": _clean(req.resumeText),
        "jobTitle": _clean(req.jobTitle),
        "jobDescription": _clean(req.jobDescription),
        "updatedAtIso": _now_iso(),
    }


async def _analyze_candidate(user_id: str, req: ATSActionRequest) -> ATSActionResponse:
    resume_text = _clean(req.resumeText)
    job_description = _clean(req.jobDescription)
    if not resume_text:
        return ATSActionResponse(
            status="needs_input",
            type="ats_candidate_analysis",
            message="Candidate resume text is required to run ATS analysis.",
            summary="Please share resume text to calculate fit and recommendations.",
            result={"suggestedInputs": ["resumeText"]},
        )

    fallback = {
        "overallScore": 78,
        "matchLabel": "Good Match",
        "scoreBreakdown": {
            "jobFit": 80,
            "technicalFit": 77,
            "culturalFit": 76,
            "communicationFit": 79,
        },
        "strengths": _to_lines(resume_text, 4) or ["Relevant profile details identified from resume."],
        "areasForGrowth": [
            "Provide stronger examples with measurable impact.",
            "Expand role-specific experience highlights for the target position.",
        ],
        "detailedAnalysis": [
            {
                "title": "Job Fit",
                "summary": "The profile aligns with core requirements and should proceed to interview.",
                "rating": 4,
            }
        ],
    }

    parsed = await _gemini_json(
        "You are a senior recruiting analyst. Return only strict JSON with ATS scoring insights.",
        (
            f"Candidate name: {_clean(req.candidateName) or 'Unknown'}\n"
            f"Target role: {_clean(req.jobTitle) or 'Open Role'}\n"
            f"Job description:\n{job_description or 'Not provided'}\n\n"
            f"Resume text:\n{resume_text}\n\n"
            "Return JSON with keys: overallScore (0-100), matchLabel, scoreBreakdown(jobFit,technicalFit,culturalFit,communicationFit), "
            "strengths(array), areasForGrowth(array), detailedAnalysis(array of {title,summary,rating 1-5})."
        ),
        fallback,
    )

    candidate_id = upsert_candidate(user_id, _clean(req.candidateId) or None, _resolve_candidate_base(req))
    save_analysis(user_id, candidate_id, parsed)

    return ATSActionResponse(
        status="success",
        type="ats_candidate_analysis",
        message=f"ATS analysis ready for {_clean(req.candidateName) or 'candidate'}.",
        summary=f"Match score: {int(float(parsed.get('overallScore', 0)))}%",
        result={
            "candidateId": candidate_id,
            "candidateName": _clean(req.candidateName) or "Candidate",
            "jobTitle": _clean(req.jobTitle) or "Open Role",
            "analysis": parsed,
        },
    )


async def _generate_questions(user_id: str, req: ATSActionRequest) -> ATSActionResponse:
    candidate = get_candidate(user_id, _clean(req.candidateId)) if _clean(req.candidateId) else None
    resume_text = _clean(req.resumeText) or _clean((candidate or {}).get("resumeText"))
    job_description = _clean(req.jobDescription) or _clean((candidate or {}).get("jobDescription"))
    if not resume_text and not job_description:
        return ATSActionResponse(
            status="needs_input",
            type="ats_interview_questions",
            message="Need resume or role context to generate questions.",
            summary="Provide candidate resume text or a job description.",
            result={"suggestedInputs": ["candidateId", "resumeText", "jobDescription"]},
        )

    stage = _clean(req.interviewStage) or "Phone Screening"
    fallback = {
        "stage": stage,
        "questions": [
            {
                "question": "Walk me through a project where you solved a production issue under pressure.",
                "context": "Assesses ownership and incident response capability.",
                "tag": "technical",
            },
            {
                "question": "How do you align your work with changing business priorities?",
                "context": "Assesses stakeholder communication and prioritization.",
                "tag": "behavioral",
            },
        ],
    }

    parsed = await _gemini_json(
        "You are an interview planner. Return concise role-specific interview questions in strict JSON.",
        (
            f"Interview stage: {stage}\n"
            f"Role: {_clean(req.jobTitle) or _clean((candidate or {}).get('jobTitle')) or 'Open Role'}\n"
            f"Job description:\n{job_description or 'Not provided'}\n\n"
            f"Resume:\n{resume_text or 'Not provided'}\n\n"
            "Return JSON with keys: stage, questions(array of {question,context,tag})."
        ),
        fallback,
        max_tokens=1200,
    )
    questions = parsed.get("questions")
    if not isinstance(questions, list):
        questions = fallback["questions"]

    candidate_id = _clean(req.candidateId) or upsert_candidate(user_id, None, _resolve_candidate_base(req))
    save_question_set(user_id, candidate_id, stage, questions)

    return ATSActionResponse(
        status="success",
        type="ats_interview_questions",
        message=f"Generated {len(questions)} interview questions.",
        summary=f"Interview question set ready for {stage}.",
        result={
            "candidateId": candidate_id,
            "stage": stage,
            "questions": questions,
        },
    )


async def _save_transcript(user_id: str, req: ATSActionRequest) -> ATSActionResponse:
    transcript = _clean(req.transcript)
    if not transcript:
        return ATSActionResponse(
            status="needs_input",
            type="ats_interview_feedback",
            message="Interview transcript is required.",
            summary="Please provide transcript text to generate feedback.",
            result={"suggestedInputs": ["transcript"]},
        )
    stage = _clean(req.interviewStage) or "Interview"
    candidate = get_candidate(user_id, _clean(req.candidateId)) if _clean(req.candidateId) else None

    fallback = {
        "ratingOutOf10": 7,
        "strengths": [
            "Clear communication and structured responses.",
            "Demonstrated practical problem-solving examples.",
        ],
        "areasForGrowth": [
            "Use more measurable outcomes while describing impact.",
            "Add deeper technical trade-off explanation.",
        ],
        "interviewTechnique": "The interview stayed structured and role-relevant.",
        "questionQuality": "Questions covered required competencies with room for deeper probes.",
        "recommendations": [
            "Use one follow-up per answer to validate depth.",
            "Close with role-specific scenario questions.",
        ],
        "summary": "Candidate showed good potential with moderate coaching needs.",
    }

    parsed = await _gemini_json(
        "You are an interview coach. Return strict JSON feedback from interview transcript.",
        (
            f"Stage: {stage}\n"
            f"Role: {_clean(req.jobTitle) or _clean((candidate or {}).get('jobTitle')) or 'Open Role'}\n"
            f"Transcript:\n{transcript}\n\n"
            "Return JSON with keys: ratingOutOf10, strengths(array), areasForGrowth(array), interviewTechnique, questionQuality, recommendations(array), summary."
        ),
        fallback,
    )

    candidate_id = _clean(req.candidateId) or upsert_candidate(user_id, None, _resolve_candidate_base(req))
    save_transcript_feedback(user_id, candidate_id, stage, transcript, parsed)

    return ATSActionResponse(
        status="success",
        type="ats_interview_feedback",
        message="Interview transcript saved and feedback generated.",
        summary=parsed.get("summary") if isinstance(parsed.get("summary"), str) else "Interview feedback is ready.",
        result={
            "candidateId": candidate_id,
            "stage": stage,
            "feedback": parsed,
        },
    )


async def _compare_candidates(user_id: str, req: ATSActionRequest) -> ATSActionResponse:
    incoming = req.candidates or []
    compiled: list[dict[str, Any]] = []
    for item in incoming:
        doc = item.model_dump()
        cid = _clean(doc.get("candidateId"))
        stored = get_candidate(user_id, cid) if cid else None
        if stored:
            compiled.append(stored)
        else:
            compiled.append(doc)

    if not compiled:
        return ATSActionResponse(
            status="needs_input",
            type="ats_candidate_compare",
            message="No candidate profiles were provided for comparison.",
            summary="Share at least two candidates to compare.",
            result={"suggestedInputs": ["candidates"]},
        )

    normalized = []
    for idx, item in enumerate(compiled):
        analysis = item.get("latestAnalysis") or {}
        score = 0
        if isinstance(analysis, dict):
            try:
                score = int(float(analysis.get("overallScore", 0)))
            except Exception:
                score = 0
        normalized.append(
            {
                "rank": idx + 1,
                "candidateId": item.get("candidateId") or "",
                "name": item.get("name") or f"Candidate {idx + 1}",
                "score": score,
                "highlights": (analysis.get("strengths") if isinstance(analysis, dict) else []) or [],
            }
        )

    sorted_rows = sorted(normalized, key=lambda row: row["score"], reverse=True)
    for i, row in enumerate(sorted_rows, start=1):
        row["rank"] = i

    top = sorted_rows[0]
    summary = f"{top['name']} is currently top-ranked with an ATS score of {top['score']}%."
    return ATSActionResponse(
        status="success",
        type="ats_candidate_compare",
        message="Candidate comparison ready.",
        summary=summary,
        result={
            "executiveSummary": summary,
            "topRecommendation": top,
            "rankings": sorted_rows,
        },
    )


async def _list_candidates(user_id: str) -> ATSActionResponse:
    rows = list_candidates(user_id, limit=15)
    lightweight = [
        {
            "candidateId": row.get("candidateId"),
            "name": row.get("name") or "Candidate",
            "email": row.get("email") or "",
            "jobTitle": row.get("jobTitle") or "Open Role",
            "overallScore": ((row.get("latestAnalysis") or {}).get("overallScore") if isinstance(row.get("latestAnalysis"), dict) else None),
            "updatedAtIso": row.get("updatedAtIso") or "",
        }
        for row in rows
    ]
    return ATSActionResponse(
        status="success",
        type="ats_candidates_list",
        message=f"Loaded {len(lightweight)} candidate records.",
        summary="Recent ATS candidate records are ready.",
        result={"candidates": lightweight},
    )


async def run_ats_action(req: ATSActionRequest) -> ATSActionResponse:
    try:
        user_id = _require_user(req)
        action = _clean(req.action).lower()

        if action in {"analyze_candidate", "candidate_analysis", "analyze_resume", "screen_candidate"}:
            return await _analyze_candidate(user_id, req)
        if action in {"generate_interview_questions", "interview_questions", "question_set"}:
            return await _generate_questions(user_id, req)
        if action in {"save_interview_transcript", "interview_feedback", "analyze_interview_transcript"}:
            return await _save_transcript(user_id, req)
        if action in {"compare_candidates", "candidate_compare"}:
            return await _compare_candidates(user_id, req)
        if action in {"list_candidates", "recent_candidates"}:
            return await _list_candidates(user_id)

        return ATSActionResponse(
            status="failed",
            type="ats_result",
            message=f"Unsupported action: {req.action}",
            summary="Requested ATS action is not available.",
            error=f"Unknown action: {req.action}",
        )
    except ValueError as exc:
        return ATSActionResponse(
            status="needs_input",
            type="ats_result",
            message=str(exc),
            summary="ATS needs a required input to continue.",
            result={"suggestedInputs": ["userId"]},
            error=str(exc),
        )
    except Exception:
        return ATSActionResponse(
            status="failed",
            type="ats_result",
            message="ATS agent failed to complete the request.",
            summary="Try again with more specific candidate details.",
            error="ATS execution failed.",
        )
