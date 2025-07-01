import pickle
from pathlib import Path
from typing import List, Union
from gameobjects import Sun, Earth, PhysicsObject, ObjectType
import numpy as np
from physics import G
from packet_types import DataGramPacketType
import struct
from session import Session

class Chunk:
    def __init__(self, galaxy: int, system: int, filepath: Union[str, Path], managed_by):
        self.galaxy = galaxy
        self.system = system
        self.path = Path(filepath)
        self.objects: List[PhysicsObject] = []
        self.id_to_object = {}
        self.ready = False
        self.manager = managed_by

        print(f"🌌 Chunk created for galaxy {galaxy}, system {system}. File: {self.path}")
        self.deserialize_chunk()
        self.ready = True

    def is_ready(self) -> bool:
        return self.ready

    def add_object(self, obj: PhysicsObject):
        self.objects.append(obj)
        self.id_to_object[obj.object_id] = obj

    def signed_to_unsigned64(self, value: int) -> int:
        return value % (1 << 64)

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
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.OBJECT_STREAM)  # u8
        chunkpacket += struct.pack('<H', len(self.objects))  # u16: number of objects

        for i, obj in enumerate(self.objects):
            acc = forces[i] / obj.mass
            vel[i] += acc * dt
            pos[i] += vel[i] * dt
            obj.velocity = tuple(vel[i])
            obj.position = tuple(pos[i])
            #Stream to players within the chunk
            obj_x, obj_y = obj.position
            obj_vx, obj_vy = obj.velocity
            chunkpacket += struct.pack(
                '<QQQQQ',                   # < = little-endian
                obj.object_id,                  # Q: uint64
                self.signed_to_unsigned64(int(obj_x)),                
                self.signed_to_unsigned64(int(obj_y)),                
                self.signed_to_unsigned64(int(obj_vx)),               
                self.signed_to_unsigned64(int(obj_vy))                
            )

        for player in self.manager.shared.players.values():
            if(player.galaxy == self.galaxy and player.system == self.system):
                session = player.session
                if session and session.udp_port and session.alive:
                    print("streaming objects to a player")
                    addr = (session.remote_ip, session.udp_port)
                    # Demeter's law doesn't apply to python and we all know it
                    self.manager.shared.udp_server.transport.sendto(chunkpacket, addr)

                    


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
                self.id_to_object = {obj.object_id: obj for obj in self.objects}
                print(f"✅ Loaded {len(self.objects)} objects from {self.path}")
        except Exception as e:
            print(f"❌ Failed to load chunk: {e}")
            self.objects = []


    def get_object_by_id(self, obj_id: int) -> Union[PhysicsObject, None]:
        return self.id_to_object.get(obj_id)

