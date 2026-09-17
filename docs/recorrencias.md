# Receitas e despesas recorrentes

Uma **recorrência** é uma receita ou despesa que se registra sozinha todo mês, num dia escolhido: a
mensalidade do streaming no dia 5, o aluguel no dia 10, o salário no dia 30. A pessoa cadastra o
lançamento uma vez, marca "repetir todo mês", e a partir do mês seguinte a API grava o lançamento
no dia certo, sem ninguém pedir.

O que é gravado é um **lançamento comum** em `transactions` — aparece no extrato, entra no saldo e
no PDF, e se edita ou exclui como qualquer outro. A recorrência é só o molde (valor, categoria,
descrição e dia) e a data da próxima ocorrência.

## Como usar

### Pelo formulário do lançamento

É o caminho do dia a dia: `POST /transactions` com `recurrence`. O lançamento e a regra nascem no
**mesmo commit** — não existe o lançamento salvo com a recorrência perdida.

```bash
curl -X POST localhost:8000/api/v1/transactions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
        "amount": "39.90",
        "category_id": "<id da categoria Streaming>",
        "occurred_on": "2026-09-03",
        "description": "Netflix",
        "recurrence": { "day_of_month": 5 }
      }'
```

```json
{ "id": "…", "amount": "39.90", "kind": "expense", "occurred_on": "2026-09-03",
  "description": "Netflix", "category": { "…": "…" },
  "recurring_transaction_id": "8a0c…", "created_at": "…" }
```

O lançamento enviado **é o do mês dele**, então a recorrência começa no mês seguinte: o exemplo
acima registra a Netflix de 3 de setembro agora, e a próxima sai sozinha em **5 de outubro**. Sem
isso, marcar "todo dia 5" no dia 3 geraria uma segunda Netflix dois dias depois.

### Pela edição de um lançamento que já existe

A despesa foi lançada avulsa e só depois a pessoa decidiu que ela se repete: o mesmo `recurrence`
vai no `PATCH`, junto com o que mais tiver mudado no formulário.

```bash
curl -X PATCH localhost:8000/api/v1/transactions/<id> \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{ "amount": "55.90", "recurrence": { "day_of_month": 5 } }'
```

A regra é a mesma da criação, aplicada ao lançamento **já editado**: ela copia valor, categoria e
descrição novos, e começa no mês seguinte ao da data do lançamento — que não muda. Edição e regra
saem no mesmo commit.

- Lançamento que **já tem** recorrência — criado com ela, ou registrado por uma regra — responde
  `409 transaction_already_recurring`, e nada da requisição é gravado. A regra que existe se edita em
  `/recurring-transactions`; uma segunda lançaria o mesmo gasto duas vezes por mês.
- `recurrence: null` (ou ausente) é "não mexa", como todo campo de PATCH. Não há como desfazer a
  recorrência por aqui: pausar ou excluir é na rota da regra.
- Excluída a regra, o vínculo some (`SET NULL`) e o lançamento pode pedir recorrência de novo.
- Pedir recorrência trava a linha do lançamento até o commit: dois envios do mesmo formulário ao
  mesmo tempo não criam duas regras — o segundo espera, relê o vínculo e leva o `409`.

### Só a regra

`POST /recurring-transactions` cria a recorrência sem lançar nada agora — para cadastrar uma conta
que ainda vai começar, por exemplo:

```bash
curl -X POST localhost:8000/api/v1/recurring-transactions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{ "amount": "1500.00", "category_id": "<id de Aluguel>", "description": "Aluguel",
        "day_of_month": 10, "starts_on": "2026-11-01" }'
```

```json
{ "id": "…", "amount": "1500.00", "kind": "expense", "description": "Aluguel",
  "category": { "…": "…" }, "day_of_month": 10, "next_occurrence_on": "2026-11-10",
  "is_active": true, "created_at": "…" }
```

`starts_on` é opcional; ausente é hoje. A primeira ocorrência é a primeira data em `starts_on` ou
depois que cai no dia pedido — no próprio mês, se o dia ainda não passou.

## Rotas

Todas sob `/api/v1`, autenticadas, com o escopo por dono de sempre (a regra de outra pessoa é `404`).

