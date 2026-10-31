from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class Snapshot:
    id: str
    title: str
    url: str | None
    fingerprint: tuple
    elements: dict = field(default_factory=dict)
    stale: bool = False


class ElementStore:
    """Mantiene referencias UIA privadas; el Brain solo recibe e1, e2..."""
    def __init__(self):
        self._snapshots = {}
        self._last_id = None

    def create(self, title, url, fingerprint, elements):
        snapshot_id = f"s{uuid4().hex[:8]}"
        self._snapshots[snapshot_id] = Snapshot(snapshot_id, title, url, tuple(fingerprint), elements)
        self._last_id = snapshot_id
        return snapshot_id

    def get(self, snapshot_id=None):
        return self._snapshots.get(snapshot_id or self._last_id)

    def resolve(self, element_id, snapshot_id=None):
        snapshot = self.get(snapshot_id)
        if not snapshot:
            return None, None, "snapshot_not_found"
        if snapshot.stale:
            return snapshot, None, "stale_element"
        item = snapshot.elements.get(element_id)
        return snapshot, item, None if item else "element_not_found"

    def mark_stale(self, snapshot_id=None):
        snapshot = self.get(snapshot_id)
        if snapshot:
            snapshot.stale = True
