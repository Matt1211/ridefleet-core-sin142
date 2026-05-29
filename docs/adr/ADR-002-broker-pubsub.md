# ADR-002 — Broker Pub/Sub

**Data:** 2026-04-11
**Status:** Implementado
**Autores:** Subgrupo Core — RideFleet

## Contexto

O Core precisa gerenciar tópicos pub/sub para rotear eventos entre os serviços (corrida criada, proposta, status, locks, compensação). A escolha do broker afeta todos os grupos, pois eles precisam se conectar para receber eventos.

## Opções avaliadas

| Opção | Prós | Contras |
|-------|------|---------|
| Redis Streams | Container único, baixa barreira | Não é broker dedicado; menos observabilidade |
| **RabbitMQ** ✅ | Broker maduro, admin UI excelente, bibliotecas em todas as linguagens, modelo topic/exchange flexível | Container adicional (~150 MB); curva AMQP |
| Apache Kafka | Padrão indústria, alta durabilidade | Operacionalmente pesado (ZooKeeper/KRaft); excessivo para 5 grupos |

## Decisão

**RabbitMQ 3.13** com exchange do tipo **topic** (`ridefleet.core.events`).

## Implementação

- Exchange: `ridefleet.core.events` (topic, durable)
- 8 filas declaradas automaticamente na inicialização do Core (`app/rabbitmq.py`)
- Mensagens persistentes (`DeliveryMode.PERSISTENT`)
- Auction worker usa `prefetch_count=1` para serializar leilões por vez

Ver [`broker/README.md`](../../broker/README.md) para a tabela completa de filas e routing keys.

## Impacto nos grupos

- Grupos **não precisam consumir RabbitMQ diretamente** para participar do leilão — o Core usa callbacks HTTP
- A fila `ridefleet.groups.ride_created` está disponível para observabilidade/logging se o grupo quiser assinar
- Credenciais padrão: `ridefleet` / `ridefleet`; management UI em `:15672`
