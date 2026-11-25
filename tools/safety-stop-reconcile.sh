#!/usr/bin/env bash
# 安全停机对账（管理员操作）
#
# controller 的 /v1/executions/<id>/safety-stop/reconcile **只接受 mTLS 管理员证书**，
# 不支持 Authorization: Bearer。请提供客户端证书、私钥与（可选的）CA。
#
#   ./tools/safety-stop-reconcile.sh \
#       --execution <execution_id> \
#       --cert ~/.config/roboguide/admin.crt --key ~/.config/roboguide/admin.key \
#       --attestation "现场已确认安全，场地无人，机械臂已断电"      # 至少 16 字
#
#   ./tools/safety-stop-reconcile.sh --execution <id> --cert ... --key ... --dry-run
#
# 环境变量:
#   CONTROLLER   默认 https://127.0.0.1:8443（mTLS 监听；明文 HTTP 监听无法执行该操作）
#   CA           可选，服务端 CA 证书
#   DISPOSITION  stopped（默认）| no-effects
set -euo pipefail

CONTROLLER="${CONTROLLER:-https://127.0.0.1:8443}"
CA="${CA:-}"
DISPOSITION="${DISPOSITION:-stopped}"
EXECUTION=""; ATTESTATION=""; CERT=""; KEY=""; DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --execution)   EXECUTION="$2"; shift 2 ;;
    --attestation) ATTESTATION="$2"; shift 2 ;;
    --disposition) DISPOSITION="$2"; shift 2 ;;
    --cert)        CERT="$2"; shift 2 ;;
    --key)         KEY="$2"; shift 2 ;;
    --ca)          CA="$2"; shift 2 ;;
    --dry-run)     DRY=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

[ -n "$EXECUTION" ] || { echo "必须给 --execution <execution_id>" >&2; exit 2; }
[ -n "$CERT" ] && [ -n "$KEY" ] || { echo "必须给 mTLS 管理员证书：--cert 与 --key" >&2; exit 2; }
if [ "$DRY" -eq 0 ] && [ "${#ATTESTATION}" -lt 16 ]; then
  echo "--attestation 至少 16 个字（记录在案的责任说明）" >&2; exit 2
fi

tls=(-sS --cert "$CERT" --key "$KEY")
[ -n "$CA" ] && tls+=(--cacert "$CA")

view=$(curl -fsS "${tls[@]}" "$CONTROLLER/v1/executions/$EXECUTION/safety-stop")
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

echo "--- POST $CONTROLLER/v1/executions/$EXECUTION/safety-stop/reconcile"
printf '%s\n' "$body"
[ "$DRY" -eq 1 ] && exit 0

curl -fsS "${tls[@]}" -X POST -H 'Content-Type: application/json' -d "$body" \
  "$CONTROLLER/v1/executions/$EXECUTION/safety-stop/reconcile"
echo
