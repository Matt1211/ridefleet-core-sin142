# Changelog — RideFleet Core API

Todas as mudanças notáveis na especificação da API serão documentadas aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).
Versionamento segue [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2026-04-18

**Status:** Entregue e aprovado

### Adicionado
- `POST /rides` — solicitar nova corrida; retorna 202 com `rideUuid`
- `GET /rides` — listar corridas com filtros (state, originServiceId, assignedServiceId)
- `GET /rides/{rideUuid}/status` — consultar estado atual da saga + lock
- `PATCH /rides/{rideUuid}/status` — transição de estado (conformizado pelo core)
- `GET /rides/{rideUuid}/proposals` — resultado do leilão (propostas + vencedor)
- `GET /rides/{rideUuid}/audit` — log causal completo com relógios de Lamport
- `POST /locks/{rideUuid}` — adquirir/renovar lock distribuído com TTL configurável
- `DELETE /locks/{rideUuid}` — liberar lock distribuído
- `POST /groups/register` — registrar grupo e obter API Key
- `GET /groups/register` — listar grupos registrados
- `GET /health` — health check do core
- `GET /metrics` — endpoint de métricas Prometheus
- Schemas: `RideRequest`, `RideProposal`, `RideStatus`, `RideStatusUpdate`, `RideAccepted`, `AuditLog`, `AuditEvent`, `Location`, `LockRequest`, `LockResponse`, `ErrorResponse`
- Máquina de estados da saga: `request → match → confirm → in_transit → complete`
- Estados de compensação: `compensating → cancelled`

### Modelo de propostas (leilão via callback)
Grupos **não submetem propostas diretamente ao Core**. O fluxo é:
1. Core chama `POST {serviceUrl}/rides/incoming` em cada grupo (scatter HTTP)
2. Grupo responde `200` com `{estimatedEta, estimatedPrice, logicalTimestamp}` ou `204` para recusar
3. Core seleciona vencedor, transfere lock e chama `POST {serviceUrl}/rides/{uuid}/assigned`

### Decisões arquiteturais
- Protocolo: REST sobre HTTP/1.1 (ADR-001)
- Timestamps lógicos de Lamport obrigatórios em todos os payloads
- Lock com TTL padrão de 60s (vencedor do leilão); configurável por chamada
- Broker: RabbitMQ 3.13 com exchange topic (ADR-002)

---

## [0.4.1] — 2026-05-28

### Modificado
- `POST /groups/register` agora é **idempotente (upsert)**:
  - Primeiro registro retorna `201` com a API Key gerada (comportamento anterior).
  - Re-registro com mesmo `groupId` retorna `200` com a **mesma API Key**
    e atualiza `serviceUrl`, `groupName` e `contactEmail`.
  - Resposta `409` removida — não é mais retornada por este endpoint.

### Motivação
Serviços cliente reiniciavam o container e perdiam a API Key (não persistida
localmente), ficando impedidos de re-registrar e integrar. O upsert elimina
esse atrito na demo individual e na integração multi-grupo.

---

## Próximas versões

### Planejado para v0.5.0
- Paginação no endpoint de auditoria (`GET /rides/{id}/audit`)
- Suporte a filtros avançados na listagem de corridas
