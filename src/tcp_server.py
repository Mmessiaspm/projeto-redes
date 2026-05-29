import socket
import os
from common import CUSTOM_AUTH

HOST = "0.0.0.0"
PORT = 5001

os.makedirs("data", exist_ok=True)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen()
    print(f"[TCP Server] Aguardando conexão na porta {PORT}...")

    while True:
        conn, addr = s.accept()
        with conn:
            print(f"[TCP Server] Conectado: {addr}")

            # Lê TODOS os dados primeiro
            dados = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                dados += chunk

            # Separa cabeçalho do arquivo
            # Cabeçalho termina com \n
            separador = dados.find(b"\n")
            header  = dados[:separador].decode()
            arquivo = dados[separador+1:]

            print(f"[TCP Server] Header: {header.strip()}")

            if CUSTOM_AUTH not in header:
                print("[TCP Server] ERRO: Auth inválido!")
            else:
                print("[TCP Server] Auth OK! Salvando arquivo...")
                with open("data/recebido_tcp.bin", "wb") as f:
                    f.write(arquivo)
                print(f"[TCP Server] Arquivo recebido! Total: {len(arquivo)} bytes")
