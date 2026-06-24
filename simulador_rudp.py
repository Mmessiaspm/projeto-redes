"""
================================================================================
SIMULADOR R-UDP EM SIMPY — Fase 2 do Projeto de Redes PPGCC/UFPI 2026-1
================================================================================
Aluno: Manoel Messias Pereira Medeiros
Matricula: 20251014777

Este script implementa um simulador de eventos discretos em SimPy que espelha
o comportamento do sistema R-UDP (Go-Back-N) construido na Fase 1, e executa
as 10 tarefas de validacao exigidas no enunciado, comparando os resultados
simulados com os dados reais coletados via tcpdump/aplicacao na Fase 1.

Estrutura do arquivo:
    - Parametros reais da Fase 1 (constantes de referencia)
    - Classe RUDPSimulator (motor de simulacao SimPy)
    - Tarefa 1  - Modelagem de Atraso (distribuicao normal)
    - Tarefa 2  - Modelo de Perda de Bernoulli
    - Tarefa 3  - Simulacao de Timeout / Retransmissoes
    - Tarefa 4  - Curva de Vazao (1MB a 100MB)
    - Tarefa 5  - Sensibilidade da Janela (variar N)
    - Tarefa 6  - Validacao de RTT
    - Tarefa 7  - Impacto do Jitter
    - Tarefa 8  - Cenario de Estresse (25% perda)
    - Tarefa 9  - Analise de Eficiencia (dados vs ACKs)
    - Tarefa 10 - Convergencia Estatistica (IC 95%, 30+ execucoes)
    - Exportacao de todos os resultados para CSV (para uso no Colab)
================================================================================
"""

import simpy
import random
import math
import json
import csv
import os
import statistics
from dataclasses import dataclass, field, asdict

# ==============================================================================
# PARAMETROS REAIS DA FASE 1 (coletados via tcpdump / aplicacao Python)
# Usados como base de validacao em todas as 10 tarefas.
# ==============================================================================

DADOS_REAIS_FASE1 = {
    "A": {  # 0% perda, 10ms delay
        "perda_pct": 0,
        "delay_ms": 10,
        "rtt_medio_ms": 11.85,      # medido via ping (Fase 1)
        "rtt_std_ms": 1.121,
        "throughput_mbps": 372.90,
        "tempo_s": 0.225,
        "retransmissoes": 0,
        "bytes_totais": 10485760,
        "eficiencia_pct": 100.0,
    },
    "B": {  # 10% perda, 50ms delay
        "perda_pct": 10,
        "delay_ms": 50,
        "rtt_medio_ms": 53.870,
        "rtt_std_ms": 1.741,
        "throughput_mbps": 0.20,
        "tempo_s": 620.822,
        "retransmissoes": 3600,
        "bytes_totais": 15525760,
        "eficiencia_pct": 67.54,
    },
    "C": {  # 20% perda, 100ms delay
        "perda_pct": 20,
        "delay_ms": 100,
        "rtt_medio_ms": 103.477,
        "rtt_std_ms": 1.968,
        "throughput_mbps": 0.12,
        "tempo_s": 1345.906,
        "retransmissoes": 7180,
        "bytes_totais": 20537040,
        "eficiencia_pct": 51.06,
    },
}

# Parametros fixos do protocolo R-UDP (identicos a Fase 1)
BLOCK_SIZE = 1400          # bytes de payload por bloco
HEADER_SIZE = 25           # bytes de cabecalho R-UDP (seq+flags+md5+len)
WINDOW_SIZE_DEFAULT = 4    # tamanho de janela Go-Back-N (Fase 1)
TIMEOUT_S = 0.5            # timeout fixo (Fase 1)
ARQUIVO_PADRAO_BYTES = 10 * 1024 * 1024  # 10 MB (igual a Fase 1)

OUTPUT_DIR = "/app/data/csv/fase2"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ==============================================================================
# MOTOR DE SIMULACAO — RUDPSimulator
# ==============================================================================

