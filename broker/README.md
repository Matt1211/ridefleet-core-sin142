# Broker — RideFleet Core

Configuração dos tópicos pub/sub do ecossistema RideFleet.

## Status

**Broker em definição — aguardando ADR-002.**

Ver `docs/adr/ADR-002-broker-pubsub.md` para as opções em discussão (Redis Streams, RabbitMQ, Kafka).

## Tópicos definidos

| Tópico | Publisher | Subscribers | Descrição |
|--------|-----------|-------------|-----------|
| `ride_created` | core | todos os grupos | Nova corrida disponível para leilão |
| `proposal_submitted` | core | serviço de origem | Proposta registrada |
| `ride_status_changed` | core | todos os grupos | Transição de estado da saga |
| `lock_event` | core | interno/observabilidade | Eventos de lock (aquisição/liberação/expiração) |
| `compensation_triggered` | core | serviço atribuído + origem | Compensação iniciada |

## Configuração completa

Após a decisão no ADR-002, este diretório receberá:
- Configuração específica do broker escolhido
- Script de criação de tópicos/exchanges/queues
- Exemplo de cliente em Python para consumo dos tópicos
