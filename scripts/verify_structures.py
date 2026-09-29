"""Read-only public-provider verification; writes only a disposable cache."""
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ['CHEMRD_STRUCTURE_CACHE'] = str(root / '.smoke-current' / 'structures')

from rdkit import Chem
from backend.app.live_research import chemical_identity_lookup
from backend.app.structure_service import structure_asset

names = sys.argv[1:] or ['Capsaicin', 'paclitaxel', 'vitamin B12', 'aspirin', 'sodium hydroxide', 'sodium chloride', 'caffeine', 'sodium hydrogen carbonate']
failed = []
for name in names:
    try:
        record, error = chemical_identity_lookup(name)
        if not record:
            raise ValueError(error)
        svg, _, _ = structure_asset('2d', record['smiles'], name)
        sdf, _, source = structure_asset('3d', record['smiles'], name)
        mol = Chem.MolFromMolBlock(sdf.decode(), removeHs=False)
        assert mol and mol.GetConformer().Is3D() and b'<svg' in svg
        assert Chem.MolToSmiles(Chem.RemoveHs(mol)) == Chem.MolToSmiles(Chem.MolFromSmiles(record['smiles']))
        print(json.dumps({'query': name, 'resolved_name': record['name'], 'formula': record['formula'], 'source': record['source']['publisher'], '3d_atoms': mol.GetNumAtoms(), 'coordinates': source}), flush=True)
    except Exception as exc:
        failed.append(name)
        print(json.dumps({'query': name, 'error': str(exc)}), flush=True)
if failed:
    raise SystemExit(1)