@dataclass
class ResultadoSimulacao:
    cenario: str
    perda_pct: float
    delay_ms: float
    window_size: int
    arquivo_bytes: int
    tempo_s: float = 0.0
    throughput_mbps: float = 0.0
    retransmissoes: int = 0
    pacotes_dados: int = 0
    pacotes_ack: int = 0
    rtts: list = field(default_factory=list)
    bytes_totais: int = 0
    eficiencia_pct: float = 0.0
    saturado: bool = False


class RUDPSimulator:
    """
    Simulador de eventos discretos do protocolo R-UDP com janela
    deslizante Go-Back-N, espelhando o comportamento implementado
    na Fase 1 (rudp_client.py / rudp_server.py).

    Modelo:
      - Cada bloco de dados gera um evento de envio.
      - O atraso de rede (RTT/2 ida + RTT/2 volta) e modelado por
        distribuicao normal (Tarefa 1), com jitter opcional (Tarefa 7).
      - A perda de pacotes e modelada por ensaio de Bernoulli
        (Tarefa 2), aplicada independentemente a pacotes de dados
        e a ACKs.
      - Ao detectar timeout (Tarefa 3), o simulador retransmite toda
        a janela corrente, replicando o Go-Back-N real.
    """

    def __init__(self, env, perda_pct, delay_ms, window_size=WINDOW_SIZE_DEFAULT,
                 arquivo_bytes=ARQUIVO_PADRAO_BYTES, timeout_s=TIMEOUT_S,
                 jitter_ms=0.0, seed=None):
        self.env = env
        self.p_loss = perda_pct / 100.0
        self.delay_ms = delay_ms
        self.jitter_ms = jitter_ms
        self.window_size = window_size
        self.timeout_s = timeout_s
        self.total_blocos = math.ceil(arquivo_bytes / BLOCK_SIZE)
        self.arquivo_bytes = arquivo_bytes

        if seed is not None:
            self.rng = random.Random(seed)
        else:
            self.rng = random.Random()

        self.retransmissoes = 0
        self.pacotes_dados_enviados = 0
        self.pacotes_ack_recebidos = 0
        self.rtts_observados = []
        self.tempo_inicio = None
        self.tempo_fim = None
        self.saturado = False

    # --------------------------------------------------------------------
    # Tarefa 1: Modelagem de Atraso (distribuicao normal)
    # --------------------------------------------------------------------
    def _rtt_completo(self):
        """
        Amostra o RTT completo (ida+volta) a partir de distribuicao normal
        N(delay_ms, sigma). O parametro delay_ms representa o RTT medio
        real medido via ping na Fase 1 (nao um atraso de sentido unico),
        portanto a amostragem e feita diretamente sobre o RTT total,
        evitando duplicacao do atraso.
        """
        sigma = max(self.jitter_ms, self.delay_ms * 0.05)  # jitter base 5%
        amostra = self.rng.gauss(self.delay_ms, sigma)
        return max(0.1, amostra)

    # --------------------------------------------------------------------
    # Tarefa 2: Modelo de Perda de Bernoulli
    # --------------------------------------------------------------------
    def _pacote_perdido(self):
        """Ensaio de Bernoulli: True com probabilidade p_loss."""
        return self.rng.random() < self.p_loss

    # --------------------------------------------------------------------
    # Processo principal de transmissao (Go-Back-N)
    # --------------------------------------------------------------------
    def transmitir(self):
        self.tempo_inicio = self.env.now
        base = 0
        prox = 0
        MAX_RODADAS = 50_000  # protecao contra saturacao total (Tarefa 5)
        rodadas = 0
        self.saturado = False

        while base < self.total_blocos:
            rodadas += 1
            if rodadas > MAX_RODADAS:
                # Janela tao grande / perda tao alta que o progresso e
                # praticamente nulo: caracteriza saturacao teorica (Tarefa 5).
                self.saturado = True
                break

            # Envia pacotes dentro da janela (pipeline Go-Back-N)
            while prox < base + self.window_size and prox < self.total_blocos:
                self.pacotes_dados_enviados += 1
                prox += 1

            tamanho_janela_atual = prox - base

            # Verifica se ALGUM pacote da janela foi perdido (dado OU ACK).
            # Em vez de testar pacote a pacote (lento para janelas grandes),
            # usa-se a probabilidade fechada de Bernoulli: a chance de pelo
            # menos uma perda em N pacotes independentes e
            # P(falha) = 1 - (1 - p)^N (Tarefa 2).
            p_falha_janela = 1 - (1 - self.p_loss) ** tamanho_janela_atual
            algum_perdido = self.rng.random() < p_falha_janela

            # Simula o RTT observado para esta rodada de janela
            rtt = self._rtt_completo()
            self.rtts_observados.append(rtt)
            yield self.env.timeout(rtt / 1000.0)

            if algum_perdido:
                # Tarefa 3: timeout -> Go-Back-N retransmite a janela inteira
                yield self.env.timeout(self.timeout_s)
                self.retransmissoes += tamanho_janela_atual
                prox = base  # volta toda a janela
            else:
                # Toda a janela foi confirmada: ACKs individuais contabilizados
                self.pacotes_ack_recebidos += tamanho_janela_atual
                base = prox  # avanca a janela inteira de uma vez

        self.tempo_fim = self.env.now

    # --------------------------------------------------------------------
    def resultado(self, cenario_label="custom"):
        tempo_s = self.tempo_fim - self.tempo_inicio
        bytes_uteis = self.arquivo_bytes
        bytes_dados = self.pacotes_dados_enviados * (BLOCK_SIZE + HEADER_SIZE)
        bytes_ack = self.pacotes_ack_recebidos * HEADER_SIZE
        bytes_totais = bytes_dados + bytes_ack

        throughput_mbps = (bytes_uteis * 8 / 1_000_000) / tempo_s if tempo_s > 0 else 0
        eficiencia = (bytes_uteis / bytes_totais * 100) if bytes_totais > 0 else 0

        return ResultadoSimulacao(
            cenario=cenario_label,
            perda_pct=self.p_loss * 100,
            delay_ms=self.delay_ms,
            window_size=self.window_size,
            arquivo_bytes=self.arquivo_bytes,
            tempo_s=tempo_s,
            throughput_mbps=throughput_mbps,
            retransmissoes=self.retransmissoes,
            pacotes_dados=self.pacotes_dados_enviados,
            pacotes_ack=self.pacotes_ack_recebidos,
            rtts=self.rtts_observados,
            bytes_totais=bytes_totais,
            eficiencia_pct=eficiencia,
            saturado=self.saturado,
        )


