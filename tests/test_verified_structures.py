from urllib.parse import parse_qs, urlparse

import pytest
from rdkit import Chem

from backend.app import live_research as research
from backend.app.chemistry import structure_svg, valid_smiles
from backend.app.structure_service import structure_asset

CAPSAICIN = 'COc1cc(CNC(=O)CCCC/C=C/C(C)C)ccc1O'


def test_chebi_uses_term_and_rejects_unrelated_ontology_hit(monkeypatch):
    seen = []

    def fetch(url):
        seen.append(url)
        return {'results': [{'_source': {'id': 'CHEBI:13193', 'name': 'hydrogen acceptor', 'smiles': '*'}}]}, None

    monkeypatch.setattr(research, '_fetch_json', fetch)
    record, error, _ = research._chebi_lookup('Capsaicin')
    assert record is None
    assert error == 'No ChEBI match'
    assert parse_qs(urlparse(seen[0]).query)['term'] == ['Capsaicin']
    assert len(seen) == 1


def test_class_entries_with_exact_names_still_cannot_become_molecules(monkeypatch):
    def fetch(url):
        if 'es_search' in url:
            return {'results': [{'_source': {'id': 'CHEBI:13193', 'name': 'hydrogen acceptor'}}]}, None
        return {'name': 'hydrogen acceptor', 'default_structure': {'smiles': '*'}}, None
    monkeypatch.setattr(research, '_fetch_json', fetch)
    assert research._chebi_lookup('hydrogen acceptor')[0] is None
    assert not valid_smiles('*')
    assert not valid_smiles('CC(*)O')


def test_pubchem_fallback_requires_an_exact_name_match_and_valid_structure(monkeypatch):
    def fetch(url):
        if '/property/' in url:
            return {'PropertyTable': {'Properties': [{
                'CID': 1548943, 'Title': 'Capsaicin', 'IUPACName': 'capsaicin',
                'MolecularFormula': 'C18H27NO3', 'MolecularWeight': '305.41',
                'SMILES': CAPSAICIN, 'InChIKey': 'YKPUWZUDDOIDPM-SOFGYWHQSA-N',
            }]}}, None
        return {'InformationList': {'Information': [{'CID': 1548943, 'Synonym': ['Capsaicin', '404-86-4']}]}}, None

    monkeypatch.setattr(research, '_fetch_json', fetch)
    record, error, _ = research._pubchem_lookup('Capsaicin')
    assert error is None
    assert record['pubchem_cid'] == 1548943
    assert record['smiles'] == CAPSAICIN
    assert record['cas_numbers'] == ['404-86-4']

    def unrelated(url):
        if '/property/' in url:
            return {'PropertyTable': {'Properties': [{'CID': 1, 'Title': 'Hydrogen acceptor', 'SMILES': '*'}]}}, None
        return {'InformationList': {'Information': [{'CID': 1, 'Synonym': ['Hydrogen acceptor']}]}}, None

    monkeypatch.setattr(research, '_fetch_json', unrelated)
    record, error, _ = research._pubchem_lookup('Capsaicin')
    assert record is None
    assert error == 'No exact PubChem compound match'


@pytest.mark.parametrize('smiles', [CAPSAICIN, 'CCO', 'Oc1ccccc1', '[Na+].[Cl-]'])
def test_offline_2d_3d_assets_have_real_atoms_and_cache(monkeypatch, smiles):
    monkeypatch.setattr('backend.app.structure_service.urlopen', lambda *_args, **_kw: pytest.fail('Local coordinates must not need the internet'))
    svg, media, _ = structure_asset('2d', smiles, None)
    assert b'<svg' in svg and b'<path' in svg
    assert media == 'image/svg+xml'
    sdf, media, _ = structure_asset('3d', smiles, None)
    mol = Chem.MolFromMolBlock(sdf.decode(), removeHs=False)
    assert mol is not None and mol.GetNumAtoms() >= Chem.MolFromSmiles(smiles).GetNumAtoms()
    assert mol.GetConformer().Is3D()
    assert Chem.MolToSmiles(Chem.RemoveHs(mol)) == Chem.MolToSmiles(Chem.MolFromSmiles(smiles))
    assert structure_asset('3d', smiles, None)[0] == sdf
    if '.' in smiles:
        assert mol.GetConformer().GetAtomPosition(0).Distance(mol.GetConformer().GetAtomPosition(1)) >= 3


def test_multiword_names_and_supplied_smiles_are_supported(monkeypatch):
    assert research._entity_candidate('sodium hydrogen carbonate') == 'sodium hydrogen carbonate'
    assert research._identity_intent('sodium hydrogen carbonate')
    monkeypatch.setattr(research, '_fetch_json', lambda *_: pytest.fail('SMILES must resolve offline'))
    record, error = research.chemical_identity_lookup(CAPSAICIN)
    assert error is None
    assert record['formula'] == 'C18H27NO3'
    assert record['inchikey'] == 'YKPUWZUDDOIDPM-SOFGYWHQSA-N'
    assert 'supplied' in record['source']['publisher']


def test_unresolved_structure_is_explicit_not_fake_ring():
    svg = structure_svg('*')
    assert 'No concrete structure' in svg
    assert '<path' not in svg


def test_identity_cache_survives_provider_outage(monkeypatch):
    first, _ = research.chemical_identity_lookup('CCO')
    monkeypatch.setattr(research, '_fetch_json', lambda *_: pytest.fail('Cached identity must not need network'))
    second, _ = research.chemical_identity_lookup('CCO')
    assert second['cache_hit']
    assert first['smiles'] == second['smiles']
