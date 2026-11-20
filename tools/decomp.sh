#!/usr/bin/env bash
# Decompile stock fw functions with Ghidra headless (read-only project).
# Usage: tools/decomp.sh OUTFILE ADDR [ADDR...]   (addresses like 0x2C0BFBC8)
# Project: ghidra/stock65 (built from tools/mkelf.sh: flash at 0x2C020000 + RAM copies).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$1"; shift
PROJ_DIR="${GHIDRA_PROJ_DIR:-$(realpath "$here/../ghidra")}"
PROJ_NAME="${GHIDRA_PROJ_NAME:-stock65}"
/opt/ghidra/support/analyzeHeadless "$PROJ_DIR" "$PROJ_NAME" -process -noanalysis -readOnly \
  -scriptPath "$(realpath "$here/../ghidra_scripts")" -postScript DecompileAt.java "$@" > "$out.log" 2>&1 || true
# keep only script output
awk '/DecompileAt.java> /{p=1} /^INFO  REPORT/{p=0} p' "$out.log" | sed -e 's/^INFO  DecompileAt.java> //' -e 's/ (GhidraScript)  $//' > "$out"
grep -c "=====" "$out" >/dev/null && echo "ok: $(grep -c '=====' "$out") functions -> $out" || { echo "no output, see $out.log"; tail -5 "$out.log"; }