def rodar_simulacao(perda_pct, delay_ms, window_size=WINDOW_SIZE_DEFAULT,
                     arquivo_bytes=ARQUIVO_PADRAO_BYTES, jitter_ms=0.0,
                     seed=None, cenario_label="custom"):
    """Funcao utilitaria: cria o ambiente SimPy, roda e retorna o resultado."""
    env = simpy.Environment()
    sim = RUDPSimulator(env, perda_pct, delay_ms, window_size,
                         arquivo_bytes, jitter_ms=jitter_ms, seed=seed)
    env.process(sim.transmitir())
    env.run()
    return sim.resultado(cenario_label)


def salvar_csv(nome_arquivo, linhas, campos):
    path = os.path.join(OUTPUT_DIR, nome_arquivo)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for linha in linhas:
            writer.writerow(linha)
    print(f"[Fase2] Salvo: {path}")


# ==============================================================================
# TAREFA 1 — MODELAGEM DE ATRASO (distribuicao normal baseada em dados reais)
# ==============================================================================

def tarefa1_modelagem_atraso():
    print("\n" + "=" * 70)
    print("TAREFA 1 — Modelagem de Atraso (Distribuicao Normal)")
    print("=" * 70)

    linhas = []
    for cen, dados in DADOS_REAIS_FASE1.items():
        # Amostra 1000 RTTs simulados com a mesma media/desvio real
        amostras = [random.gauss(dados["rtt_medio_ms"], dados["rtt_std_ms"])
                    for _ in range(1000)]
        media_sim = statistics.mean(amostras)
        std_sim = statistics.stdev(amostras)

        erro_media = abs(media_sim - dados["rtt_medio_ms"]) / dados["rtt_medio_ms"] * 100

        print(f"Cenario {cen}: RTT real={dados['rtt_medio_ms']:.2f}ms (DP={dados['rtt_std_ms']:.2f}) "
              f"| RTT simulado={media_sim:.2f}ms (DP={std_sim:.2f}) | erro={erro_media:.2f}%")

        linhas.append({
            "cenario": cen,
            "rtt_real_ms": dados["rtt_medio_ms"],
            "rtt_real_std_ms": dados["rtt_std_ms"],
            "rtt_simulado_ms": round(media_sim, 3),
            "rtt_simulado_std_ms": round(std_sim, 3),
            "erro_pct": round(erro_media, 3),
        })

    salvar_csv("tarefa1_modelagem_atraso.csv", linhas,
               ["cenario", "rtt_real_ms", "rtt_real_std_ms",
                "rtt_simulado_ms", "rtt_simulado_std_ms", "erro_pct"])
    return linhas


