# Cobertura de testes

Medida em **2026-09-05** com `make coverage`, sobre o estado que este commit entrega — a fase 3
(transações) concluída. Não há hash aqui de propósito: o documento vive dentro do commit que ele
descreve, e um hash nesta linha ou é o do commit anterior ou não existe ainda. Para saber se
envelheceu, compare a tabela de fases do `README.md` com a lista de módulos abaixo.

| | |
|---|---|
| **Cobertura total** | **93%** — 1463 linhas executáveis, 80 sem cobertura |
| Suíte | 239 testes: 95 unitários, 144 de integração |
| Só os unitários | 46% — e está certo assim: unitário cobre regra, não fiação |

Os 46% não são uma meta frustrada. Os testes unitários exercitam relógio, configuração,
criptografia e a regra dos serviços; rota, repositório e sessão só ganham sentido contra um Postgres
de verdade, e é a suíte de integração que os cobre. Quem responde pela cobertura é a suíte inteira.

> **Ao medir, `concurrency = ["thread", "greenlet"]` não é opcional.** A ponte async do SQLAlchemy
> executa dentro de um greenlet, e sem essa declaração o rastreador perde tudo que roda depois de um
> `await` no banco — o relatório acusava 51% no `auth_service`, que os testes cobrem de ponta a
> ponta. Está fixado no `pyproject.toml`; se um número despencar sem motivo, desconfie disto antes
> de sair escrevendo teste.

## Por módulo

| Módulo (`src/`) | Linhas | Cobertura |
|---|---|---|
| `api/router.py` | 9 | 100% |
| `api/routes/admin_users.py` | 26 | 100% |
| `api/routes/auth.py` | 22 | 100% |
| `api/routes/categories.py` | 35 | 100% |
| `api/routes/health.py` | 26 | 92% |
| `api/routes/transactions.py` | 36 | 100% |
| `api/routes/users.py` | 23 | 100% |
| `cli.py` | 89 | 50% |
| `core/clock.py` | 29 | 100% |
| `core/config.py` | 79 | 95% |
| `core/database.py` | 28 | 93% |
| `core/errors.py` | 103 | 98% |
| `core/security.py` | 72 | 95% |
| `dependencies/auth.py` | 27 | 100% |
| `dependencies/database.py` | 9 | 67% |
| `dependencies/repositories.py` | 19 | 100% |
| `dependencies/services.py` | 30 | 100% |
| `dependencies/state.py` | 17 | 100% |
| `main.py` | 39 | 95% |
| `models/category.py` | 27 | 93% |
| `models/refresh_token.py` | 19 | 95% |
| `models/transaction.py` | 32 | 97% |
| `models/user.py` | 30 | 97% |
| `repositories/admin_user_repository.py` | 43 | 100% |
| `repositories/category_repository.py` | 33 | 85% |
| `repositories/refresh_token_repository.py` | 18 | 100% |
| `repositories/transaction_repository.py` | 52 | 84% |
| `repositories/user_repository.py` | 27 | 94% |
| `schemas/auth.py` | 28 | 100% |
| `schemas/category.py` | 25 | 100% |
| `schemas/transaction.py` | 48 | 100% |
| `schemas/user.py` | 58 | 100% |
| `services/admin_user_service.py` | 58 | 100% |
| `services/auth_service.py` | 89 | 91% |
| `services/category_service.py` | 49 | 100% |
| `services/transaction_service.py` | 71 | 100% |
| `services/user_service.py` | 37 | 100% |
| `version.py` | 1 | 100% |

22 dos 38 módulos estão em 100%, entre eles `services/` e `schemas/` inteiros — com a exceção do
`auth_service`, tratada abaixo. Os dois repositórios abaixo de 90% são a fase 3 recém-entregue, e a
razão está na lista de lacunas.

## O que não está coberto

### Lacunas reais

Comportamento que existe no código e nenhum teste exercita. Em ordem de risco:

