# Cobertura de testes

Medida em **2026-09-27** com a suíte completa (`pytest --cov`) contra o PostgreSQL de teste.
Os limites de gastos e objetivos têm testes de contrato HTTP, regra de cálculo, isolamento por
usuário e migração. Os números abaixo descrevem o estado desta branch.

| | |
|---|---|
| **Cobertura total** | **98%** — 3952 linhas executáveis, 74 sem cobertura |
| Suíte | 1060 testes coletados: 447 unitários, 474 de API, 139 de integração |
| Sem Postgres | 921 testes unitários e de API |
| Endpoints | 53 de 53 exercitados (100%) |

Para o papel de cada suíte e como rodá-la, veja `testes.md`. A medição usa
`concurrency = ["thread", "greenlet"]` no `pyproject.toml`, necessária para
cobrir o código async que executa na ponte do SQLAlchemy.

## Por módulo

| Módulo (`src/`) | Linhas | Cobertura |
|---|---:|---:|
| `api/router.py` | 14 | 100% |
| `api/routes/admin_users.py` | 26 | 100% |
| `api/routes/auth.py` | 23 | 100% |
| `api/routes/balance.py` | 30 | 100% |
| `api/routes/categories.py` | 35 | 100% |
| `api/routes/health.py` | 26 | 92% |
| `api/routes/investments.py` | 62 | 100% |
| `api/routes/planning.py` | 49 | 100% |
| `api/routes/recurring_transactions.py` | 34 | 100% |
| `api/routes/reports.py` | 37 | 100% |
| `api/routes/transactions.py` | 37 | 100% |
| `api/routes/users.py` | 28 | 100% |
| `cli.py` | 89 | 50% |
| `core/clock.py` | 44 | 100% |
| `core/config.py` | 119 | 98% |
| `core/database.py` | 28 | 96% |
| `core/errors.py` | 143 | 99% |
| `core/pdf.py` | 192 | 100% |
| `core/rate_limit.py` | 83 | 100% |
| `core/scheduler.py` | 33 | 100% |
| `core/security.py` | 72 | 95% |
| `dependencies/auth.py` | 27 | 100% |
| `dependencies/database.py` | 9 | 67% |
| `dependencies/repositories.py` | 28 | 100% |
| `dependencies/services.py` | 47 | 100% |
| `dependencies/state.py` | 28 | 100% |
| `jobs/investment_quotes.py` | 163 | 98% |
| `jobs/recurring_transactions.py` | 23 | 100% |
| `main.py` | 82 | 100% |
| `models/category.py` | 27 | 93% |
| `models/investment.py` | 91 | 97% |
| `models/investment_goal.py` | 16 | 100% |
| `models/recurring_transaction.py` | 35 | 97% |
| `models/refresh_token.py` | 19 | 95% |
| `models/spending_limit.py` | 18 | 100% |
| `models/transaction.py` | 35 | 97% |
| `models/user.py` | 30 | 97% |
| `providers/base.py` | 54 | 100% |
| `providers/bcb.py` | 47 | 100% |
| `providers/brapi.py` | 45 | 100% |
| `providers/twelve_data.py` | 55 | 100% |
| `repositories/admin_user_repository.py` | 43 | 100% |
| `repositories/balance_repository.py` | 37 | 100% |
| `repositories/category_repository.py` | 37 | 95% |
| `repositories/investment_quote_repository.py` | 45 | 100% |
| `repositories/investment_repository.py` | 70 | 100% |
| `repositories/planning_repository.py` | 38 | 100% |
| `repositories/recurring_transaction_repository.py` | 40 | 100% |
| `repositories/refresh_token_repository.py` | 18 | 100% |
| `repositories/transaction_repository.py` | 57 | 97% |
| `repositories/user_repository.py` | 27 | 94% |
| `schemas/auth.py` | 28 | 100% |
| `schemas/balance.py` | 25 | 100% |
| `schemas/base.py` | 7 | 100% |
| `schemas/category.py` | 25 | 100% |
| `schemas/investment.py` | 183 | 100% |
| `schemas/planning.py` | 56 | 100% |
| `schemas/recurring_transaction.py` | 43 | 100% |
| `schemas/transaction.py` | 61 | 100% |
| `schemas/user.py` | 57 | 100% |
| `services/admin_user_service.py` | 65 | 100% |
| `services/auth_service.py` | 105 | 97% |
| `services/balance_service.py` | 63 | 100% |
| `services/category_service.py` | 51 | 100% |
| `services/fixed_income.py` | 25 | 100% |
| `services/investment_service.py` | 208 | 100% |
| `services/market_service.py` | 23 | 100% |
| `services/planning_service.py` | 73 | 97% |
| `services/recurrence.py` | 25 | 100% |
| `services/recurring_transaction_service.py` | 103 | 100% |
| `services/report_service.py` | 102 | 100% |
| `services/transaction_service.py` | 91 | 100% |
| `services/user_service.py` | 37 | 100% |
| `version.py` | 1 | 100% |

