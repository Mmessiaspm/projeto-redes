#!/bin/bash
CENARIO=$1
PROTO=$2

# Para o tcpdump
kill $(cat /tmp/tcpdump.pid) 2>/dev/null
sleep 1

PCAP="/app/data/pcaps/${PROTO}_cenario${CENARIO}.pcap"
CSV="/app/data/csv/pcap_${PROTO}_cenario${CENARIO}.csv"

echo "[Capture] Exportando $PCAP para $CSV"

# Exporta para CSV usando tcpdump mesmo
tcpdump -r $PCAP -nn -q 2>/dev/null | awk '{
    print NR","$1","$3","$5","$7
}' > $CSV

echo "[Capture] Arquivo CSV gerado: $CSV"
echo "[Capture] Total de pacotes: $(wc -l < $CSV)"
