# Cobertura de testes

Medida em **2026-09-17** com `make coverage`, sobre o estado que este commit entrega — a fase 5
concluída, as receitas e despesas recorrentes (`docs/recorrencias.md`), mais duas frentes de
autenticação. O encerramento de sessões: sair de todos os
dispositivos (`POST /users/me/logout-all`), a desativação pelo admin revogando os refresh tokens, e
`tests/api/test_sessions.py` fixando quanto dura cada token e o que encerra cada sessão. E o limite
de tentativas no login (`core/rate_limit.py`, com o slowapi confinado a ele, e a regra em
`AuthService.login`). Por último, `tests/api/test_admin_users.py`: o contrato de `/admin/users` que
o painel de administração do front passou a consumir. Não há hash aqui de propósito: o documento vive dentro do commit que ele descreve, e um
hash nesta linha ou é o do commit anterior ou não existe ainda. Para saber se envelheceu, compare a
tabela de fases do `README.md` com a lista de módulos abaixo.

Para **onde** cada tipo de teste mora, o que ele prova e quando rodá-lo, veja `testes.md`.

| | |
|---|---|
| **Cobertura total** | **97%** — 2566 linhas executáveis, 69 sem cobertura |
| Suíte | 746 testes: 325 unitários, 292 de API, 129 de integração (2 pulados) |
| Sem Postgres (`-m "not integration"`) | 95% — os 617 testes que rodam sem Docker |
| Só os unitários | 81% — número de import, não de regra; ver abaixo |

Os 81% dos unitários pedem leitura cuidadosa. Até a fase 5 eram 55%, e o salto não veio de regra
nova coberta: o teste do aviso de subida em produção (`tests/unit/test_rate_limit.py`) chama
`create_app`, e montar a aplicação importa todas as rotas, schemas e repositórios — as linhas de
definição (decorador, classe, assinatura) passam a contar como executadas, embora nenhuma rota rode.
O que os unitários de fato exercitam continua sendo relógio, configuração, criptografia, a regra dos
serviços, o desenho do PDF, o limite de tentativas e agora o calendário das recorrências e o laço do
agendador; rota, repositório e sessão precisam da
aplicação de pé, e é a suíte de API que os alcança, daí os 94% sem nenhum banco externo. Os três
pontos que faltam para o total são o que só o Postgres de verdade exercita: migrations, as
categorias que a migration semeia, o agrupamento mensal do saldo e a tradução de constraint pelo
nome. Quem responde pela cobertura é a suíte inteira.

A suíte de API também responde por **quantos endpoints** têm teste, e o número sai no fim de toda
rodada dela — hoje, 36 de 36. É uma cobertura diferente da de linhas: mede o contrato publicado, não
o código executado, e é a que denuncia rota nova sem teste. Ver `testes.md`.

> **Ao medir, `concurrency = ["thread", "greenlet"]` não é opcional.** A ponte async do SQLAlchemy
> executa dentro de um greenlet, e sem essa declaração o rastreador perde tudo que roda depois de um
> `await` no banco — o relatório acusava 51% no `auth_service`, que os testes cobrem de ponta a
> ponta. Está fixado no `pyproject.toml`; se um número despencar sem motivo, desconfie disto antes
> de sair escrevendo teste.

## Por módulo

