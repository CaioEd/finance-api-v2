# Relatórios em PDF — guia de consumo

Como a tela pede um relatório à API e entrega o arquivo ao usuário. Escrito para quem está do lado
do front: aqui estão as rotas, os filtros, os erros com o que mostrar em cada um e o código do
download pronto para copiar.

Para *como* isso é gerado por dentro, veja a seção "Relatórios em PDF" do `README.md` e
`src/core/pdf.py`. Você não precisa de nada disso para consumir.

## A ideia em uma frase

**Cada rota de relatório é o par de uma rota de leitura que você já usa, e aceita exatamente os
mesmos filtros.** Monte a query string uma vez, use-a nas duas: a lista que aparece na tela e o PDF
que o botão baixa descrevem o mesmo recorte, porque são a mesma consulta.

| Botão da tela | Rota do PDF | Espelha |
|---|---|---|
| Exportar lançamentos / receitas / despesas | `GET /api/v1/reports/transactions` | `GET /transactions` |
| Exportar saldo mês a mês | `GET /api/v1/reports/balance/monthly` | `GET /balance/monthly` |
| Exportar saldo do período | `GET /api/v1/reports/balance/range` | `GET /balance/range` |

Não existe rota de "receitas" nem de "despesas": as duas são o extrato com `kind=income` ou
`kind=expense`, e é o filtro que vira o título da folha e o nome do arquivo.

Também não existe `/reports/balance/current`. O mês corrente é um intervalo, e `GET /balance/current`
já devolve as duas pontas dele (`first_day` e `last_day`) para você repassar ao `range` — ver
[Receitas prontas](#receitas-prontas).

## Autenticação

Igual a todo o resto da API: `Authorization: Bearer <access_token>`.

Isso tem uma consequência prática que decide como o download é feito: **navegador não manda cabeçalho
em navegação.** `<a href="...">`, `window.open(...)` e `window.location = ...` vão à rota sem o
token e voltam com `401`. Não existe hoje link assinado de curta duração que dispense o cabeçalho.
O download é sempre `fetch` + blob.

## A resposta

```
HTTP/1.1 200 OK
content-type: application/pdf
content-disposition: attachment; filename="lancamentos-2026-08-01_2026-09-30.pdf"
cache-control: no-store
content-length: 6049
```

| Cabeçalho | Para que serve no front |
|---|---|
| `content-disposition` | traz o **nome do arquivo** já pronto: período, tipo, tudo. Use-o no `download` do link |
| `cache-control: no-store` | extrato tem saldo e descrição de gasto; não guarde em cache nem em storage |
| `content-length` | tamanho real do arquivo, se você quiser barra de progresso |

> **CORS:** ler o `Content-Disposition` só funciona porque a API o declara em `expose_headers`. Se
> `getResponseHeader("Content-Disposition")` voltar `null`, a origem do seu front não está em
> `CORS_ORIGINS` no `.env` da API — o arquivo baixa, mas sem nome. É configuração do backend, não
> código seu.

## O download

Uma função para os três relatórios. Ela devolve o arquivo ao usuário e lança um erro tipado quando a
API recusa — a recusa é **sempre JSON**, nunca um PDF quebrado.

```ts
const API = "http://localhost:8000/api/v1"; // de onde a sua configuração tirar a base da API

export class ErroDeRelatorio extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

type Filtros = Record<string, string | number | undefined | null>;

export async function baixarRelatorio(rota: string, filtros: Filtros, token: string): Promise<void> {
  const query = new URLSearchParams(
    Object.entries(filtros)
      .filter(([, valor]) => valor !== undefined && valor !== null && valor !== "")
      .map(([chave, valor]) => [chave, String(valor)]),
  );

  const resposta = await fetch(`${API}${rota}?${query}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!resposta.ok) {
    // O envelope de erro da API: { error: { code, message, details } }.
    const corpo = await resposta.json().catch(() => null);
    throw new ErroDeRelatorio(
      resposta.status,
      corpo?.error?.code ?? "http_error",
      corpo?.error?.message ?? "Não foi possível gerar o relatório.",
    );
  }

  const url = URL.createObjectURL(await resposta.blob());
  const link = Object.assign(document.createElement("a"), {
    href: url,
    download: nomeDoArquivo(resposta) ?? "relatorio.pdf",
  });
  document.body.appendChild(link); // o Firefox ignora o clique num link fora do documento
  link.click();
  link.remove();
  // Depois do clique, e não antes: revogar na mesma linha cancela o download em alguns navegadores.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function nomeDoArquivo(resposta: Response): string | null {
  return resposta.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ?? null;
}
```

Num componente, o par "desabilita o botão / mostra o erro" é o que falta:

```tsx
const [baixando, setBaixando] = useState(false);

