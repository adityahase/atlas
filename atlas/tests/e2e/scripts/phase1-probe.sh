#!/bin/bash
# Phase 1 e2e probe: echo a variable, exit 0.
set -euo pipefail
: "${NAME:?required}"
echo "hello ${NAME}"