# ==============================================================================
# TAREFA 2 — MODELO DE PERDA DE BERNOULLI (validar contra o tc)
# ==============================================================================

def tarefa2_modelo_perda_bernoulli():
    print("\n" + "=" * 70)
    print("TAREFA 2 — Modelo de Perda de Bernoulli (validacao contra tc)")
    print("=" * 70)

    linhas = []
    N_ENSAIOS = 100_000
    for cen, dados in DADOS_REAIS_FASE1.items():
        p_real = dados["perda_pct"] / 100.0
        perdidos = sum(1 for _ in range(N_ENSAIOS) if random.random() < p_real)
        taxa_simulada = perdidos / N_ENSAIOS * 100
        erro_abs = abs(taxa_simulada - dados["perda_pct"])

        print(f"Cenario {cen}: perda configurada (tc)={dados['perda_pct']}% "
              f"| perda simulada (Bernoulli, n={N_ENSAIOS})={taxa_simulada:.3f}% "
              f"| erro absoluto={erro_abs:.3f} p.p.")

        linhas.append({
            "cenario": cen,
            "perda_tc_pct": dados["perda_pct"],
            "perda_simulada_pct": round(taxa_simulada, 4),
            "erro_absoluto_pp": round(erro_abs, 4),
            "n_ensaios": N_ENSAIOS,
        })

    salvar_csv("tarefa2_perda_bernoulli.csv", linhas,
               ["cenario", "perda_tc_pct", "perda_simulada_pct",
                "erro_absoluto_pp", "n_ensaios"])
    return linhas


# ==============================================================================
# TAREFA 3 — SIMULACAO DE TIMEOUT / RETRANSMISSOES (validar contra tcpdump)
# ==============================================================================

def tarefa3_simulacao_timeout():
    print("\n" + "=" * 70)
    print("TAREFA 3 — Simulacao de Timeout e Retransmissoes")
    print("=" * 70)
    print("NOTA: o simulador adota o modelo classico de janela Go-Back-N,")
    print("no qual o emissor aguarda o RTT completo antes de liberar a")
    print("janela seguinte. Por isso, o throughput simulado tende a ser")
    print("muito menor que o throughput real medido na Fase 1, pois a")
    print("implementacao real em Python/socket envia e recebe de forma")
    print("mais agressiva (sem bloquear estritamente por RTT completo a")
    print("cada janela). Essa discrepancia teoria-vs-pratica e justamente")
    print("o ponto de investigacao central da Fase 2.\n")

    linhas = []
    for cen, dados in DADOS_REAIS_FASE1.items():
        resultado = rodar_simulacao(
            perda_pct=dados["perda_pct"],
            delay_ms=dados["delay_ms"],
            seed=42,
            cenario_label=cen,
        )

        retr_real = dados["retransmissoes"]
        retr_sim = resultado.retransmissoes
        erro_pct = (abs(retr_sim - retr_real) / retr_real * 100) if retr_real > 0 else (
            0 if retr_sim == 0 else 100)

        print(f"Cenario {cen}: retransmissoes reais (tcpdump)={retr_real} "
              f"| retransmissoes simuladas={retr_sim} | erro={erro_pct:.2f}%")
        print(f"           throughput real={dados['throughput_mbps']:.2f} Mbps "
              f"| throughput simulado (modelo teorico GBN)={resultado.throughput_mbps:.4f} Mbps")

        linhas.append({
            "cenario": cen,
            "retransmissoes_reais": retr_real,
            "retransmissoes_simuladas": retr_sim,
            "erro_pct": round(erro_pct, 2),
            "tempo_simulado_s": round(resultado.tempo_s, 3),
            "tempo_real_s": dados["tempo_s"],
            "throughput_real_mbps": dados["throughput_mbps"],
            "throughput_simulado_mbps": round(resultado.throughput_mbps, 4),
        })

    salvar_csv("tarefa3_timeout_retransmissoes.csv", linhas,
               ["cenario", "retransmissoes_reais", "retransmissoes_simuladas",
                "erro_pct", "tempo_simulado_s", "tempo_real_s",
                "throughput_real_mbps", "throughput_simulado_mbps"])
    return linhas


