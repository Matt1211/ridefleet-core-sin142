# ADR-003 — Linguagem de Implementação do Core: Python

**Data:** 2026-04-11
**Status:** Aceito ✅
**Autores:** Subgrupo Core — RideFleet
**Aprovado por:** (aguardando ratificação do Prof. Damaso)

## Contexto

O core precisa de uma linguagem para implementar os serviços de conformização (lock-manager, saga-coordinator) e a API REST. A escolha deve balancear produtividade (prazo curto), familiaridade do time do core, e adequação às tarefas de sistemas distribuídos.

## Decisão

Adotar **Python 3.12** com **FastAPI** como linguagem e framework do core.

- Framework web: FastAPI (async, auto-docs via OpenAPI, Pydantic para validação)
- Runtime: Uvicorn (ASGI)
- Containerização: imagem oficial `python:3.12-slim`

## Consequências

**Positivas:**
- Máxima familiaridade no ambiente acadêmico brasileiro (Python é a linguagem mais ensinada em TI na UFV)
- FastAPI gera documentação interativa automática (`/docs`) — útil para os grupos testarem a API
- Pydantic elimina boilerplate de validação de payload
- Desenvolvimento rápido; adequado ao prazo de 7 dias para a primeira entrega
- Biblioteca padrão rica para threading (lock manager), datetime (timestamps lógicos)

**Negativas:**
- Performance inferior a Go/Rust para cargas muito altas (irrelevante para escala acadêmica)
- GIL limita paralelismo CPU-bound (irrelevante: o core é I/O-bound)

## Alternativas descartadas

| Alternativa | Motivo de descarte |
|-------------|-------------------|
| Go | Melhor para sistemas distribuídos de produção, mas menor familiaridade do time do core |
| Node.js/TypeScript | Viable; descartado em favor de Python por familiaridade do representante |
| Java/Spring Boot | Excessivamente verboso para um prazo de 7 dias |

## Nota

Esta decisão afeta **apenas o core**. Cada grupo implementa seu serviço na linguagem de sua escolha (ver spec do projeto, seção R1).
