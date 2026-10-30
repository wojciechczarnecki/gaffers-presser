#!/bin/bash
# Judges every exported item in one headless Claude Code turn each (docs/EVALUATION.md).
#
#   scripts/session_judge.sh DIR [PARALLEL] [MODEL] [SCHEMA] [EFFORT]
#
# DIR holds inputs/ from an `export-judge-inputs` command (judge-prompt.md plus <id>.md). For
# every input without a verdict it runs `claude -p` with the judge prompt as the system prompt,
# the input as the only message and no tools, then writes verdicts/<id>.json (the bare JSON)
# and raw/<id>.json (the CLI's result with usage). Run it again to retry what failed: a reply
# that fails validation is moved to raw/<id>.invalid.json (kept, its tokens still counted), so
# the next run calls the judge again for that input only.
# SCHEMA is the verdict format: presser (default) {"claims": [{"claim", "label"}]}, or
# retrieval {"labels": {"P1": true, ...}} with one boolean for each [Pn] ID in the input, or
# extraction {"events": [{"player", "team", "event_type", "certainty"}], "other_match_lineup": bool},
# or corroboration {"label": "supports" | "contradicts" | "related" | "unrelated"}.
# EFFORT, when given, is passed to `claude --effort`.
# The judges count against the Claude subscription: about 15-30k tokens per item.
set -euo pipefail

DIR=$(realpath "$1")
PARALLEL=${2:-6}
MODEL=${3:-opus}
SCHEMA=${4:-presser}
EFFORT=${5:-}
mkdir -p "$DIR/verdicts" "$DIR/raw"

case "$SCHEMA" in
    presser)
        FORMAT='Reply with JSON only, no prose and no code fence: {"claims": [{"claim": "...", "label": "supported" | "unsupported"}]}'
        ;;
    retrieval)
        FORMAT='Reply with JSON only, no prose and no code fence: {"labels": {"P1": true, "P2": false}} with exactly one boolean for every post ID listed in the input.'
        ;;
    extraction)
        FORMAT='Reply with JSON only, no prose and no code fence: {"events": [{"player": "...", "team": "..." | null, "event_type": "out" | "doubt" | "benched" | "confirmed_starter", "certainty": "confirmed" | "likely" | "rumour"}], "other_match_lineup": true | false}'
        ;;
    corroboration)
        FORMAT='Reply with JSON only, no prose and no code fence: {"label": "supports" | "contradicts" | "related" | "unrelated"}'
        ;;
    *)
        echo "unknown schema: $SCHEMA (presser, retrieval, extraction or corroboration)" >&2
        exit 2
        ;;
esac

SYSTEM="$(cat "$DIR/inputs/judge-prompt.md")

$FORMAT"
EFFORT_ARGS=""
[ -n "$EFFORT" ] && EFFORT_ARGS="--effort $EFFORT"
export DIR MODEL SYSTEM SCHEMA EFFORT_ARGS

judge() {
    local case_id=$1
    # A valid raw answer is parsed again rather than paid for twice; delete raw/<id>.json to
    # judge the item again. Run outside the repository so no project settings or CLAUDE.md
    # reach the judge.
    # shellcheck disable=SC2086
    [ -s "$DIR/raw/$case_id.json" ] || (cd /tmp && claude -p --model "$MODEL" $EFFORT_ARGS --tools "" \
        --no-session-persistence --output-format json --system-prompt "$SYSTEM" \
        < "$DIR/inputs/$case_id.md" > "$DIR/raw/$case_id.json") || return 0
    python3 - "$DIR" "$case_id" "$SCHEMA" <<'PY' || echo "invalid verdict: $case_id" >&2
import json, os, re, sys
directory, case_id, schema = sys.argv[1:4]
raw = f"{directory}/raw/{case_id}.json"


def reject(reason):
    target, n = f"{directory}/raw/{case_id}.invalid.json", 1
    while os.path.exists(target):
        n += 1
        target = f"{directory}/raw/{case_id}.invalid.{n}.json"
    os.replace(raw, target)
    print(f"{case_id}: {reason}", file=sys.stderr)
    sys.exit(1)


try:
    result = json.load(open(raw))["result"]
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip())
    verdict = json.loads(text)
    if schema == "presser":
        assert all(c["label"] in ("supported", "unsupported") for c in verdict["claims"])
    elif schema == "corroboration":
        assert isinstance(verdict, dict) and set(verdict) == {"label"}, "keys"
        assert verdict["label"] in ("supports", "contradicts", "related", "unrelated"), "label"
    elif schema == "extraction":
        assert isinstance(verdict, dict) and set(verdict) == {"events", "other_match_lineup"}, "keys"
        assert isinstance(verdict["other_match_lineup"], bool), "other_match_lineup is not a boolean"
        assert isinstance(verdict["events"], list), "events is not a list"
        for event in verdict["events"]:
            assert isinstance(event, dict) and set(event) <= {"player", "team", "event_type", "certainty"}, "event keys"
            assert isinstance(event.get("player"), str) and event["player"], "player"
            assert event.get("team") is None or isinstance(event["team"], str), "team"
            assert event.get("event_type") in ("out", "doubt", "benched", "confirmed_starter"), "event_type"
            assert event.get("certainty") in ("confirmed", "likely", "rumour"), "certainty"
    else:
        ids = re.findall(r"^\[(P\d+)\]", open(f"{directory}/inputs/{case_id}.md").read(), re.M)
        labels = verdict["labels"]
        assert isinstance(labels, dict) and set(labels) == set(ids), "labels differ from the IDs"
        assert all(isinstance(v, bool) for v in labels.values()), "a label is not a boolean"
except Exception as exc:
    reject(f"{type(exc).__name__} {exc}")
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