# ==============================================================================
# TAREFA 4 — CURVA DE VAZAO (arquivos de 1MB a 100MB)
# ==============================================================================

def tarefa4_curva_vazao():
    print("\n" + "=" * 70)
    print("TAREFA 4 — Curva de Vazao (1MB a 100MB)")
    print("=" * 70)

    tamanhos_mb = [1, 5, 10, 25, 50, 75, 100]
    linhas = []

    for cen, dados in DADOS_REAIS_FASE1.items():
        for tam_mb in tamanhos_mb:
            arquivo_bytes = tam_mb * 1024 * 1024
            resultado = rodar_simulacao(
                perda_pct=dados["perda_pct"],
                delay_ms=dados["delay_ms"],
                arquivo_bytes=arquivo_bytes,
                seed=hash((cen, tam_mb)) % (2**31),
                cenario_label=cen,
            )
            print(f"Cenario {cen} | {tam_mb:4d}MB -> "
                  f"throughput={resultado.throughput_mbps:.4f} Mbps | "
                  f"tempo={resultado.tempo_s:.2f}s | "
                  f"retransmissoes={resultado.retransmissoes}")

            linhas.append({
                "cenario": cen,
                "tamanho_mb": tam_mb,
                "throughput_mbps": round(resultado.throughput_mbps, 4),
                "tempo_s": round(resultado.tempo_s, 3),
                "retransmissoes": resultado.retransmissoes,
            })

    salvar_csv("tarefa4_curva_vazao.csv", linhas,
               ["cenario", "tamanho_mb", "throughput_mbps", "tempo_s", "retransmissoes"])
    return linhas


# ==============================================================================
# TAREFA 5 — SENSIBILIDADE DA JANELA (variar N, identificar saturacao)
# ==============================================================================

def tarefa5_sensibilidade_janela():
    print("\n" + "=" * 70)
    print("TAREFA 5 — Sensibilidade da Janela (saturacao teorica)")
    print("=" * 70)

    janelas = [1, 2, 4, 8, 16, 32, 64, 128, 256]
    linhas = []

    for cen, dados in DADOS_REAIS_FASE1.items():
        for n in janelas:
            resultado = rodar_simulacao(
                perda_pct=dados["perda_pct"],
                delay_ms=dados["delay_ms"],
                window_size=n,
                arquivo_bytes=1 * 1024 * 1024,  # 1MB fixo para comparacao rapida
                seed=hash((cen, n)) % (2**31),
                cenario_label=cen,
            )
            status = "SATURADO" if resultado.saturado else "ok"
            print(f"Cenario {cen} | N={n:3d} -> "
                  f"throughput={resultado.throughput_mbps:.4f} Mbps | "
                  f"retransmissoes={resultado.retransmissoes} | {status}")

            linhas.append({
                "cenario": cen,
                "window_size": n,
                "throughput_mbps": round(resultado.throughput_mbps, 4),
                "tempo_s": round(resultado.tempo_s, 3),
                "retransmissoes": resultado.retransmissoes,
                "saturado": resultado.saturado,
            })

    salvar_csv("tarefa5_sensibilidade_janela.csv", linhas,
               ["cenario", "window_size", "throughput_mbps", "tempo_s",
                "retransmissoes", "saturado"])
    return linhas


