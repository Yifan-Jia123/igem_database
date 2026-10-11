"""Server-side SMILES -> InChIKey conversion (RDKit).

RDKit is **imported at module level** here on purpose: if the package is missing,
importing this module fails, and main.py imports it when mounting routes, so
uvicorn won't even start. That's the desired failure mode -- backend/
requirements.txt is the install contract, and a missing install should surface in
those first seconds of startup, not become a 500 on that endpoint when the first
caller submits a SMILES. So there is no try/except ImportError fallback here.

Aligned with update_tool/build_all_nodes.py: same rdkit, RDLogger disabled the
same way. Note the compound.inchi_key_derived column is also computed by RDKit --
a key computed at query time must be able to match it, so it has to share the
same rdkit that produced it. Version-change caveats are in
update_tool/requirements.txt.
"""
from typing import Optional

from rdkit import Chem, RDLogger

# RDKit prints parse/InChI diagnostics to stderr. Suppressing it is not "silent
# degradation": failures still propagate through the return value (None vs ""),
# which the caller turns into an API error code. Precedent in
# update_tool/build_all_nodes.py.
RDLogger.DisableLog('rdApp.*')


def smiles_to_inchikey(smiles: str) -> Optional[str]:
    """Convert SMILES to a standard InChIKey.

    Three return values, which the caller must distinguish:

    - ``None`` -- RDKit can't parse the input at all (syntax error, garbage).
    - ``""``   -- parsed fine, but no key can be computed. R-groups / Markush
      formulas containing ``*`` take this branch: it isn't a bad SMILES, it just
      isn't a molecule, and any other converter behaves the same.
    - otherwise -- the 27-char InChIKey (RDKit already returns it uppercase).

    Note an empty string is a "valid molecule" to RDKit and also lands in the
    ``""`` branch. So the empty check must be done by the caller **before
    entering this function**, otherwise ``?smiles=`` is read as "a generic
    formula was given" rather than "no parameter given".
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return Chem.MolToInchiKey(mol) or ""
