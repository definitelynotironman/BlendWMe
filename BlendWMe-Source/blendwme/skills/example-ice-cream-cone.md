# WORKED EXAMPLE (tested): ice cream cone with waffle pattern, two scoops, drips and a cherry. Copy its structure for any small stylised food or prop.

Use this for small props built from a few round forms. Every step below ran cleanly in Blender 4.2.
Scale is real: a cone is about 12 cm tall. The prop stands on a small plate so it has a ground.

## Plan (call plan with parts like these)
1. Waffle cone, point down, 12 cm tall, 3.5 cm radius at the top
2. Waffle pattern on the cone and a rolled rim
3. First scoop (strawberry) sitting in the rim, with a drip ring
4. Second scoop (vanilla) on top of the first
5. Cherry with a stem on top
6. Display plate and light

## Step 1: waffle cone
```python
col = bwm.collection("IceCream")
waffle = bwm.material("Waffle", (0.82, 0.56, 0.27), 0.75)
# lathe: (radius, height) from bottom to top. Rings along the height make the waffle grid possible.
profile = [(0.0, 0.0), (0.006, 0.02), (0.012, 0.04), (0.018, 0.06), (0.024, 0.08), (0.03, 0.10), (0.035, 0.12)]
bwm.lathe("Cone", profile, location=(0, 0, 0.01), segments=16, collection=col, material=waffle, smooth=False)
```

## Step 2: waffle pattern and rim
```python
col = bwm.collection("IceCream")
dark = bwm.material("Waffle_Dark", (0.6, 0.36, 0.15), 0.8)
grid = bwm.duplicate("Cone", "Cone_Waffle")
grid.scale = (1.03, 1.03, 1.0)
wf = grid.modifiers.new("Wireframe", "WIREFRAME")
wf.thickness = 0.0025
bwm.assign(grid, dark)
bwm.torus("Cone_Rim", major_radius=0.035, minor_radius=0.005, location=(0, 0, 0.13), segments=24,
          collection=col, material="Waffle")
```

## Step 3: first scoop with a drip ring
```python
col = bwm.collection("IceCream")
pink = bwm.material("Strawberry", (0.95, 0.55, 0.62), 0.45)
s1 = bwm.sphere("Scoop_1", (0.042, 0.042, 0.036), (0, 0, 0.3), collection=col, material=pink)
bwm.place_on(s1, "Cone_Rim", gap=-0.012)          # sits IN the rim, slightly sunk
z = bwm.bounds(s1).min.z + 0.012
drip = bwm.torus("Drip_1", major_radius=0.04, minor_radius=0.007, location=(0, 0, z), segments=20,
                 collection=col, material=pink)
drip.scale = (1.0, 1.0, 0.7)
```

## Step 4: second scoop
```python
col = bwm.collection("IceCream")
vanilla = bwm.material("Vanilla", (0.98, 0.93, 0.78), 0.45)
s2 = bwm.sphere("Scoop_2", (0.036, 0.036, 0.031), (0, 0, 0.5), collection=col, material=vanilla)
bwm.place_on(s2, "Scoop_1", gap=-0.014)
```

## Step 5: cherry on top
```python
col = bwm.collection("IceCream")
red = bwm.material("Cherry", (0.75, 0.03, 0.05), 0.25)
stem_mat = bwm.material("Stem", (0.25, 0.35, 0.12), 0.6)
cherry = bwm.sphere("Cherry", 0.011, (0, 0, 0.6), collection=col, material=red)
bwm.place_on(cherry, "Scoop_2", gap=-0.003)
top = bwm.top(cherry)
bwm.tube("Cherry_Stem", [(0, 0, top - 0.004), (0.003, 0, top + 0.01), (0.01, 0, top + 0.02)], 0.0012,
         segments=6, collection=col, material=stem_mat)
```

## Step 6: plate and light
```python
col = bwm.collection("IceCream")
plate = bwm.material("Plate", (0.92, 0.92, 0.95), 0.3)
bwm.cylinder("Plate", 0.08, 0.01, (0, 0, 0.005), segments=32, collection=col, material=plate)
key = D.lights.get("Key") or D.lights.new("Key", "AREA")
key.energy = 20
key.size = 0.3
lamp = D.objects.get("Key") or D.objects.new("Key", key)
if lamp.name not in C.scene.collection.objects:
    C.scene.collection.objects.link(lamp)
lamp.location = (0.25, -0.25, 0.35)
bwm.rotate(lamp, (45, 0, 45))
```

## What good looks like
- A clear silhouette: narrow point, wide rim, round scoops stacked slightly off-perfect.
- Scoops sink into the rim and each other a little (no floating balls, no visible gaps).
- Warm waffle browns against cool or pastel ice cream, one strong accent (the cherry).
- Real scale (about 12 cm cone). Frame it with the look tool to judge it, not the default view.

## Common mistakes
- A cone pointing up: the tip of an ice cream cone points DOWN (lathe from radius 0 at the bottom, or cone(..., axis='-Z')).
- Scoops floating above the cone: use place_on with a small negative gap.
- A 1 m ice cream: keep real sizes, or say clearly that it is a giant display prop.

## Teaching it (Watch / Teach)
- Spin (lathe) a profile: draw half the outline, then the Screw modifier or Alt+E > Spin in Edit Mode.
- Scoops: UV sphere (Shift+A > Mesh > UV Sphere), S Z to squash, Ctrl+2 for Subdivision.
- Waffle grid: Wireframe modifier on a copy (Properties > Modifiers > Generate > Wireframe).
- Point at: the Modifiers tab (Properties editor NAVIGATION_BAR), the Add menu in the 3D Viewport HEADER.
