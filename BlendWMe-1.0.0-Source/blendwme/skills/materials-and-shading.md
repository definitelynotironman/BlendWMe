# Materials and shading (Blender 4.2 Principled BSDF)

## Quick materials
Use bwm.material(name, (r,g,b), roughness, metallic). Colours are linear 0..1. Typical stylised values:
- Cream wall (0.85, 0.78, 0.62), terracotta roof (0.62, 0.22, 0.12), dark wood (0.25, 0.13, 0.06), light wood (0.55, 0.36, 0.2)
- Grass (0.22, 0.45, 0.12), stone (0.45, 0.45, 0.43), glass dark blue (0.05, 0.08, 0.12) roughness 0.1
- Metal: metallic 1, roughness 0.3-0.5. Plastic: roughness 0.4. Skin stylised: (0.8, 0.52, 0.4) + Subsurface Weight 0.1.

## Node work (when needed)
```python
mat = bwm.material("Brick", (0.5, 0.2, 0.1))
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
brick = nt.nodes.new('ShaderNodeTexBrick'); brick.location = (-400, 200)
nt.links.new(brick.outputs['Color'], bsdf.inputs['Base Color'])
```
- 4.2 input names: "Base Color", "Metallic", "Roughness", "IOR", "Alpha", "Normal", "Subsurface Weight", "Specular IOR Level", "Coat Weight", "Sheen Weight", "Emission Color", "Emission Strength".
- Noise variation: ShaderNodeTexNoise -> ShaderNodeValToRGB (color ramp) -> Base Color, mixing two close colours.
- Bump: ShaderNodeBump fed by a texture -> bsdf.inputs['Normal'].
- Always find nodes by type, never by name.

## Seeing it
- Solid view uses mat.diffuse_color (bwm.material sets it). Material Preview uses the node tree.
- look(shading="material") to judge colour.

## Mistakes
- Pure white (1,1,1) or pure black. Use 0.9 and 0.03 at most.
- Too many saturated colours. Keep one accent.
