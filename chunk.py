import pickle
from pathlib import Path
from typing import List, Union
from gameobjects import Sun, Earth, PhysicsObject, ObjectType
import numpy as np
from physics import G


class Chunk:
    def __init__(self, galaxy: int, system: int, filepath: Union[str, Path]):
        self.galaxy = galaxy
        self.system = system
        self.path = Path(filepath)
        self.objects: List[PhysicsObject] = []
        self.ready = False

        print(f"🌌 Chunk created for galaxy {galaxy}, system {system}. File: {self.path}")
        self.deserialize_chunk()
        self.ready = True

    def is_ready(self) -> bool:
        return self.ready

    def add_object(self, obj: PhysicsObject):
        self.objects.append(obj)

    def update_objects(self, dt=1.0):
        """Simple O(n^2) n-body simulation"""
        n = len(self.objects)
        if n < 2:
            return

        pos = np.array([obj.position for obj in self.objects])  # shape (n, 2)
        vel = np.array([obj.velocity for obj in self.objects])  # shape (n, 2)
        mass = np.array([obj.mass for obj in self.objects])     # shape (n,)

        forces = np.zeros((n, 2))

        # Brute-force pairwise calculation
        for i in range(n):
            for j in range(i + 1, n):
                diff = pos[j] - pos[i]
                dist_sq = np.dot(diff, diff) + 1e-5  # avoid divide-by-zero
                dist = np.sqrt(dist_sq)
                force_mag = G * mass[i] * mass[j] / dist_sq
                direction = diff / dist

                force = force_mag * direction
                forces[i] += force
                forces[j] -= force  # Newton's third law

        # Update velocities and positions
        for i, obj in enumerate(self.objects):
            acc = forces[i] / obj.mass
            vel[i] += acc * dt
            pos[i] += vel[i] * dt
            obj.velocity = tuple(vel[i])
            obj.position = tuple(pos[i])

    def serialize_chunk(self):
        print(f"💾 Serializing chunk to {self.path}")
        with open(self.path, 'wb') as f:
            pickle.dump(self.objects, f)

    def deserialize_chunk(self):
        if not self.path.exists():
            print(f"⚠️ No chunk file found at {self.path}.")
            return

        try:
            with open(self.path, 'rb') as f:
                self.objects = pickle.load(f)
                print(f"✅ Loaded {len(self.objects)} objects from {self.path}")
        except Exception as e:
            print(f"❌ Failed to load chunk: {e}")
            self.objects = []