# ==============================================================================
# TAREFA 6 — VALIDACAO DE RTT (comparar RTT medio simulado vs real/tcpdump)
# ==============================================================================

def tarefa6_validacao_rtt():
    print("\n" + "=" * 70)
    print("TAREFA 6 — Validacao de RTT (Simulado vs tcpdump)")
    print("=" * 70)

    linhas = []
    for cen, dados in DADOS_REAIS_FASE1.items():
        resultado = rodar_simulacao(
            perda_pct=dados["perda_pct"],
            delay_ms=dados["delay_ms"],
            arquivo_bytes=2 * 1024 * 1024,  # 2MB, suficiente para amostrar RTTs
            seed=7,
            cenario_label=cen,
        )
        rtt_sim_medio = statistics.mean(resultado.rtts)
        rtt_sim_std = statistics.stdev(resultado.rtts) if len(resultado.rtts) > 1 else 0.0
        rtt_real = dados["rtt_medio_ms"]
        erro_pct = abs(rtt_sim_medio - rtt_real) / rtt_real * 100

        print(f"Cenario {cen}: RTT real (tcpdump/ping)={rtt_real:.2f}ms "
              f"| RTT simulado={rtt_sim_medio:.2f}ms (DP={rtt_sim_std:.2f}) "
              f"| erro={erro_pct:.2f}%")

        linhas.append({
            "cenario": cen,
            "rtt_real_ms": rtt_real,
            "rtt_simulado_medio_ms": round(rtt_sim_medio, 3),
            "rtt_simulado_std_ms": round(rtt_sim_std, 3),
            "erro_pct": round(erro_pct, 2),
            "n_amostras": len(resultado.rtts),
        })

    salvar_csv("tarefa6_validacao_rtt.csv", linhas,
               ["cenario", "rtt_real_ms", "rtt_simulado_medio_ms",
                "rtt_simulado_std_ms", "erro_pct", "n_amostras"])
    return linhas


# ==============================================================================
# TAREFA 7 — IMPACTO DO JITTER (estabilidade do fluxo)
# ==============================================================================

def tarefa7_impacto_jitter():
    print("\n" + "=" * 70)
    print("TAREFA 7 — Impacto do Jitter na Estabilidade do Fluxo")
    print("=" * 70)

    niveis_jitter_ms = [0, 5, 10, 20, 40, 80]
    linhas = []

    # Usa o Cenario A (rede mais estavel) como base para isolar o efeito do jitter
    dados_base = DADOS_REAIS_FASE1["A"]

    for jitter in niveis_jitter_ms:
        rtts_finais = []
        for rep in range(10):
            resultado = rodar_simulacao(
                perda_pct=dados_base["perda_pct"],
                delay_ms=dados_base["delay_ms"],
                jitter_ms=jitter,
                arquivo_bytes=2 * 1024 * 1024,
                seed=1000 + rep,
                cenario_label="A",
            )
            rtts_finais.extend(resultado.rtts)

        media = statistics.mean(rtts_finais)
        desvio = statistics.stdev(rtts_finais)
        cv = (desvio / media * 100) if media > 0 else 0  # coeficiente de variacao

        print(f"Jitter={jitter:3d}ms -> RTT medio={media:.2f}ms | "
              f"DP={desvio:.2f}ms | Coef. Variacao={cv:.2f}%")

        linhas.append({
            "jitter_ms": jitter,
            "rtt_medio_ms": round(media, 3),
            "rtt_std_ms": round(desvio, 3),
            "coef_variacao_pct": round(cv, 3),
        })

    salvar_csv("tarefa7_impacto_jitter.csv", linhas,
               ["jitter_ms", "rtt_medio_ms", "rtt_std_ms", "coef_variacao_pct"])
    return linhas


# ==============================================================================
# TAREFA 8 — CENARIO DE ESTRESSE (25% de perda, fora dos cenarios reais)
# ==============================================================================

