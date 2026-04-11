# Contribuindo para o RideFleet Core

## Política de Branches

| Branch | Uso |
|--------|-----|
| `main` | Produção. Somente merge via PR com 2 aprovações. Protegida. |
| `develop` | Branch de integração contínua |
| `feat/xxx` | Novas funcionalidades (ex: `feat/lock-manager`) |
| `fix/xxx` | Correções (ex: `fix/saga-timeout`) |
| `spec/xxx` | Alterações na spec da API (ex: `spec/add-proposals-endpoint`) |

**Regra crítica:** Qualquer mudança em `spec/api/openapi.yaml` ou `spec/schemas/` usa branch `spec/xxx` e exige aprovação de **todos os representantes do core** antes do merge.

## Fluxo de trabalho

1. Crie uma branch a partir de `develop`
2. Faça suas alterações
3. Verifique localmente:
   ```bash
   docker compose -f infra/docker-compose.core.yml up -d
   curl http://localhost:8080/api/v1/health
   docker compose -f infra/docker-compose.core.yml down
   ```
4. Abra uma PR usando o template (`.github/pull_request_template.md`)
5. Aguarde CI passar e as aprovações necessárias

## Proteção da branch `main`

Configure em **Settings → Branches → Branch protection rules**:
- ✅ Require a pull request before merging
- ✅ Require approvals: 2 aprovações mínimas
- ✅ Require status checks to pass (CI obrigatório)
- ✅ Require branches to be up to date before merging
- ✅ Do not allow bypassing the above settings

## Labels

| Label | Uso |
|-------|-----|
| `spec:breaking-change` | Mudança que quebra contrato existente |
| `spec:additive` | Adição não-breaking na API |
| `core:lock-manager` | Relacionado ao árbitro de locks |
| `core:saga` | Relacionado ao coordenador de saga |
| `core:broker` | Relacionado ao broker pub/sub |
| `infra` | Docker, CI, observabilidade |
| `groups:needs-migration` | Grupos precisam atualizar suas implementações |
| `approved` | Spec aprovada pelo arquiteto sênior |
| `needs-senior-architect` | Escalar ao Prof. Damaso |

## Comunicação com os grupos

Toda mudança de spec que afete os grupos deve ser comunicada com **antecedência mínima de 48h** via Issue com label `groups:needs-migration`, mencionando os representantes de cada grupo.
