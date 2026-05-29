#!/bin/bash
IFACE="eth0"
CENARIO=$1

tc qdisc del dev $IFACE root 2>/dev/null

case $CENARIO in
  A)
    echo "[TC] Cenario A: 0% perda / 10ms delay"
    tc qdisc add dev $IFACE root netem delay 10ms
    ;;
  B)
    echo "[TC] Cenario B: 10% perda / 50ms delay"
    tc qdisc add dev $IFACE root netem delay 50ms loss 10%
    ;;
  C)
    echo "[TC] Cenario C: 20% perda / 100ms delay"
    tc qdisc add dev $IFACE root netem delay 100ms loss 20%
    ;;
  OFF)
    echo "[TC] Removendo todas as regras"
    ;;
  *)
    echo "Uso: $0 [A|B|C|OFF]"
    exit 1
    ;;
esac

echo "[TC] Configuracao aplicada:"
tc qdisc show dev $IFACE
