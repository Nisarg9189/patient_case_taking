#!/usr/bin/env bash
# The container's start: Redis and Kafka on 127.0.0.1, then the app. Their data lives in the
# container only (it is the 1-hour interview state and the queue to the summary worker; the
# cases themselves are in Neon), so a restart begins empty. Set EMBEDDED_SERVICES=0 to use
# your own REDIS_* and KAFKA_* settings instead.
set -eu

wait_for_port() {  # name port
  python3 - "$1" "$2" <<'PY'
import socket, sys, time
name, port = sys.argv[1], int(sys.argv[2])
for _ in range(120):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        sys.exit(0)
    except OSError:
        time.sleep(0.5)
print(f"{name} did not start on port {port}", file=sys.stderr)
sys.exit(1)
PY
}

if [ "${EMBEDDED_SERVICES:-1}" = "1" ]; then
  redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no --loglevel warning &
  wait_for_port redis 6379

  export LOG_DIR=/tmp/kafka-logs KAFKA_HEAP_OPTS="-Xms128m -Xmx256m"
  mkdir -p "$LOG_DIR"
  /opt/kafka/bin/kafka-storage.sh format --ignore-formatted -t "$(/opt/kafka/bin/kafka-storage.sh random-uuid)" \
    -c /app/kafka.properties >"$LOG_DIR/format.log" 2>&1
  /opt/kafka/bin/kafka-server-start.sh /app/kafka.properties >"$LOG_DIR/kafka.out" 2>&1 &
  if ! wait_for_port kafka 9092; then tail -n 40 "$LOG_DIR/kafka.out"; exit 1; fi

  # point the app at them, whatever the environment says (old hosted settings must not win)
  export REDIS_HOST=127.0.0.1 REDIS_PORT=6379 KAFKA_HOST=127.0.0.1 KAFKA_PORT=9092
  unset REDIS_USERNAME REDIS_PASSWORD KAFKA_USER KAFKA_PASSWORD KAFKA_CA_CERT
fi

exec uvicorn app:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'