| Onde | O que não é exercitado |
|---|---|
| `repositories/category_repository.py:70-71` | excluir categoria que tem lançamento → `CategoryInUseError` |
| `repositories/transaction_repository.py:49,51,53,55` | os quatro filtros da listagem: tipo, categoria e as duas pontas do intervalo de datas |
| `services/auth_service.py:90` | login de conta desativada → `AccountInactiveError` |
| `services/auth_service.py:117` | refresh com token expirado |
| `services/auth_service.py:121` | refresh de usuário que não existe mais |
| `services/auth_service.py:123` | refresh de conta desativada |
| `services/auth_service.py:93` | rehash da senha quando o custo do argon2 mudou |
| `core/security.py:154` | access token sem os claims obrigatórios |
| `core/security.py:66` | `verify()` diante de um hash corrompido (`InvalidHashError`) |
| `core/config.py:103` | `JWT_SECRET_KEY` com menos de 32 caracteres |
| `core/errors.py:209-210` | handler de exceção não tratada — o `500` genérico |
| `api/routes/health.py:46-47` | readiness quando o banco não responde |
| `repositories/user_repository.py:55`, `category_repository.py:72`, `transaction_repository.py:134` | o erro genérico para constraint desconhecida |
| `core/config.py:118` | `is_production` |
| `cli.py` (50%) | `create-admin` e o `main()` do argparse; só `seed-dev` é testado |

**Não existe `tests/integration/test_transactions.py`.** O domínio de transações chegou com 20
testes unitários de serviço e 18 de contrato de schema, e o que o exercita contra Postgres é a
matriz de autorização — que cria categoria e lançamento de verdade em cada rota, e por isso leva
`api/routes/transactions.py` e `services/transaction_service.py` a 100%. O que falta é o degrau do
banco: os filtros da listagem, a paginação, e as duas garantias que só a FK dá.

As duas primeiras linhas da tabela são as que mais pesam nesta fase. A exclusão de categoria em uso
e o comportamento da FK sem `ON DELETE` **foram verificados à mão contra o Postgres de teste** — a
categoria em uso é recusada, a exclusão de conta cascateia sem deixar órfão e as categorias globais
sobrevivem —, mas verificação à mão não impede regressão. É a primeira coisa a escrever.

Depois delas vêm os quatro caminhos de `auth_service`: são **negação de acesso**, exatamente onde um
erro não aparece em teste manual e vira brecha em produção.

### Não são lacunas de teste

| Onde | Por quê |
|---|---|
| `models/user.py:85` (`is_admin`) | **nada no código chama esta property** — `require_role` compara `user.role` |
| `models/refresh_token.py:55` (`is_usable_at`) | idem: `AuthService.refresh` checa `revoked_at` e `expires_at` direto |
| `dependencies/database.py:19-21` | `get_session` é substituído por `dependency_overrides` em todo teste, para cada um rodar numa transação com rollback |
| `core/database.py:86,90` | properties `engine`/`sessionmaker`, alcançadas só pelo `get_session` acima |
| `core/config.py:124` | `get_settings()` com `lru_cache`; a suíte constrói `Settings` explicitamente, de propósito |
| `main.py:76` | o middleware de CORS só entra quando `CORS_ORIGINS` não é vazio, e a configuração de teste o deixa vazio |
| `models/user.py:88`, `models/category.py:110-111`, `models/transaction.py:121` | `__repr__`, texto de depuração |

As duas primeiras linhas são o relatório de cobertura fazendo o trabalho dele: apontou código morto,
não teste faltando. Ou passam a ser usadas, ou saem.

## Como regenerar

```bash
make coverage                    # sobe o db-test, roda tudo e imprime o relatório
```

Gera também `.cache/coverage.json`, que é de onde a tabela acima sai. Para navegar linha a linha:

```bash
.venv/bin/pytest --cov --cov-report=html    # abre htmlcov/index.html
```

## Manutenção

Este arquivo é atualizado **junto com o código**, no mesmo commit: teste novo ou módulo novo pedem
`make coverage` e a revisão dos números daqui. Um documento de cobertura desatualizado é pior que
nenhum — ele afirma com precisão numérica uma coisa que deixou de ser verdade.

Ao atualizar, mexa nos três lugares: a tabela de topo, a tabela por módulo e as duas listas de
lacunas. Lacuna que virou teste sai da lista; código novo sem teste entra nela, mesmo que derrube o
número — a lista existe para ser honesta, não para ficar bonita.
