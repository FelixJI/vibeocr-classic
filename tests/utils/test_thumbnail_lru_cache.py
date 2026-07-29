"""Tests for ThumbnailLruCache — 缩略图按需渲染的 LRU 缓存。"""

import pytest
from PySide6.QtGui import QPixmap

from vibeocr.classic.utils.thumbnail_lru_cache import ThumbnailLruCache


@pytest.fixture(autouse=True)
def _qapp(qapp):
    """QPixmap 操作需要 QApplication。"""
    yield


def _pm(tag: int) -> QPixmap:
    """造一个可区分的 1×1 QPixmap。"""
    pm = QPixmap(1, 1)
    pm.fill(tag)
    return pm


class TestThumbnailLruCache:
    def test_get_miss_returns_none(self):
        cache = ThumbnailLruCache(capacity=10)
        assert cache.get(0) is None

    def test_put_then_get(self):
        cache = ThumbnailLruCache(capacity=10)
        pm = _pm(1)
        cache.put(5, pm)
        assert cache.get(5) is pm

    def test_evicts_oldest_when_full(self):
        """容量满后,put 新条目淘汰最久未访问的。"""
        cache = ThumbnailLruCache(capacity=3)
        cache.put(0, _pm(0))
        cache.put(1, _pm(1))
        cache.put(2, _pm(2))
        # 容量满,put 3 → 淘汰 0(最久未访问)
        cache.put(3, _pm(3))
        assert cache.get(0) is None
        assert cache.get(3) is not None

    def test_get_marks_as_recently_used(self):
        """get 后该条目变为最近使用,不被优先淘汰。"""
        cache = ThumbnailLruCache(capacity=3)
        cache.put(0, _pm(0))
        cache.put(1, _pm(1))
        cache.put(2, _pm(2))
        # 访问 0 → 0 变最新;淘汰顺序应为 1, 2, 0
        cache.get(0)
        cache.put(3, _pm(3))  # 淘汰 1(最久未访问)
        assert cache.get(0) is not None  # 0 仍在
        assert cache.get(1) is None  # 1 被淘汰

    def test_invalidate_single_page(self):
        cache = ThumbnailLruCache(capacity=10)
        cache.put(0, _pm(0))
        cache.put(1, _pm(1))
        cache.invalidate(0)
        assert cache.get(0) is None
        assert cache.get(1) is not None

    def test_clear(self):
        cache = ThumbnailLruCache(capacity=10)
        cache.put(0, _pm(0))
        cache.put(1, _pm(1))
        cache.clear()
        assert cache.get(0) is None
        assert cache.get(1) is None

    def test_put_overwrite_updates_recency(self):
        """同 key 再次 put 覆盖值并更新为最近使用。"""
        cache = ThumbnailLruCache(capacity=2)
        cache.put(0, _pm(0))
        cache.put(1, _pm(1))
        new_pm = _pm(99)
        cache.put(0, new_pm)  # 覆盖 0,0 变最新
        cache.put(2, _pm(2))  # 淘汰 1
        assert cache.get(0) is new_pm
        assert cache.get(1) is None