55 dos 74 módulos estão em 100% nesta medição.

## O que não está coberto

### Lacunas reais

Comportamento que existe no código e nenhum teste exercita. Em ordem de risco:

| Onde | O que não é exercitado |
|---|---|
| `services/auth_service.py:164` | refresh de usuário que não existe mais — só por corrida: excluir a conta apaga os refresh tokens junto |
| `services/auth_service.py:104` | rehash da senha quando o custo do argon2 mudou |
| `core/security.py:154-155` | access token sem os claims obrigatórios |
| `core/security.py:66-67` | `verify()` diante de um hash corrompido (`InvalidHashError`) |
| `core/config.py:153` | `JWT_SECRET_KEY` com menos de 32 caracteres |
| `core/errors.py:256-257` | handler de exceção não tratada — o `500` genérico |
| `api/routes/health.py:46-47` | readiness quando o banco não responde |
| `repositories/user_repository.py:55`, `category_repository.py:73`, `transaction_repository.py:145` | o erro genérico para constraint desconhecida |
| `cli.py` (50%) | `create-admin` e o `main()` do argparse; só `seed-dev` é testado |

**Três dos quatro caminhos de `auth_service` que abriam esta lista saíram dela** — login e refresh de
conta desativada, e refresh com token expirado —, cobertos por `tests/api/test_sessions.py`. Eram
negação de acesso, exatamente onde um erro não aparece em teste manual e vira brecha em produção. O
quarto, refresh de um usuário que não existe mais, só acontece se a conta for excluída entre as duas
consultas da renovação.

Escrever esses testes achou uma brecha que a cobertura não acusava. `services/admin_user_service.py`
estava em 100%, e **desativar uma conta não revogava os refresh tokens**: enquanto ela ficava
desativada nada passava, mas reativá-la devolvia as sessões antigas — inclusive a de quem tivesse
motivado a desativação. A linha estava coberta; ninguém tinha afirmado o que acontece com as sessões
depois. Agora a desativação revoga tudo no mesmo commit, e o teste de reativação reprova se isso
voltar. `POST /users/me/logout-all` entra sem lacuna própria.

**As recorrências entram sem lacuna de linha**, e a divisão de trabalho vale o registro, porque a
garantia que mais importa nelas não é uma linha. O calendário — dia 31 em fevereiro, meses perdidos,
regra pausada — é função pura e está em `tests/unit/test_recurrence.py`; a regra do serviço (início
no mês seguinte ao lançamento, retomada que não cobra a pausa, troca de dia que não pula nem repete
mês), com dublês, em `test_recurring_transaction_service.py` e `test_transaction_service.py`; o laço
em segundo plano e a ligação dele na subida, sem banco, em `test_scheduler.py`; a travessia HTTP,
com o agendador chamado direto e um relógio parado em 2099, em `tests/api/test_recurring_transactions.py`
— inclusive tornar recorrente, pelo `PATCH`, um lançamento que nasceu avulso, e o `409` de quem já é.
Esse `PATCH` também tem teste contra Postgres, porque lá a FK é conferida a cada instrução: o `UPDATE`
do lançamento só passa se sair depois do `INSERT` da regra.
Só que o SQLite ignora `FOR UPDATE ... SKIP LOCKED`, e é essa cláusula que impede duas réplicas de
lançarem o mesmo mês duas vezes: `repositories/recurring_transaction_repository.py` estaria em 100%
com ela apagada. Por isso `tests/integration/test_recurring_transactions.py` trava a regra numa
conexão e confere que a outra a pula, e roda dois agendadores juntos contra a mesma regra vencida.
Removida a trava, os dois testes reprovam — o segundo contando 6 lançamentos onde deviam ser 3. Os
dois comitam dado de verdade, única exceção ao rollback da suíte, e apagam a conta no fim.

**O limite de tentativas entra sem lacuna de linha**, e com a divisão de trabalho de sempre. A regra
— IP antes do e-mail, e-mail em hash, a tentativa barrada sem banco nem argon2 — está em
`tests/unit/test_auth_service.py`, com dublês; o resolvedor de IP e o contador de verdade, com o
relógio parado para a janela móvel, em `tests/unit/test_rate_limit.py`; o `429` com `Retry-After`
atravessando a aplicação, em `tests/api/test_login_rate_limit.py`. O que ele tem de não coberto não
é código deste repositório: o storage Redis é configuração do `limits` (nenhum teste sobe um Redis),
e qual cabeçalho cada proxy grava só o deploy mostra — `rate-limit.md` descreve como conferir o IP
no log. `core/config.py:134` (`is_production`) saiu da lista: o aviso de subida a usa, e o teste do
aviso a exercita.

