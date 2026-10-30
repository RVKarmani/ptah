"""Pydantic schemas for the Ptah taste profile."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from . import qloo


class QlooEntity(BaseModel):
    """A taste entity grounded in the Qloo taste graph.

    Validation is the guardrail against invented entities: ``qloo_id`` must be a Qloo entity UUID (stored
    uppercase), and while a run is active (``qloo.start_run()``) the id/name/type must match something
    qloo_search / qloo_related really returned. Outside a run (loading a saved profile) only the format is checked.
    """

    qloo_id: str = Field(
        description="Qloo entity UUID copied exactly from real qloo_search / qloo_related output (never invented).")
    name: str = Field(description="Canonical entity name exactly as returned by Qloo.")
    type: str = Field(
        description="Entity type as returned by Qloo, e.g. artist, movie, brand, place, book, designer; "
                    "use destination for countries and cities.")
    evidence: str = Field(
        description="Short reason this entity belongs to the user's taste, citing post ids or an artist from music."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence 0-1 that this is a durable taste signal.")

    @field_validator("qloo_id")
    @classmethod
    def _uuid_format(cls, v: str) -> str:
        v = v.strip()
        if v.startswith("stub_") and qloo.is_stub():   # dry runs without a Qloo key use marked stub ids
            return v
        return qloo.normalize_uuid(v)

    @model_validator(mode="after")
    def _provenance(self) -> "QlooEntity":
        problem = qloo.check_entity(self.qloo_id, self.name, self.type)   # no-op unless a run is active
        if problem:
            raise ValueError(problem)
        return self


class Theme(BaseModel):
    """A recurring taste theme found in the user's posts."""

    name: str = Field(description="Short theme name: a hobby, film, artist, place, food or style that recurs in the posts.")
    post_count: int = Field(ge=0, description="Approximate number of posts supporting the theme.")
    example_post_ids: list[str] = Field(default_factory=list, description="A few representative post ids.")


class TasteProfile(BaseModel):
    """The extracted taste of a person, used downstream to furnish their room."""

    entities: list[QlooEntity] = Field(default_factory=list)
    aesthetic_tags: list[str] = Field(
        default_factory=list, description="Short visual/interior descriptors, e.g. 'warm minimalism'."
    )
    themes: list[Theme] = Field(default_factory=list)
    style_summary: str = Field(
        default="",
        description="2-3 sentences describing the person's likely interior/visual taste.",
    )
