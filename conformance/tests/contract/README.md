# Contract Tests

Testes de contrato que verificam a conformidade de cada serviço de grupo com a spec da API do core.

## Status

**Entrega prevista: 16/05/2026** (conforme cronograma do projeto).

## Estrutura planejada

```
contract/
├── package.json          ← dependências (jest, supertest, openapi-fetch)
├── jest.config.js
├── tests/
│   ├── rides.test.js     ← testes para /rides e /rides/{id}/status
│   ├── locks.test.js     ← testes para /locks/{id}
│   └── audit.test.js     ← testes para /rides/{id}/audit
└── reports/              ← artefatos gerados pelo CI
```

## Como rodar (quando implementado)

```bash
# Subir o core + serviço do grupo a testar
docker compose -f infra/docker-compose.core.yml up -d

# Rodar os testes de contrato
cd conformance/tests/contract
npm ci
GROUP=A npm test
```

## Variável de ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `CORE_URL` | URL base do core | `http://localhost:8080/api/v1` |
| `GROUP` | ID do grupo sendo testado | — |
| `GROUP_URL` | URL do serviço do grupo | — |
