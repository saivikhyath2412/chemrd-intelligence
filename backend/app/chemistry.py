from __future__ import annotations

from functools import lru_cache


def molecule(smiles: str | None):
    if not smiles or len(smiles) > 20000:
        return None
    from rdkit import Chem, rdBase
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles)
    if mol is None or not mol.GetNumAtoms() or any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
        return None
    return mol


def valid_smiles(smiles: str | None) -> bool:
    try:
        return molecule(smiles) is not None
    except Exception:
        return False


@lru_cache(maxsize=512)
def structure_svg(smiles: str | None, width: int = 600, height: int = 380) -> str:
    """Render the actual molecular graph without invented fallback bonds."""
    mol = molecule(smiles)
    if mol is None:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 380"><rect width="600" height="380" fill="#f8fafc"/><text x="300" y="180" text-anchor="middle" fill="#334155">No concrete structure: provide a name, CAS number or SMILES</text></svg>'
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D
    rdDepictor.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    drawer.drawOptions().padding = 0.08
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


@lru_cache(maxsize=256)
def conformer_sdf(smiles: str | None) -> bytes | None:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    mol = molecule(smiles)
    if mol is None:
        return None
    mol = Chem.AddHs(mol)
    for random_coords, seed in ((False, 0xC0FFEE), (True, 42), (True, 2718)):
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.useRandomCoords = random_coords
        params.maxIterations = 1000
        params.timeout = 12
        if AllChem.EmbedMolecule(mol, params) >= 0:
            try:
                if AllChem.MMFFHasAllMoleculeParams(mol):
                    AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
                elif AllChem.UFFHasAllMoleculeParams(mol):
                    AllChem.UFFOptimizeMolecule(mol, maxIters=500)
            except Exception:
                pass
            # Disconnected ions/fragments are laid out separately, not on top
            # of each other. This is a molecular model, not a crystal lattice.
            fragments = Chem.GetMolFrags(mol)
            if len(fragments) > 1:
                conf = mol.GetConformer()
                offset = 0.0
                for fragment in fragments:
                    points = [conf.GetAtomPosition(i) for i in fragment]
                    left, right = min(p.x for p in points), max(p.x for p in points)
                    for i, point in zip(fragment, points):
                        conf.SetAtomPosition(i, (point.x - left + offset, point.y, point.z))
                    offset += right - left + 3.0
            return (Chem.MolToMolBlock(mol) + '\n$$$$\n').encode('utf-8')
    return None
