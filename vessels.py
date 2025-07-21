from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Any
from physics import G
from gameobjects import PhysicsObject, GameObject, ObjectType
from enum import Enum
import math



@dataclass
class AttachedVesselComponent:
    id: int
    x: float
    y: float

@dataclass
class Vessel(PhysicsObject):
    components: List[AttachedVesselComponent] = field(default_factory=list)
    constructed_by: int = 0
    agency_id: int = 0
    shared: Any = field(default=None, repr=False, init=False)
    name : str = "Unnamed Vessel"

    def __post_init__(self):
        print("VESSEL SPAWNED")


    def calculate_mass(self, component_data_lookup: Dict[int, Dict]) -> float:
        """Sum the mass of all components using the shared data."""
        return sum(component_data_lookup.get(comp.id, {}).get("mass", 0) for comp in self.components)

    def validate_structure(self, component_data_lookup: Dict[int, Dict]) -> bool:
        """Basic structure validation — stub for now."""
        # Later, check snap points and logical connectivity
        return True
    
    def get_id(self) -> int:
        return self.object_id

def construct_vessel_from_request(shared, player, vessel_request_data) -> Vessel:
    #GET A REFERENCE TO THE COMPONENT DATA
    component_data_lookup = shared.component_data
    components = []
    total_cost = 0
    planet_id = vessel_request_data.get("planet", 2)
    print(f"Constructing vessel for planet ID: {planet_id}")
    launchpad_data = vessel_request_data.get("launchpad_data", {})
    vessel_name = vessel_request_data.get("name", "Unnamed Vessel")

    for component in vessel_request_data["vessel_data"]:
        comp_id = int(component["id"])
        placement_x = int(component["x"]) - 2500
        placement_y = int(component["y"])  - 2500 
        
        component_definition = component_data_lookup.get(comp_id)
        if component_definition is None:
            raise ValueError(f"Invalid component ID: {comp_id}")
        
        total_cost += component_definition.get("cost", 0)
        components.append(AttachedVesselComponent(id=comp_id, x=placement_x, y=placement_y))
        if player.money < total_cost:
            raise ValueError(f"Insufficient funds: cost={total_cost}, player has={player.money}")
        
        #Subtract money
        player.money -= total_cost
        print("Creating vessel")
        #Create the vessel
        center_component = components[0]
        vessel = Vessel(
            object_type=ObjectType.BASIC_VESSEL,
            components=components,
            constructed_by=player.steamID,
            agency_id=player.agency_id,
            position=(0, 0),
            velocity=(0, 0),
            mass=1000.0
        )
        vessel.shared=shared
        vessel.name = vessel_name

        # Add vessel to its agency
        agency = shared.agencies.get(player.agency_id)
        if agency is not None:
            agency.vessels.append(vessel)
            print(f"✅ Vessel {vessel.object_id} added to Agency {agency.id64}")
        else:
            print(f"⚠️ No agency found with ID {player.agency_id}, vessel not tracked.")

        chunk_key = (player.galaxy, player.system)
        chunk = shared.chunk_manager.loaded_chunks.get(chunk_key)

        if chunk is not None:
            chunk.add_object(vessel)
            print(f"Vessel {vessel.object_id} added to chunk {chunk_key}")
        else:
            print(f"⚠️ No chunk found for galaxy/system {chunk_key}, vessel not added to chunk.")



    return vessel