| Módulo (`src/`) | Linhas | Cobertura |
|---|---|---|
| `api/router.py` | 12 | 100% |
| `api/routes/admin_users.py` | 26 | 100% |
| `api/routes/auth.py` | 23 | 100% |
| `api/routes/balance.py` | 30 | 100% |
| `api/routes/categories.py` | 35 | 100% |
| `api/routes/health.py` | 26 | 92% |
| `api/routes/recurring_transactions.py` | 34 | 100% |
| `api/routes/reports.py` | 37 | 100% |
| `api/routes/transactions.py` | 37 | 100% |
| `api/routes/users.py` | 28 | 100% |
| `cli.py` | 89 | 50% |
| `core/clock.py` | 44 | 100% |
| `core/config.py` | 112 | 98% |
| `core/database.py` | 28 | 96% |
| `core/errors.py` | 126 | 98% |
| `core/pdf.py` | 192 | 100% |
| `core/rate_limit.py` | 83 | 100% |
| `core/scheduler.py` | 33 | 100% |
| `core/security.py` | 72 | 95% |
| `dependencies/auth.py` | 27 | 100% |
| `dependencies/database.py` | 9 | 67% |
| `dependencies/repositories.py` | 25 | 100% |
| `dependencies/services.py` | 43 | 100% |
| `dependencies/state.py` | 24 | 100% |
| `jobs/recurring_transactions.py` | 23 | 100% |
| `main.py` | 57 | 100% |
| `models/category.py` | 27 | 93% |
| `models/recurring_transaction.py` | 36 | 97% |
| `models/refresh_token.py` | 19 | 95% |
| `models/transaction.py` | 34 | 97% |
| `models/user.py` | 30 | 97% |
| `repositories/admin_user_repository.py` | 43 | 100% |
| `repositories/balance_repository.py` | 37 | 100% |
| `repositories/category_repository.py` | 34 | 95% |
| `repositories/recurring_transaction_repository.py` | 40 | 100% |
| `repositories/refresh_token_repository.py` | 18 | 100% |
| `repositories/transaction_repository.py` | 57 | 97% |
| `repositories/user_repository.py` | 27 | 94% |
| `schemas/auth.py` | 28 | 100% |
| `schemas/balance.py` | 25 | 100% |
| `schemas/base.py` | 7 | 100% |
| `schemas/category.py` | 25 | 100% |
| `schemas/recurring_transaction.py` | 45 | 100% |
| `schemas/transaction.py` | 60 | 100% |
| `schemas/user.py` | 57 | 100% |
| `services/admin_user_service.py` | 65 | 100% |
| `services/auth_service.py` | 105 | 97% |
| `services/balance_service.py` | 63 | 100% |
| `services/category_service.py` | 49 | 100% |
| `services/recurrence.py` | 25 | 100% |
| `services/recurring_transaction_service.py` | 104 | 100% |
| `services/report_service.py` | 102 | 100% |
| `services/transaction_service.py` | 91 | 100% |
| `services/user_service.py` | 37 | 100% |
| `version.py` | 1 | 100% |

39 dos 55 módulos estão em 100%, entre eles `services/` e `schemas/` inteiros — com a exceção do
`auth_service`, tratada abaixo. Os oito módulos das recorrências entram todos em 100%, menos o
`__repr__` do model (ver "Não são lacunas"). `core/rate_limit.py` também está em 100%, e `main.py`
chegou lá junto: o middleware de CORS, que nenhum teste montava, agora é exercitado pelo teste que
confere o `Retry-After` exposto ao front. Os três módulos da fase 5 seguem em 100%, e nenhum
repositório ficou abaixo de 94%.

## O que não está coberto

### Lacunas reais

Comportamento que existe no código e nenhum teste exercita. Em ordem de risco:

| Onde | O que não é exercitado |
|---|---|
| `services/auth_service.py:164` | refresh de usuário que não existe mais — só por corrida: excluir a conta apaga os refresh tokens junto |
| `services/auth_service.py:104` | rehash da senha quando o custo do argon2 mudou |
| `core/security.py:154-155` | access token sem os claims obrigatórios |
| `core/security.py:66-67` | `verify()` diante de um hash corrompido (`InvalidHashError`) |
| `core/config.py:155` | `JWT_SECRET_KEY` com menos de 32 caracteres |
| `core/errors.py:260-261` | handler de exceção não tratada — o `500` genérico |
| `api/routes/health.py:46-47` | readiness quando o banco não responde |
| `repositories/user_repository.py:55`, `category_repository.py:74`, `transaction_repository.py:149` | o erro genérico para constraint desconhecida |
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
| `core/config.py:176` | `get_settings()` com `lru_cache`; a suíte constrói `Settings` explicitamente, de propósito |
| `models/user.py:88`, `models/category.py:110-111`, `models/transaction.py:136`, `models/recurring_transaction.py:157` | `__repr__`, texto de depuração |

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
