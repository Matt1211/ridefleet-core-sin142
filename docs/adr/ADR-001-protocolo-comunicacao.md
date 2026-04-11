# ADR-001 — Protocolo de Comunicação: REST sobre HTTP/1.1

**Data:** 2026-04-11
**Status:** Aceito ✅
**Autores:** Subgrupo Core — RideFleet
**Aprovado por:** (aguardando ratificação do Prof. Damaso)

## Contexto

O core precisava escolher um protocolo de comunicação padrão para todo o ecossistema RideFleet. Todos os grupos devem implementar o mesmo protocolo para que a interoperabilidade funcione. As opções consideradas foram REST/HTTP, gRPC, WebSockets e mensageria assíncrona (RabbitMQ, Kafka).

## Decisão

Adotar **REST sobre HTTP/1.1** como protocolo padrão, com payloads em JSON. A especificação formal é descrita em `spec/api/openapi.yaml` (OpenAPI 3.1).

## Consequências

**Positivas:**
- Familiaridade universal — todos os grupos conhecem HTTP/REST independente de linguagem
- Tooling maduro: Postman, curl, Swagger UI, OpenAPI validators
- Fácil de testar e depurar (HTTP é legível em texto)
- Sem necessidade de code generation ou IDL (ao contrário de gRPC)

**Negativas:**
- Sem streaming nativo (WebSockets seria melhor para notificações em tempo real)
- Overhead maior que gRPC (JSON vs Protobuf)
- Sem contratos tipados ao nível de transporte (resolvido parcialmente pelo OpenAPI)

## Alternativas descartadas

| Alternativa | Motivo de descarte |
|-------------|-------------------|
| gRPC | Requer Protobuf e tooling extra; curva de aprendizagem em ambiente acadêmico |
| WebSockets | Melhor para push, mas sem modelo request/response padronizado para delegação |
| Mensageria pura | Adiciona complexidade de broker antes do ADR-002 estar fechado |

## Notas de implementação

- Base URL: `http://core:8080/api/v1` (Docker Compose) / `http://localhost:8080/api/v1` (dev)
- Content-Type: `application/json` em todos os payloads
- Versioning: prefixo `/v1` na URL (ver ADR-004)
