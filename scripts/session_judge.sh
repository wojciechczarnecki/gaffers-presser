#!/bin/bash
# Judges every exported item in one headless Claude Code turn each (docs/EVALUATION.md).
#
#   scripts/session_judge.sh DIR [PARALLEL] [MODEL]
#
# DIR holds inputs/ from `export-judge-inputs` (judge-prompt.md plus <case_id>.md). For every
# input without a verdict it runs `claude -p` with the judge prompt as the system prompt, the
# input as the only message and no tools, then writes verdicts/<case_id>.json (the bare JSON)
# and raw/<case_id>.json (the CLI's result with usage). Run it again to retry what failed.
# The judges count against the Claude subscription: about 15-30k tokens per item.
set -euo pipefail

DIR=$(realpath "$1")
PARALLEL=${2:-6}
MODEL=${3:-opus}
mkdir -p "$DIR/verdicts" "$DIR/raw"

SYSTEM="$(cat "$DIR/inputs/judge-prompt.md")

Reply with JSON only, no prose and no code fence: {\"claims\": [{\"claim\": \"...\", \"label\": \"supported\" | \"unsupported\"}]}"
export DIR MODEL SYSTEM

judge() {
    local case_id=$1
    # A raw answer from an earlier run is parsed again rather than paid for twice; delete
    # raw/<case_id>.json to judge the item again.
    # Run outside the repository so no project settings or CLAUDE.md reach the judge.
    [ -s "$DIR/raw/$case_id.json" ] || (cd /tmp && claude -p --model "$MODEL" --tools "" --no-session-persistence \
        --output-format json --system-prompt "$SYSTEM" \
        < "$DIR/inputs/$case_id.md" > "$DIR/raw/$case_id.json") || return 0
    python3 - "$DIR" "$case_id" <<'PY' || echo "invalid verdict: $case_id" >&2
import json, re, sys
directory, case_id = sys.argv[1], sys.argv[2]
result = json.load(open(f"{directory}/raw/{case_id}.json"))["result"]
text = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip())
verdict = json.loads(text)
assert all(c["label"] in ("supported", "unsupported") for c in verdict["claims"])
json.dump(verdict, open(f"{directory}/verdicts/{case_id}.json", "w"), ensure_ascii=False)
PY
}
export -f judge

for path in "$DIR"/inputs/*.md; do
    case_id=$(basename "$path" .md)
    [ "$case_id" = judge-prompt ] && continue
    [ -f "$DIR/verdicts/$case_id.json" ] && continue
    echo "$case_id"
done | xargs -r -P "$PARALLEL" -I{} bash -c 'judge "$@"' _ {}

echo "verdicts: $(ls "$DIR/verdicts" | wc -l) of $(($(ls "$DIR/inputs" | wc -l) - 1))"
python3 - "$DIR" <<'PY'
import glob, json, sys
raws = [json.load(open(p)) for p in glob.glob(f"{sys.argv[1]}/raw/*.json")]
tokens = sum(sum(u.get(k, 0) for k in ("input_tokens", "output_tokens",
             "cache_creation_input_tokens", "cache_read_input_tokens"))
             for u in (r.get("usage", {}) for r in raws))
print(f"judge calls: {len(raws)}  tokens: {tokens}")
PY
