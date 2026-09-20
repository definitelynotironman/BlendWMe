"""Reproducible extension + source archives, excluding caches and transient files."""
from pathlib import Path
import zipfile
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'deliverables';OUT.mkdir(exist_ok=True)

def allowed(p):
    return not any(x in ('__pycache__','.git','dist') for x in p.parts) and p.suffix not in ('.pyc','.blend1') and p.name not in ('phase','root-test.png','secret-save.blend','SHA256.json')

with zipfile.ZipFile(OUT/'blendwme-0.2.3.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted((ROOT/'extension').rglob('*')):
        if p.is_file() and allowed(p):z.write(p,p.relative_to(ROOT/'extension'))
    z.write(ROOT/'LICENSE','LICENSE')
with zipfile.ZipFile(OUT/'BlendWMe-0.2.3-Source.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(ROOT.rglob('*')):
        if p.is_file() and allowed(p):z.write(p,Path('BlendWMe')/p.relative_to(ROOT))
    z.write(OUT/'blendwme-0.2.3.zip','BlendWMe/dist/blendwme-0.2.3.zip')
hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.zip')}
(OUT/'SHA256.json').write_text(json.dumps(hashes,indent=2))
for p in sorted(OUT.glob('*.zip')):print(p,p.stat().st_size)
