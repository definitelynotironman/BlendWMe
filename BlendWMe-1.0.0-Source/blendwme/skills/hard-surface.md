# Hard surface: machines, vehicles, props, sci-fi panels

- Start from a clean blockout of big volumes. Get the silhouette right from the side view first.
- Booleans for cut-ins: cutter objects with display_type = 'WIRE' and hide_render = True. Boolean modifier operation 'DIFFERENCE', solver 'EXACT'. Keep cutters parented to the main object so they move together.
- Bevel modifier (ANGLE limit 30 degrees, width small, segments 3, harden normals on) after the Booleans. Add 'WEIGHTED_NORMAL' modifier last with Keep Sharp on, and shade smooth.
- Panel lines: thin inset faces extruded inward 2-5 mm. Bolts: small cylinders arrayed.
- Wheels: cylinder 24-32 sides, tyre as a torus or beveled cylinder with Solidify, rims inset.
- Paint: base colour, darker trim colour, one accent (warning stripes, lights with emission).