| Método | Rota | O que faz |
|---|---|---|
| POST | `/transactions` | com `recurrence: {day_of_month}`, registra o lançamento e cria a recorrência |
| PATCH | `/transactions/{id}` | com `recurrence`, torna recorrente um lançamento que ainda não é (`409` se já for) |
| GET | `/recurring-transactions` | lista as próprias, pelo dia do mês (`?kind=income\|expense`, `limit`, `offset`) |
| POST | `/recurring-transactions` | cria só a regra (`amount`, `category_id`, `day_of_month`, `description?`, `starts_on?`) |
| GET | `/recurring-transactions/{id}` | detalha uma |
| PATCH | `/recurring-transactions/{id}` | muda valor, categoria, descrição, dia; `is_active` pausa e retoma |
| DELETE | `/recurring-transactions/{id}` | exclui a regra; o que ela já lançou fica |

| Campo | Entra | Sai | Observação |
|---|---|---|---|
| `amount` | sim | sim | string de duas casas, `> 0` — como em lançamento |
| `category_id` / `category` | id | objeto | o `kind` vem da categoria, nunca do corpo |
| `description` | sim | sim | até 200 caracteres |
| `day_of_month` | sim | sim | de 1 a 31 |
| `starts_on` | só na criação | não | no passado vale como hoje |
| `next_occurrence_on` | **nunca** | sim | a próxima data a ser registrada |
| `is_active` | só no PATCH | sim | regra nasce ativa |

Erros, no envelope de sempre: `422 invalid_category` (categoria inexistente ou de outra pessoa),
`422 validation_error` (dia fora de 1–31, campo desconhecido, `next_occurrence_on` no corpo),
`404 recurring_transaction_not_found`, `409 transaction_already_recurring` (no `PATCH /transactions`).
Excluir uma categoria que alguma recorrência usa responde
`409 category_in_use`, como já acontecia com categoria que tem lançamento.

`GET /transactions` passou a trazer `recurring_transaction_id` em cada lançamento: o id da regra que
o registrou (ou que ele criou), e `null` no lançamento avulso — é o que permite ao front marcar a
linha como recorrente.

## O calendário

| Situação | O que acontece |
|---|---|
| Dia maior que o mês ("todo dia 31" em fevereiro) | cai no último dia: 28/2 (29 no bissexto), 30/4 — e volta a 31 em março |
| Lançamento criado ou editado com `recurrence` | vale pelo mês dele; a regra começa no mês seguinte |
| Lançamento retroativo com `recurrence` (julho, lançado em setembro) | a regra conta a partir de hoje: agosto não é lançado de uma vez |
| `starts_on` no passado | vale como hoje |
| Primeira data é hoje | o lançamento de hoje entra na própria resposta |
| API fora do ar por meses | cada mês perdido vira o seu lançamento, com a data dele, na primeira rodada |
| Pausar (`is_active: false`) | nada é registrado; a data não se move |
| Retomar (`is_active: true`) | recomeça da próxima data **a partir de hoje** — o que venceu na pausa não volta |
| Trocar o dia | a próxima ocorrência fica no mesmo mês, no dia novo; se esse dia já passou, ela entra na hora |
| Mudar valor, categoria ou descrição | vale para as próximas; o que já foi lançado não muda |
| Excluir a regra | os lançamentos dela ficam, com `recurring_transaction_id: null` |
| Editar ou excluir um lançamento gerado | é lançamento comum; a regra não é tocada, e o mês não é gerado de novo |
| Pedir `recurrence` para um lançamento que já tem regra | `409 transaction_already_recurring`; nada muda |
| Excluir a conta | regras vão junto, em cascata |

Duas decisões por trás da tabela:

- **Cada data sai do dia da regra, não da data anterior.** Somar um mês à ocorrência anterior
  prenderia "todo dia 31" no 28 para sempre depois do primeiro fevereiro.
- **Recorrência não lança o passado.** Início no passado, lançamento retroativo e retomada depois
  de pausa contam a partir de hoje. A exceção é o agendador fora do ar: aí os meses perdidos são
  devidos de verdade — a regra estava ativa — e entram todos.

E uma garantia: **depois de qualquer resposta da API, uma regra ativa tem a próxima data no
futuro.** Criar, editar e retomar terminam registrando o que já venceu, no mesmo commit.

## O agendador

Quem grava os lançamentos no dia é um laço em segundo plano **dentro do processo da API**
(`core/scheduler.py`), ligado no `lifespan` de `main.py`. Ele roda uma vez na subida e depois a cada
`RECURRING_SCHEDULER_INTERVAL_SECONDS` (15 minutos por padrão).

```
lifespan
 └─ PeriodicJob (subida + a cada intervalo)
     └─ jobs.recurring_transactions.register_due_recurrences(database, clock)
         └─ lote de 100, numa sessão:
             RecurringTransactionService.register_due
               1. lock_due(hoje): SELECT … WHERE is_active AND next_occurrence_on <= hoje
                                  FOR UPDATE OF recurring_transactions SKIP LOCKED
               2. para cada regra: um lançamento por mês vencido, e avança next_occurrence_on
               3. COMMIT — lançamentos e datas novas juntos
         └─ lote incompleto? acabou
```

