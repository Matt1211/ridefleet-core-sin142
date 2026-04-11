# Integration Tests

Testes de integração do próprio core (lock-manager + saga-coordinator + API).

## Status

**Entrega prevista: 16/05/2026** (junto com os contract tests).

## O que será testado

- Fluxo completo: criar corrida → adquirir lock → transitar saga → liberar lock
- Cenários de compensação: falha em etapa intermediária
- Contenção de lock: dois serviços tentando aceitar a mesma corrida
- Timeout de lock: corrida travada é compensada automaticamente
- Ordenação causal: log de auditoria com timestamps corretos
