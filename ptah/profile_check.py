"""Post-run provenance check for a TasteProfile (works on plain dicts, so saved profiles can be re-checked).

An entity is *kept* only if its id is a Qloo UUID that a tool really returned (``seen`` registry), its name
matches Qloo's name for that id and its type is consistent with Qloo's type (``qloo.check_entity``).
Duplicate ids are collapsed (highest confidence wins).
"""
from __future__ import annotations

from typing import Any

from . import qloo


def entity_problems(entity: dict, seen: dict) -> list[str]:
    """Reasons an entity dict fails the guardrails ([] if fine)."""
    qid = str(entity.get("qloo_id") or "")
    if not qloo.is_uuid(qid.strip()):
        return [f"qloo_id {qid!r} is not a Qloo entity UUID"]
    problem = qloo.check_entity(qid.strip(), str(entity.get("name") or ""), str(entity.get("type") or ""), seen=seen)
    return [problem] if problem else []


def check_profile(profile: dict[str, Any], seen: dict) -> tuple[dict[str, Any], list[dict]]:
    """-> (profile with only the passing entities, dropped=[{"entity": {...}, "reasons": [...]}])."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for e in profile.get("entities") or []:
        reasons = entity_problems(e, seen)
        if reasons:
            dropped.append({"entity": e, "reasons": reasons})
            continue
        dup = next((k for k in kept if k["qloo_id"].upper() == e["qloo_id"].upper()), None)
        if dup is None:
            kept.append(e)
        elif (e.get("confidence") or 0) > (dup.get("confidence") or 0):
            kept[kept.index(dup)] = e
            dropped.append({"entity": dup, "reasons": [f"duplicate qloo_id of {e.get('name')!r}"]})
        else:
            dropped.append({"entity": e, "reasons": [f"duplicate qloo_id of {dup.get('name')!r}"]})
    return {**profile, "entities": kept}, dropped
