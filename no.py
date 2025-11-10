import socket
import threading
import time
import argparse
import random
from config import *
from mensagem import Mensagem, Historico, lock_lamport, GerenciadorTempoBerkeley, VotacaoChute
import json

class No:
    def __init__(self, porta, nome=None):
        self.nome = nome or f"No:{porta}"
        self.porta = porta
        self.ip = ENDERECO_PADRAO_BIND if TESTE_MAQUINA_UNICA_LOCAL else socket.gethostbyname(socket.gethostname())
        self.addr = (self.ip, self.porta)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.ip, self.porta))

        self.msock = criar_socket_multicast(endereco_bind=self.ip, porta=PORTA_MULTICAST)

        self.eh_coordenador = False
        self.coordenador_id = None
        self.coordenador_addr = None
        self.coordenador_nome = None

        self.id = None
        self.proximo_id = 1
        self.peers = {}
        self.lock_peers = threading.Lock()

        self.historico = Historico()
        self.lamport = 0

        self.gerenciador_tempo = GerenciadorTempoBerkeley()

        self.ultimo_heartbeat = time.time()
        self.lock_heartbeat = threading.Lock()

        self._eleicao_recebeu_ok = False
        self._em_eleicao = False

        self._respostas_tempo = {}
        self._lock_tempo = threading.Lock()
        self._aguardando_respostas_tempo = False

        self.votacao_ativa = None
        self.lock_votacoes = threading.Lock()
        # usuários chutados (sem expiração temporal)
        self.usuarios_chutados = {}
        self.lock_chutados = threading.Lock()
        
        # controle para sincronização periódica de peers (coordenador)
        self._peers_sync_running = False
        self._peers_sync_lock = threading.Lock()

        self.rodando = True

    def incrementa_lamport(self):
        with lock_lamport:
            self.lamport += 1
            return self.lamport

    def atualiza_lamport_recebendo(self, valor):
        with lock_lamport:
            self.lamport = max(self.lamport, valor) + 1
            return self.lamport

    def multicast_enviar(self, mensagem):
        data = mensagem.to_json().encode('utf-8')
        self.sock.sendto(data, (GRUPO_MULTICAST, PORTA_MULTICAST))

    def unicast_enviar(self, addr, mensagem):
        try:
            data = mensagem.to_json().encode('utf-8')
            self.sock.sendto(data, tuple(addr))
        except Exception as e:
            print(f"[WARN] falha ao enviar para {addr}: {e}")

    def listener_multicast(self):
        while self.rodando:
            try:
                data, addr = self.msock.recvfrom(TAMANHO_BUFFER)
                msg = Mensagem.from_json(data.decode('utf-8'))
                if msg.origem_addr and tuple(msg.origem_addr) == self.addr:
                    continue
                self.tratar_mensagem_multicast(msg, addr)
            except Exception as e:
                if self.rodando:
                    print(f"[ERROR] erro listener_multicast: {e}")

    def listener_unicast(self):
        while self.rodando:
            try:
                data, addr = self.sock.recvfrom(TAMANHO_BUFFER)
                msg = Mensagem.from_json(data.decode('utf-8'))
                self.tratar_mensagem_unicast(msg, addr)
            except Exception as e:
                if self.rodando:
                    print(f"[ERROR] erro listener_unicast: {e}")

    def tratar_mensagem_multicast(self, msg, addr):
        tipo = msg.tipo
        self.atualiza_lamport_recebendo(msg.lamport)
        
        if tipo == "JOIN":
            if self.eh_coordenador:
                novo_id = self.proximo_id
                self.proximo_id += 1
                with self.lock_peers:
                    self.peers[novo_id] = {
                        "addr": tuple(msg.origem_addr),
                        "nome": msg.origem_nome
                    }
                payload = {
                    "assigned_id": novo_id,
                    "coordenador_id": self.id,
                    "coordenador_addr": self.addr,
                    "coordenador_nome": self.nome,
                    "peers": self.peers,
                    "historico": self.historico.to_list()
                }
                resposta = Mensagem("ASSIGN_ID", origem_id=self.id, origem_addr=self.addr, 
                                  origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo=payload)
                destino = tuple(msg.origem_addr)
                print(f"[COORD] atribuindo id {novo_id} ao nó {msg.origem_nome} ({destino})")
                self.unicast_enviar(destino, resposta)
                
        elif tipo == "HEARTBEAT":
            if msg.origem_id is not None:
                with self.lock_heartbeat:
                    self.ultimo_heartbeat = time.time()
                    self.coordenador_id = msg.origem_id
                    self.coordenador_addr = tuple(msg.origem_addr) if msg.origem_addr else None
                    self.coordenador_nome = msg.origem_nome
                    
        elif tipo == "ELECTION":
            candidato_id = msg.origem_id
            print(f"[ELEIÇÃO] Recebida mensagem ELECTION de {msg.origem_nome} (id {candidato_id})")
            
            if self.id is not None and self.id > candidato_id:
                # Este nó tem ID maior, deve responder com OK
                print(f"[ELEIÇÃO] Respondendo com OK para {msg.origem_nome} (meu id {self.id} > {candidato_id})")
                ok = Mensagem("OK", 
                            origem_id=self.id, 
                            origem_addr=self.addr, 
                            origem_nome=self.nome, 
                            lamport=self.incrementa_lamport(), 
                            conteudo=None)
                
                # Responde via UNICAST diretamente ao solicitante
                if msg.origem_addr:
                    try:
                        self.unicast_enviar(tuple(msg.origem_addr), ok)
                        print(f"[ELEIÇÃO] OK enviado para {msg.origem_addr}")
                    except Exception as e:
                        print(f"[ELEIÇÃO] Falha ao enviar OK: {e}")
                
                # Inicia própria eleição se não estiver em uma
                if not self._em_eleicao:
                    print(f"[ELEIÇÃO] Iniciando própria eleição como nó maior...")
                    threading.Thread(target=self.iniciar_eleicao, daemon=True).start()
            else:
                print(f"[ELEIÇÃO] Ignorando ELECTION (meu id {self.id} <= {candidato_id})")
                            
        elif tipo == "OK":
            print(f"[ELEIÇÃO] Recebido OK de {msg.origem_nome} (id {msg.origem_id})")
            self._eleicao_recebeu_ok = True
            
        elif tipo == "COORDINATOR":
            self.coordenador_id = msg.origem_id
            self.coordenador_addr = tuple(msg.origem_addr) if msg.origem_addr else None
            self.coordenador_nome = msg.origem_nome
            # tenta sincronizar lista de peers enviada pelo novo coordenador (se houver)
            payload = msg.conteudo or {}
            peers_payload = payload.get("peers")
            if peers_payload:
                with self.lock_peers:
                    # converte chaves para int e endereços para tuplas
                    try:
                        new_peers = {}
                        for pid, info in peers_payload.items():
                            pid_int = int(pid)
                            if isinstance(info.get("addr"), (list, tuple)):
                                info = dict(info)
                                info["addr"] = tuple(info["addr"])
                            new_peers[pid_int] = info
                        self.peers = new_peers
                        # atualiza próximo id razoavelmente
                        if self.peers:
                            self.proximo_id = max(self.peers.keys()) + 1
                    except Exception:
                        pass
            self.eh_coordenador = (self.coordenador_id == self.id)
            with self.lock_heartbeat:
                self.ultimo_heartbeat = time.time()
            if self.eh_coordenador:
                print(f"[INFO] {self.nome} foi eleito coordenador.")
                threading.Thread(target=self.iniciar_sincronizacao_berkeley, daemon=True).start()
            else:
                print(f"[INFO] Novo coordenador: {self.coordenador_nome} (id {self.coordenador_id})")
                
        elif tipo == "CHAT":
            conteudo = msg.conteudo
            if isinstance(conteudo, dict) and "texto" in conteudo:
                texto = conteudo["texto"]
                origem_id = msg.origem_id
                origem_nome = msg.origem_nome or f"Unknown_{origem_id}"
                lamport = msg.lamport
                ts_berkeley = self.gerenciador_tempo.get_tempo()
                self.historico.adiciona(lamport, origem_id, origem_nome, texto, ts_berkeley)
                tempo_formatado = self.gerenciador_tempo.formatar_tempo(ts_berkeley)
                print(f"\n[{tempo_formatado}] {origem_nome}: {texto}\n> ", end="")
                
        elif tipo == "TIME_REQUEST":
            if not self.eh_coordenador:
                tempo_local = time.time()
                resposta = Mensagem("TIME_RESPONSE", origem_id=self.id, origem_addr=self.addr,
                                  origem_nome=self.nome, lamport=self.incrementa_lamport(),
                                  conteudo={"tempo_local": tempo_local})
                self.unicast_enviar(tuple(msg.origem_addr), resposta)
                
        elif tipo == "TIME_ADJUST":
            if not self.eh_coordenador:
                correcao = msg.conteudo.get("correcao", 0)
                self.gerenciador_tempo.set_correcao(correcao)
                print(f"[TEMPO] Tempo ajustado: correção de {correcao:.2f} segundos aplicada")
                
        elif tipo == "KICK_VOTE_START":
            self.tratar_inicio_votacao_chute(msg)
            
        elif tipo == "KICK_VOTE":
            self.tratar_voto_chute(msg)
            
        elif tipo == "KICK_RESULT":
            self.tratar_resultado_chute(msg)
        
        elif tipo == "PEERS_UPDATE":
            # atualização autoritativa de peers enviada pelo coordenador
            try:
                self.tratar_peers_update(msg)
            except Exception as e:
                print(f"[WARN] falha ao aplicar PEERS_UPDATE: {e}")

        elif tipo == "GOODBYE":
            saiu_id = None
            try:
                saiu_id = msg.conteudo.get("id") if isinstance(msg.conteudo, dict) else None
            except Exception:
                saiu_id = None
            if saiu_id is not None:
                with self.lock_peers:
                    if saiu_id in self.peers:
                        nome_saiu = self.peers[saiu_id].get("nome", f"Unknown_{saiu_id}")
                        del self.peers[saiu_id]
                        print(f"[INFO] {nome_saiu} saiu do chat.")
                # se o coordenador saiu, dispara eleição
                if saiu_id == self.coordenador_id:
                    print(f"[ALERTA] Coordenador ({self.coordenador_nome}) saiu — iniciando eleição...")
                    threading.Thread(target=self.iniciar_eleicao, daemon=True).start()

    def tratar_mensagem_unicast(self, msg, addr):
        tipo = msg.tipo
        self.atualiza_lamport_recebendo(msg.lamport)
        
        if tipo == "ASSIGN_ID":
            payload = msg.conteudo
            assigned_id = payload.get("assigned_id")
            self.id = assigned_id
            self.coordenador_id = payload.get("coordenador_id")
            self.coordenador_addr = tuple(payload.get("coordenador_addr"))
            self.coordenador_nome = payload.get("coordenador_nome")
            peers = payload.get("peers", {})
            with self.lock_peers:
                new_peers = {}
                for pid, peer_info in peers.items():
                    try:
                        pid_int = int(pid)
                    except Exception:
                        pid_int = pid
                    # garante que addr seja tupla
                    if isinstance(peer_info.get("addr"), list):
                        peer_info = dict(peer_info)
                        peer_info["addr"] = tuple(peer_info["addr"])
                    new_peers[pid_int] = peer_info
                self.peers = new_peers
            hist = payload.get("historico", [])
            self.historico.estende(hist)
            peer_nomes = [info.get("nome", f"Unknown_{pid}") for pid, info in self.peers.items() if pid != self.id]
            print(f"[INFO] Recebi ID {self.id} do coordenador {self.coordenador_nome}. Peers ativos: {peer_nomes}")
            
        elif tipo == "PEERS_REQUEST":
            # um nó solicitou a lista de peers; responde se eu for coordenador
            if self.eh_coordenador:
                with self.lock_peers:
                    peers_for_send = {}
                    for pid, info in self.peers.items():
                        addr = info.get("addr")
                        peers_for_send[str(pid)] = {"addr": list(addr) if isinstance(addr, (list, tuple)) else addr, "nome": info.get("nome")}
                resp = Mensagem("PEERS_RESPONSE", origem_id=self.id, origem_addr=self.addr,
                                origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo={"peers": peers_for_send})
                # envia de volta para o solicitante
                self.unicast_enviar(tuple(msg.origem_addr), resp)
        elif tipo == "PEERS_RESPONSE":
            # resposta a uma requisição de peers — aplica a lista recebida
            try:
                self.tratar_peers_update(msg)
            except Exception as e:
                print(f"[WARN] falha aplicando PEERS_RESPONSE: {e}")
        elif tipo == "HISTORY":
            hist = msg.conteudo
            self.historico.estende(hist)
            
        elif tipo == "GOODBYE":
            saiu_id = msg.conteudo.get("id")
            with self.lock_peers:
                if saiu_id in self.peers:
                    nome_saiu = self.peers[saiu_id].get("nome", f"Unknown_{saiu_id}")
                    del self.peers[saiu_id]
            print(f"[INFO] {nome_saiu} saiu do chat.")
            
        elif tipo == "TIME_RESPONSE":
            if self.eh_coordenador and self._aguardando_respostas_tempo:
                origem_id = msg.origem_id
                tempo_local = msg.conteudo.get("tempo_local")
                with self._lock_tempo:
                    self._respostas_tempo[origem_id] = tempo_local

    def tratar_inicio_votacao_chute(self, msg):

            conteudo = msg.conteudo
            alvo_id = conteudo.get("alvo_id")
            alvo_nome = conteudo.get("alvo_nome")
            iniciador_id = msg.origem_id
            iniciador_nome = msg.origem_nome
            
            with self.lock_peers:
                if alvo_id not in self.peers:
                    print(f"[VOTAÇÃO] Alvo {alvo_nome} não encontrado para chute")
                    return
                    
            with self.lock_votacoes:
                if self.votacao_ativa is not None:
                    print(f"[VOTAÇÃO] Já existe uma votação ativa")
                    return
                    
                self.votacao_ativa = VotacaoChute(alvo_id, alvo_nome, iniciador_id, iniciador_nome)
                
            payload = {
                "alvo_id": alvo_id,
                "alvo_nome": alvo_nome,
                "iniciador_id": iniciador_id,
                "iniciador_nome": iniciador_nome,
                "duracao": DURACAO_VOTACAO
            }
            vote_start_msg = Mensagem("KICK_VOTE_START", origem_id=self.id, origem_addr=self.addr,
                                    origem_nome=self.nome, lamport=self.incrementa_lamport(),
                                    conteudo=payload)
            self.multicast_enviar(vote_start_msg)
            
            print(f"\n[VOTAÇÃO] Iniciada votação para chutar {alvo_nome}")
            print(f"[VOTAÇÃO] Use 'votar sim' ou 'votar não' para participar")
            print(f"[VOTAÇÃO] Tempo restante: {DURACAO_VOTACAO} segundos\n> ", end="")
            
            threading.Thread(target=self.monitorar_votacao, daemon=True).start()

            

    def tratar_voto_chute(self, msg):
        if self.eh_coordenador and self.votacao_ativa:
            conteudo = msg.conteudo
            voter_id = msg.origem_id
            voto = conteudo.get("voto")
            
            with self.lock_votacoes:
                if self.votacao_ativa and not self.votacao_ativa.encerrada:
                    sucesso = self.votacao_ativa.adicionar_voto(voter_id, voto)
                    if sucesso:
                        print(f"[VOTAÇÃO] Voto recebido de {msg.origem_nome}: {'SIM' if voto else 'NÃO'}")

    def tratar_resultado_chute(self, msg):
        conteudo = msg.conteudo
        alvo_id = conteudo.get("alvo_id")
        alvo_nome = conteudo.get("alvo_nome")
        chutado = conteudo.get("chutado")
        votos_favor = conteudo.get("votos_favor", 0)
        votos_contra = conteudo.get("votos_contra", 0)
        
        with self.lock_votacoes:
            self.votacao_ativa = None
            
        if chutado:
            with self.lock_chutados:
                # marca permanentemente como chutado (sem expiração)
                self.usuarios_chutados[alvo_id] = True
            print(f"\n[VOTAÇÃO] {alvo_nome} foi CHUTADO!")
            print(f"[VOTAÇÃO] Votos: {votos_favor} a favor, {votos_contra} contra\n> ", end="")
            
            # Se eu for o chutado, interrompe imediatamente (não permite enviar mensagens)
            if alvo_id == self.id:
                print(f"[CHUTE] Você ({self.nome}) foi chutado — desconectando...")
                # marca como não rodando e fecha sockets sem enviar GOODBYE
                self.rodando = False
                try:
                    self.msock.close()
                except:
                    pass
                try:
                    self.sock.close()
                except:
                    pass
                return
            
            if self.eh_coordenador:
                with self.lock_peers:
                    if alvo_id in self.peers:
                        KICK_msg = Mensagem("KICK_RESULT", origem_id=self.id, origem_addr=self.addr,
                                         origem_nome=self.nome, lamport=self.incrementa_lamport(),
                                         conteudo={"chutado": True, "motivo": "Votação da comunidade"})
                        alvo_addr = self.peers[alvo_id]["addr"]
                        self.unicast_enviar(alvo_addr, KICK_msg)
                        del self.peers[alvo_id]
                        # informa todos que o usuário saiu (propaga remoção)
                        goodbye = Mensagem("GOODBYE", origem_id=self.id, origem_addr=self.addr,
                                           origem_nome=self.nome, lamport=self.incrementa_lamport(),
                                           conteudo={"id": alvo_id})
                        self.multicast_enviar(goodbye)
        else:
            print(f"\n[VOTAÇÃO] {alvo_nome} NÃO foi chutado")
            print(f"[VOTAÇÃO] Votos: {votos_favor} a favor, {votos_contra} contra\n> ", end="")

    def monitorar_votacao(self):
        while self.rodando:
            time.sleep(1)
            # captura cópia segura da votação atual
            with self.lock_votacoes:
                vot = self.votacao_ativa
            if not vot:
                break
            if vot.encerrada:
                with self.lock_votacoes:
                    self.votacao_ativa = None
                break

            with self.lock_peers:
                total_usuarios = len(self.peers)

            resultado = vot.verificar_resultado(total_usuarios)

            if resultado is not None:
                with self.lock_votacoes:
                    votos_favor = sum(1 for v in vot.votos.values() if v)
                    votos_contra = sum(1 for v in vot.votos.values() if not v)
                    
                    payload = {
                        "alvo_id": vot.alvo_id,
                        "alvo_nome": vot.alvo_nome,
                        "chutado": resultado,
                        "votos_favor": votos_favor,
                        "votos_contra": votos_contra
                    }
                    
                    result_msg = Mensagem("KICK_RESULT", origem_id=self.id, origem_addr=self.addr,
                                        origem_nome=self.nome, lamport=self.incrementa_lamport(),
                                        conteudo=payload)
                    self.multicast_enviar(result_msg)
                    
                    self.votacao_ativa = None
                break

    def iniciar_votacao_chute(self, alvo_nome):
        if not self.eh_coordenador:
            print("[ERRO] Apenas o coordenador pode iniciar votações de chute")
            return
            
        alvo_id = None
        with self.lock_peers:
            for pid, info in self.peers.items():
                if info["nome"].lower() == alvo_nome.lower():
                    alvo_id = pid
                    break
                    
        if alvo_id is None:
            print(f"[ERRO] Usuário '{alvo_nome}' não encontrado")
            return
            
        if alvo_id == self.id:
            print("[ERRO] Você não pode iniciar uma votação contra si mesmo")
            return
            
        with self.lock_votacoes:
            if self.votacao_ativa is not None:
                print("[ERRO] Já existe uma votação ativa")
                return
                
        payload = {
            "alvo_id": alvo_id,
            "alvo_nome": self.peers[alvo_id]["nome"]
        }
        vote_start = Mensagem("KICK_VOTE_START", origem_id=self.id, origem_addr=self.addr,
                            origem_nome=self.nome, lamport=self.incrementa_lamport(),
                            conteudo=payload)
        self.multicast_enviar(vote_start)

    def votar_chute(self, voto):
        if not self.votacao_ativa:
            print("[ERRO] Não há votação ativa no momento")
            return
            
        if self.votacao_ativa.encerrada:
            print("[ERRO] A votação já foi encerrada")
            return
            
        voto_bool = voto.lower() in ['sim', 's', 'yes', 'y', 'true']
        
        payload = {"voto": voto_bool}
        vote_msg = Mensagem("KICK_VOTE", origem_id=self.id, origem_addr=self.addr,
                          origem_nome=self.nome, lamport=self.incrementa_lamport(),
                          conteudo=payload)
        self.multicast_enviar(vote_msg)
        
        print(f"[VOTAÇÃO] Seu voto ({'SIM' if voto_bool else 'NÃO'}) foi enviado")

    def verificar_chute(self, user_id):
        with self.lock_chutados:
            return user_id in self.usuarios_chutados

    def entrar_na_rede(self):
        if self.verificar_chute(self.id):
            print("[ERRO] Você está chutado e não pode entrar no chat")
            self.rodando = False
            return
            
        print(f"[ENTRADA] {self.nome} enviando JOIN via multicast")
        payload = {"addr": self.addr}
        join = Mensagem("JOIN", origem_id=self.id, origem_addr=self.addr, 
                       origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo=payload)
        self.multicast_enviar(join)
        espera = 0.0
        while self.id is None and espera < TIMEOUT_SINCRONIZACAO_HISTORICO:
            time.sleep(0.2)
            espera += 0.2
        if self.id is None:
            print("[WARN] não recebeu ASSIGN_ID — assumindo coordenador (rede vazia)")
            self.tornar_coordenador_inicial()
        else:
            print(f"[OK] {self.nome} entrou com id {self.id}")

    def tornar_coordenador_inicial(self):
        self.id = 1
        self.eh_coordenador = True
        self.coordenador_id = self.id
        self.coordenador_addr = self.addr
        self.coordenador_nome = self.nome
        with self.lock_peers:
            self.peers[self.id] = {
                "addr": self.addr,
                "nome": self.nome
            }
        self.proximo_id = 2
        print(f"[COORDENADOR] {self.nome} criado como coordenador inicial com id 1")
        threading.Thread(target=self.iniciar_sincronizacao_berkeley, daemon=True).start()
        # inicia broadcast periódico da lista de peers
        threading.Thread(target=self._peers_sync_loop, daemon=True).start()

    def loop_heartbeat(self):
        while self.rodando:
            if self.eh_coordenador:
                hb = Mensagem("HEARTBEAT", origem_id=self.id, origem_addr=self.addr, 
                             origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo=None)
                self.multicast_enviar(hb)
            time.sleep(INTERVALO_HEARTBEAT)

    def monitor_heartbeat(self):
        while self.rodando:
            time.sleep(0.5)
            with self.lock_heartbeat:
                delta = time.time() - self.ultimo_heartbeat
            if not self.eh_coordenador and self.coordenador_id is not None and delta > TIMEOUT_HEARTBEAT:
                print(f"[ALERTA] Não houve heartbeat do coordenador {self.coordenador_nome}. Iniciando eleição...")
                threading.Thread(target=self.iniciar_eleicao, daemon=True).start()
                with self.lock_heartbeat:
                    self.ultimo_heartbeat = time.time()

    def iniciar_eleicao(self):
        self.enviar_atualizacao_peers()
        if self.id is None:
            return
            
        self._em_eleicao = True
        self._eleicao_recebeu_ok = False
        
        print(f"[ELEIÇÃO] {self.nome} (id {self.id}) iniciou eleição!")
        
        # Envia mensagem ELECTION via UNICAST para todos os nós com id maior
        maiores = []
        with self.lock_peers:
            for pid, peer in self.peers.items():
                if pid > self.id:
                    maiores.append((pid, peer))
        
        if not maiores:
            # Não há nó maior, vira coordenador
            print(f"[ELEIÇÃO] {self.nome} não encontrou nó maior, tornando-se coordenador!")
            self.anunciar_coordenador()
            self._em_eleicao = False
            return
            
        # Envia ELECTION via UNICAST para cada nó maior
        election_sent = False
        for pid, peer in maiores:
            try:
                msg = Mensagem("ELECTION", 
                            origem_id=self.id, 
                            origem_addr=self.addr, 
                            origem_nome=self.nome, 
                            lamport=self.incrementa_lamport(), 
                            conteudo=None)
                print(f"[ELEIÇÃO] Enviando ELECTION para {peer['nome']} (id {pid}) em {peer['addr']}")
                self.unicast_enviar(peer["addr"], msg)
                election_sent = True
            except Exception as e:
                print(f"[ELEIÇÃO] Falha ao enviar ELECTION para {peer['nome']}: {e}")
        
        if not election_sent:
            print(f"[ELEIÇÃO] Não foi possível enviar ELECTION para nenhum nó maior")
            self.anunciar_coordenador()
            self._em_eleicao = False
            return
        
        # Aguarda OK por um tempo limitado
        print(f"[ELEIÇÃO] Aguardando respostas OK por {TIMEOUT_ELEICAO} segundos...")
        inicio = time.time()
        
        while time.time() - inicio < TIMEOUT_ELEICAO:
            if self._eleicao_recebeu_ok:
                print(f"[ELEIÇÃO] {self.nome} recebeu OK de nó maior, aguardando anúncio de coordenador...")
                # Reseta flag para eleições futuras
                self._eleicao_recebeu_ok = False
                self._em_eleicao = False
                return
            time.sleep(0.1)
        
        # Se não recebeu OK dentro do timeout, assume coordenação
        print(f"[ELEIÇÃO] {self.nome} não recebeu OK dentro do timeout, tornando-se coordenador!")
        self.anunciar_coordenador()
        self._em_eleicao = False

    def anunciar_coordenador(self):
        self.eh_coordenador = True
        self.coordenador_id = self.id
        self.coordenador_addr = self.addr
        self.coordenador_nome = self.nome
        with self.lock_peers:
            self.peers[self.id] = {
                "addr": self.addr,
                "nome": self.nome
            }
        coord_msg = Mensagem("COORDINATOR", origem_id=self.id, origem_addr=self.addr, 
                           origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo={"peers": self.peers})
        self.multicast_enviar(coord_msg)
        print(f"[COORDENADOR] {self.nome} anunciado como coordenador via multicast")
        threading.Thread(target=self.iniciar_sincronizacao_berkeley, daemon=True).start()
        # inicia broadcast periódico da lista de peers
        threading.Thread(target=self._peers_sync_loop, daemon=True).start()

    def iniciar_sincronizacao_berkeley(self):
        # Reescrita do algoritmo de sincronização Berkeley
        if not self.eh_coordenador:
            return
        while self.rodando and self.eh_coordenador:
            time.sleep(INTERVALO_SINCRONIZACAO_BERKELEY + random.uniform(-5, 5))
            if not self.rodando or not self.eh_coordenador:
                break
            print("[TEMPO] Iniciando sincronização de tempo Berkeley...")
            self._sincronizar_berkeley()
    def sincronizar_berkeley(self):
        # 1. Envia TIME_REQUEST para todos os peers
        with self.lock_peers:
            peers_ativos = {pid: peer for pid, peer in self.peers.items() if pid != self.id}
        if not peers_ativos:
            print("[TEMPO] Nenhum nó ativo para sincronização")
            return
        respostas = {}
        # 2. Envia requisição de tempo
        msg_req = Mensagem("TIME_REQUEST", origem_id=self.id, origem_addr=self.addr, origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo={})
        for pid, peer in peers_ativos.items():
            try:
                self.unicast_enviar(peer["addr"], msg_req)
            except Exception as e:
                print(f"[TEMPO] Falha ao enviar requisição de tempo para {peer['nome']}: {e}")
        # 3. Aguarda respostas
        inicio = time.time()
        while time.time() - inicio < TIMEOUT_REQUISICAO_BERKELEY:
            with self._lock_tempo:
                respostas.update(self._respostas_tempo)
            if len(respostas) >= len(peers_ativos):
                break
            time.sleep(0.2)
        if not respostas:
            print("[TEMPO] Nenhuma resposta recebida para sincronização")
            return
        # 4. Calcula média
        tempos = [time.time()] + list(respostas.values())
        media = sum(tempos) / len(tempos)
        print(f"[TEMPO] Média calculada: {time.strftime('%H:%M:%S', time.localtime(media))} (de {len(tempos)} nós)")
        # 5. Calcula correções e envia TIME_ADJUST
        for pid, peer in peers_ativos.items():
            correcao = media - respostas.get(pid, time.time())
            msg_adj = Mensagem("TIME_ADJUST", origem_id=self.id, origem_addr=self.addr, origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo={"correcao": correcao})
            try:
                self.unicast_enviar(peer["addr"], msg_adj)
                print(f"[TEMPO] Enviando correção de {correcao:.2f}s para nó {pid}")
            except Exception as e:
                print(f"[TEMPO] Falha ao enviar ajuste de tempo para {peer['nome']}: {e}")
        # Aplica correção local
        correcao_local = media - time.time()
        self.gerenciador_tempo.ajustar_correcao(correcao_local)
        print(f"[TEMPO] Correção local aplicada: {correcao_local:.2f}s")

    def executar_berkeley_sync(self):
        if not self.eh_coordenador:
            return
            
        with self.lock_peers:
            peers_ativos = [pid for pid in self.peers.keys() if pid != self.id]
            
        if not peers_ativos:
            print("[TEMPO] Nenhum nó ativo para sincronização")
            return
            
        with self._lock_tempo:
            self._respostas_tempo = {}
            self._aguardando_respostas_tempo = True
            
        time_request = Mensagem("TIME_REQUEST", origem_id=self.id, origem_addr=self.addr,
                              origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo={})
        
        for peer_id in peers_ativos:
            peer_addr = self.peers[peer_id]["addr"]
            self.unicast_enviar(peer_addr, time_request)
            
        time.sleep(TIMEOUT_REQUISICAO_BERKELEY)
        
        with self._lock_tempo:
            self._aguardando_respostas_tempo = False
            respostas_recebidas = len(self._respostas_tempo)
            
        if respostas_recebidas == 0:
            print("[TEMPO] Nenhuma resposta recebida para sincronização")
            return
            
        todos_tempos = {self.id: time.time()}
        todos_tempos.update(self._respostas_tempo)
        tempos = list(todos_tempos.values())
        media = sum(tempos) / len(tempos)
        print(f"[TEMPO] Média calculada: {time.strftime('%H:%M:%S', time.localtime(media))} (de {len(tempos)} nós)")
        correcoes = {}
        for node_id, node_time in todos_tempos.items():
            correcao = media - node_time
            correcoes[node_id] = correcao
        correcao_local = correcoes.get(self.id, 0)
        self.gerenciador_tempo.ajustar_correcao(correcao_local)
        print(f"[TEMPO] Correção local aplicada: {correcao_local:.2f}s")
        for peer_id in peers_ativos:
            if peer_id in correcoes:
                correcao_peer = correcoes[peer_id]
                time_adjust = Mensagem("TIME_ADJUST", origem_id=self.id, origem_addr=self.addr,
                                     origem_nome=self.nome, lamport=self.incrementa_lamport(),
                                     conteudo={"correcao": correcao_peer})
                peer_addr = self.peers[peer_id]["addr"]
                self.unicast_enviar(peer_addr, time_adjust)
                print(f"[TEMPO] Enviando correção de {correcao_peer:.2f}s para nó {peer_id}")

    # envia a lista autoritativa de peers em formato serializável (addr -> [ip,port])
    def enviar_atualizacao_peers(self):
        if not self.eh_coordenador:
            return
        with self.lock_peers:
            peers_for_send = {}
            for pid, info in self.peers.items():
                addr = info.get("addr")
                addr_list = list(addr) if isinstance(addr, (list, tuple)) else addr
                peers_for_send[str(pid)] = {"addr": addr_list, "nome": info.get("nome")}

        msg = Mensagem("PEERS_UPDATE", origem_id=self.id, origem_addr=self.addr,
                       origem_nome=self.nome, lamport=self.incrementa_lamport(), conteudo={"peers": peers_for_send})
        self.multicast_enviar(msg)

    def _peers_sync_loop(self):
        with self._peers_sync_lock:
            if self._peers_sync_running:
                return
            self._peers_sync_running = True

        try:
            while self.rodando and self.eh_coordenador:
                try:
                    self.enviar_atualizacao_peers()
                except Exception as e:
                    print(f"[AVISO] falha enviando PEERS_UPDATE: {e}")
                time.sleep(INTERVAL_PEERS_SYNC)
        finally:
            with self._peers_sync_lock:
                self._peers_sync_running = False

    def tratar_peers_update(self, msg):
        payload = msg.conteudo or {}
        peers = payload.get("peers", {})
        with self.lock_peers:
            new_peers = {}
            for pid, info in peers.items():
                try:
                    pid_int = int(pid)
                except Exception:
                    pid_int = pid
                info_copy = dict(info)
                if isinstance(info_copy.get("addr"), list):
                    info_copy["addr"] = tuple(info_copy["addr"])
                new_peers[pid_int] = info_copy
            # substitui pela lista autoritativa
            self.peers = new_peers
            # atualiza proximo_id
            if self.peers:
                try:
                    self.proximo_id = max(self.peers.keys()) + 1
                except Exception:
                    pass

    def enviar_chat(self, texto):
        if self.id is None:
            print("[ERR] Você ainda não tem ID; espere o coordenador atribuir.")
            return
        if self.verificar_chute(self.id):
            print("[ERRO] Você está chutado e não pode enviar mensagens")
            return
        lam = self.incrementa_lamport()
        payload = {"texto": texto}
        msg = Mensagem("CHAT", origem_id=self.id, origem_addr=self.addr, 
                      origem_nome=self.nome, lamport=lam, conteudo=payload)
        ts_berkeley = self.gerenciador_tempo.get_tempo()
        self.historico.adiciona(lam, self.id, self.nome, texto, ts_berkeley)
        self.multicast_enviar(msg)
        tempo_formatado = self.gerenciador_tempo.formatar_tempo(ts_berkeley)
        print(f"[{tempo_formatado}] {self.nome}: {texto}")

    def sair(self):
        if self.id is None:
            self.rodando = False

            return
        # sempre multicast para que todos atualizem suas listas de peers
        goodbye = Mensagem("GOODBYE", origem_id=self.id, origem_addr=self.addr, 
                         origem_nome=self.nome, lamport=self.incrementa_lamport(), 
                         conteudo={"id": self.id})
        try:
            self.multicast_enviar(goodbye)
        except Exception:
            # fallback: tenta enviar ao coordenador se multicast falhar
            if self.coordenador_addr:
                try:
                    self.unicast_enviar(self.coordenador_addr, goodbye)
                except Exception:
                    pass
        print(f"[INFO] {self.nome} saindo...")
        self.rodando = False
        try:
            self.msock.close()
            self.sock.close()
        except:
            pass

    def repl(self):
        print(f"[REPL] {self.nome} conectado. Digite mensagens para enviar.")
        print("Comandos: 'historico' mostra histórico, 'usuarios' lista participantes, 'tempo' mostra tempo, 'chutar <nome>' inicia votação, 'votar <sim/não>' vota, 'sair' deixa o chat, 'ajuda' para ver os comandos")
        try:
            while self.rodando:
                cmd = input("> ").strip()

                if cmd == "":
                    print("Comandos: 'historico' mostra histórico, 'usuarios' lista participantes, 'tempo' mostra tempo, 'chutar <nome>' inicia votação, 'votar <sim/não>' vota, 'sair' deixa o chat")
                    continue

                if cmd.lower() == "sair":
                    self.sair()
                    break
                elif cmd.lower() == "historico":
                    print("\n--- HISTÓRICO DE MENSAGENS ---")
                    for it in self.historico.to_list():
                        nome = it.get('origem_nome', f"Unknown_{it['origem_id']}")
                        tempo_formatado = self.gerenciador_tempo.formatar_tempo(it.get('ts_berkeley', it['ts_real']))
                        print(f"[{tempo_formatado}] {nome}: {it['texto']}")
                    print("--- FIM DO HISTÓRICO ---\n")
                elif cmd.lower() == "usuarios":
                    with self.lock_peers:
                        print("\n--- PARTICIPANTES ---")
                        for pid, info in self.peers.items():
                            nome = info.get("nome", f"Unknown_{pid}")
                            status = "(você)" if pid == self.id else ""
                            print(f"  {nome} {status}")
                        print("--- FIM DA LISTA ---\n")
                elif cmd.lower() == "tempo":
                    tempo_atual = self.gerenciador_tempo.get_tempo()
                    correcao = self.gerenciador_tempo.get_correcao()
                    tempo_formatado = self.gerenciador_tempo.formatar_tempo(tempo_atual)
                    print(f"\n[TEMPO] Tempo sincronizado: {tempo_formatado}")
                    print(f"[TEMPO] Correção aplicada: {correcao:.2f} segundos")
                    print(f"[TEMPO] Tempo local: {time.strftime('%H:%M:%S', time.localtime(time.time()))}\n")
                elif cmd.lower().startswith("chutar "):
                    partes = cmd.split(" ", 1)
                    if len(partes) > 1:
                        alvo_nome = partes[1]
                        self.iniciar_votacao_chute(alvo_nome)
                    else:
                        print("Uso: chutar <nome_do_usuario>")
                elif cmd.lower().startswith("votar "):
                    partes = cmd.split(" ", 1)
                    if len(partes) > 1:
                        voto = partes[1]
                        self.votar_chute(voto)
                    else:
                        print("Uso: votar <sim/não>")
                else:
                    if self.verificar_chute(self.id):
                        print("[ERRO] Você está chutado e não pode enviar mensagens")
                    else:
                        self.enviar_chat(cmd)
        except KeyboardInterrupt:
            self.sair()
        except EOFError:
            self.sair()

    def start(self):
        threading.Thread(target=self.listener_multicast, daemon=True).start()
        threading.Thread(target=self.listener_unicast, daemon=True).start()
        threading.Thread(target=self.loop_heartbeat, daemon=True).start()
        threading.Thread(target=self.monitor_heartbeat, daemon=True).start()
        self.entrar_na_rede()
        self.repl()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nó do sistema de chat distribuído")
    parser.add_argument("--port", type=int, required=True, help="porta UDP do nó")
    parser.add_argument("--name", type=str, default=None, help="nome do nó")
    args = parser.parse_args()

    no = No(porta=args.port, nome=args.name)
    no.start()