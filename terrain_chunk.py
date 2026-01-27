import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

TERRAIN_CHUNK_VERSION = 1


@dataclass
class TerrainEntity:
    entity_id: int
    kind: str
    x: float = 0.0
    y: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": int(self.entity_id),
            "kind": str(self.kind),
            "x": float(self.x),
            "y": float(self.y),
            "data": self.data,
        }

    @classmethod
    def from_json(cls, payload: Dict[str, Any]) -> "TerrainEntity":
        if not isinstance(payload, dict):
            return cls(entity_id=0, kind="unknown")
        return cls(
            entity_id=int(payload.get("id", 0)),
            kind=str(payload.get("kind", "unknown")),
            x=float(payload.get("x", 0.0)),
            y=float(payload.get("y", 0.0)),
            data=dict(payload.get("data", {}) or {}),
        )


class TerrainChunk:
    """Planet surface chunk; shared_object_ids remain in the parent system chunk."""
    def __init__(
        self,
        galaxy: int,
        system: int,
        planet_id: int,
        filepath: Union[str, Path],
        planet_name: str = "",
        terrain_data: Optional[Dict[str, Any]] = None,
    ):
        self.galaxy = int(galaxy)
        self.system = int(system)
        self.planet_id = int(planet_id)
        self.planet_name = str(planet_name)
        self.path = Path(filepath)

        self.entities: List[TerrainEntity] = []
        self.shared_object_ids: List[int] = []
        self.terrain: Dict[str, Any] = dict(terrain_data or {})
        self.ready = False

        self.deserialize()
        self.ready = True

    def is_ready(self) -> bool:
        return self.ready

    def add_entity(self, entity: TerrainEntity) -> None:
        self.entities.append(entity)

    def add_shared_object_id(self, object_id: int) -> None:
        oid = int(object_id)
        if oid not in self.shared_object_ids:
            self.shared_object_ids.append(oid)

    def remove_shared_object_id(self, object_id: int) -> None:
        oid = int(object_id)
        if oid in self.shared_object_ids:
            self.shared_object_ids.remove(oid)

    def _payload(self, entities: Optional[List[TerrainEntity]] = None) -> Dict[str, Any]:
        use_entities = self.entities if entities is None else entities
        return {
            "version": TERRAIN_CHUNK_VERSION,
            "galaxy": int(self.galaxy),
            "system": int(self.system),
            "planet_id": int(self.planet_id),
            "planet_name": str(self.planet_name),
            "terrain": self.terrain,
            "shared_object_ids": [int(x) for x in self.shared_object_ids],
            "entities": [e.to_json() for e in use_entities],
        }

    def to_json_bytes(self) -> bytes:
        payload = self._payload()
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def to_json_bytes_with_entities(self, entities: List[TerrainEntity]) -> bytes:
        payload = self._payload(entities=entities)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def hash_from_bytes(self, payload_bytes: bytes) -> int:
        digest = hashlib.blake2b(payload_bytes, digest_size=8).digest()
        return int.from_bytes(digest, "little")

    def terrain_hash(self) -> int:
        return self.hash_from_bytes(self.to_json_bytes())

    def serialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = self._payload()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def deserialize(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"WARN: Failed to load terrain chunk {self.path}: {e}")
            return

        try:
            self.galaxy = int(data.get("galaxy", self.galaxy))
            self.system = int(data.get("system", self.system))
            self.planet_id = int(data.get("planet_id", self.planet_id))
            self.planet_name = str(data.get("planet_name", self.planet_name))

            terrain = data.get("terrain", None)
            if isinstance(terrain, dict):
                self.terrain = terrain

            shared = data.get("shared_object_ids", []) or []
            if isinstance(shared, list):
                self.shared_object_ids = [int(x) for x in shared]

            entities_raw = data.get("entities", []) or []
            if isinstance(entities_raw, list):
                self.entities = [TerrainEntity.from_json(e) for e in entities_raw]
        except Exception as e:
            print(f"WARN: Terrain chunk {self.path} had invalid data: {e}")
