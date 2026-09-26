# WORKED EXAMPLE (tested): modern room interior, two walls, open sides for the viewer, bed, PC setup, closet. Copy its structure for any bedroom, office, studio or living room.

Use this when the brief is a room or interior. A "cutaway" room has walls only on the back and one
side, so the viewer looks in from the front-right. Every step below ran cleanly in Blender 4.2.

## Plan (call plan with parts like these)
1. Floor 5 x 4 m, top at z = 0
2. Back wall (+Y side) and left wall (-X side), 2.7 m high, front and right open
3. Window in the back wall, above where the desk will go
4. Bed against the back wall, on the left
5. PC setup: desk against the back wall on the right, monitor, keyboard, PC tower, chair
6. Closet (wardrobe) against the left wall
7. Rug, shelf, plant and lamp for life
8. Light: sun through the open side and a warm lamp

## Step 1: floor
```python
col = bwm.collection("Room")
wood = bwm.material("Floor_Oak", (0.55, 0.38, 0.24), 0.5)
bwm.floor("Floor", size=(5, 4), location=(0, 0, 0), thickness=0.12, collection=col, material=wood)
```

## Step 2: two walls, open on the viewer's sides
```python
col = bwm.collection("Room")
paint = bwm.material("Wall_Paint", (0.86, 0.85, 0.82), 0.9)
# walls run along the floor edges: start and end are (x, y) on the floor
bwm.wall("Wall_Back", start=(-2.5, 2.0), end=(2.5, 2.0), height=2.7, thickness=0.15, collection=col, material=paint)
bwm.wall("Wall_Left", start=(-2.5, -2.0), end=(-2.5, 2.0), height=2.7, thickness=0.15, collection=col, material=paint)
```

## Step 3: window in the back wall
```python
col = bwm.collection("Room")
bwm.window("Window", width=1.2, height=1.1, location=(1.5, 2.0, 1.65), wall="Wall_Back", collection=col,
           frame_material=(0.15, 0.15, 0.16), sill=False)
```

## Step 4: bed against the back wall
```python
col = bwm.collection("Room")
bwm.bed("Bed", size=(1.6, 2.0), location=(-1.3, 1.0, 0), against="Wall_Back", collection=col,
        blanket_material=(0.24, 0.33, 0.45))
```

## Step 5: PC setup
```python
col = bwm.collection("Room")
bwm.desk("Desk", size=(1.4, 0.7, 0.75), location=(1.5, 1.0, 0), against="Wall_Back", collection=col,
         material=(0.9, 0.9, 0.88))
bwm.monitor("Monitor", on="Desk", collection=col)
bwm.keyboard("Keyboard", on="Desk", collection=col)
bwm.pc_tower("PC_Tower", location=(2.35, 1.0, 0), against="Wall_Back", collection=col)
bwm.chair("Chair", at="Desk", collection=col, cushion_material=(0.1, 0.1, 0.12))
```

## Step 6: closet against the left wall
```python
col = bwm.collection("Room")
bwm.wardrobe("Closet", size=(1.2, 0.6, 2.1), location=(-2.0, -1.0, 0), against="Wall_Left",
             collection=col, material=(0.78, 0.66, 0.5))
```

## Step 7: rug, shelf, plant, lamp
```python
col = bwm.collection("Room")
bwm.rug("Rug", size=(2.2, 1.5), location=(0, -0.2, 0), collection=col, material=(0.62, 0.55, 0.48))
bwm.shelf("Shelf", size=(0.7, 0.3, 1.6), location=(0.05, 1.5, 0), against="Wall_Back", collection=col)
bwm.plant("Plant", location=(-2.1, 0.25, 0), height=0.9, collection=col)
bwm.lamp("Lamp", location=(2.2, -1.4, 0), height=1.6, collection=col)
```

## Step 8: light
```python
sun_data = D.lights.get("Sun") or D.lights.new("Sun", "SUN")
sun_data.energy = 3.0
sun = D.objects.get("Sun") or D.objects.new("Sun", sun_data)
if sun.name not in C.scene.collection.objects:
    C.scene.collection.objects.link(sun)
bwm.rotate(sun, (55, 0, -40))       # light comes in through the open front-right
```

## What good looks like
- Every piece stands on the floor and backs onto a wall, nothing intersects a wall.
- The open sides face the viewer (-Y front and +X right), so Front and three-quarter views see in.
- Clear zones: sleeping (bed), working (desk, chair, PC), storage (closet, shelf).
- A calm palette (light walls, warm wood) with one or two accents (blanket, plant).

## Common mistakes
- Furniture rotated the wrong way: use against="WallName" instead of guessing rot_deg.
- Monitor floating above the desk: use on="Desk". Chair facing away: use at="Desk".
- Walls on all four sides: the viewer cannot see in. Two walls make a cutaway.
- Furniture larger than real life. Real sizes: bed 1.4-1.8 x 2.0 m, desk height 0.75 m, closet 2.0-2.2 m tall.

## Teaching it (Watch / Teach)
- Block out the room with planes and cubes first (Shift+A), then furniture as simple boxes.
- Snapping (Shift+Tab, or the magnet icon in the header) puts furniture against walls quickly.
- Use Collections (M > New Collection) to keep Room, Furniture and Lights tidy.
- Point at: the Snap menu in the 3D Viewport HEADER, the Outliner for collections.
