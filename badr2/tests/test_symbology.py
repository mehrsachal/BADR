import base64

import pytest

from badr.symbology import sidc as S


def test_build_and_parse_roundtrip():
    code = S.build("land_unit", "121100", "hostile", echelon="platoon")
    assert code == "10061000141211000000"
    p = S.parse(code)
    assert p["affiliation"] == "hostile" and p["entity"] == "121100" and p["echelon"] == "14"
    assert S.with_affiliation(code, "friend")[3] == "3"
    assert S.with_status(code, "planned")[6] == "1"


def test_aliases_and_validation():
    assert S.norm_affiliation("enemy") == "hostile"
    assert S.norm_affiliation("Blue") == "friend"
    with pytest.raises(ValueError):
        S.build("land_unit", "12", "friend")
    with pytest.raises(ValueError):
        S.norm_affiliation("purple")


def test_catalog_is_complete_and_searchable():
    cat = S.catalog()
    assert len(cat["sets"]) == 13
    assert sum(len(s["entities"]) for s in cat["sets"].values()) > 1500
    top = S.search("IED explosion")[0]
    assert (top["symbol_set"], top["entity"]) == ("40", "110301")
    assert S.search("infantry", ["land_unit"])[0]["entity"] == "121100"
    assert S.search("safe house")[0]["entity"] == "111700"
    assert "Infantry" in S.describe("10061000141211000000")


def test_palette_codes_exist_in_catalog():
    from badr.symbology.palette import PALETTE
    cat = S.catalog()["sets"]
    for group, items in PALETTE.items():
        for item in items:
            if item.get("layer") in ("lines", "areas"):
                continue
            codes = {e["code"] for e in cat[item["set"]]["entities"]}
            assert item["entity"] in codes, f"{group}/{item['label']} -> {item['set']}:{item['entity']}"
            for sector in ("mod1", "mod2"):
                if item.get(sector):
                    mods = {m["code"] for m in cat[item["set"]]["modifiers"]["1" if sector == "mod1" else "2"]}
                    assert item[sector] in mods


def test_engine_renders_real_app6_symbols(qgis_app):
    from badr.symbology.engine import SymbolEngine
    eng = SymbolEngine.instance()
    assert eng.backend.startswith("milsymbol"), "QtQml missing: only the fallback renderer is available"
    b64, w, h = eng.render("10061000141211000000", {"name": "2 PL", "higher": "A COY"})
    svg = base64.b64decode(b64).decode()
    assert svg.startswith("<svg") and "2 PL" in svg and w > 20 and h > 20
    # hostile frame colour from milsymbol, proves this is not the fallback path
    assert "rgb(255,128,128)" in svg.replace(" ", "")
    plain = eng.render("10031000001211000000")
    assert plain[1] < w   # amplifier text widens the image; frame scale is unchanged


def test_expression_functions_drive_a_qgis_symbol(qgis_app, operation):
    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils
    from badr.symbology.styles import apply_styles
    apply_styles(operation)
    uid = operation.add_point("units", 34.0, 71.5, {"sidc": "10031000151211000000", "name": "B COY"})
    feat = operation.find("units", uid)
    ctx = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(operation.layer("units")))
    ctx.setFeature(feat)
    val = QgsExpression('milsym("sidc","name")').evaluate(ctx)
    assert val.startswith("base64:")
    assert QgsExpression('milsym_size("sidc","name")').evaluate(ctx) > 20


def test_fallback_renderer_is_self_contained():
    from badr.symbology.engine import fallback_svg
    svg, w, h = fallback_svg("10061000141211000000", {"name": "X"})
    assert "<svg" in svg and w > 0 and h > 0


def test_every_cop_layer_symbol_expression_evaluates_with_empty_attributes(qgis_app, operation):
    """Regression: NULL attributes or a missing field must never blank the symbol."""
    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsProperty, QgsSymbolLayer
    from badr.symbology.styles import apply_styles
    apply_styles(operation)
    code = "10031000001211000000"
    operation.add_point("units", 1.0, 1.0, {"sidc": code})
    operation.add_point("incidents", 1.0, 1.0, {"sidc": code})
    operation.add_point("detections", 1.0, 1.0, {"sidc": code, "label": "x", "confidence": 0.5})
    operation.add_point("tracks", 1.0, 1.0, {"sidc": code, "name": "T"})
    for key in ("units", "incidents", "detections", "tracks"):
        layer = operation.layer(key)
        svg_layer = layer.renderer().symbol().symbolLayers()[-1]
        ctx = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        ctx.setFeature(next(layer.getFeatures()))
        for prop in (QgsSymbolLayer.PropertyName, QgsSymbolLayer.PropertySize):
            expr = QgsExpression(svg_layer.dataDefinedProperties().property(prop).expressionString())
            val = expr.evaluate(ctx)
            assert not expr.hasEvalError(), f"{key}: {expr.evalErrorString()}"
            assert val not in (None, "") and str(val) != "NULL", key