def tarefa8_cenario_estresse():
    print("\n" + "=" * 70)
    print("TAREFA 8 — Cenario de Estresse (25% de perda)")
    print("=" * 70)

    resultado = rodar_simulacao(
        perda_pct=25,
        delay_ms=100,   # mesmo delay do Cenario C, perda mais severa
        seed=99,
        cenario_label="D-estresse",
    )

    print(f"Cenario D (estresse, 25% perda, 100ms delay):")
    print(f"  Tempo previsto:        {resultado.tempo_s:.3f}s")
    print(f"  Throughput previsto:   {resultado.throughput_mbps:.4f} Mbps")
    print(f"  Retransmissoes:        {resultado.retransmissoes}")
    print(f"  Eficiencia:            {resultado.eficiencia_pct:.2f}%")

    # Compara extrapolacao com a tendencia observada entre B e C reais
    tempo_b = DADOS_REAIS_FASE1["B"]["tempo_s"]
    tempo_c = DADOS_REAIS_FASE1["C"]["tempo_s"]
    crescimento_b_c = tempo_c / tempo_b  # fator de crescimento de B(10%) para C(20%)
    extrapolacao_linear = tempo_c * crescimento_b_c  # estimativa simples B->C->D

    linhas = [{
        "cenario": "D-estresse-25pct",
        "perda_pct": 25,
        "delay_ms": 100,
        "tempo_simulado_s": round(resultado.tempo_s, 3),
        "throughput_simulado_mbps": round(resultado.throughput_mbps, 4),
        "retransmissoes_simuladas": resultado.retransmissoes,
        "eficiencia_simulada_pct": round(resultado.eficiencia_pct, 2),
        "extrapolacao_linear_b_c_s": round(extrapolacao_linear, 3),
    }]

    print(f"  Extrapolacao linear (tendencia B->C): {extrapolacao_linear:.2f}s")

    salvar_csv("tarefa8_cenario_estresse.csv", linhas,
               ["cenario", "perda_pct", "delay_ms", "tempo_simulado_s",
                "throughput_simulado_mbps", "retransmissoes_simuladas",
                "eficiencia_simulada_pct", "extrapolacao_linear_b_c_s"])
    return linhas


# ==============================================================================
# TAREFA 9 — ANALISE DE EFICIENCIA (razao dados/ACKs)
# ==============================================================================

def tarefa9_analise_eficiencia():
    print("\n" + "=" * 70)
    print("TAREFA 9 — Analise de Eficiencia (Pacotes de Dados vs ACKs)")
    print("=" * 70)

    linhas = []
    for cen, dados in DADOS_REAIS_FASE1.items():
        resultado = rodar_simulacao(
            perda_pct=dados["perda_pct"],
            delay_ms=dados["delay_ms"],
            seed=21,
            cenario_label=cen,
        )

        razao = (resultado.pacotes_dados / resultado.pacotes_ack
                  if resultado.pacotes_ack > 0 else float("inf"))

        print(f"Cenario {cen}: pacotes de dados={resultado.pacotes_dados} | "
              f"ACKs={resultado.pacotes_ack} | razao dados/ACK={razao:.3f} | "
              f"eficiencia simulada={resultado.eficiencia_pct:.2f}% "
              f"(real={dados['eficiencia_pct']:.2f}%)")

        linhas.append({
            "cenario": cen,
            "pacotes_dados": resultado.pacotes_dados,
            "pacotes_ack": resultado.pacotes_ack,
            "razao_dados_ack": round(razao, 4),
            "eficiencia_simulada_pct": round(resultado.eficiencia_pct, 2),
            "eficiencia_real_pct": dados["eficiencia_pct"],
        })

    salvar_csv("tarefa9_analise_eficiencia.csv", linhas,
               ["cenario", "pacotes_dados", "pacotes_ack", "razao_dados_ack",
                "eficiencia_simulada_pct", "eficiencia_real_pct"])
    return linhas


# ==============================================================================
# TAREFA 10 — CONVERGENCIA ESTATISTICA (IC 95%, 30+ execucoes)
# ==============================================================================

