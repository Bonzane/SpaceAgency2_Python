# Terrain Generation Plan (WIP)

This document tracks the current plan for server-authoritative terrain rules with efficient client-side rendering. It is intentionally incomplete and will evolve.

## Goals
- Efficient multiplayer: minimal bandwidth, deterministic or server-verified rules.
- Anti-cheat: server validates movement and interactions.
- Per-planet flexibility: each planet can have its own biome list.

## Current Direction
- Client renders terrain locally.
- Server is authoritative over *rules* (movement, borders, collisions).
- Server provides a compact, per-planet **mask** describing biome indices and blocked/water areas.
- Mask uses biome indices into a **planet-specific biome table** (Earth can have many biomes; other planets can have fewer).
- Terrain coordinate system should place the origin near the **top-center** of the map region so players spawn near the top and explore downward/sideways.

## Data Model (Draft)
- Planet Biome Table (per planet)
  - `biomes[]`: name, color(s), rule metadata (ranges, thresholds).
- Terrain Mask (per terrain chunk, per planet)
  - Resolution: fixed grid (e.g., 64x64 or 128x128).
  - Fields:
    - `biome_id` (u8) → index into that planet's biome list.
    - `blocked` (bit) → cannot traverse.
    - `water` (bit) → water region (optional).

## Networking (Draft)
- New packet(s) to request and receive the mask for a planet/terrain chunk.
- Client caches mask by planet_id + mask_hash.
- Server only sends mask when hash changes.

## Server Responsibilities (Draft)
- Generate/store the terrain mask for each planet.
- Validate client movement and interactions against the mask.
- Provide mask hashes for cache validation.

## Client Responsibilities (Draft)
- Request mask on entering terrain.
- Cache masks and reuse when hash matches.
- Use mask to enforce local movement limits (but server remains authoritative).
- Render visuals using local generation; rely on mask only for rules.

## Open Questions
- Mask resolution and coordinate mapping.
- Terrain chunk tiling strategy for large surfaces.
- Biome rule schema for procedural generation.
