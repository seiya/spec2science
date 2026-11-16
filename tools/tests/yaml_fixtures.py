"""One reader for the checked-in YAML fixtures the suite parses again and again.

The evidence IRs are large and the pure-Python loader is slow on them (the
`shallow_water2d_channel` one: 1.5-1.9 s a parse), and the rows that sweep every evidence IR
parsed each file once per row. Parsing once per process is what the rows share, so the gain
is largest where a worker runs several of those modules: the five files that read through
this, run together with 8 workers, 86 s before and 35 s after (measured 2026-11-16 at
f639aea2).
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
