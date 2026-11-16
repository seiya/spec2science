"""The object cache of the rows that compile: an identical compile is run once.

Within one process always; across the xdist workers of one run when `conftest.py` has set
`ROOT` to a directory of that run, which the controller removes when the run ends. Before that
each of 24 workers compiled the fixture harness and every shared model for itself: `nvcc` ran
189 times where the four files that use it run it 159 times serially, the MPI wrapper 335
against 180 (measured 2026-11-16 at 68692c3c).
"""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

#: Where entries live. `conftest.py` sets it; a run without it (the `unittest` entry) makes a
#: directory of its own at the first compile and removes it at exit.
ROOT: Path | None = None


def _root() -> Path:
    global ROOT
    if ROOT is None:
        ROOT = Path(tempfile.mkdtemp(prefix="spec2science-objcache-"))
        atexit.register(shutil.rmtree, ROOT, True)
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT


def compile_cached(argv: list[str], cwd: Path) -> None:
    """Run the compile ``argv`` in ``cwd``, or copy in what an identical compile left before.

    The key is the command line and every file directly in ``cwd`` (name and bytes), so no
    caller lists what a compile reads and none can leave an input out: a row that edits a
    source, a header or a `.mod`-producing dependency misses and compiles. Callers put each
    source in the directory just before its own compile, which keeps a stage's key to the
    stages before it (and its hits to the rows that share them). What an entry holds is every
    file the compile created or rewrote in ``cwd`` (the object, a Fortran compile's `.mod`
    files). For the sources every row of a class repeats: the harness is 3.2 s of a 7 s `nvcc`
    build and 1.35 s of an MPI one (measured 2026-11-11). An entry appears by one rename, so a
    worker reads a whole entry or none; two workers that miss the same key both compile, and
    the second one's copy is dropped."""
    root = _root()
    digest = hashlib.sha256("\0".join(argv).encode())
    for path in sorted(p for p in cwd.iterdir() if p.is_file()):
        digest.update(b"\0" + path.name.encode() + b"\0" + path.read_bytes())
    entry = root / digest.hexdigest()
    if not entry.is_dir():
        def stamps() -> dict[str, tuple[int, int, int]]:
            return {e.name: (e.stat().st_ino, e.stat().st_mtime_ns, e.stat().st_size)
                    for e in os.scandir(cwd) if e.is_file()}
        before = stamps()
        build = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        if build.returncode != 0:
            raise AssertionError(" ".join(argv) + build.stdout[:2000] + build.stderr[:3000])
        staging = Path(tempfile.mkdtemp(dir=root))
        # new files, and files the compile rewrote (an output that was already there)
        for name, stamp in stamps().items():
            if before.get(name) != stamp:
                shutil.copy2(cwd / name, staging / name)
        try:
            staging.rename(entry)
        except OSError:   # another worker published the same key first
            shutil.rmtree(staging, ignore_errors=True)
        return
    for item in entry.iterdir():
        shutil.copy2(item, cwd / item.name)
