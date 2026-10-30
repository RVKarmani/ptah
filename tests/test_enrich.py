import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from ptah.products.enrich import (  # noqa: E402
    classify_material,
    enrich_listing,
    extract_colors,
    infer_template,
    normalize_category,
    parse_light_spec,
)


def hex_dist(h, rgb):
    a = [int(h[i:i + 2], 16) for i in (1, 3, 5)]
    return sum((x - y) ** 2 for x, y in zip(a, rgb)) ** 0.5


def product_image(bg=(255, 255, 255), size=(200, 160), mode="RGB"):
    """A brown 'sofa' with a cream cushion band and a small red accent, on a studio backdrop."""
    im = Image.new(mode, size, bg if mode == "RGB" else bg + (255,))
    d = ImageDraw.Draw(im)
    d.rectangle([30, 40, 170, 130], fill=(140, 100, 60))     # body (~63% of the product)
    d.rectangle([40, 50, 160, 75], fill=(235, 225, 205))      # cream band
    d.rectangle([140, 100, 160, 120], fill=(200, 40, 40))    # red accent
    return im


# ---------------------------------------------------------------- colours
def test_extract_colors_white_backdrop():
    cols = extract_colors(product_image())
    assert cols is not None
    assert hex_dist(cols["primary"], (140, 100, 60)) < 25          # the body, not the white backdrop
    assert hex_dist(cols["secondary"], (235, 225, 205)) < 30
    assert hex_dist(cols["accent"], (200, 40, 40)) < 30


def test_extract_colors_accepts_bytes_path_and_grey_backdrop(tmp_path):
    im = product_image(bg=(238, 238, 240))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    from_bytes = extract_colors(buf.getvalue())
    path = tmp_path / "p.png"
    im.save(path)
    from_path = extract_colors(str(path))
    assert from_bytes == from_path
    assert hex_dist(from_bytes["primary"], (140, 100, 60)) < 25


def test_extract_colors_transparent_backdrop():
    im = Image.new("RGBA", (120, 120), (255, 255, 255, 0))
    ImageDraw.Draw(im).ellipse([20, 20, 100, 100], fill=(30, 80, 160, 255))
    cols = extract_colors(im)
    assert hex_dist(cols["primary"], (30, 80, 160)) < 20
    assert cols["accent"] is None


def test_extract_colors_ignores_tiny_clusters():
    im = Image.new("RGB", (200, 200), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, 160, 160], fill=(60, 90, 70))
    d.rectangle([100, 100, 102, 102], fill=(250, 0, 0))   # 9 px speck
    cols = extract_colors(im)
    assert hex_dist(cols["primary"], (60, 90, 70)) < 15
    assert cols["secondary"] is None and cols["accent"] is None


