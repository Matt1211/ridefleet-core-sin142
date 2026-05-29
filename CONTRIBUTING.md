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

## Membros do Time e Contribuições

| Membro | Contato | Área Principal |
|--------|---------|---------------|
| Matheus Costa | @Matt1211 | Arquitetura, Infra, Especificação de API |
| Emanuel Teixeira Martins | emanuel@itexto.com.br | Reestruturação da arquitetura, autenticação de grupos, Makefile, Swagger |
| Luana Amie | luanaamie@gmail.com | Integração RabbitMQ (ambiente), Documentação |
| Gabriel Rodrigues | @GabrielRodrigues22 | Integração RabbitMQ (endpoints FastAPI) |
| John Kauan | @johnkauan | Configuração de exchanges e filas (broker) |
| Phelipe Romano Magalhães Rosa | phromanomr@gmail.com | Circuit Breaker por grupo, endpoint `/metrics` |
| Arcanjo Gabriel | arcanjog544@gmail.com | Relógio de Lamport, integração nos workers |
| Rafael Carvalho | @rapah10000 | Implementação do sistema de leilão (auction worker) |
| Arthur Neto | @ArthurNeto | Enforcer de transições de estado, publicação de eventos |
| Mariana Castro | mariana.d.castro@ufv.br | Métricas Prometheus, máquina de estados |
| Jose Guedes de Carvalho Godoi | jose.godoi@ufv.br | Modelo de dados de corridas, repositório |

### Detalhamento por membro

**Matheus Costa**
- Criação da estrutura inicial do repositório e do stack principal
- Definição e evolução da especificação OpenAPI (`spec/api/openapi.yaml`, v0.1 → v0.4)
- Estruturação dos módulos `lock-manager` e `saga-coordinator`
- Configurações de infraestrutura (`infra/docker-compose.core.yml`) e correções para inicialização correta do RabbitMQ + banco de dados
- Coordenação de merges e resolução de conflitos

**Emanuel Teixeira Martins**
- Reestruturação completa da arquitetura do projeto (realizada de forma independente)
- Implementação do endpoint de registro de grupos (`POST /groups/register` e `GET /groups/register`)
- Criação e manutenção do `Makefile` de automação de desenvolvimento
- Atualizações na especificação Swagger e configuração do ambiente PyCharm/FastAPI

**Luana Amie**
- Integração das variáveis de ambiente do RabbitMQ no stack de desenvolvimento
- Atualização e revisão do `README.md`

**Gabriel Rodrigues**
- Integração do cliente RabbitMQ nos endpoints FastAPI (`app/rabbitmq.py`)
- Publicação e consumo de mensagens nos fluxos de corrida

**John Kauan**
- Criação e configuração de exchanges e filas no RabbitMQ (`broker/`)
- Setup inicial do broker para os tópicos do ecossistema RideFleet

**Phelipe Romano Magalhães Rosa**
- Implementação da classe `CircuitBreaker` e `CircuitBreakerManager` (`app/core/circuit_breaker_manager.py`)
- Criação de instância de breaker por grupo registrado
- Integração no fluxo de delegação/leilão: ignorar parceiros OPEN, testar HALF_OPEN
- Exposição da métrica `ridefleet_circuit_breaker_state` rotulada por grupo
- Criação do endpoint `/metrics` e integração com `prometheus_client`

**Arcanjo Gabriel**
- Implementação do relógio de Lamport thread-safe (`app/core/lamport_clock.py`): métodos `tick()` e `update()`, proteção contra saltos excessivos (`MAX_CLOCK_JUMP`)
- Integração do clock nos workers de leilão e métricas (`ridefleet_logical_timestamp`)

**Rafael Carvalho**
- Implementação do sistema de leilão de corridas (`app/workers/auction_worker.py`): lógica de scatter-gather HTTP, critério de seleção de vencedor (preço → ETA → group\_id), transferência de lock ao vencedor e chamada de `POST /rides/{uuid}/assigned`

**Arthur Neto**
- Implementação do enforcer de transições de estado da saga (`app/services/ride_service.py`): validação de transições via `_TRANSICOES_VALIDAS`, publicação de `ride_status_changed` no broker
- Integração do enforcement no `auction_worker` e no `lock_monitor` para transições internas (match, compensating)
- Criação dos testes de contrato de transição (`app/tests/tests_ride_status.py`)

**Mariana Castro**
- Implementação dos endpoints de métricas Prometheus (`app/core/metrics.py`): contadores de saga, locks, delegação e corridas locais com labels padronizados
- Contribuição na máquina de estados (`app/services/state_machine_service.py`): definição de `_REQUER_LOCK` e refinamento das transições válidas
- Ajustes no `lock_monitor` para emissão correta de métricas de expiração

**Jose Guedes de Carvalho Godoi**
- Implementação do modelo ORM de corridas (`app/models/ride.py`) com todos os campos da saga
- Criação do repositório de corridas (`app/repositories/ride_repository.py`) e integração inicial no `ride_service`
