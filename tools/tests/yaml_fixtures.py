"""One reader for the checked-in YAML fixtures the suite parses again and again.

The evidence IRs are large (the `shallow_water2d_channel` one is 20263 lines) and the
pure-Python loader takes 1.4-1.8 s for it; rows that sweep every evidence IR parsed each file
once per row. Measured 2026-11-16 with every `yaml.safe_load` of the suite parsed once per
text: 90 s of 1530 s of summed test time, most of it in the four files that sweep the IRs.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml

_PARSED: dict[bytes, Any] = {}


def load_yaml_fixture(path: Path) -> Any:
    """`yaml.safe_load` of ``path``'s text, parsed once per process for each content.

    The key is the digest of the bytes read now, so an edited file is parsed afresh, and each
    caller gets a deep copy, so a row that edits its IR changes no other row's. For a file the
    row itself wrote, or a row about the parse, call `yaml.safe_load`."""
    data = Path(path).read_bytes()
    key = hashlib.sha256(data).digest()
    if key not in _PARSED:
        _PARSED[key] = yaml.safe_load(data.decode("utf-8"))
    return copy.deepcopy(_PARSED[key])
