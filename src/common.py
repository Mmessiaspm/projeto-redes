import hashlib, time, csv, os

MATRICULA = "20251014777"   # coloca sua matrícula aqui
NOME      = "Manoel Messias Pereira Medeiros"  # coloca seu nome aqui
CUSTOM_AUTH = f"{MATRICULA}-{NOME}"

BLOCK_SIZE  = 1400
TIMEOUT     = 0.5
WINDOW_SIZE = 4

def checksum(data: bytes) -> bytes:
    return hashlib.md5(data).digest()

class Metrics:
    def __init__(self, protocolo, cenario):
        self.protocolo    = protocolo
        self.cenario      = cenario
        self.inicio       = None
        self.fim          = None
        self.bytes_enviados   = 0
        self.retransmissoes   = 0

    def start(self): self.inicio = time.time()
    def stop(self):  self.fim    = time.time()

    @property
    def duracao(self):
        return self.fim - self.inicio

    @property
    def throughput_mbps(self):
        return (self.bytes_enviados * 8) / (self.duracao * 1e6)

    def salvar_csv(self, path="data/csv/metricas.csv"):
        os.makedirs("data/csv", exist_ok=True)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                self.protocolo,
                self.cenario,
                round(self.duracao, 4),
                round(self.throughput_mbps, 4),
                self.bytes_enviados,
                self.retransmissoes
            ])
        print(f"[Metrics] Salvo em {path}")
