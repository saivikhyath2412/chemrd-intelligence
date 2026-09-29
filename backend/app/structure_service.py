"""Shared structure recovery and persistent public-data cache for all views."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from .chemistry import conformer_sdf, molecule, structure_svg, valid_smiles


def cache_dir() -> Path:
    root = Path(os.getenv('CHEMRD_STRUCTURE_CACHE') or Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'ChemRD-Intelligence' / 'structure-cache')
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # An unwritable cache must not prevent structure generation.
    return root


def resolve_smiles(smiles: str | None, name: str | None) -> str | None:
    if valid_smiles(smiles):
        return smiles
    if valid_smiles(name):
        return name
    if name:
        from .live_research import chemical_identity_lookup
        record, _ = chemical_identity_lookup(name)
        if record and valid_smiles(record.get('smiles')):
            return record['smiles']
    return None


def rcsb_conformer(smiles: str) -> bytes | None:
    """Recover CCD ideal coordinates by exact InChIKey, never similarity."""
    from rdkit import Chem, rdBase
    from .live_research import _fetch_json
    with rdBase.BlockLogs():
        key = Chem.MolToInchiKey(molecule(smiles))
    if not key:
        return None
    query = {'query': {'type': 'terminal', 'service': 'text_chem', 'parameters': {'attribute': 'rcsb_chem_comp_descriptor.InChIKey', 'operator': 'exact_match', 'value': key}}, 'return_type': 'mol_definition', 'request_options': {'paginate': {'start': 0, 'rows': 3}}}
    result, _ = _fetch_json('https://search.rcsb.org/rcsbsearch/v2/query?json=' + quote(json.dumps(query), safe=''))
    for entry in (result or {}).get('result_set', []):
        identifier = str(entry.get('identifier', ''))
        if not identifier.isalnum():
            continue
        try:
            url = f'https://files.rcsb.org/ligands/download/{identifier}_ideal.sdf'
            with urlopen(Request(url, headers={'User-Agent': 'ChemRD/0.3'}), timeout=15) as response:
                payload = response.read()
            with rdBase.BlockLogs():
                mol = Chem.MolFromMolBlock(payload.decode(), removeHs=False)
                if mol and mol.GetNumConformers() and mol.GetConformer().Is3D() and Chem.MolToInchiKey(mol) == key:
                    return payload
        except Exception:
            continue
    return None


def pubchem_conformer(smiles: str) -> bytes | None:
    """Fetch PubChem 3D coordinates only when its structure key is an exact match."""
    from rdkit import Chem, rdBase
    reference = molecule(smiles)
    if reference is None:
        return None
    with rdBase.BlockLogs():
        key = Chem.MolToInchiKey(reference)
    if not key:
        return None
    url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{quote(smiles, safe="")}/record/SDF?record_type=3d'
    try:
        with urlopen(Request(url, headers={'User-Agent': 'ChemRD/0.3', 'Accept': 'chemical/x-mdl-sdfile'}), timeout=18) as response:
            payload = response.read()
        with rdBase.BlockLogs():
            conformer = Chem.MolFromMolBlock(payload.decode('utf-8', errors='replace'), removeHs=False)
            if conformer and conformer.GetNumConformers() and conformer.GetConformer().Is3D() and Chem.MolToInchiKey(Chem.RemoveHs(conformer)) == key:
                return payload if b'$$$$' in payload else payload + b'\n$$$$\n'
    except Exception:
        pass
    return None


def structure_asset(asset: str, smiles: str | None, name: str | None):
    from rdkit import Chem
    resolved = resolve_smiles(smiles, name)
    if not resolved:
        raise ValueError('No verified molecular structure was found. Try the full chemical name, CAS number, or SMILES.')
    canonical = Chem.MolToSmiles(molecule(resolved), isomericSmiles=True)
    key = hashlib.sha256(('v2:' + canonical + ':' + asset).encode()).hexdigest()
    target = cache_dir() / (key + ('.svg' if asset == '2d' else '.sdf'))
    try:
        payload = target.read_bytes()
        if (asset == '2d' and b'<svg' in payload) or (asset == '3d' and b'M  END' in payload):
            return payload, 'image/svg+xml' if asset == '2d' else 'chemical/x-mdl-sdfile', 'Cached verified structure (calculated)'
    except OSError:
        pass
    if asset == '2d':
        payload = structure_svg(canonical).encode()
        source = 'Local RDKit 2D molecular depiction'
    else:
        payload = conformer_sdf(canonical)
        source = 'RDKit ETKDG 3D conformer (calculated)'
        if not payload:
            payload = pubchem_conformer(canonical)
            source = 'PubChem 3D coordinates (exact InChIKey verified)'
        if not payload:
            payload = rcsb_conformer(canonical)
            source = 'RCSB PDB CCD ideal 3D coordinates (exact identity checked)'
        if not payload:
            # An independent provider is tried only when local embedding fails.
            url = f'https://cactus.nci.nih.gov/chemical/structure/{quote(canonical, safe="")}/file?format=sdf&get3d=true'
            try:
                with urlopen(Request(url, headers={'User-Agent': 'ChemRD/0.3'}), timeout=15) as response:
                    external = response.read()
                mol = Chem.MolFromMolBlock(external.decode(), removeHs=False)
                reference = Chem.RemoveHs(molecule(canonical))
                if mol is not None and mol.GetNumConformers() and mol.GetConformer().Is3D() and Chem.MolToSmiles(Chem.RemoveHs(mol)) == Chem.MolToSmiles(reference):
                    payload = external if b'$$$$' in external else external + b'\n$$$$\n'
                    source = 'NCI/CADD 3D conformer (identity checked)'
            except Exception:
                pass
        if not payload:
            raise ValueError('A 3D conformer could not be generated for this structure. The verified 2D structure remains available.')
    # Write atomically: concurrent views must never read a half-written SDF.
    import uuid
    temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(payload)
        temporary.replace(target)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return payload, 'image/svg+xml' if asset == '2d' else 'chemical/x-mdl-sdfile', source
