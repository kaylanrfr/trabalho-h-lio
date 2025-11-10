# Sistema de Chat Distribuído com Tolerância a Falhas

Implementação de um sistema de chat peer-to-peer distribuído desenvolvido para a disciplina de Sistemas Distribuídos. O sistema opera sem servidor central, utilizando uma arquitetura distribuída onde cada nó participa ativamente da rede.

## Requisitos do Sistema

### Execução do Sistema

#### Execução Automatizada
1. Execute o arquivo `iniciar_teste.bat` para iniciar 4 instâncias do sistema
2. Cada instância será iniciada em uma porta diferente (10001-10004)

#### Execução Manual
```
# Primeiro nó (assume função de coordenador)
python no.py --port 10001 --name Node1

# Nós subsequentes
python no.py --port 10002 --name Node2
python no.py --port 10003 --name Node3
python no.py --port 10004 --name Node4
```

### Comandos do Sistema
- `usuarios` - Lista participantes ativos
- `historico` - Exibe histórico de mensagens
- `tempo` - Exibe informações de tempo sincronizado
- `chutar <nome>` - Inicia processo de votação para remoção (exclusivo do coordenador)
- `votar sim/nao` - Participação em votação ativa
- `sair` - Encerra a conexão do nó

## Implementação dos Algoritmos

### 1. Algoritmo de Eleição (Bully)
O sistema implementa o algoritmo Bully para eleição de coordenador, garantindo:
- Detecção de falha do coordenador por ausência de heartbeat
- Processo de eleição baseado em IDs
- Reorganização automática da rede após falhas

### 2. Sincronização de Relógios (Berkeley) x AINDA NÃO FUNCIONA
Implementação do algoritmo de Berkeley para sincronização temporal:
- Coordenador coleta tempos locais periodicamente
- Cálculo de média dos tempos recebidos
- Distribuição de ajustes para todos os nós
- Intervalo de sincronização configurável

### 3. Protocolo de Comunicação
- Implementação baseada em UDP Multicast
- Endereço do grupo: 224.1.1.1
- Porta padrão: 5007
- Suporte a descoberta automática de nós

### 4. Sistema de Votação
Mecanismo democrático para remoção de nós:
- Iniciado exclusivamente pelo coordenador
- Requer quórum mínimo
- Contabilização automática de votos
- Execução da decisão após término da votação

### 5. Consistência do Histórico
- Implementação de relógios lógicos de Lamport
- Garantia de ordem causal das mensagens
- Sincronização de histórico para novos nós
- Manutenção de consistência entre participantes

## Arquitetura do Sistema

### Componentes Principais
- `no.py`: Implementação core dos nós da rede
- `mensagem.py`: Classes de mensagens e gerenciamento de histórico
- `config.py`: Configurações e constantes do sistema
- `iniciar_teste.bat`: Script de inicialização para testes