async function exportar() {
  setBaixando(true);
  try {
    await baixarRelatorio("/reports/transactions", filtrosDaTela, token);
  } catch (erro) {
    if (erro instanceof ErroDeRelatorio) notificar(erro.message); // a mensagem já vem em português
    else notificar("Não foi possível gerar o relatório.");
  } finally {
    setBaixando(false);
  }
}
```

**Por que desabilitar o botão:** o PDF é montado na hora, e o tempo cresce com o número de linhas —
cerca de 30 ms para 50 lançamentos, 0,3 s para 500 e 1,2 s no teto de 2000 (mais a consulta ao
banco). Mostre carregando a partir do primeiro clique; dois cliques geram dois arquivos.

## Os filtros, rota a rota

Todos são opcionais, exceto onde indicado. Datas em `YYYY-MM-DD`, meses em `YYYY-MM`, e **os dois
extremos sempre entram** no recorte.

### `GET /reports/transactions`

| Parâmetro | Valores | Ausente significa |
|---|---|---|
| `kind` | `income` \| `expense` | receitas e despesas juntas |
| `category_id` | UUID de uma categoria visível | todas as categorias |
| `occurred_from` | data | sem limite no início |
| `occurred_to` | data | sem limite no fim |

Os mesmos de `GET /transactions`, **menos `limit` e `offset`**: relatório não pagina, o arquivo sai
inteiro. No lugar deles existe um teto — ver [`report_too_large`](#erros).

### `GET /reports/balance/monthly`

| Parâmetro | Valores | Ausente significa |
|---|---|---|
| `from_month` | `YYYY-MM` | 12 meses antes de `to_month` |
| `to_month` | `YYYY-MM` | 12 meses depois de `from_month` |

Sem nenhum dos dois, são os 12 meses que terminam no mês corrente. Máximo de 120 meses por
relatório. A série sai **sem buraco**: mês sem lançamento aparece zerado, não some.

### `GET /reports/balance/range`

| Parâmetro | Valores | Obrigatório |
|---|---|---|
| `occurred_from` | data | **sim** |
| `occurred_to` | data | **sim** |

## O nome do arquivo

Vem pronto no `Content-Disposition`; você não precisa montar nada. O formato existe para a pasta de
downloads não virar cinco `relatorio.pdf`:

| Pedido | Arquivo |
|---|---|
| extrato de um período | `lancamentos-2026-08-01_2026-09-30.pdf` |
| `kind=income` | `receitas-2026-08-01_2026-09-30.pdf` |
| `kind=expense` | `despesas-2026-08-01_2026-09-30.pdf` |
| só `occurred_from` | `lancamentos-desde-2026-08-01.pdf` |
| só `occurred_to` | `lancamentos-ate-2026-09-30.pdf` |
| sem período | `lancamentos-2026-09-12.pdf` (a data em que foi gerado) |
| saldo mês a mês | `saldo-mensal-2026-08_2026-09.pdf` |
| saldo de um intervalo | `saldo-2026-09-01_2026-09-30.pdf` |

## Erros

Todos no envelope único da API — `{ "error": { "code", "message", "details" } }` — com
`content-type: application/json`. **Cheque `resposta.ok` antes de tratar o corpo como PDF.**

| `code` | Status | Quando | O que fazer na tela |
|---|---|---|---|
| `invalid_token` | 401 | sem `Authorization`, ou token que não é nosso | renove pelo `/auth/refresh` e repita |
| `token_expired` | 401 | access token venceu (ele dura 15 min) | idem |
| `account_inactive` | 403 | conta desativada | mande para o login |
| `report_too_large` | 422 | recorte com mais de 2000 lançamentos | a `message` já diz quantos são e o teto: mostre-a e sugira estreitar o período |
| `invalid_period` | 422 | início depois do fim | aponte o campo de data |
| `period_too_long` | 422 | mais de 120 meses na série mensal | idem |
| `validation_error` | 422 | parâmetro malformado ou obrigatório ausente | `details` traz `field`, `message` e `type` por campo |

Exemplos reais:

```jsonc
// GET /reports/balance/range  (sem as duas pontas)
{"error":{"code":"validation_error","message":"Dados inválidos.","details":[
  {"field":"query.occurred_from","message":"Field required","type":"missing"},
  {"field":"query.occurred_to","message":"Field required","type":"missing"}]}}

