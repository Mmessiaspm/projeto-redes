import socket
import sys
import time
from common import CUSTOM_AUTH, BLOCK_SIZE, Metrics

HOST    = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.2"
ARQUIVO = sys.argv[2] if len(sys.argv) > 2 else "teste.bin"
CENARIO = sys.argv[3] if len(sys.argv) > 3 else "A"
PORT    = 5001

m = Metrics("TCP", CENARIO)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(30)  # timeout de 30 segundos
    s.connect((HOST, PORT))
    print(f"[TCP Client] Conectado ao servidor {HOST}:{PORT}")

    s.sendall(f"X-Custom-Auth: {CUSTOM_AUTH}\n".encode())

    m.start()
    with open(ARQUIVO, "rb") as f:
        while True:
            bloco = f.read(BLOCK_SIZE)
            if not bloco:
                break
            s.sendall(bloco)
            m.bytes_enviados += len(bloco)
    m.stop()

print(f"[TCP Client] Transferência concluída!")
print(f"[TCP Client] Tempo:      {m.duracao:.3f}s")
print(f"[TCP Client] Throughput: {m.throughput_mbps:.2f} Mbps")
print(f"[TCP Client] Bytes:      {m.bytes_enviados}")
m.salvar_csv()
