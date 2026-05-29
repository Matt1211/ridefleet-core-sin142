# Fluxo Completo de Delegação — RideFleet

Documentação do fluxo de delegação de corridas no ecossistema RideFleet, incluindo cenários de sucesso e falha.

---

## Cenário Feliz

```mermaid
sequenceDiagram
    participant P as Passageiro
    participant A as Serviço A (origem)
    participant C as Core (API)
    participant AW as Auction Worker
    participant RMQ as RabbitMQ
    participant B as Serviço B (parceiro)

    P->>A: solicita corrida
    A->>C: POST /api/v1/rides<br/>(logicalTimestamp: 1)
    C->>C: adquire lock inicial em nome de A
    C->>RMQ: publica auction_request
    C-->>A: 202 {rideUuid}

    RMQ->>AW: consome auction_request
    AW->>RMQ: publica ride_created
    AW->>B: POST /rides/incoming (scatter HTTP)
    B-->>AW: 200 {estimatedEta, estimatedPrice, logicalTimestamp}
    Note over AW: Seleciona B como vencedor<br/>(1º menor preço → 2º menor ETA → 3º group_id)
    AW->>C: registra match + transfere lock para B (TTL 60s)
    AW->>RMQ: publica ride_status_changed (match)
    AW->>B: POST /rides/{uuid}/assigned

    B->>C: POST /api/v1/locks/{uuid}<br/>(TTL 60s)
    C-->>B: 200 {lockExpiresAt}
    B->>C: PATCH /status → confirm
    C->>RMQ: publica ride_status_changed (confirm)
    B->>C: PATCH /status → in_transit
    C->>RMQ: publica ride_status_changed (in_transit)
    B->>C: PATCH /status → complete
    C->>C: libera lock automaticamente
    C->>RMQ: publica ride_status_changed (complete)
    A->>C: GET /rides/{uuid}/audit
    C-->>A: log causal completo (eventos + Lamport timestamps)
```

### Descrição por passo

| Passo | Ator | Ação | Estado resultante |
|-------|------|------|-------------------|
| 1 | Passageiro | Solicita corrida ao Serviço A | — |
| 2 | Serviço A | `POST /rides` com origin/destination | `request` |
| 3 | Core | Adquire lock inicial (TTL 60s) em nome de A; publica `auction_request` | lock ativo |
| 4 | Auction Worker | Publica `ride_created`; chama `POST /rides/incoming` em todos os grupos elegíveis (scatter) | `request` |
| 5 | Serviço B | Responde com proposta (ETA + preço) via retorno HTTP 200 | proposta registrada |
| 6 | Auction Worker | Seleciona vencedor (menor preço → menor ETA → group_id); transfere lock para B (60s); chama `POST /rides/{uuid}/assigned` em B | `match` |
| 7 | Serviço B | `POST /locks/{uuid}` para confirmar detenção do lock | lock em B |
| 8 | Serviço B | `PATCH /status → confirm` (requer lock) | `confirm` |
| 9 | Serviço B | `PATCH /status → in_transit` (requer lock) | `in_transit` |
| 10 | Serviço B | `PATCH /status → complete` (requer lock) | `complete` (lock liberado automaticamente) |

---

## Cenário de Falha — Lock Expirado

```mermaid
sequenceDiagram
    participant B as Serviço B (vencedor)
    participant C as Core (API)
    participant LM as Lock Monitor
    participant RMQ as RabbitMQ
    participant AW as Auction Worker
    participant X as Serviço X (novo candidato)

    Note over B,C: B venceu o leilão — lock ativo (TTL 60s)
    B->>C: PATCH /status → confirm
    Note over B: B para de responder...

    loop a cada 5s
        LM->>LM: verifica locks expirados
    end
    LM->>C: lock de B expirado detectado
    LM->>C: incrementa circuit breaker de B (threshold: 2 → OPEN por 20s)
    LM->>C: registra lock_expired no audit log
    LM->>C: transição interna → compensating
    LM->>RMQ: publica auction_request (excludedGroups: [B])
    LM->>RMQ: publica ride_status_changed (compensating)

    RMQ->>AW: consome auction_request
    AW->>X: POST /rides/incoming
    X-->>AW: 200 {estimatedEta, estimatedPrice}
    AW->>C: registra match com X + transfere lock
    AW->>X: POST /rides/{uuid}/assigned
    X->>C: PATCH /status → confirm → in_transit → complete
```

### Como o Core detecta a falha

1. **Timeout de TTL:** o `lock_monitor` roda a cada 5s e detecta locks com `expires_at < agora`
2. **Punição via circuit breaker:** cada lock expirado incrementa o breaker do grupo faltoso; com 2+ falhas, o grupo fica bloqueado (503) por 20s
3. **Compensação automática:** o Core transiciona a corrida para `compensating`, exclui o grupo faltoso e inicia novo leilão

### Ações de compensação por estado

| Estado no momento da falha | Ação do Core |
|---------------------------|-------------|
| `match` ou posterior | Libera lock; transiciona para `compensating`; publica `auction_request` excluindo o grupo faltoso |
| Nenhuma proposta no leilão | Corrida vai direto para `cancelled` (sem re-leilão) |

> **Nota:** a corrida permanece em `compensating` enquanto o re-leilão está em curso. Se o re-leilão também falhar, vai para `cancelled`.

---

## Critério de Seleção do Vencedor do Leilão

O Core usa o seguinte critério **determinístico** (implementado em `app/workers/auction_worker.py`):

1. **Menor `estimatedPrice`** (preço estimado)
2. Em caso de empate: **menor `estimatedEta`** (tempo de chegada)
3. Em caso de empate: **ordem lexicográfica de `groupId`** (determinístico, reproduzível)

---

## Regras de Relógio de Lamport no Fluxo

- Cada serviço mantém seu próprio clock lógico local
- Ao enviar mensagem ao Core: inclua `logicalTimestamp` atual
- Core aplica `max(core_clock, received) + 1` a cada evento recebido
- O Core rejeita transições com `logicalTimestamp ≤ último registrado` para a corrida (proteção contra duplicatas/eventos atrasados)
- O log de auditoria ordena eventos por `logicalTimestamp` para reconstruir a causalidade
- Nenhum serviço deve reutilizar um timestamp já enviado para a mesma corrida
