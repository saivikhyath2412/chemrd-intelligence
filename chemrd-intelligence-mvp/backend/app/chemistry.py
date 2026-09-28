from __future__ import annotations

import html
import re


def _fallback_atoms(smiles: str) -> list[str]:
    # A deliberately small, dependency-free atom tokenizer used when RDKit is
    # unavailable. It is a visual fallback, not a chemistry parser.
    atoms = re.findall(r"Br|Cl|Si|[A-Z][a-z]?", smiles or "")
    return atoms[:18] or ["?"]


def structure_svg(smiles: str | None, width: int = 360, height: int = 220) -> str:
    """Return a safe inline SVG; use RDKit when installed and a visual fallback otherwise."""
    if not smiles:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 220"><text x="20" y="110" fill="#94a3b8">No structure available</text></svg>'
    try:
        from rdkit import Chem
        from rdkit.Chem import Draw

        mol = Chem.MolFromSmiles(smiles)
        if mol:
            drawer = Draw.MolDraw2DSVG(width, height)
            drawer.DrawMolecule(mol)
            drawer.FinishDrawing()
            return drawer.GetDrawingText()
    except Exception:
        pass

    atoms = _fallback_atoms(smiles)
    center_x, center_y = width / 2, height / 2
    radius = min(78, 22 + len(atoms) * 4)
    points = []
    for i, atom in enumerate(atoms):
        angle = (i / max(1, len(atoms))) * 6.28318 - 1.57
        x = center_x + radius * __import__("math").cos(angle)
        y = center_y + radius * __import__("math").sin(angle)
        points.append((x, y, atom))
    lines = []
    for i in range(len(points)):
        x1, y1, _ = points[i]
        x2, y2, _ = points[(i + 1) % len(points)]
        lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#64748b" stroke-width="2"/>')
    labels = [f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="13" font-weight="700" fill="#dbeafe">{html.escape(atom)}</text>' for x, y, atom in points]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Molecular structure fallback"><rect width="100%" height="100%" rx="18" fill="#0f172a"/>{''.join(lines)}{''.join(labels)}<text x="18" y="202" fill="#94a3b8" font-size="11">SMILES: {html.escape(smiles)}</text></svg>'''

