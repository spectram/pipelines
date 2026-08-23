#!/bin/bash
# Golden-diff regression harness for processMeerKAT.py's job-generation code
# (write_command/write_sbatch/write_master/write_spw_master/format_args).
#
# There's no automated test suite for this repo (it's validated by actually running
# the pipeline against a MeasurementSet), and -B (building a config from a real MS)
# needs real MS metadata, so this harness only covers -R: regenerating job scripts
# from an already-built, fixed fixture config. That's deliberately exactly the code
# path the Pawsey refactor touches (script_registry migration, cluster-config move,
# -H flag, etc.) -- run this before and after any change to those functions, and any
# unexpected diff is a regression signal.
#
# Usage:
#   tools/golden_diff.sh                 # compare current code's output against baseline/
#   tools/golden_diff.sh --update-baseline   # regenerate baseline/ from current code's output
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
GOLDEN_DIR="$SCRIPT_DIR/golden_diff"
BASELINE_DIR="$GOLDEN_DIR/baseline"
FIXTURE_CONFIG="$GOLDEN_DIR/fixture_config.txt"

RUN_DIR=$(mktemp -d)
trap 'rm -rf "$RUN_DIR"' EXIT

export PATH="$REPO_ROOT/processMeerKAT:$PATH"
export PYTHONPATH="$REPO_ROOT/processMeerKAT:${PYTHONPATH:-}"

cp "$FIXTURE_CONFIG" "$RUN_DIR/fixture_config.txt"
# validate_args only checks that the configured MS path exists as a directory -- an
# empty stand-in is enough for job-generation code, which never reads MS content.
mkdir -p "$RUN_DIR/fixture.ms"
(cd "$RUN_DIR" && python3 "$REPO_ROOT/processMeerKAT/processMeerKAT.py" -R -C fixture_config.txt)

# Drop the fixture config itself (it's copied/mutated in place by write_jobs -- not
# part of the generated output we're regression-testing) and jobScripts/*.txt (a
# verbatim copy of the, already-excluded, config).
rm -f "$RUN_DIR/fixture_config.txt"
rm -rf "$RUN_DIR/fixture.ms"
rm -f "$RUN_DIR"/jobScripts/*.txt 2>/dev/null || true
# write_sbatch() unconditionally mkdirs LOG_DIR ('logs') in the CWD it's run from, but
# -R never actually writes into it (that only happens when a job runs). It stays empty,
# so git never tracked it in baseline/ (git can't store empty directories) -- drop it
# here too so an empty 'logs' dir doesn't show up as a spurious one-sided diff.
rmdir "$RUN_DIR/logs" 2>/dev/null || true

# Normalize the repo's own absolute path before comparing/saving. Generated scripts
# embed REPO_ROOT (via PYTHONPATH and the script's own path) verbatim, so a baseline
# captured from one checkout location (e.g. the canonical clone) spuriously "differs"
# from a run done from another (e.g. a worktree-isolated agent session nested under
# .claude/worktrees/<id>/) even with zero code changes. Collapse any absolute path
# immediately preceding '/processMeerKAT' down to a fixed placeholder -- that's exactly
# and only the REPO_ROOT-derived prefix -- so the harness is comparing generated
# *content*, not the accident of where this checkout happens to sit on disk. Other
# absolute paths (e.g. the container path) are untouched since they don't end in
# '/processMeerKAT'.
normalize_repo_root() {
    # Excludes ':' and '=' too, not just whitespace: PYTHONPATH-style --env values pack
    # multiple colon-separated paths into one whitespace-delimited token (e.g. `--env
    # PYTHONPATH=/foo/idianext_mpi4py:/bar/processMeerKAT`) -- matching only up to the
    # nearest ':'/'=' keeps the unrelated preceding path (and the PYTHONPATH= key itself)
    # intact instead of swallowing them into the replacement.
    grep -rlZ . "$1" 2>/dev/null | xargs -0 -r sed -i -E 's#[^[:space:]:=]*/processMeerKAT#__REPO_ROOT__/processMeerKAT#g'
}

if [[ "${1:-}" == "--update-baseline" ]]; then
    normalize_repo_root "$RUN_DIR"
    rm -rf "$BASELINE_DIR"
    mkdir -p "$BASELINE_DIR"
    cp -r "$RUN_DIR"/. "$BASELINE_DIR"/
    echo "Baseline updated at $BASELINE_DIR"
    exit 0
fi

if [[ ! -d "$BASELINE_DIR" ]]; then
    echo "No baseline found at $BASELINE_DIR -- run with --update-baseline first." >&2
    exit 1
fi

NORM_BASELINE_DIR=$(mktemp -d)
trap 'rm -rf "$RUN_DIR" "$NORM_BASELINE_DIR"' EXIT
cp -r "$BASELINE_DIR"/. "$NORM_BASELINE_DIR"/
normalize_repo_root "$NORM_BASELINE_DIR"
normalize_repo_root "$RUN_DIR"

if diff -ru "$NORM_BASELINE_DIR" "$RUN_DIR"; then
    echo "OK: generated job scripts match the baseline."
else
    echo "REGRESSION: generated job scripts differ from the baseline (see diff above)." >&2
    exit 1
fi
