"""缩略图 LRU 缓存 — 按需渲染时只保留最近访问的若干页缩略图。

大 PDF（数百页）全量缓存缩略图内存不可控且首次渲染慢。
按需渲染配合 LRU：只缓存最近查看过的页（默认 200 页 ≈ 20MB @160×160），
滚动到未缓存页时触发渲染，淘汰最久未访问的。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap


class ThumbnailLruCache:
    """按页索引缓存缩略图 QPixmap 的 LRU 容器。"""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._store: OrderedDict[int, QPixmap] = OrderedDict()

    def get(self, page_index: int) -> QPixmap | None:
        """返回缓存的 pixmap（同时标记为最近访问）；未命中返回 None。"""
        pm = self._store.get(page_index)
        if pm is None:
            return None
        self._store.move_to_end(page_index)
        return pm

    def put(self, page_index: int, pixmap: QPixmap) -> None:
        """存入/覆盖缩略图；超容量淘汰最久未访问的。"""
        if page_index in self._store:
            self._store.move_to_end(page_index)
        self._store[page_index] = pixmap
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def invalidate(self, page_index: int) -> None:
        """移除单页缓存（旋转/删除后内容失效）。"""
        self._store.pop(page_index, None)

    def clear(self) -> None:
        """清空全部缓存（切文件时）。"""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, page_index: int) -> bool:
        return page_index in self._store