def tarefa10_convergencia_estatistica(n_execucoes=30):
    print("\n" + "=" * 70)
    print(f"TAREFA 10 — Convergencia Estatistica (IC 95%, n={n_execucoes})")
    print("=" * 70)

    linhas = []
    for cen, dados in DADOS_REAIS_FASE1.items():
        throughputs = []
        tempos = []
        retransmissoes_lista = []

        for i in range(n_execucoes):
            resultado = rodar_simulacao(
                perda_pct=dados["perda_pct"],
                delay_ms=dados["delay_ms"],
                arquivo_bytes=2 * 1024 * 1024,  # 2MB por execucao (mais rapido)
                seed=None,  # seed aleatoria a cada execucao
                cenario_label=cen,
            )
            throughputs.append(resultado.throughput_mbps)
            tempos.append(resultado.tempo_s)
            retransmissoes_lista.append(resultado.retransmissoes)

        media_tp = statistics.mean(throughputs)
        std_tp = statistics.stdev(throughputs)
        # IC 95% aproximado por distribuicao t (n-1 graus de liberdade);
        # usa-se 1.96 (aproximacao normal) por simplicidade com n>=30
        margem_erro = 1.96 * (std_tp / math.sqrt(n_execucoes))
        ic_inferior = media_tp - margem_erro
        ic_superior = media_tp + margem_erro

        print(f"Cenario {cen} (n={n_execucoes}): "
              f"throughput medio={media_tp:.4f} Mbps "
              f"| IC95%=[{ic_inferior:.4f}, {ic_superior:.4f}] "
              f"| DP={std_tp:.4f}")

        linhas.append({
            "cenario": cen,
            "n_execucoes": n_execucoes,
            "throughput_medio_mbps": round(media_tp, 4),
            "throughput_std_mbps": round(std_tp, 4),
            "ic95_inferior_mbps": round(ic_inferior, 4),
            "ic95_superior_mbps": round(ic_superior, 4),
            "tempo_medio_s": round(statistics.mean(tempos), 3),
            "retransmissoes_media": round(statistics.mean(retransmissoes_lista), 1),
        })

    salvar_csv("tarefa10_convergencia_ic95.csv", linhas,
               ["cenario", "n_execucoes", "throughput_medio_mbps", "throughput_std_mbps",
                "ic95_inferior_mbps", "ic95_superior_mbps", "tempo_medio_s",
                "retransmissoes_media"])
    return linhas


# ==============================================================================
# EXECUCAO PRINCIPAL — roda as 10 tarefas em sequencia
# ==============================================================================

def main():
    print("#" * 70)
    print("# SIMULADOR R-UDP EM SIMPY — FASE 2")
    print("# PPGCC/UFPI 2026-1 | Manoel Messias Pereira Medeiros | 20251014777")
    print("#" * 70)

    resultados = {}
    resultados["tarefa1"] = tarefa1_modelagem_atraso()
    resultados["tarefa2"] = tarefa2_modelo_perda_bernoulli()
    resultados["tarefa3"] = tarefa3_simulacao_timeout()
    resultados["tarefa4"] = tarefa4_curva_vazao()
    resultados["tarefa5"] = tarefa5_sensibilidade_janela()
    resultados["tarefa6"] = tarefa6_validacao_rtt()
    resultados["tarefa7"] = tarefa7_impacto_jitter()
    resultados["tarefa8"] = tarefa8_cenario_estresse()
    resultados["tarefa9"] = tarefa9_analise_eficiencia()
    resultados["tarefa10"] = tarefa10_convergencia_estatistica(n_execucoes=30)

    # Salva um JSON consolidado com tudo, para facilitar importacao no Colab
    json_path = os.path.join(OUTPUT_DIR, "resultados_consolidados_fase2.json")
    with open(json_path, "w") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)
    print(f"\n[Fase2] JSON consolidado salvo em: {json_path}")

    print("\n" + "#" * 70)
    print("# TODAS AS 10 TAREFAS CONCLUIDAS")
    print(f"# CSVs disponiveis em: {OUTPUT_DIR}")
    print("#" * 70)


if __name__ == "__main__":
    main()
