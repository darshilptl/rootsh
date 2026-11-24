"""Print this host's K2 AppArmor profile (design 09 §5), exactly as
`sudo rootsh install --system` would write it, so the author can load it
alone and run the live K2 test without a full system install:

  uv run python research/p9/k2_profile.py > "$SCRATCH/rootsh.aa"
  sudo apparmor_parser -r "$SCRATCH/rootsh.aa"
  uv run pytest -q tests/run -k k2_live
  sudo apparmor_parser -R "$SCRATCH/rootsh.aa"
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from rootsh.install import Layout, k2_profile
from rootsh.kernel import apparmor
from rootsh.run.sandbox import code_paths


def main() -> None:
    layout = Layout.for_user(getpass.getuser(), Path(sys.executable).absolute())
    text = k2_profile(layout, code_paths())
    apparmor.check(text)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
