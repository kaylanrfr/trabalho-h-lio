import json
import time
import threading
from threading import Lock
from config import *

lock_lamport = Lock()

class Mensagem:
    def __init__(self, tipo, origem_id=None, origem_addr=None, origem_nome=None, lamport=0, conteudo=None):
        self.tipo = tipo
        self.origem_id = origem_id
        self.origem_addr = origem_addr
        self.origem_nome = origem_nome
        self.lamport = lamport
        self.conteudo = conteudo

    def to_json(self):
        return json.dumps({
            "tipo": self.tipo,
            "origem_id": self.origem_id,
            "origem_addr": self.origem_addr,
            "origem_nome": self.origem_nome,
            "lamport": self.lamport,
            "conteudo": self.conteudo
        })

    @staticmethod
    def from_json(s):
        data = json.loads(s)
        return Mensagem(
            tipo=data.get("tipo"),
            origem_id=data.get("origem_id"),
            origem_addr=data.get("origem_addr"),
            origem_nome=data.get("origem_nome"),
            lamport=data.get("lamport", 0),
            conteudo=data.get("conteudo")
        )

class Historico:
    def __init__(self):
        self.itens = []
        self.lock = Lock()

    def adiciona(self, lamport, origem_id, origem_nome, texto, ts_berkeley=None):
        with self.lock:
            if ts_berkeley is None:
                ts_berkeley = time.time()
            self.itens.append({
                "lamport": lamport,
                "origem_id": origem_id,
                "origem_nome": origem_nome,
                "texto": texto,
                "ts_real": time.time(),
                "ts_berkeley": ts_berkeley
            })
            self.itens.sort(key=lambda x: (x["lamport"], x["origem_id"]))

    def estende(self, lista):
        with self.lock:
            for item in lista:
                existe = any((it["lamport"] == item["lamport"] and 
                            it["origem_id"] == item["origem_id"] and 
                            it["texto"] == item["texto"]) for it in self.itens)
                if not existe:
                    self.itens.append(item)
            self.itens.sort(key=lambda x: (x["lamport"], x["origem_id"]))

    def to_list(self):
        with self.lock:
            return list(self.itens)

class GerenciadorTempoBerkeley:
    def __init__(self):
        self.correcao = 0.0
        self.lock = Lock()
        self.ultima_sincronizacao = 0
        
    def get_tempo(self):
        with self.lock:
            return time.time() + self.correcao
            
    def get_correcao(self):
        with self.lock:
            return self.correcao
            
    def set_correcao(self, nova_correcao):
        with self.lock:
            if abs(nova_correcao) > LIMITE_CORRECAO_TEMPO:
                nova_correcao = LIMITE_CORRECAO_TEMPO if nova_correcao > 0 else -LIMITE_CORRECAO_TEMPO
            self.correcao = nova_correcao
            self.ultima_sincronizacao = time.time()
            
    def ajustar_correcao(self, delta):
        with self.lock:
            nova_correcao = self.correcao + delta
            if abs(nova_correcao) > LIMITE_CORRECAO_TEMPO:
                return
            self.correcao = nova_correcao
            self.ultima_sincronizacao = time.time()
            
    def formatar_tempo(self, timestamp=None):
        if timestamp is None:
            timestamp = self.get_tempo()
        return time.strftime("%H:%M:%S", time.localtime(timestamp))

class VotacaoChute:
    def __init__(self, alvo_id, alvo_nome, iniciador_id, iniciador_nome):
        self.alvo_id = alvo_id
        self.alvo_nome = alvo_nome
        self.iniciador_id = iniciador_id
        self.iniciador_nome = iniciador_nome
        self.inicio_tempo = time.time()
        self.votos = {iniciador_id: True}
        self.encerrada = False
        self.resultado = None
        self.lock = threading.Lock()
        
    def adicionar_voto(self, voter_id, voto):
        with self.lock:
            if self.encerrada:
                return False
            self.votos[voter_id] = voto
            return True
            
    def verificar_resultado(self, total_usuarios):
        with self.lock:
            if self.encerrada:
                return self.resultado
                
            votos_favor = sum(1 for v in self.votos.values() if v)
            votos_contra = sum(1 for v in self.votos.values() if not v)
            
            if votos_favor >= VOTOS_MINIMOS + 1:
                self.encerrada = True
                self.resultado = True
                return True
            elif votos_contra > total_usuarios / 2:
                self.encerrada = True
                self.resultado = False
                return False
            elif time.time() - self.inicio_tempo > DURACAO_VOTACAO:
                self.encerrada = True
                self.resultado = (votos_favor >= VOTOS_MINIMOS + 1)
                return self.resultado
                
            return None
            
    def tempo_restante(self):
        return max(0, DURACAO_VOTACAO - (time.time() - self.inicio_tempo))