// GET /reports/transactions?kind=receita
{"error":{"code":"validation_error","message":"Dados inválidos.","details":[
  {"field":"query.kind","message":"Input should be 'income' or 'expense'","type":"enum"}]}}

// GET /reports/balance/monthly?from_month=1900-01&to_month=2026-09
{"error":{"code":"period_too_long",
  "message":"O período pedido tem 1521 meses, e o máximo por consulta é 120.","details":[]}}
```

O `field` vem prefixado por `query.` porque o erro é do parâmetro de consulta — tire o prefixo para
casar com o nome do campo no seu formulário.

Recorte vazio **não é erro**: responde `200` com um PDF que diz "Nenhum lançamento neste recorte".
Quem decide se vale a pena avisar antes de baixar é a tela — o `total` de `GET /transactions` já
está na mão.

## Receitas prontas

```ts
// O que está na tela: os mesmos filtros, a mesma resposta.
await baixarRelatorio("/reports/transactions", filtrosDaTela, token);

// Só as receitas / só as despesas do período.
await baixarRelatorio("/reports/transactions", { ...filtrosDaTela, kind: "income" }, token);
await baixarRelatorio("/reports/transactions", { ...filtrosDaTela, kind: "expense" }, token);

// Uma categoria.
await baixarRelatorio("/reports/transactions", { category_id: categoria.id }, token);

// O mês corrente: as pontas vêm de /balance/current, no fuso da aplicação —
// não calcule "primeiro e último dia do mês" com a data do navegador.
const mes = await api.get("/balance/current"); // { month, first_day, last_day, income, expense, net }
await baixarRelatorio(
  "/reports/balance/range",
  { occurred_from: mes.first_day, occurred_to: mes.last_day },
  token,
);

// O ano fechado, mês a mês.
await baixarRelatorio("/reports/balance/monthly", { from_month: "2026-01", to_month: "2026-12" }, token);
```

## O que já vem dentro do PDF

Para você não montar nada disso na tela nem mandar junto na requisição:

- **logo e data de geração** no alto de toda página (a data é a do servidor, no fuso da aplicação);
- **título** conforme o filtro — "Lançamentos", "Receitas" ou "Despesas" — e o período embaixo dele;
- **os filtros que geraram a folha**: tipo e categoria, por extenso;
- **os totais em destaque**: receitas, despesas, saldo e a contagem de lançamentos, com o saldo
  negativo em vermelho;
- **a tabela** — data, descrição, categoria, receita e despesa em colunas separadas — com cabeçalho
  repetido a cada página;
- **rodapé** com o nome e o e-mail de quem pediu e "Página X de N".

Valores em formato brasileiro (`1.234,56`) e datas em `dd/mm/aaaa`. Sem símbolo de moeda: a API não
guarda moeda em lugar nenhum, e o PDF não inventa uma.

## Cuidados

- **Não use `<a href>` nem `window.open`** para a rota: sem cabeçalho, sem token, `401`.
- **Não chame `.json()` antes de `resposta.ok`**: no caminho feliz o corpo é binário.
- **Renove o token antes de exportar** se ele estiver perto dos 15 minutos: o usuário clica, espera o
  spinner e recebe um 401 por um motivo que não tem nada a ver com o relatório.
- **Não guarde o blob** em cache, `localStorage` ou service worker — é por isso que a resposta vem
  com `no-store`. Precisou de novo, peça de novo.
- **Um clique, um arquivo.** Desabilite o botão enquanto o `fetch` estiver em voo.
