# Máquina de Estados — Saga da Corrida

## Estados

| Estado       | Descrição                                      |
|--------------|------------------------------------------------|
| `request`    | Corrida solicitada, aguardando leilão          |
| `match`      | Serviço vencedor selecionado                   |
| `confirm`    | Corrida confirmada pelo motorista              |
| `in_transit` | Passageiro embarcado                           |
| `complete`   | Corrida finalizada com sucesso                 |
| `compensating` | Rollback em andamento (falha detectada)      |
| `cancelled`  | Corrida cancelada após compensação             |

## Transições Válidas

| De           | Para           | Ator              | Ação de compensação se falhar         |
|--------------|----------------|-------------------|---------------------------------------|
| `request`    | `match`        | Core (leilão)     | Volta para `request`                  |
| `match`      | `confirm`      | Serviço externo   | Cancela match, nova delegação         |
| `confirm`    | `in_transit`   | Serviço externo   | Cancela corrida → `compensating`      |
| `in_transit` | `complete`     | Serviço externo   | —                                     |
| qualquer     | `compensating` | Core              | —                                     |
| `compensating` | `cancelled`  | Core              | —                                     |

## Diagrama

```
                  ┌──────────┐
         ┌───────►│ request  │◄──────────────────────┐
         │        └────┬─────┘                       │
         │             │ Core (leilão)                │ compensação
         │             ▼                              │
         │        ┌──────────┐                        │
         │        │  match   │─────────────►  compensating
         │        └────┬─────┘                        │
         │             │ Serviço externo               │
         │             ▼                              │
         │        ┌──────────┐                        │
         │        │ confirm  │─────────────►  compensating
         │        └────┬─────┘                        │
         │             │ Serviço externo               │
         │             ▼                              │
         │       ┌────────────┐                       │
         │       │ in_transit │─────────────►  compensating
         │       └─────┬──────┘                       │
         │             │ Serviço externo               │
         │             ▼                              ▼
         │        ┌──────────┐              ┌───────────┐
         └────────│ complete │              │ cancelled │
                  └──────────┘              └───────────┘
```

## Regras de Conformização do Core

1. **Timestamp lógico**: Toda transição deve incluir `logicalTimestamp` válido (maior que o último registrado para a corrida). O core rejeita transições com timestamp inferior ou igual ao último (proteção contra eventos atrasados/duplicados).

2. **Lock obrigatório**: Apenas o serviço detentor do lock ativo pode transitar para `confirm`, `in_transit` e `complete`. O core valida a titularidade do lock antes de aplicar a transição.

3. **Compensação forçada**: O core pode forçar transição para `compensating` após timeout configurável (padrão: 120s sem progressão). Isso garante que corridas travadas não permaneçam em estados intermediários indefinidamente.

4. **Idempotência**: Submeter a mesma transição duas vezes (mesmo `serviceId` + `logicalTimestamp`) retorna `200` sem alterar o estado (idempotência).

5. **Finalidade**: Estados `complete` e `cancelled` são terminais — nenhuma transição é aceita após atingi-los.

## Ações de Compensação

| Falha em     | Compensação executada                                    |
|--------------|----------------------------------------------------------|
| `match`      | Libera lock; corrida retorna para `request`; novo leilão |
| `confirm`    | Libera lock; corrida vai para `compensating → cancelled` |
| `in_transit` | Corrida vai para `compensating → cancelled`              |
| Timeout      | Corrida vai para `compensating → cancelled`              |
