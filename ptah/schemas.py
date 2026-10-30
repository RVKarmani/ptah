"""Pydantic schemas for the Ptah taste profile."""
from __future__ import annotations

from pydantic import BaseModel, Field


class QlooEntity(BaseModel):
    """A taste entity grounded in the Qloo taste graph."""

    qloo_id: str = Field(description="Qloo entity id, as returned by qloo_search / qloo_related.")
    name: str = Field(description="Canonical entity name as returned by Qloo.")
    type: str = Field(description="Entity type, e.g. artist, movie, brand, place, book, designer.")
    evidence: str = Field(
        description="Short reason this entity belongs to the user's taste, citing post ids or an artist from music."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence 0-1 that this is a durable taste signal.")


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
