# Client Chunk Loading Guide

Summary of how chunks are organized and when the client should unload/load world data as vessels move between them.

## Chunk types and scales
- System chunks: `galaxies/{g}/systems/system_{id}.chunk`, units = km (1 unit = 1 km).
- Galaxy starmap chunks: `galaxies/{g}/interstellarMap.sa2map`, units = km / 1e6 (1 unit = 1,000,000 km).
- Universe map chunk: `intergalacticMap.sa2map`, units = km / 1e9 (1 unit = 1,000,000,000 km).

## Transition rules (all distances measured from (0,0) in the current chunk)
- System → Galaxy starmap: if radius > 2.0e13 km, unload the system and load the galaxy starmap for that galaxy.
- Starmap → System: if within 1.0e10 starmap units of a system point in the starmap file, unload the starmap and load that system chunk.
- Starmap → Universe: if radius > 5.0e11 starmap units, unload the starmap and load the universe map.
- Universe → Starmap: if within 1.0e11 universe units of a galaxy point in the universe map, unload the universe map and load that galaxy’s starmap.

## Spawn placement on entry
- Always spawn 20,000,000,000,000 km from origin in the direction of approach to the entry point.
- Apply the target chunk’s scale: starmap spawn radius = 20e12 / 1e6 units; universe spawn radius = 20e12 / 1e9 units; system spawn radius = 20e12 km.
- Velocity is scaled when changing levels: dividing when going outward (system→starmap, starmap→universe), multiplying when going inward.

## Starmap/universe files (`.sa2map`)
- Stored as JSON: `{ "points": [ { "id": <int>, "name": <str>, "x": <float>, "y": <float> }, ... ] }`.
- Galaxy starmap points represent star systems in that galaxy.
- Universe map points represent galaxies.

## Client responsibilities
- When a transition condition is met, unload all objects from the old chunk and load the new chunk’s objects/state.
- Move the controlling player with the vessel into the new chunk immediately; other vessels remain in their current chunk.
- Use the new chunk’s scale for rendering and physics client-side; coordinates are always relative to that chunk and centered at (0,0).
- Region rendering: the nearest in-range region (smallest radius) applies when near planets; for deep space, regions depend on distance bands from origin.

