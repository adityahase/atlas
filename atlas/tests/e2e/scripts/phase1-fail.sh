#!/bin/bash
# Phase 1 e2e probe: write to stderr, exit non-zero.
set -euo pipefail
echo "boom" >&2
exit 7
