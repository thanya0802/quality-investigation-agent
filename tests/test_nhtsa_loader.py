"""The loader is tested against rows in NHTSA's documented 51-field layout (values made up)."""
from data.load_nhtsa import component, parse_rows

def row(cmplid, make, model, year, comp, ldate, text, crash="N"):
    f = [""] * 51
    f[0], f[1], f[3], f[4], f[5], f[6], f[7], f[8], f[9] = cmplid, "11500001", make, model, year, crash, "20250101", "N", "0"
    f[11], f[13], f[15], f[16], f[19] = comp, "MI", "20250110", ldate, text
    return "\t".join(f)

def test_parses_official_layout_and_filters():
    lines = [row("1", "FORD", "ESCAPE", "2023", "SERVICE BRAKES, HYDRAULIC:FOUNDATION COMPONENTS", "20250108", "brakes soft", "Y"),
             row("2", "FORD", "ESCAPE", "2018", "STEERING", "20250108", "old model year"),
             row("3", "TOYOTA", "RAV4", "2023", "STEERING", "20250108", "other make")] * 10
    out = parse_rows(lines, "FORD", set(), 2020, 2026)
    assert {r["complaint_id"] for r in out} == {"1"}
    r = out[0]
    assert r["component"] == "SERVICE BRAKES" and r["received_date"] == "2025-01-08"
    assert r["week_start"] == "2025-01-06" and r["crash"] == 1

def test_component_normalisation():
    assert component("ELECTRICAL SYSTEM:12V/24V/48V BATTERY") == "ELECTRICAL SYSTEM"
    assert component("FUEL SYSTEM, GASOLINE:DELIVERY:HOSES") == "FUEL SYSTEM"