**O painel de administração não mexe em número nenhum**, e isso não é defeito da medição: as rotas,
o serviço e o repositório de `/admin/users` já estavam em 100%, e os 29 testes de
`tests/api/test_admin_users.py` passam por linhas que as matrizes e `tests/integration/` já
executavam. O que eles acrescentam é afirmação, não linha — que a listagem traz a conta de quem
administra, que o `409` diz qual campo colidiu pelo `code`, que o papel dado na criação vale na
requisição seguinte, que a edição recusa senha e que a exclusão leva junto lançamentos, categorias
e sessões. É exatamente do que a tela depende, e agora roda
sem Docker.

**A listagem de lançamentos não tem mais filtro sem teste.** Os dois que faltavam — por tipo e por
categoria — saíram da lista na fase 5, e por um caminho que vale registrar: quem os exercita é
`tests/api/test_reports.py`, porque exportar "só as despesas" ou "só o Mercado" é a mesma consulta
com o mesmo filtro. Os testes existem para afirmar que o PDF sai com o recorte pedido; cobrir o
`WHERE` foi consequência — como já havia acontecido com as duas pontas do filtro de datas, cobertas
por `test_balance.py`. Continua não existindo `tests/integration/test_transactions.py`, e continua
não fazendo falta: a matriz de CRUD exercita o ciclo inteiro de transações contra Postgres.

