import socket
import struct
import hashlib
import os
from common import CUSTOM_AUTH, BLOCK_SIZE

HOST = "0.0.0.0"
PORT = 5002

HDR_FORMAT = "!I B 16s I"
HDR_SIZE   = struct.calcsize(HDR_FORMAT)

FLAG_DATA = 0x01
FLAG_ACK  = 0x02
FLAG_FIN  = 0x04

def parse_pkt(pkt):
    seq, flags, ck, plen = struct.unpack(HDR_FORMAT, pkt[:HDR_SIZE])
    payload = pkt[HDR_SIZE:HDR_SIZE + plen]
    return seq, flags, ck, payload

def make_ack(seq):
    return struct.pack(HDR_FORMAT, seq, FLAG_ACK, b'\x00'*16, 0)

os.makedirs("data", exist_ok=True)

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.bind((HOST, PORT))
    print(f"[R-UDP Server] Aguardando na porta {PORT}...")

    esperado = 0
    arquivo  = open("data/recebido_rudp.bin", "wb")
    auth_ok  = False

    while True:
        pkt, addr = s.recvfrom(HDR_SIZE + BLOCK_SIZE + 64)
        seq, flags, ck_recv, payload = parse_pkt(pkt)

        # FIN — encerra transferência
        if flags == FLAG_FIN:
            print(f"[R-UDP Server] FIN recebido. Transferência concluída!")
            s.sendto(make_ack(seq), addr)
            break

        # Primeiro pacote — verifica auth (seq=0, ainda sem auth_ok)
        if seq == 0 and not auth_ok:
            header = payload.decode(errors="ignore")
            if CUSTOM_AUTH in header:
                auth_ok = True
                print(f"[R-UDP Server] Auth OK!")
                s.sendto(make_ack(0), addr)
                esperado = 1
            else:
                print(f"[R-UDP Server] ERRO: Auth inválido!")
                # Reenvia ACK 0 negativo — ignora e aguarda
                s.sendto(make_ack(0), addr)
            continue

        # Verifica checksum
        ck_calc = hashlib.md5(payload).digest()

        if seq == esperado and ck_calc == ck_recv:
            arquivo.write(payload)
            s.sendto(make_ack(seq), addr)
            esperado += 1
            print(f"[R-UDP Server] Pacote {seq} OK | Esperando {esperado}")
        else:
            # Pacote duplicado ou fora de ordem — ACK do último correto
            ack_seq = max(0, esperado - 1)
            print(f"[R-UDP Server] Pacote {seq} ERRO | Esperado {esperado}")
            s.sendto(make_ack(ack_seq), addr)

    arquivo.close()
    print(f"[R-UDP Server] Arquivo salvo!")
