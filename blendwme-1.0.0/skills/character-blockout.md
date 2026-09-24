# Stylised character: blockout, refine, pose (cartoon people, creatures, mascots)

## Proportions (stylised)
- Cartoon: head 1/3 to 1/4 of height. Big hands and feet. Simple torso shape (bean, pear or inverted triangle).
- Realistic adult: 7.5 heads tall. Shoulders about 2 head widths. Elbows at the waist. Wrists at the crotch.
- Decide a shape language: round = friendly, square = strong, triangle = sharp/villain.

## Blockout (keep it all editable)
1. Make a collection "Character_<Name>".
2. Torso: UV sphere or cube with Subdivision, scaled into the main shape. Mirror modifier on X.
3. Head: sphere. Neck: short cylinder hidden partly inside head and torso.
4. Limbs: tapered cylinders or curves with bevel (bpy.data.curves with bevel_depth) for arms/legs. Joints overlap slightly so there are no gaps.
5. Hands: palm block + 3-4 capsule fingers (cartoon). Feet: rounded boxes, longer than you think.
6. Face: eye spheres (white + dark pupil), brows as thin bent cubes, nose a small sphere, mouth a curve or a thin dark torus segment.
7. Parent all parts to the torso. Name them Body, Head, Arm_L, Arm_R...

## Refine
- Merge forms when the blockout is approved: join parts and add a Remesh (voxel) + Subdivision, or keep separate parts for a "toy" look.
- Add secondary shapes: clothes as Solidify shells slightly bigger than the body, hair as stretched spheres/curves.

## Pose and expression
- Contrapposto: tilt hips one way, shoulders the other. Weight on one leg.
- Expression: brows and mouth do most of the work. Mischievous = one brow up, one down, asymmetric smile.
- For posing, add an armature (see rigging-basics) or rotate parts around joint pivots (set origin at the joint first).

## Check with look(mode="angles", views=["front","right","three_quarter"]) and compare_reference when a reference exists.
