"""Product-listing enrichment (colours, materials, light specs, model templates).  No network."""
from .enrich import (
    classify_material,
    enrich_listing,
    extract_colors,
    infer_template,
    normalize_category,
    parse_light_spec,
)

__all__ = ["classify_material", "enrich_listing", "extract_colors", "infer_template", "normalize_category", "parse_light_spec"]