- **O mesmo mês não entra duas vezes** porque o INSERT do lançamento e o avanço de
  `next_occurrence_on` são o mesmo commit, sobre a linha travada. Não há `UNIQUE (regra, data)`: ela
  reprovaria a rodada inteira no dia em que alguém movesse um lançamento gerado para a data de uma
  ocorrência futura, que é uma edição legítima.
- **Várias réplicas podem rodar ao mesmo tempo.** `SKIP LOCKED` dá cada regra a quem a travou
  primeiro; as outras seguem para as que sobraram. `tests/integration/test_recurring_transactions.py`
  roda dois agendadores juntos contra a mesma regra e confere que cada mês entrou uma vez — e, sem a
  trava, o mesmo teste conta o dobro.
- **Falha não derruba o laço.** A exceção de uma rodada vai para o log (`trabalho periódico
  'recorrências' falhou`) e a seguinte tenta de novo. Um lote que falha sofre rollback inteiro e
  continua vencido.
- **Em lotes de 100 regras por transação**, para uma rodada grande não segurar travadas todas as
  recorrências do sistema enquanto alguém tenta editar a sua.
- **"Hoje" é o do `Clock`**, no `APP_TIMEZONE`: a despesa do dia 5 entra a partir da meia-noite do
  dia 5 no fuso da aplicação, no primeiro ciclo depois dela.
- A API editando uma regra trava a linha (`get_owned(lock=True)`): o agendador a pula nessa rodada, e
  a própria edição registra o que tiver vencido.

Serviço dormindo (plano que suspende o container) não perde lançamento: a rodada da subida registra
tudo que venceu enquanto ele estava parado.

## Configuração

| Variável | Padrão | O que faz |
|---|---|---|
| `RECURRING_SCHEDULER_ENABLED` | `true` | `false` desliga o laço; as rotas continuam funcionando, mas nada é lançado sozinho |
| `RECURRING_SCHEDULER_INTERVAL_SECONDS` | `900` | intervalo entre rodadas; zero ou negativo impede a subida |

As suítes de teste o desligam nos seus `Settings` e chamam `register_due_recurrences` direto, com o
relógio que querem.

## Dados

```
recurring_transactions
  id, user_id → users (CASCADE), category_id → categories (NO ACTION)
  amount NUMERIC(14,2) CHECK > 0, description, day_of_month SMALLINT CHECK 1..31
  next_occurrence_on DATE, is_active BOOLEAN, created_at, updated_at
  ix (user_id, day_of_month) · ix (next_occurrence_on) WHERE is_active · ix (category_id)

transactions
  + recurring_transaction_id → recurring_transactions (SET NULL), ix
```

Migration `5d28986187b0` (`alembic/versions/20260917_1400_cria_recurring_transactions.py`).

## Onde está o quê

| Peça | Arquivo |
|---|---|
| Model | `models/recurring_transaction.py`; a FK nova em `models/transaction.py` |
| Contrato | `schemas/recurring_transaction.py`; `recurrence` e `recurring_transaction_id` em `schemas/transaction.py` |
| Calendário (funções puras) | `services/recurrence.py` |
| Regra do CRUD e da rodada | `services/recurring_transaction_service.py`; a criação junto do lançamento (no `create` e no `update`) em `services/transaction_service.py` |
| Consultas e a trava | `repositories/recurring_transaction_repository.py` |
| Rotas | `api/routes/recurring_transactions.py` |
| Laço em segundo plano | `core/scheduler.py`, ligado em `main.py` |
| Montagem da rodada fora de requisição | `jobs/recurring_transactions.py` |

Testes: o calendário em `tests/unit/test_recurrence.py`; a regra do serviço em
`tests/unit/test_recurring_transaction_service.py` e `test_transaction_service.py` (inclusive
tornar recorrente na edição, e o `409`); o contrato de
entrada em `test_recurring_transaction_schemas.py`; o laço e a ligação na subida em
`test_scheduler.py`; a travessia HTTP — do formulário ao lançamento aparecendo no extrato e no
saldo — em `tests/api/test_recurring_transactions.py`; a trava entre conexões, o `SET NULL`, o `409`
pelo nome da FK, o `PATCH` que torna recorrente (a ordem `INSERT` da regra → `UPDATE` do lançamento,
que o Postgres confere a cada instrução) e o índice parcial em
`tests/integration/test_recurring_transactions.py`.
