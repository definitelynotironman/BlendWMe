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

## Trees and plants (stylised)
- Trunk: cylinder 6-8 sides, tapered (scale top verts), brown.
- Canopy: 2-4 icospheres (subdivisions 1) of different sizes overlapping, slightly squashed, 2 greens.

## Checks
- look(mode="angles") from front and three_quarter: do roof and walls meet cleanly? Is the overhang even?
- Silhouette should read as the object even in solid grey.
