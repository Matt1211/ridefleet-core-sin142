# ADR-002 — Broker Pub/Sub

**Data:** 2026-04-11
**Status:** Em Discussão 🔄
**Autores:** Subgrupo Core — RideFleet
**Aprovado por:** (pendente — escalar ao Prof. Damaso se não houver consenso até 18/04)

## Contexto

O core precisa gerenciar tópicos pub/sub para rotear eventos entre os serviços (corrida criada, proposta submetida, status atualizado, etc.). A escolha do broker afeta todos os grupos, pois eles precisarão se conectar a ele para receber e publicar eventos.

Por ora, o `docker-compose.core.yml` usa **Redis** como placeholder provisional. Esse arquivo deve ser atualizado após a decisão.

## Opções em discussão

### Opção A — Redis Streams

**Prós:**
- Container único, já presente no stack (também usado como lock store provisional)
- Baixíssima barreira de entrada; todos os grupos conhecem Redis
- Redis Streams tem semântica pub/sub + consumer groups adequada ao projeto

**Contras:**
- Não é um broker de mensagens dedicado
- Menos tooling de observabilidade nativo que Kafka/RabbitMQ

---

### Opção B — RabbitMQ (Recomendada pelo core)

**Prós:**
- Broker de mensagens maduro com suporte a múltiplos protocolos (AMQP, STOMP, MQTT)
- Interface de administração excelente (`http://localhost:15672`)
- Bem documentado; bibliotecas em todas as linguagens dos grupos
- Modelo de exchange/queue flexível

**Contras:**
- Container adicional (~150 MB)
- AMQP tem curva de aprendizado inicial

---

### Opção C — Apache Kafka

**Prós:**
- Padrão da indústria para event streaming
- Alta durabilidade e replayability de eventos (relevante para audit log)

**Contras:**
- Operacionalmente pesado (requer ZooKeeper ou KRaft)
- Excessivo para a escala do projeto (5 grupos)

## Decisão pendente

O subgrupo core deve deliberar e fechar esta decisão **até 18/04/2026**.

Para forçar consenso: votação simples entre os representantes. Em caso de empate, escalar ao Prof. Damaso com label `needs-senior-architect`.

## Impacto após decisão

1. Atualizar `broker/config/topics.yaml` com a tecnologia escolhida
2. Atualizar `infra/docker-compose.core.yml` (substituir Redis pelo broker definitivo se diferente)
3. Comunicar os grupos com **mínimo 48h de antecedência** antes de exigir integração
4. Criar issue com label `groups:needs-migration`
