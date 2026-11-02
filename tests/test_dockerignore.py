"""#554 — a local build ships none of the machine's Bitwarden helpers.

`bw-env.sh` and `bw-seed.sh` are gitignored symlinks in the repo root on the VM,
so CI's checkout never has them and CI's image check could not see them come back.
This can only read the ignore file; #554's PR records the built image's listing.
"""
from pathlib import Path

import pytest

DOCKERIGNORE = Path(__file__).resolve().parent.parent / ".dockerignore"


@pytest.mark.skipif(not DOCKERIGNORE.exists(), reason="not present in this image build")
@pytest.mark.criterion(554, "A local build ships no Bitwarden helper")
def test_a_local_build_ships_no_bitwarden_helper():
    ignored = DOCKERIGNORE.read_text().splitlines()
    assert {"bw-env.sh", "bw-seed.sh"} <= set(ignored)
