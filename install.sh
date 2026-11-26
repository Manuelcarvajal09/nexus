#!/usr/bin/env bash
set -euo pipefail
NEXUS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$NEXUS_ROOT/scripts/integrate.py" install "$@"
