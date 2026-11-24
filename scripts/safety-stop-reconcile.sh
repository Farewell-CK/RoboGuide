#!/usr/bin/env bash
# 安全停机对账入口（需要管理员身份；operator 会被 403 拒绝）
#
#   ./scripts/safety-stop-reconcile.sh --execution <execution_id> --attestation "现场已确认安全(>=16字)"
#   ./scripts/safety-stop-reconcile.sh --execution <execution_id> --dry-run        # 只打印将提交的 body
#
# 环境变量:
#   CONTROLLER        默认 http://127.0.0.1:8080
#   ADMIN_TOKEN       管理员令牌（若你们的部署用 token 鉴权；无则留空，走 mTLS/本机管理员）
#   DISPOSITION       默认 stopped（可填 no-effects）
set -euo pipefail

CONTROLLER="${CONTROLLER:-http://127.0.0.1:8080}"
DISPOSITION="${DISPOSITION:-stopped}"
EXECUTION=""; ATTESTATION=""; DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --execution) EXECUTION="$2"; shift 2 ;;
    --attestation) ATTESTATION="$2"; shift 2 ;;
    --disposition) DISPOSITION="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

[ -n "$EXECUTION" ] || { echo "必须给 --execution <execution_id>" >&2; exit 2; }
if [ "$DRY" -eq 0 ] && [ "${#ATTESTATION}" -lt 16 ]; then
  echo "--attestation 至少 16 个字（记录在案的责任说明）" >&2; exit 2
fi

view=$(curl -fsS "$CONTROLLER/v1/executions/$EXECUTION/safety-stop")
digest=$(printf '%s' "$view" | python3 -c "import json,sys;print(json.load(sys.stdin)['record_digest'])")
generation=$(printf '%s' "$view" | python3 -c "
import json,sys
d=json.load(sys.stdin)['purpose']
print(d.get('execution_generation') or (d.get('record') or {}).get('generation') or 1)")

body=$(python3 - "$digest" "$generation" "$DISPOSITION" "${ATTESTATION:-dry-run attestation placeholder}" <<'PY'
import json, sys, uuid
digest, generation, disposition, attestation = sys.argv[1:5]
print(json.dumps({
    "schema_version": "roboguide.stop-reconciliation/v0.1",
    "command_id": f"reconcile-{uuid.uuid4()}",
    "expected_record_digest": digest,
    "execution_generation": int(generation),
    "confirmed": True,
    "disposition": disposition,
    "attestation": attestation,
    "evidence": {"basis": "operator-manual-inspection"},
}, ensure_ascii=False))
PY
)

echo "--- 将提交到 $CONTROLLER/v1/executions/$EXECUTION/safety-stop/reconcile"
printf '%s\n' "$body"
[ "$DRY" -eq 1 ] && exit 0

auth=()
[ -n "${ADMIN_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $ADMIN_TOKEN")
curl -sS -X POST -H 'Content-Type: application/json' "${auth[@]}" -d "$body" \
  "$CONTROLLER/v1/executions/$EXECUTION/safety-stop/reconcile"
echo
