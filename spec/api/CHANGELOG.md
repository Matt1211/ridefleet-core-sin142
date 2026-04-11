# Changelog — RideFleet Core API

Todas as mudanças notáveis na especificação da API serão documentadas aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).
Versionamento segue [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2026-04-18

**Status:** Em revisão — aguardando aprovação do Prof. Damaso

### Adicionado
- `POST /rides` — solicitar nova corrida; retorna 202 com `rideId`
- `POST /rides/{rideId}/proposals` — submeter proposta de aceite (leilão)
- `GET /rides/{rideId}/status` — consultar estado atual da saga
- `PATCH /rides/{rideId}/status` — transição de estado (conformizado pelo core)
- `GET /rides/{rideId}/audit` — log causal completo com relógios de Lamport
- `POST /locks/{rideId}` — adquirir lock distribuído com TTL configurável
- `DELETE /locks/{rideId}` — liberar lock distribuído
- `GET /health` — health check do core
- Schemas: `RideRequest`, `RideProposal`, `RideStatus`, `RideStatusUpdate`, `RideAccepted`, `AuditLog`, `AuditEvent`, `Location`, `LockRequest`, `LockResponse`, `ErrorResponse`
- Máquina de estados da saga: `request → match → confirm → in_transit → complete`
- Estados de compensação: `compensating → cancelled`

### Decisões arquiteturais
- Protocolo: REST sobre HTTP/1.1 (ADR-001)
- Timestamps lógicos de Lamport obrigatórios em todos os payloads
- Lock com TTL padrão de 30s; configurável por chamada

---

## Próximas versões

### Planejado para v0.2.0
- Endpoint de métricas Prometheus (`GET /metrics`)
- Suporte a broker pub/sub (pendente ADR-002)
- Paginação no endpoint de auditoria
