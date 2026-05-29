#!/bin/bash
CENARIO=$1
PROTO=$2

if [ "$PROTO" = "tcp" ]; then
    PORTA="5001"
else
    PORTA="5002"
fi

mkdir -p /app/data/pcaps /app/data/csv

echo "[Capture] Iniciando captura: protocolo=$PROTO cenario=$CENARIO porta=$PORTA"

tcpdump -i eth0 port $PORTA -w /app/data/pcaps/${PROTO}_cenario${CENARIO}.pcap &
echo $! > /tmp/tcpdump.pid
echo "[Capture] tcpdump PID: $(cat /tmp/tcpdump.pid)"
