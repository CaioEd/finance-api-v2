---
name: endpoint-novo
description: Checklist obrigatório ao criar, renomear ou remover um endpoint HTTP desta API — declarar a rota nas duas matrizes de `tests/api/`, cobri-la com um teste que receba 2xx e conferir o relatório de cobertura de endpoints. Use SEMPRE que a tarefa acrescentar uma rota (`@router.get/post/patch/put/delete`), um arquivo novo em `src/api/routes/`, um `include_router` em `src/api/router.py`, ou uma fase nova do roadmap (saldos, relatório em PDF, rotas administrativas). Use também quando o `make test-api` acusar endpoint sem cobertura, ou quando um teste reprovar com "rotas sem decisão de autorização" ou "rotas sem contrato de CRUD verificado".
---

# Endpoint novo entra com teste de API junto

Esta API tem uma suíte que exercita **todos** os endpoints sem depender de
banco nenhum: `tests/api/`, `TestClient` contra um SQLite em memória. Ela roda
em qualquer máquina, em segundos, e é a rede que impede o erro que originou o
projeto — rota nova subindo sem ninguém decidir quem a acessa e o que ela faz.

Endpoint que entra sem passar por aqui aparece como lacuna no fim de toda
rodada, para todo mundo, até alguém fechá-la. **Feche na mesma tarefa que criou
a rota.**

## O que fazer, na ordem

1. **Implemente a rota** como o resto do projeto: arquivo em `src/api/routes/`,
   `APIRouter(dependencies=[Depends(get_current_user)])` no router inteiro
   (nunca rota a rota), `include_router` só em `src/api/router.py`.

2. **Declare quem a acessa** em `tests/api/test_authorization_matrix.py`:
   - `PUBLIC_ROUTES` — existe para quem não tem token (hoje, só `/auth/*`);
   - `PROTECTED_ROUTES` — basta autenticar;
   - `ADMIN_ROUTES` — exige `require_role(Role.ADMIN)`.

   O caminho declarado é o **molde do OpenAPI**
   (`/api/v1/categories/{category_id}`), não uma URL concreta. Rota com
   parâmetro precisa de um `setup=` que crie o recurso do dono e devolva o
   caminho; corpo que referencie outro recurso precisa de um `body_setup=`.

3. **Declare o que ela faz** em `tests/api/test_crud_contract.py`:
   - recurso de coleção (criar → ler → listar → atualizar → excluir) entra em
     `RESOURCES`, e ganha de graça as ~19 invariantes parametrizadas: 404 de id
     inexistente, 422 de id malformado, PATCH parcial, forma da resposta,
     paginação;
   - rota que **não** é CRUD de recurso (emitir token, endpoint agregado,
     ação sobre um singleton) entra em `NOT_CRUD`, que existe para a ausência
     ser uma decisão registrada, e não um esquecimento.

4. **Escreva o teste do que a rota tem de próprio.** As duas matrizes cobrem o
   que é igual em toda rota; a regra específica (o cálculo do saldo, o filtro
   novo, o 409 daquele conflito) é um arquivo `tests/api/test_<assunto>.py`.
   Só o que precisa de Postgres de verdade vai para `tests/integration/`.

5. **Rode e confira o relatório**:

   ```bash
   make test-api
   ```

   A última linha diz quantos endpoints a suíte cobre. **`N de N` é a meta.**
   Um endpoint só conta como coberto quando alguma requisição recebeu dele uma
   resposta **2xx** — bater na rota e levar 401 prova que ela existe, não que
   ela funciona.

6. **Atualize a documentação** que enumera rotas: a tabela de endpoints do
   `README.md` e, se a contagem de testes mudou, a tabela de
   `docs/testes.md`. Teste novo também pede `make coverage` e a atualização de
   `docs/cobertura-de-testes.md` no mesmo commit (regra do `CLAUDE.md`).

## Quando o build reprovar

As duas matrizes se comparam com o schema OpenAPI da aplicação, então a
mensagem de falha já diz onde declarar:

| Mensagem | Onde resolver |
|---|---|
| `rotas sem decisão de autorização registrada` | `PUBLIC_ROUTES` / `PROTECTED_ROUTES` / `ADMIN_ROUTES` |
| `rotas sem contrato de CRUD verificado` | `RESOURCES` ou `NOT_CRUD` |
| `a matriz cita rotas que não existem mais` | apagou ou renomeou uma rota: tire-a da matriz também |

## O que a suíte de API **não** cobre

O SQLite de `tests/api/` é uma tradução do schema (ver o topo de
`tests/api/sqlite_backend.py`), não o banco de produção. Continua sendo assunto
de `tests/integration/`, contra Postgres de verdade:

- **migrations** — lá o schema nasce delas, aqui nasce do `Base.metadata`;
- **as categorias do sistema**, que a migration insere;
- `NUMERIC`, agregação com `FILTER` e a semântica de índice parcial;
- a violação de chave estrangeira que vira `409 category_in_use`: o SQLite não
  diz *qual* FK falhou, e a resposta sai como `conflict` genérico.

Se o comportamento novo depende de um destes, o teste vai para
`tests/integration/` — e o endpoint continua precisando da declaração nas duas
matrizes de `tests/api/`.

## Comandos

```bash
make test-api                  # a suíte de API inteira, sem Docker (~13 s)
make test-api k=categorias     # um recorte: vai direto para o -k do pytest
make test                      # tudo, incluindo o que exige Postgres
```
