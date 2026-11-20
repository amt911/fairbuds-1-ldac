#!/usr/bin/env bash
# List Ghidra references to addresses in the stock project. Usage: tools/xrefs.sh ADDR [ADDR...]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
/opt/ghidra/support/analyzeHeadless "$(realpath "$here/../ghidra")" "${GHIDRA_PROJ_NAME:-stock65}" -process -noanalysis -readOnly \
  -scriptPath "$(realpath "$here/../ghidra_scripts")" -postScript XrefsTo.java "$@" 2>&1 \
  | sed -n 's/^INFO  XrefsTo.java> \(.*\) (GhidraScript)  $/\1/p'
