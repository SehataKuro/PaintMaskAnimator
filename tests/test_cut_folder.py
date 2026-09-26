from paintmaskanimator import cut_folder as cf
from paintmaskanimator.cut_folder import CelSource, block, text


VALUES = {"title": "PMA", "scene": "", "episode": "3", "cut": "12"}


def test_pma_standard_renders_folder_cels_and_timesheet():
    layout = cf.pma_standard_layout()
    plan = cf.plan_export(
        layout,
        VALUES,
        [CelSource(0, "A", 1), CelSource(0, "A", 2), CelSource(1, "B", 1)],
    )
    assert plan.ok, plan.problems
    assert plan.folder_name == "PMA_03_c012"
    assert [path for path, _ in plan.files] == [
        "A/A0001.png", "A/A0002.png", "B/B0001.png",
    ]
    assert plan.folders == ["A", "B"]
    assert plan.timesheet_path == "PMA_03_c012.xdts"


def test_empty_field_drops_prefix_and_suffix():
    token = block("scene", prefix="s", suffix="-")
    assert cf.format_field(token, "") == ""
    assert cf.format_field(token, "3") == "s3-"


def test_digits_and_case_options():
    assert cf.format_field(block("number", digits=4), 7) == "0007"
    assert cf.format_field(block("number"), 7) == "7"
    assert cf.format_field(block("cell", case="lower"), "AB") == "ab"


def test_missing_value_used_by_template_is_reported():
    plan = cf.plan_export(
        cf.pma_standard_layout(), dict(VALUES, episode=""), [CelSource(0, "A", 1)]
    )
    assert ("missing_value", "episode") in plan.problems


def test_unused_empty_value_is_not_a_problem():
    # シーン is empty but the standard layout never uses it.
    plan = cf.plan_export(cf.pma_standard_layout(), VALUES, [CelSource(0, "A", 1)])
    assert plan.ok


def test_template_without_number_collides_and_is_reported():
    layout = cf.pma_standard_layout()
    layout.cell_file = (block("cell"),)
    plan = cf.plan_export(
        layout, VALUES, [CelSource(0, "A", 1), CelSource(0, "A", 2)]
    )
    assert ("no_number", "cell_file") in plan.problems
    assert ("duplicate", "A/A.png") in plan.problems


def test_invalid_characters_are_reported():
    layout = cf.pma_standard_layout()
    layout.folder = (block("title"),)
    plan = cf.plan_export(layout, dict(VALUES, title="a:b"), [])
    assert ("bad_name", "a:b", "folder") in plan.problems


def test_extra_folders_are_rendered_and_nested_paths_allowed():
    layout = cf.pma_standard_layout()
    layout.extra_folders = [(text("BG"),), (text("LO/"), block("cut", prefix="c"))]
    plan = cf.plan_export(layout, VALUES, [])
    assert plan.folders == ["BG", "LO/c12"]


def test_json_round_trip():
    layout = cf.pma_standard_layout()
    layout.extra_folders = [(text("BG"),)]
    layout.timesheet_folder = (text("_ts"),)
    restored = cf.CutFolderLayout.from_json(layout.to_json())
    assert restored == layout


def test_explode_and_compact_are_inverse():
    template = (text("_x"), block("cut"), text("."), text("y"))
    exploded = cf.explode(template)
    assert len(exploded) == 5
    assert cf.compact(exploded) == (text("_x"), block("cut"), text(".y"))


def test_regex_matches_rendered_names_and_loose_digits():
    template = cf.pma_standard_layout().cell_file
    pattern = cf.to_regex(template)
    match = pattern.fullmatch("A0012")
    assert match and match["cell"] == "A" and int(match["number"]) == 12
    match = pattern.fullmatch("B3")
    assert match and match["number"] == "3"


def test_regex_prefix_and_repeated_field():
    template = (block("cut", prefix="c", digits=3), text("_"), block("cut"))
    pattern = cf.to_regex(template)
    assert pattern.fullmatch("c012_012")
    assert not pattern.fullmatch("c012_013")


def test_guess_cut_number():
    assert cf.guess_cut_number("s03_c012_v2") == "012"
    assert cf.guess_cut_number("cut-7") == "7"
    assert cf.guess_cut_number("shot7") == "7"
    assert cf.guess_cut_number("draft") == ""


def test_default_layer_names_are_cel_letters():
    from paintmaskanimator.models import default_layer_name, next_layer_name

    assert [default_layer_name(i) for i in (0, 1, 25, 26, 27, 51, 52)] == [
        "A", "B", "Z", "AA", "AB", "AZ", "BA",
    ]
    assert next_layer_name(["A", "B"]) == "C"
    assert next_layer_name(["B", "BG"]) == "A"
    assert next_layer_name(["a", "C"]) == "B"
