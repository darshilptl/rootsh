#!/bin/sh
# Build the tool venv offered to agents during gold collection (design 04 §5):
# a plain Python with pytest, so test-running scenarios need no network.
# Usage: research/gold/make_toolenv.sh <dir>
set -eu
dir=$1
uv venv --quiet --python 3.12 "$dir"
uv pip install --quiet --python "$dir/bin/python" pytest==9.2.0
"$dir/bin/python" -m pytest --version
