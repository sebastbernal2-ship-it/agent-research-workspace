#!/usr/bin/env bash
set -euo pipefail

SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${SPIKE_DIR}/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  uv venv "${VENV}" --python 3.12
fi
uv pip install --python "${VENV}/bin/python" \
  'termcolor' 'python-dotenv' 'tiktoken' 'pydantic>=2.6.1,<3' \
  'pyyaml>=6.0.2' 'packaging' 'httpx>=0.28.1,<1' 'anyio>=4,<5' \
  'fast-depends[pydantic]>=3.0.8,<4' 'watchdog>=4,<7' \
  'websockets>=14,<17' 'jsonschema>=4.18,<5'

# The vendored source intentionally omits license-related files, so pip's
# wheel-metadata build cannot run. AG2 asks importlib.metadata for its version;
# install only that version marker, and import all code directly from upstream/.
SITE_PACKAGES="$("${VENV}/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
mkdir -p "${SITE_PACKAGES}/ag2-1.0.0.dist-info"
printf 'Metadata-Version: 2.1\nName: ag2\nVersion: 1.0.0\n' \
  > "${SITE_PACKAGES}/ag2-1.0.0.dist-info/METADATA"

echo "AG2 v1.0.0 test environment ready; source remains in ${SPIKE_DIR}/upstream."
