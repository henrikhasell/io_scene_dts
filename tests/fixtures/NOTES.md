# Fixture provenance

Small representative files copied from the local game-data corpora.  The
filename prefix records the DTS/DSQ version.

From `~/Documents/Repositories/hasell-engine/t2/shapes/` (Tribes 2 game data):

| fixture | original | why |
|---|---|---|
| v15_chaingun_shot.dts | chaingun_shot.dts | oldest version read and written: keyframe-major animation, mesh index list |
| v19_turret_muzzlepoint.dts | turret_muzzlepoint.dts | smallest v19 |
| v19_weapon_chaingun_ammocasing.dts | weapon_chaingun_ammocasing.dts | v19 multi-detail |
| v19_xorg20.dts | xorg20.dts | v19 sorted mesh + translucent material |
| v21_xorg21.dts | xorg21.dts | v21 (rare) sorted + multi-detail |
| v21_weapon_energy.dts | weapon_energy.dts | multi-matframe (17 frames on 3 meshes) |
| v22_porg1.dts | porg1.dts | smallest v22 |
| v22_porg5.dts | porg5.dts | v22 multi-detail |
| v22_energy_explosion.dts | energy_explosion.dts | v22 animated + IFL materials |
| v22_station_teleport.dts | station_teleport.dts | v22 with rotation and translation animating different nodes, and matters bits naming objects it does not have |
| v22_turret_belly_barrell.dts | turret_belly_barrell.dts | v22 decal mesh |
| v23_weapon_energy_vehicle.dts | weapon_energy_vehicle.dts | smallest v23, multi-detail |
| v23_pack_upgrade_shield.dts | pack_upgrade_shield.dts | v23 animated |
| v23_bioderm_light.dts | bioderm_light.dts | v23 skinned player, decals, sequences |

From `~/Documents/Repositories/hasell-engine/files/shapes/tribes2/`:

| fixture | original | why |
|---|---|---|
| v16_borg11.dts | borg11.dts | v16: keyframe-major animation with null-mesh type words rather than a mesh index list, 8 details, sorted mesh |

From `~/Documents/Repositories/agentic-torque/mygame/` (TGE 1.5 SDK data):

| fixture | original | why |
|---|---|---|
| v18_octahedron.dts | animation-test/.../markers/octahedron.dts | smallest flat-stream (pre-v19) shape |
| v19_vehicle_air_scout_wreck.dts | animation-test/.../tribes2/vehicle_air_scout_wreck.dts | the only pre-v20 shape in any corpus with decals — the empty mesh header in front of one |
| v24_octahedron.dts | starter.fps/.../markers/octahedron.dts | smallest v24 |
| v24_woodDoor01.dts | starter.fps/.../door/woodDoor01.dts | v24 animated |
| v24_shrub.dts | starter.fps/.../trees/shrub.dts | v24 translucent material |
| v24_ammo.dts | starter.fps/.../crossbow/ammo.dts | v24 multi-detail |
| v24_w_sqknest.dts | animation-test/.../hl/w_sqknest/w_sqknest.dts | v24 skinned (mdlExportDts output) |
| v24_player_root.dsq | animation-test/.../player/player_root.dsq | v24 DSQ |
| v24_player_dance.dsq | animation-test/.../player/player_dance.dsq | v24 DSQ, large |
| v22_player_back.dsq | animation-test/.../tutorial-player/player_back.dsq | v22 DSQ (old layout) |
