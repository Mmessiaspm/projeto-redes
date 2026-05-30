import socket
import struct
import hashlib
import sys
from common import CUSTOM_AUTH, BLOCK_SIZE, TIMEOUT, WINDOW_SIZE, Metrics

HOST    = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.2"
ARQUIVO = sys.argv[2] if len(sys.argv) > 2 else "teste.bin"
CENARIO = sys.argv[3] if len(sys.argv) > 3 else "A"
VERBOSE = "--verbose" in sys.argv
PORT    = 5002

HDR_FORMAT = "!I B 16s I"
HDR_SIZE   = struct.calcsize(HDR_FORMAT)
FLAG_DATA = 0x01
FLAG_ACK  = 0x02
FLAG_FIN  = 0x04

def make_pkt(seq, payload, flags=FLAG_DATA):
    ck  = hashlib.md5(payload).digest()
    hdr = struct.pack(HDR_FORMAT, seq, flags, ck, len(payload))
    return hdr + payload

def parse_ack(pkt):
    seq, flags, _, _ = struct.unpack(HDR_FORMAT, pkt[:HDR_SIZE])
    return seq

blocos = []
with open(ARQUIVO, "rb") as f:
    while b := f.read(BLOCK_SIZE):
        blocos.append(b)

blocos.insert(0, CUSTOM_AUTH.encode())
total = len(blocos)

# Pontos de progresso fixos: 25%, 50%, 75%
checkpoints = {int(total * p): f"{p*100:.0f}%" for p in [0.25, 0.50, 0.75]}

# Limita timeouts impressos a no maximo 3
MAX_TIMEOUT_PRINTS = 3
timeout_prints = 0

print(f"[R-UDP] Cenario {CENARIO} | Blocos: {total} | Janela: {WINDOW_SIZE} | Timeout: {TIMEOUT}s")
print("-" * 55)

m = Metrics("R-UDP", CENARIO)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(TIMEOUT)

base = 0
prox = 0
m.start()

while base < len(blocos):
    while prox < base + WINDOW_SIZE and prox < len(blocos):
        pkt = make_pkt(prox, blocos[prox])
        s.sendto(pkt, (HOST, PORT))
        if prox > 0:
            m.bytes_enviados += len(blocos[prox])
        prox += 1

    try:
        ack_pkt, _ = s.recvfrom(HDR_SIZE)
        ack_seq = parse_ack(ack_pkt)
        base = ack_seq + 1

        if VERBOSE:
            print(f"  ACK {ack_seq} | Janela: {base} a {base+WINDOW_SIZE}")
        elif base in checkpoints:
            label = checkpoints.pop(base)
            print(f"[R-UDP] {label:>4} | Bloco {base:5d}/{total} | Retransmissoes: {m.retransmissoes}")

    except socket.timeout:
        m.retransmissoes += (prox - base)
        prox = base
        if timeout_prints < MAX_TIMEOUT_PRINTS:
            print(f"[R-UDP] TIMEOUT no bloco {base} | Retransmissoes acumuladas: {m.retransmissoes}")
            timeout_prints += 1
        elif timeout_prints == MAX_TIMEOUT_PRINTS:
            print(f"[R-UDP] (timeouts suprimidos para brevidade...)")
            timeout_prints += 1

s.sendto(make_pkt(0, b'', FLAG_FIN), (HOST, PORT))
m.stop()
s.close()

print("-" * 55)
print(f"[R-UDP] Transferencia concluida!")
print(f"[R-UDP] Tempo:          {m.duracao:.3f}s")
print(f"[R-UDP] Throughput:     {m.throughput_mbps:.2f} Mbps")
print(f"[R-UDP] Retransmissoes: {m.retransmissoes}")
m.salvar_csv()