def test_extract_colors_white_product_on_white():
    im = Image.new("RGB", (160, 160), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([30, 40, 130, 120], fill=(243, 238, 228))
    cols = extract_colors(im)
    assert cols is not None and hex_dist(cols["primary"], (243, 238, 228)) < 12


@pytest.mark.parametrize("bad", [b"", b"not an image", "/nonexistent/file.png", None, Image.new("RGB", (160, 160), (255, 255, 255))])
def test_extract_colors_fallback_none(bad):
    assert extract_colors(bad) is None


# ---------------------------------------------------------------- materials
@pytest.mark.parametrize("title,desc,primary", [
    ("Sample Bouclé armchair", "", "boucle"),
    ("Acacia wood side table", "", "light_wood"),
    ("Console with brushed gold frame", "", "brass"),
    ("Velvet sofa", "polyester blend", "velvet"),
    ("Light oak coffee table", "", "light_wood"),
    ("Oak dining table", "solid oak", "oak"),
    ("Dark walnut sideboard", "", "walnut"),
    ("Rattan pendant", "woven", "rattan"),
    ("Marble top bistro table", "", "marble"),
    ("Matte black metal floor lamp", "", "black_metal"),
    ("Chrome arc lamp", "", "chrome"),
    ("Genuine leather recliner", "", "leather"),
    ("Opal glass globe pendant", "", "glass"),
    ("Stoneware table lamp", "", "ceramic"),
    ("Mahogany console", "", "dark_wood"),
])
def test_classify_material_primary(title, desc, primary):
    assert classify_material(title, desc)["primary"] == primary


def test_classify_material_secondary_and_title_weight():
    m = classify_material("Velvet sofa with brass legs", "Frame in birch plywood. Cotton cushions.")
    assert m["primary"] == "velvet"
    assert m["secondary"] in ("brass", "light_wood", "cotton")
    m = classify_material("Linen armchair with walnut legs", "")
    assert (m["primary"], m["secondary"]) == ("linen", "walnut")
    assert classify_material("Mystery object", "") == {"primary": None, "secondary": None}
    assert classify_material(None, None) == {"primary": None, "secondary": None}
    # word boundaries: "washable" must not read as ash, "candle" not as cane
    assert classify_material("Washable candle holder", "")["primary"] is None


# ---------------------------------------------------------------- lights
def test_parse_light_spec_explicit_values():
    l = parse_light_spec("Dimmable LED floor lamp 2700K", "800 lumens warm glow", "floor_lamp", dims=(40, 40, 160))
    assert l["layer"] == "task" and l["kind"] == "point"
    assert l["color_temp_k"] == 2700 and l["lumens"] == 800 and l["dimmable"] is True
    assert l["rgb"] is None and l["emit_offset"][1] == pytest.approx(160 * 0.94, abs=1)
    assert "lumens" not in l.get("estimated", []) and "color_temp_k" not in l.get("estimated", [])


def test_parse_light_spec_words_and_watts():
    assert parse_light_spec("Pendant", "warm white", "pendant")["color_temp_k"] == 2700
    assert parse_light_spec("Pendant", "daylight bulb", "pendant")["color_temp_k"] == 5000
    assert parse_light_spec("Pendant", "", "pendant")["color_temp_k"] == 2700
    led = parse_light_spec("Table lamp", "10W LED bulb included", "table_lamp")
    assert led["lumens"] == 900 and "lumens" in led["estimated"]
    inc = parse_light_spec("Table lamp", "40W incandescent bulb", "table_lamp")
    assert inc["lumens"] == 560
    multi = parse_light_spec("Pendant", "3 x 5W LED", "pendant")
    assert multi["lumens"] == 1350
    socket = parse_light_spec("Table lamp", "E27 socket, max 60W", "table_lamp")
    assert socket["lumens"] == 840


def test_parse_light_spec_defaults_per_category():
    expect = {"pendant": ("ambient", "point"), "floor_lamp": ("task", "point"), "table_lamp": ("accent", "point"),
              "sconce": ("accent", "point"), "led_strip": ("accent", "line"), "picture_light": ("accent", "spot")}
    for cat, (layer, kind) in expect.items():
        l = parse_light_spec("Sample", "", cat)
        assert (l["layer"], l["kind"]) == (layer, kind), cat
        assert l["lumens"] > 0 and len(l["emit_offset"]) == 3 and l["dimmable"] is False
    assert parse_light_spec("Sample sofa", "", "sofa") is None
    # synonyms of category names
    assert parse_light_spec("x", "", "wall light")["layer"] == "accent"
    assert parse_light_spec("Standing lamp", "", "Standing Lamp")["layer"] == "task"
    # keyword overrides
    assert parse_light_spec("Desk lamp", "", "table_lamp")["layer"] == "task"
    assert parse_light_spec("Spotlight pendant", "", "pendant")["kind"] == "spot"
    assert parse_light_spec("Uplighter floor lamp", "", "floor_lamp")["layer"] == "ambient"


def test_parse_light_spec_strip_scales_with_length_and_rgb():
    l = parse_light_spec("RGB colour-changing LED strip", "450 lm/m, 5V", "led_strip", dims=(200, 2, 2))
    assert l["lumens"] == 900 and l["color_changing"] is True and l["rgb"] is None
    assert l["kind"] == "line"
    w = parse_light_spec("LED strip", "12W/m", "led_strip", dims=(100, 2, 2))
    assert w["lumens"] == 1080


def test_parse_light_spec_emit_offset_uses_dims_and_template():
    t = parse_light_spec("Pendant", "", "pendant", dims=(40, 40, 50))
    assert t["emit_offset"] == [0.0, 20.0, 0.0]
    arc = parse_light_spec("Arc floor lamp", "", "floor_lamp", dims=(60, 60, 200))
    assert arc["emit_offset"][2] > 0           # bulb sits out over the seat
    sc = parse_light_spec("Sconce", "", "sconce", dims=(15, 20, 30))
    assert sc["emit_offset"][2] > 0


# ---------------------------------------------------------------- templates
def test_infer_template_shades_and_legs():
    assert infer_template("floor_lamp", "Arc floor lamp with dome shade") == {
        "template": "floor_lamp", "params": {"shade": "dome", "legs": "none", "arc": True}}
    assert infer_template("table_lamp", "Globe glass table lamp")["params"]["shade"] == "globe"
    assert infer_template("pendant", "Cone pendant light")["params"]["shade"] == "cone"
    assert infer_template("table_lamp", "Linen drum shade lamp")["params"]["shade"] == "drum"
    assert infer_template("table_lamp", "Bare bulb edison lamp")["params"]["shade"] == "none"
    assert infer_template("coffee_table", "Round coffee table with hairpin legs")["params"] == {"shape": "round", "legs": "hairpin"}
    assert infer_template("side table", "Tapered leg end table")["template"] == "side_table"
    assert infer_template("side table", "Tapered legs end table")["params"]["legs"] == "tapered"
    sofa = infer_template("sofa", "3-seater armless L-shaped sofa")
    assert sofa["params"]["seats"] == 3 and sofa["params"]["arms"] is False and sofa["params"]["shape"] == "l_shape"


def test_infer_template_categories_and_fallbacks():
    assert infer_template("Couch", "")["template"] == "sofa"
    assert infer_template("bookcase", "5-tier open bookcase")["params"]["shelves"] == 5
    assert infer_template("tv unit", "")["template"] == "tv_stand"
    assert infer_template("painting", "")["template"] == "art"
    assert infer_template("whatever", "Mystery thing")["template"] == "generic"
    assert infer_template(None, "Floor lamp")["template"] == "floor_lamp"
    assert normalize_category("Wall Sconce") == "sconce"
    assert infer_template("pendant", "")["params"]["shade"] in ("globe", "drum", "dome", "cone", "none")


def test_enrich_listing_merges_everything():
    item = {"id": "lamp-9", "category": "floor_lamp", "name": "Sample arc lamp 2200K dimmable", "w": 50, "d": 50, "h": 190}
    out = enrich_listing(item, image=product_image(), description="Black metal base, linen shade, 900 lm")
    assert out["model"]["params"]["arc"] is True
    assert out["colors"]["primary"] is not None
    assert out["material"]["primary"] in ("black_metal", "linen")
    assert out["light"]["lumens"] == 900 and out["light"]["color_temp_k"] == 2200 and out["light"]["dimmable"]
    assert "light" not in enrich_listing({"id": "s", "category": "sofa", "name": "Sofa"})
    assert item == {"id": "lamp-9", "category": "floor_lamp", "name": "Sample arc lamp 2200K dimmable", "w": 50, "d": 50, "h": 190}
