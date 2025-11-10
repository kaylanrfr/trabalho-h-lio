import socket
import time
#variável pra teste local usando porta
TESTE_MAQUINA_UNICA_LOCAL = True
ENDERECO_PADRAO_BIND = '0.0.0.0' if not TESTE_MAQUINA_UNICA_LOCAL else '127.0.0.1'
#tamanho do buffer
TAMANHO_BUFFER = 65536

#grupo multicast para ser o único ponte de identificação
GRUPO_MULTICAST = '224.1.1.1'
PORTA_MULTICAST = 5007


#configurações para atualização do coordenador
INTERVALO_HEARTBEAT = 1.0
TIMEOUT_HEARTBEAT = 3.0
TIMEOUT_ELEICAO = 5.0
TIMEOUT_SINCRONIZACAO_HISTORICO = 5.0


#usei o berkley para sicronização dos relogios
#ainda não funciona 100% procurando uns bugs ainda mas a parte pedida do condigo funciona legal!
INTERVALO_SINCRONIZACAO_BERKELEY = 30000
TIMEOUT_REQUISICAO_BERKELEY = 3
LIMITE_CORRECAO_TEMPO = 10

INTERVAL_PEERS_SYNC = 15

#configuração para chutar de usuarios temporariamente
DURACAO_VOTACAO = 30.0
VOTOS_MINIMOS = 1
TEMPO_CHUTE = 300.0



def criar_socket_multicast(endereco_bind=ENDERECO_PADRAO_BIND, porta=PORTA_MULTICAST):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((endereco_bind, porta))
    except OSError:
        sock.bind(('', porta))
    mreq = socket.inet_aton(GRUPO_MULTICAST) + socket.inet_aton(endereco_bind)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return sock

def timestamp_legivel(timestamp=None):
    if timestamp is None:
        timestamp = time.time()
    return time.strftime("%H:%M:%S", time.localtime(timestamp))