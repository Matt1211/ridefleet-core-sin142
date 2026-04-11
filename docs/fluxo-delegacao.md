# Fluxo Completo de Delegação — RideFleet

Documentação do fluxo de delegação de corridas no ecossistema RideFleet, incluindo cenários de sucesso e falha.

---

## Cenário Feliz (passos 1–9)

```
Passageiro    Serviço A (core)    Core           Serviço B
    │                │               │               │
    │ 1. POST /rides │               │               │
    │───────────────►│               │               │
    │                │ 2. Adquire    │               │
    │                │    lock       │               │
    │                │──────────────►│               │
    │                │  lock OK      │               │
    │                │◄──────────────│               │
    │                │ 3. POST /rides/{id}/proposals │
    │                │──────────────────────────────►│
    │                │               │               │
    │                │ (outros grupos também recebem) │
    │                │               │               │
    │                │ 4. Proposta (ETA, preço)       │
    │                │◄──────────────────────────────│
    │                │               │               │
    │                │ 5. PATCH /status → match      │
    │                │──────────────►│               │
    │                │               │               │
    │                │ 6. POST /locks/{id} (Grupo B) │
    │                │◄──────────────────────────────│
    │                │               │               │
    │                │ 7. PATCH /status → confirm    │
    │                │◄──────────────────────────────│
    │                │               │               │
    │  8. rideId +   │               │               │
    │     motorista  │               │               │
    │◄───────────────│               │               │
    │                │               │               │
    │ 9. Motorista de B a caminho    │               │
```

### Descrição detalhada

| Passo | Ator | Ação | Estado resultante |
|-------|------|------|-------------------|
| 1 | Passageiro | `POST /rides` com origin/destination | `request` |
| 2 | Core | Adquire lock (TTL 60s) em nome do Serviço A | lock ativo |
| 3 | Core/Serviço A | Broadcast de proposta para grupos parceiros | `request` |
| 4 | Serviço B | `POST /rides/{id}/proposals` com ETA e preço | `request` (proposta registrada) |
| 5 | Core | Seleciona vencedor (menor ETA; desempate por menor preço) → `PATCH status` para `match` | `match` |
| 6 | Serviço B | `POST /locks/{id}` para adquirir lock | lock transferido para B |
| 7 | Serviço B | `PATCH /status` → `confirm` (requer lock) | `confirm` |
| 8 | Core | Retorna rideId + assignedServiceId para Serviço A | — |
| 9 | Serviço B | Motorista aceita corrida; `PATCH /status` → `in_transit` | `in_transit` |

---

## Cenário de Falha (passos 10–12)

```
    │          Serviço B falha!          │
    │                │                  │
    │           10. B falha             │
    │                │                  │
    │           11. Circuit breaker     │
    │               abre para B         │
    │                │                  │
    │           12. Core força          │
    │               compensating        │
    │                │                  │
    │               Core cancela lock   │
    │               de B e inicia       │
    │               nova delegação      │
    │               para Serviço C      │
```

### Como o core detecta a falha

1. **Timeout de TTL:** se Serviço B não progredir dentro do TTL do lock (30s padrão), o lock expira
2. **Compensação forçada:** core verifica corridas em estados intermediários sem progressão e força `compensating`
3. **Circuit breaker:** Serviço A monitora falhas em B e abre o circuito (implementado no serviço, não no core)

### Ações de compensação

| Falha em | Ação do core |
|----------|-------------|
| `match` | Libera lock; corrida volta para `request`; novo leilão |
| `confirm` | Libera lock; `compensating → cancelled` |
| `in_transit` | `compensating → cancelled` |

---

## Critério de seleção do vencedor do leilão

O core usa o seguinte critério determinístico para selecionar o vencedor:

1. **Menor `estimatedEta`** (tempo de chegada)
2. Em caso de empate: **menor `estimatedPrice`**
3. Em caso de empate: **menor `logicalTimestamp`** (chegou primeiro causalmente)
4. Em caso de empate total: **ordem lexicográfica de `serviceId`** (determinístico, reproduzível)

---

## Regras de Relógio de Lamport no fluxo

- Cada serviço mantém seu próprio clock lógico
- Ao enviar mensagem ao core: inclui `logicalTimestamp` atual
- Core aplica `max(core_clock, received) + 1` a cada evento
- Log de auditoria ordena eventos por `logicalTimestamp` para reconstruir causalidade
- Nenhum serviço deve reutilizar um timestamp já enviado para a mesma corrida
