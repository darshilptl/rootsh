"""The canonical paths every recorded or replayed action is written in.

Collection maps the workspace to `WORKDIR` and the home to `SANITIZED_HOME`
(P4); the model's features and the ablation compare paths against the same
two, so they live here, below both the research and the runtime code.
"""

WORKDIR = "/work"
SANITIZED_HOME = "/home/u"
