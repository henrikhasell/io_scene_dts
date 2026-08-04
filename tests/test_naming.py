"""mapping/naming.py is bpy-free and unit-testable under plain CPython."""

from mapping.naming import (
    detail_name_for_size,
    object_display_name,
    split_detail_suffix,
    strip_blender_dedup,
)


class TestDetailSuffix:
    def test_visible_details(self):
        assert split_detail_suffix("shape2") == ("shape", 2)
        assert split_detail_suffix("shape32") == ("shape", 32)
        assert split_detail_suffix("body200") == ("body", 200)

    def test_collision(self):
        assert split_detail_suffix("collision-1") == ("collision", -1)
        assert split_detail_suffix("loscollision-9") == ("loscollision", -9)

    def test_no_suffix(self):
        assert split_detail_suffix("eye") == ("eye", None)
        assert split_detail_suffix("mount0point") == ("mount0point", None)

    def test_pure_number_kept_whole(self):
        # a name that is only digits has no base — treat as suffix-less
        assert split_detail_suffix("123") == ("123", None)

    def test_roundtrip(self):
        assert object_display_name("shape", 2) == "shape2"
        assert split_detail_suffix(object_display_name("col", -1)) == ("col", -1)


class TestDetailNames:
    def test_names(self):
        assert detail_name_for_size(2) == "detail2"
        assert detail_name_for_size(128) == "detail128"
        assert detail_name_for_size(-1) == "Collision-1"
        assert detail_name_for_size(-9) == "Collision-9"


class TestBlenderDedup:
    def test_strip(self):
        assert strip_blender_dedup("run.001") == "run"
        assert strip_blender_dedup("run.12345") == "run"
        assert strip_blender_dedup("run") == "run"
        assert strip_blender_dedup("v1.2") == "v1.2"  # short numeric tail kept