A fase 5 entrou sem lacuna própria, e com a divisão de trabalho que o desenho pede. O que a folha
**diz** se verifica sem gerar PDF nenhum (`tests/unit/test_report_service.py`: linhas, totais,
filtros impressos, o teto de 2000 lançamentos, o nome do arquivo), porque o serviço devolve um
documento e não bytes. O que o renderizador **desenha** se verifica lendo o texto de volta do
arquivo (`tests/unit/test_pdf.py`: a quebra em páginas, o cabeçalho da tabela repetido, "Página X de
N", a logo ilegível caindo no nome em texto). E a travessia inteira — query string, consulta, folha,
cabeçalho de download — está em `tests/api/test_reports.py`, que também afirma o que num PDF ninguém
veria de outro jeito: **o lançamento de outra pessoa não está dentro do anexo**. Num arquivo binário
o escopo esquecido não vaza numa resposta que alguém lê; vaza onde ninguém olha.

Relatório não ganhou teste de integração, e é decisão: ele não escreve SQL próprio. As consultas são
as dos serviços de lançamentos e de saldos, que `tests/integration/` já cobre contra Postgres — e a
fiação que o prende a elas está em `dependencies/services.py`, em 100%.

**A parte 1 de investimentos entra sem lacuna de linha**, com a divisão de trabalho de sempre. A
regra — o preço médio ponderado, o que cada metade da tabela aceita, o `kind` da categoria que um
aporte exige — está em `tests/unit/test_investment_service.py`, com dublês; a fronteira do contrato
(qual campo cada `type` torna obrigatório, o símbolo em maiúsculo, a quantidade normalizada sem
notação científica) em `test_investment_schemas.py`; a travessia HTTP, inclusive o aporte aparecendo
no extrato e no saldo, em `tests/api/test_investments.py`.

Três coisas foram para `tests/integration/` porque **só o Postgres as responde**, e não por
precaução: o schema nasce das migrations e não de `Base.metadata`; o índice único funcional sobre
`upper(symbol)` — é ele que impede o catálogo de guardar "petr4" e "PETR4" como dois ativos, com
duas cotações divergentes na mesma tela; e o `ON DELETE SET NULL` de `transactions.investment_id`,
que nenhuma `relationship` do ORM cobre. Este último é o que garante que excluir uma posição **não**
apaga o aporte que saiu da conta: o dinheiro se moveu de verdade, e apagá-lo para remover um rótulo
falsificaria o saldo do mês em que aconteceu.

**A parte 2 entra sem lacuna de linha**, e com a divisão que o desenho pede. Os três clientes de
provedor se testam sobre `httpx.MockTransport` (`tests/unit/test_providers.py`), contra os corpos que
a BRAPI, a Twelve Data e o SGS **realmente devolvem** — medidos com os tokens do projeto, e não
imaginados. É a diferença entre provar que o parser lê o formato do provedor e provar que ele lê o
formato que o autor do teste achou que viria; o que se afirma inclui o que a aplicação *manda*
(caminho, query string, `Authorization`), porque o token na query string apareceria em log de proxy.

Rodar o agendador contra os provedores de verdade achou um defeito que nenhum teste de duble
pegaria, porque ele estava na **escolha da conta**, não no código: o IPCA anualizado a partir da
última leitura mensal dava `-3,77% a.a.`, porque agosto/2026 fechou em deflação — e um Tesouro
IPCA+5,8% passava a render menos que a poupança. A série mensal passou a valer pelo acumulado de
doze meses (`+4,22% a.a.`, o número que o mercado cita), e o caso virou teste em
`test_providers.py`. É o argumento de medir contra a API real em vez de contra a documentação dela.

A aritmética da renda fixa é função pura e está em `test_fixed_income.py`: as quatro modalidades
reduzidas a uma taxa efetiva, e a capitalização por dia cheio. Um teste dela vale registro — acruar
em duas etapas dá o mesmo total que acruar de uma vez. Sem isso, o patrimônio dependeria de quantas
vezes o agendador rodou, e um restart no meio do dia mudaria o número na tela de quem estivesse
olhando.

O agendador inteiro é `tests/api/test_investment_quotes.py`, com os clientes de verdade sobre
transporte de mentira e o relógio parado. Ele achou um defeito de duble que era quase um defeito de
código: a Twelve Data devolve `{"price": ...}` cru quando o lote tem **um** símbolo, e a chave por
símbolo quando tem mais — o cliente já tratava os dois, o duble não, e a rodada com um único ativo em
dólar não atualizava nada.

Contra Postgres ficaram três coisas que o SQLite não responde: o `UPDATE` em lote que revaloriza
todas as posições de um ativo em `NUMERIC(24,8)` arredondado a duas casas, a linha única por índice
do Banco Central, e o `NULLS FIRST` da fila — sem ele, o ativo recém-cadastrado seria o último a ser
cotado, que é o oposto do que se quer.

A fase 4 entra sem lacuna própria. Os quatro módulos de saldo estão em 100%, e as duas suítes se
dividem o trabalho como o desenho manda: a unitária (`tests/unit/test_balance_service.py`) cobre a
resolução da janela, o preenchimento dos meses vazios e as duas recusas; a de integração
(`tests/integration/test_balance.py`) cobre o que só um Postgres responde — o `FILTER` separando
receita de despesa, o `JOIN` que traz o `kind` da categoria, o intervalo fechado nas duas pontas, e
o escopo por dono. Este último tem uma segunda pessoa lançando em **todo** cenário, e não só no caso
feliz: num agregado, o `WHERE user_id` esquecido não vaza uma linha identificável — vaza um número,
e nada na resposta denuncia de onde ele veio.

A exclusão de categoria em uso saiu desta lista. Ela estava marcada como "verificada à mão", e a
verificação à mão tinha olhado o efeito no banco — a categoria não é excluída — sem olhar a
resposta: `CategoryService.delete` fechava por `self._session.commit()` em vez de `_commit()`,
pulava a tradução e devolvia **500 em vez de 409**. A regra estava escrita e correta, e
inalcançável. É o argumento inteiro deste documento: lacuna de cobertura não é número feio, é
comportamento que ninguém olhou.

### Não são lacunas de teste

| Onde | Por quê |
|---|---|
| `models/refresh_token.py:55` (`is_usable_at`) | **nada no código chama esta property** — `AuthService.refresh` checa `revoked_at` e `expires_at` direto |
| `dependencies/database.py:19-21` | `get_session` é substituído por `dependency_overrides` em todo teste, para cada um rodar numa transação com rollback |
| `core/database.py:86` | property `engine`, alcançada só pelo `ping()` do readiness |
| `core/config.py:174` | `get_settings()` com `lru_cache`; a suíte constrói `Settings` explicitamente, de propósito |
| `models/user.py:88`, `models/category.py:110-111`, `models/transaction.py`, `models/recurring_transaction.py:116`, `models/investment.py` (três) | `__repr__`, texto de depuração |
| `jobs/investment_quotes.py:216,224` | guardas que a própria consulta já impede (`accruable_positions` filtra `rate_index` e `applied_on` não nulos). Ficam porque as colunas são nuláveis e o `mypy --strict` as exige |

A primeira linha é o relatório de cobertura fazendo o trabalho dele: apontou código morto, não
teste faltando. Ou passa a ser usada, ou sai. `models.user.is_admin` saiu desta lista sem ninguém
escrever teste para ela — `CategoryService._mutable_or_fail` a chama para decidir quem altera uma
categoria global, e a afirmação anterior de que "nada no código chama esta property" estava errada.
`main.py:76`, o middleware de CORS, saiu pelo caminho oposto: ganhou teste, porque o `Retry-After`
do `429` só chega ao front de outra origem se o CORS o expuser.

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
