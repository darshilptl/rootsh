"""The rootsh daemon (docs/phases/07-daemon.md).

`load_model()` never raises: a missing or tampered bundle is reported as an
error string, and the service then blocks every tool call (fail closed).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rootsh.decision.policy import Model


def load_model(bundle_dir: Path) -> tuple[Model | None, str | None]:
    # Imported here, not at module level: hook commands import
    # rootsh.enforcement.daemon.client and must not pay for the model stack (P8).
    from rootsh.decision.model.bundle import read
    from rootsh.decision.policy import model_from_bundle

    try:
        model = model_from_bundle(read(bundle_dir))
        # Score once now: LightGBM's first import costs ~1.8 s (results 07 §3),
        # which the first real decision would otherwise spend inside a hook timeout.
        model.risk(dict.fromkeys(model.names, 0.0))
        return model, None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return None, f"model bundle unusable ({type(exc).__name__}: {exc})"
    except RuntimeError as exc:  # BundleError: a hash does not match
        return None, f"model bundle tampered ({exc})"
