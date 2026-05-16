#!/usr/bin/env bash
# apply-ingest-cronjob.sh -- create/update a recurring ingest CronJob
# from a source-research YAML. Idempotent.
#
# Usage:
#   apply-ingest-cronjob.sh <yaml-path> <cron-schedule>
#
# Example:
#   apply-ingest-cronjob.sh data/source-research/phase11-post-cutoff.yaml "0 4 * * *"
#
# Steps:
#   1. Derive a name from the yaml basename (sans extension).
#   2. (Re)create the ConfigMap with the yaml content under
#      <name>.yaml so the Job's volumeMount matches.
#   3. Render infra/k8s/ingest-cronjob.yaml with the name + schedule
#      substituted, apply it.
#
# The "every: 30d" hints in the YAML are guidance for humans editing
# the cadence — actual scheduling lives in this script's second arg.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <yaml-path> <cron-schedule>" >&2
  echo "examples:" >&2
  echo "  $0 data/source-research/phase11-post-cutoff.yaml '0 4 * * *'   # daily 04:00 UTC" >&2
  echo "  $0 data/source-research/phase10-rfcs.yaml '0 4 1 */2 *'        # 04:00 UTC on the 1st every 2 months" >&2
  exit 2
fi

YAML="$1"
SCHEDULE="$2"
NS="${NAMESPACE:-lighthouse}"
CTX="${KCTX:-do-fra1-ship-prod}"

if [[ ! -f "$YAML" ]]; then
  echo "yaml not found: $YAML" >&2
  exit 1
fi

NAME="$(basename "$YAML" .yaml)"
TEMPLATE="$(cd "$(dirname "$0")" && pwd)/ingest-cronjob.yaml"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "template missing: $TEMPLATE" >&2
  exit 1
fi

echo "→ source: $YAML"
echo "→ name:   $NAME"
echo "→ cron:   $SCHEDULE"

# (Re)create the ConfigMap from the yaml. The --dry-run | apply pattern
# is idempotent and updates content on every call.
kubectl --context "$CTX" -n "$NS" create configmap "$NAME" \
  --from-file="$NAME.yaml=$YAML" \
  --dry-run=client -o yaml \
  | kubectl --context "$CTX" apply -f -

# Render and apply the CronJob.
sed \
  -e "s|__NAME__|$NAME|g" \
  -e "s|__SCHEDULE__|$SCHEDULE|g" \
  "$TEMPLATE" \
  | kubectl --context "$CTX" apply -f -

echo "→ done. trigger one run now with:"
echo "    kubectl --context $CTX -n $NS create job --from=cronjob/lighthouse-ingest-$NAME lighthouse-ingest-$NAME-manual-\$(date +%s)"
