import socket
import struct
import hashlib
import sys
from common import CUSTOM_AUTH, BLOCK_SIZE, TIMEOUT, WINDOW_SIZE, Metrics

HOST    = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.2"
ARQUIVO = sys.argv[2] if len(sys.argv) > 2 else "teste.bin"
CENARIO = sys.argv[3] if len(sys.argv) > 3 else "A"
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

# Lê arquivo em blocos
blocos = []
with open(ARQUIVO, "rb") as f:
    while b := f.read(BLOCK_SIZE):
        blocos.append(b)

# Adiciona pacote de auth no início
auth_pkt = CUSTOM_AUTH.encode()
blocos.insert(0, auth_pkt)

print(f"[R-UDP Client] Total de blocos: {len(blocos)}")

m = Metrics("R-UDP", CENARIO)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(TIMEOUT)

base = 0
prox = 0
m.start()

while base < len(blocos):
    # Envia janela
    while prox < base + WINDOW_SIZE and prox < len(blocos):
        pkt = make_pkt(prox, blocos[prox])
        s.sendto(pkt, (HOST, PORT))
        if prox > 0:  # não conta o pacote de auth
            m.bytes_enviados += len(blocos[prox])
        prox += 1

    # Aguarda ACK
    try:
        ack_pkt, _ = s.recvfrom(HDR_SIZE)
        ack_seq = parse_ack(ack_pkt)
        base = ack_seq + 1
        print(f"[R-UDP Client] ACK {ack_seq} | Janela: {base} a {base+WINDOW_SIZE}")

    except socket.timeout:
        print(f"[R-UDP Client] Timeout! Retransmitindo de {base}...")
        m.retransmissoes += (prox - base)
        prox = base  # Go-Back-N: volta ao início da janela

# Envia FIN
s.sendto(make_pkt(0, b'', FLAG_FIN), (HOST, PORT))
m.stop()
s.close()

print(f"[R-UDP Client] Transferência concluída!")
print(f"[R-UDP Client] Tempo:           {m.duracao:.3f}s")
print(f"[R-UDP Client] Throughput:      {m.throughput_mbps:.2f} Mbps")
print(f"[R-UDP Client] Retransmissões:  {m.retransmissoes}")
m.salvar_csv()
