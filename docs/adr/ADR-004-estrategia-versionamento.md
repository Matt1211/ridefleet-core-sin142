# ADR-004 — Estratégia de Versionamento da API

**Data:** 2026-04-11
**Status:** Aceito ✅
**Autores:** Subgrupo Core — RideFleet
**Aprovado por:** (aguardando ratificação do Prof. Damaso)

## Contexto

A API do core é um contrato compartilhado por todos os grupos. Mudanças precisam de uma estratégia de versionamento clara para que os grupos saibam quando precisam atualizar suas implementações e quando podem ignorar uma mudança.

## Decisão

Adotar **Semantic Versioning** (semver.org) para a spec da API:

```
MAJOR.MINOR.PATCH

MAJOR: mudança breaking (ex: campo obrigatório adicionado, endpoint removido)
MINOR: adição não-breaking (ex: campo opcional adicionado, novo endpoint)
PATCH: correção de documentação ou comportamento sem mudança de contrato
```

**Versionamento na URL:** prefixo `/v{MAJOR}` (ex: `/api/v1/rides`). Quando ocorrer uma mudança MAJOR, o novo caminho será `/api/v2/...` e `/api/v1/...` ficará disponível por pelo menos 2 semanas para migração.

**Tags Git:** cada versão aprovada recebe uma tag `v{MAJOR}.{MINOR}.{PATCH}-spec` (ex: `v0.1.0-spec`).

**CHANGELOG:** toda mudança documentada em `spec/api/CHANGELOG.md`.

## Consequências

**Positivas:**
- Grupos sabem exatamente quando precisam atualizar: mudança MAJOR = migração obrigatória
- Histórico claro de mudanças no CHANGELOG
- Tags Git permitem ao professor e grupos inspecionar o estado da spec em qualquer ponto

**Negativas:**
- Mudanças breaking em MAJOR requerem comunicação antecipada (48h mínimo) e possível manutenção de duas versões simultaneamente

## Regra crítica

> Qualquer mudança em `spec/api/openapi.yaml` ou `spec/schemas/` **obrigatoriamente** usa branch `spec/xxx` e exige aprovação de **todos os representantes do core** antes do merge.

## Versões planejadas

| Versão | Data-limite | Conteúdo |
|--------|------------|---------|
| `v0.1.0-spec` | 18/04/2026 | Spec mínima viável (rides, locks, audit, health) |
| `v0.2.0-spec` | 16/05/2026 | Métricas Prometheus + integração broker (pendente ADR-002) |
