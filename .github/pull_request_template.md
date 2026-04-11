## Descrição
<!-- O que essa PR faz? -->

## Tipo de mudança
- [ ] Alteração na spec da API (requer aprovação de todos os representantes do core)
- [ ] Nova funcionalidade do core
- [ ] Correção de bug
- [ ] Infraestrutura / CI
- [ ] Documentação

## Impacto nos grupos
<!-- Esta mudança quebra algum contrato existente? Quais grupos são afetados? -->
- [ ] Não há breaking change
- [ ] Breaking change — grupos afetados: ___

## Checklist
- [ ] A spec foi atualizada em `spec/api/openapi.yaml` se aplicável
- [ ] O CHANGELOG foi atualizado em `spec/api/CHANGELOG.md`
- [ ] Os testes de contrato passam localmente (se disponíveis)
- [ ] O Docker Compose sobe sem erros (`docker compose -f infra/docker-compose.core.yml up -d`)
- [ ] O health check responde (`curl http://localhost:8080/api/v1/health`)
- [ ] Documentação atualizada se necessário

## Como testar
<!-- Passos para revisar e testar esta PR -->

## ADR relacionado (se aplicável)
<!-- Link para o ADR que embase a decisão técnica desta PR -->
