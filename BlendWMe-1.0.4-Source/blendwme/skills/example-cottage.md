# WORKED EXAMPLE (tested): stylised low poly cottage, red tiled roof, chimney, door, 4 windows, stone base, path. Copy its structure for any house, cabin, shop or hut.

Use this when the brief is a small building. Follow the same plan, change sizes, colours and
counts to the brief. Every step below ran cleanly in Blender 4.2 as one run_python call each.

## Plan (call plan with parts like these)
1. Ground: grass disc 10 m across, top at z = 0
2. Stone base 4.6 x 3.6 x 0.3 m on the ground
3. Walls 4 x 3 x 2.4 m on the base (one solid block reads clean in low poly)
4. Gable roof, ridge along X, 1.4 m rise, 0.35 m overhang, red tiles, on the walls
5. Chimney through the right roof slope, with a cap
6. Front door, centred on the front (-Y) wall, at base height
7. Four windows: two on the front, one on each side wall
8. Stone path from the door to the edge of the ground
9. Warm sun light and soft sky

## Step 1: ground
```python
col = bwm.collection("Cottage")
grass = bwm.material("Grass", (0.33, 0.55, 0.22), 0.9)
bwm.cylinder("Ground", 5.0, 0.2, (0, 0, -0.1), segments=12, collection=col, material=grass, smooth=False)
```

## Step 2: stone base
```python
col = bwm.collection("Cottage")
stone = bwm.material("Stone", (0.47, 0.45, 0.42), 0.9)
bwm.box("Base", (4.6, 3.6, 0.3), (0, 0, 0.15), col, stone, bevel=0.04)
```

## Step 3: walls
```python
col = bwm.collection("Cottage")
plaster = bwm.material("Plaster", (0.93, 0.84, 0.66), 0.85)
base_top = bwm.top("Base")
walls = bwm.box("Walls", (4.0, 3.0, 2.4), (0, 0, base_top + 1.2), col, plaster, bevel=0.02)
print("wall top", round(bwm.top(walls), 2))
```

## Step 4: gable roof with red tiles
```python
col = bwm.collection("Cottage")
tiles = bwm.material("Roof_Red", (0.68, 0.16, 0.1), 0.7)
plaster = bwm.material("Plaster", (0.93, 0.84, 0.66), 0.85)
wall_top = bwm.top("Walls")
# location z is the TOP of the walls: the roof's underside meets the wall edges exactly.
bwm.gable_roof("Roof", width=4.0, depth=3.0, rise=1.4, location=(0, 0, wall_top), overhang=0.35,
               thickness=0.15, collection=col, material=tiles, gable_material=plaster, tiles=6)
```

## Step 5: chimney with cap
```python
col = bwm.collection("Cottage")
brick = bwm.material("Brick", (0.55, 0.27, 0.2), 0.85)
ch = bwm.box("Chimney", (0.5, 0.5, 1.3), (1.1, 0.7, 6), col, brick, bevel=0.02)
bwm.place_on(ch, "Roof", sink=True)          # base buried in the slope: no gap anywhere
cap = bwm.box("Chimney_Cap", (0.62, 0.62, 0.1), (1.1, 0.7, 6), col, "Stone", bevel=0.01)
bwm.place_on(cap, ch)
bwm.parent(cap, ch)
```

## Step 6: front door
```python
col = bwm.collection("Cottage")
# location = bottom centre. wall= snaps it onto the nearest face and cuts the opening.
bwm.door("Door", width=0.95, height=1.95, location=(0, -1.5, bwm.top("Base")), wall="Walls",
         collection=col, material=(0.45, 0.26, 0.13))
```

## Step 7: four windows
```python
col = bwm.collection("Cottage")
spots = [("Window_Front_L", (-1.25, -1.5, 1.75)), ("Window_Front_R", (1.25, -1.5, 1.75)),
         ("Window_Left", (-2.0, 0, 1.75)), ("Window_Right", (2.0, 0, 1.75))]
for name, where in spots:
    bwm.window(name, width=0.8, height=0.95, location=where, wall="Walls", collection=col,
               frame_material=(0.95, 0.93, 0.88))
```

## Step 8: stone path
```python
col = bwm.collection("Cottage")
bwm.path("Path", [(0, -1.95), (0.25, -3.2), (-0.15, -4.6)], width=0.75, stones=True, collection=col)
```

## Step 9: light and sky
```python
sun_data = D.lights.get("Sun") or D.lights.new("Sun", "SUN")
sun_data.energy = 3.5
sun_data.color = (1.0, 0.93, 0.8)
sun = D.objects.get("Sun") or D.objects.new("Sun", sun_data)
if sun.name not in C.scene.collection.objects:
    C.scene.collection.objects.link(sun)
bwm.rotate(sun, (50, 10, 35))
world = C.scene.world or D.worlds.new("World")
C.scene.world = world
world.use_nodes = True
bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
bg.inputs["Color"].default_value = (0.62, 0.74, 0.9, 1.0)
bg.inputs["Strength"].default_value = 0.8
```

## What good looks like
- The house sits on the base, the base on the ground: no floating parts, no gaps.
- The roof overhangs every wall by about 10% and its ridge runs along the long side.
- The chimney goes INTO the roof slope and is taller than the ridge.
- The door is on the front (-Y) at base height. Windows line up on one height.
- 4-6 warm colours: cream walls, red roof, brown door, grey stone, green grass.

## Common mistakes
- Roof z at the middle of the wall instead of the wall top: use bwm.top("Walls").
- Windows floating in front of the wall: always pass wall="Walls".
- Chimney resting on the ridge (gap under one side): use place_on(..., sink=True).
- Re-creating a part with a new name each try. Keep names; re-running replaces your old version.

## Teaching it (Watch / Teach)
- Blockout first: Shift+A > Mesh > Cube for walls, S to scale, G Z to lift. Explain big shapes before detail.
- Roof: Edit Mode, select the top edges, merge (M > At Center) for a quick gable; or extrude the top face and scale.
- Windows and doors: Boolean modifier (Properties > Modifiers > Add Modifier > Generate > Boolean) with a cutter box.
- Point at: the Modifiers tab (Properties editor, NAVIGATION_BAR) when adding the Boolean; the Outliner to show the Cottage collection.
