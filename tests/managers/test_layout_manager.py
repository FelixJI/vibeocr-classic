"""layout_manager 布局管理器测试"""

import base64
import json

from PySide6.QtCore import QByteArray

from vibeocr.classic.managers.layout_manager import LayoutManager


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


class TestLayoutManagerWithDir:
    """使用 Path 初始化的测试"""

    def test_init_with_empty_dir(self, tmp_path):
        lm = LayoutManager(tmp_path)
        assert lm.get_main_window_geometry() is None
        assert lm.get_splitter_state("main") is None
        assert lm.get_tab_index() is None

    def test_save_and_reload(self, tmp_path):
        lm = LayoutManager(tmp_path)
        lm.set_main_window_geometry(QByteArray(b"\x01\x02\x03"))
        lm.set_splitter_state("main", QByteArray(b"\x04\x05\x06"))
        lm.set_tab_index(2)
        lm.save()

        lm2 = LayoutManager(tmp_path)
        geom = lm2.get_main_window_geometry()
        assert geom is not None and geom.data() == b"\x01\x02\x03"
        splitter = lm2.get_splitter_state("main")
        assert splitter is not None and splitter.data() == b"\x04\x05\x06"
        assert lm2.get_tab_index() == 2

    def test_load_corrupt_file(self, tmp_path):
        config = tmp_path / "layout.json"
        config.write_text("not json{{{", encoding="utf-8")
        lm = LayoutManager(tmp_path)
        assert lm.get_main_window_geometry() is None

    def test_load_version_mismatch(self, tmp_path):
        config = tmp_path / "layout.json"
        config.write_text(json.dumps({"version": 999}), encoding="utf-8")
        lm = LayoutManager(tmp_path)
        assert lm.get_main_window_geometry() is None

    def test_load_valid_config(self, tmp_path):
        config = tmp_path / "layout.json"
        data = {
            "version": 1,
            "main_window": {"geometry": _b64(b"\xaa\xbb")},
            "splitters": {"left": _b64(b"\xcc\xdd")},
            "tab_index": 1,
        }
        config.write_text(json.dumps(data), encoding="utf-8")

        lm = LayoutManager(tmp_path)
        geom2 = lm.get_main_window_geometry()
        assert geom2 is not None and geom2.data() == b"\xaa\xbb"
        splitter2 = lm.get_splitter_state("left")
        assert splitter2 is not None and splitter2.data() == b"\xcc\xdd"
        assert lm.get_tab_index() == 1

    def test_save_creates_directory(self, tmp_path):
        nested = tmp_path / "a" / "b"
        lm = LayoutManager(nested)
        lm.save()
        assert (nested / "layout.json").exists()

    def test_save_empty_data(self, tmp_path):
        lm = LayoutManager(tmp_path)
        lm.save()
        config = tmp_path / "layout.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["main_window"] == {}
        assert data["splitters"] == {}

    def test_get_splitter_state_missing(self, tmp_path):
        lm = LayoutManager(tmp_path)
        assert lm.get_splitter_state("nonexistent") is None

    def test_overwrite_existing(self, tmp_path):
        lm = LayoutManager(tmp_path)
        lm.set_tab_index(0)
        lm.save()

        lm2 = LayoutManager(tmp_path)
        lm2.set_tab_index(5)
        lm2.save()

        lm3 = LayoutManager(tmp_path)
        assert lm3.get_tab_index() == 5

    def test_load_empty_file(self, tmp_path):
        config = tmp_path / "layout.json"
        config.write_text("", encoding="utf-8")
        lm = LayoutManager(tmp_path)
        assert lm.get_main_window_geometry() is None
