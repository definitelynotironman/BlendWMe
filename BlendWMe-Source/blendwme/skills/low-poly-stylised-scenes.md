# Low poly and stylised props, buildings and environments

## Look
- Chunky simple forms, slightly exaggerated proportions (bigger roofs, thicker frames, wider doors).
- Flat shading or low-angle smooth. Few faces, but every face has a purpose.
- 4-6 colour palette with one warm accent. Matte materials (roughness 0.6-0.9, metallic 0).
- Small tilt or irregularity (1-4 degrees) makes it feel hand made: slightly leaning chimney, uneven planks.

## Building recipe (house, shop, cabin)
1. Base/foundation slab a little larger than the walls (0.1-0.2 m lip), darker stone colour.
2. Walls: one box (or 4 thin boxes with Solidify). Apply scale. Give corners or trim a contrasting colour.
3. Roof: gable = triangular prism made with bmesh or a cube with the top edge merged, overhanging walls by 0.2-0.4 m on each side and at the ends. Roof thickness 0.1-0.2 m. Optional Array of plank strips for tiles.
4. Door: recessed (inset into the wall 3-8 cm) with a frame, planks via Array, a small knob.
5. Windows: frame + recessed dark glass + cross bars + sill that sticks out. Keep them aligned to a grid.
6. Chimney on the roof slope, base buried into the roof so there is no gap, cap slightly wider.
7. Steps, path, fence, plants, lamp: small stories that sell the scene.
8. Ground: a plane or a slightly irregular disc island. Put everything on it.

## Code pattern: cottage blockout with helpers
(For a complete tested build with roof tiles, door, windows and path, load example-cottage.)
```python
col = bwm.collection("Cottage")
stone, plaster, roof_m = bwm.material("Stone", (0.45, 0.43, 0.4), 0.9), bwm.material("Plaster", (0.88, 0.8, 0.66), 0.8), bwm.material("Roof_Tile", (0.62, 0.2, 0.12), 0.7)
W, Dp, H, base_h = 4.0, 3.0, 2.4, 0.25          # walls width (x), depth (y), height; base height
bwm.box("Base", (W + 0.4, Dp + 0.4, base_h), (0, 0, base_h / 2), col, stone, bevel=0.03)
bwm.box("Walls", (W, Dp, H), (0, 0, base_h + H / 2), col, plaster, bevel=0.02)   # one solid block reads cleaner than 4 thin walls
# Gable roof: ridge along X. The roof plane passes exactly through the wall tops, so there is no gap.
o, r, top = 0.35, 1.3, base_h + H              # overhang, rise above the walls, wall top height
k = r / (Dp / 2)                                # slope
x, y, eave = W / 2 + o, Dp / 2 + o, top - k * o
roof = bwm.mesh("Roof", [(-x, -y, eave), (x, -y, eave), (x, 0, top + r), (-x, 0, top + r),
                         (x, y, eave), (-x, y, eave)],
                [(0, 1, 2, 3), (3, 2, 4, 5)], collection=col, material=roof_m)
s = roof.modifiers.new("Thickness", "SOLIDIFY"); s.thickness = 0.15; s.offset = 1   # grows upward
# Gable end walls: triangles at the left and right ends that fill the wall up to the roof
for side, xx in (("Left", -W / 2), ("Right", W / 2)):
    bwm.mesh(f"Gable_{side}", [(xx, -Dp / 2, top), (xx, Dp / 2, top), (xx, 0, top + r)],
             [(0, 1, 2)], collection=col, material=plaster)
```
Note: here the ridge runs along X, so the gables face the sides. For gables facing the front (-Y), run the ridge along Y.
Door on the front (-Y) face: bwm.box("Door", (0.9, 0.08, 1.9), (0, -Dp / 2 - 0.02, base_h + 0.95), col, wood).

## Trees and plants (stylised)
- Trunk: cylinder 6-8 sides, tapered (scale top verts), brown.
- Canopy: 2-4 icospheres (subdivisions 1) of different sizes overlapping, slightly squashed, 2 greens.

## Checks
- look(mode="angles") from front and three_quarter: do roof and walls meet cleanly? Is the overhang even?
- Silhouette should read as the object even in solid grey.
