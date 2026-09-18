# Investimentos

Carteira de renda fixa e renda variável, por usuário, com valor corrigido por um agendador que
consulta provedores externos. Este documento registra **por que** o desenho é o que é — em especial
as restrições dos planos gratuitos da BRAPI e da Twelve Data, que não são detalhe de implementação:
elas decidiram o formato do catálogo, a moeda em que o patrimônio é guardado e o orçamento de
requisições de cada rodada.

A entrega está dividida em três partes. Este documento descreve o desenho inteiro e marca o que já
existe.

| Parte | Escopo | Situação |
|---|---|---|
| 1 | Domínio, CRUD, aporte e provento ligados a `transactions`, resumo da carteira | concluída |
| 2 | Clientes BRAPI / Twelve Data / BCB, busca de ativos, agendador de 15 min | pendente |
| 3 | Tela de investimentos no front, sobre as rotas reais | pendente |

## O que os provedores realmente entregam

Medido com os tokens do projeto, não lido na documentação de vendas. **Os números abaixo são do
plano gratuito** e são a razão de metade das decisões deste documento.

| Provedor | Limite medido | Consequência no desenho |
|---|---|---|
| BRAPI | **1 ativo por requisição** (`QUOTES_PER_REQUEST_EXCEEDED` com 2+), 20 req/min, concorrência 1 | ação brasileira é cotada uma a uma, em fila |
| BRAPI | cripto é **plano pago** (`canAccessCrypto`) | cripto vem da Twelve Data |
| BRAPI | CDI/SELIC/IPCA é **plano pago** (`canAccessInflationOrPrimeRate`) | índice de renda fixa vem do Banco Central |
| Twelve Data | **8 créditos/minuto, 800/dia**; 1 crédito por símbolo, e o lote que estoura **também gasta** | ~8 símbolos por rodada; lote nunca passa de 8 |
| Twelve Data | `BTC/BRL` existe, mas `DOT/BRL`, `USDC/BRL` e `DOGE/BRL` **não** | cripto é cotada em USD e convertida por `USD/BRL` |

O endpoint de cotação da BRAPI é o do `docs/brapi_api.md`:
`GET /api/v2/stocks/quote?symbols=B3SA3` com `Authorization: Bearer <BRAPI_TOKEN>`, lendo
`results[0].data`. A busca de ações brasileiras é `GET /api/quote/list?search=<termo>` — note que
`GET /api/available?search=` devolve lista vazia e **não** serve para busca.

O Banco Central entra como terceira fonte porque a BRAPI fechou os índices atrás do plano pago. A
API SGS é pública, sem token e sem limite prático:

| Série | O que é |
|---|---|
| `12` | CDI diário |
| `11` | SELIC diária |
| `433` | IPCA mensal |
| `195` | Poupança |

`GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados/ultimos/1?formato=json`

## As duas metades

O domínio tem **um** `type`, e é ele que decide tudo o mais. A classe (renda fixa ou variável) é
**derivada** de `CLASS_OF_TYPE`, nunca gravada — mesma decisão que `models.transaction` toma sobre
`kind`: duas fontes para o mesmo fato produzem um CDB cadastrado como renda variável que ninguém
consegue corrigir, e uma alocação de carteira que discorda da lista que o usuário vê.

| Classe | Tipos | Campos próprios | Como é avaliada |
|---|---|---|---|
| Renda variável | `br_stock`, `us_stock`, `crypto` | `asset_id`, `quantity`, `average_price` | `quantity x cotação`, convertida para BRL |
| Renda fixa | `cdb`, `lci`, `treasury`, `savings` | `rate_index`, `rate_percent`, `applied_on`, `matures_on` | acrual do índice desde `applied_on` |

Campo da metade errada é **recusado**, não ignorado. Um `201` que afirma ter gravado o que descartou
é pior do que um `422`: o cliente não tem como descobrir que a taxa do CDB dele não existe. Na
criação quem recusa é o validador do schema; no PATCH é o serviço
(`InvalidInvestmentFieldsError`, com o campo no `details`).

**Poupança não tem taxa a contratar.** `rate_index` e `rate_percent` são preenchidos pelo próprio
schema (`savings` e `100`): a regra é a do Banco Central, e pedir ao usuário que a digite convidaria
ao erro sem oferecer escolha nenhuma.

## O catálogo é global

`investment_assets` não tem `user_id`. A cotação da PETR4 é pública e vale o mesmo para todo mundo.

Com a tabela por usuário, cem pessoas com PETR4 custariam cem requisições por rodada — e o limite da
BRAPI é 20 por minuto, com um ativo por requisição. Compartilhada, custam **uma**. É a diferença
entre o módulo caber no plano gratuito e não caber.

O índice único é `(type, upper(symbol))`. O `upper` está lá pela mesma razão que as categorias
comparam `lower(name)`: "petr4" e "PETR4" são o mesmo papel, e duas linhas para ele seriam duas
requisições por rodada e dois preços diferentes na mesma tela. O `type` compõe a chave para que uma
ação e uma cripto homônimas possam coexistir.

O catálogo **sobrevive** a quem o povoou: excluir a posição não exclui o ativo, e excluir a conta
também não. O preço já buscado continua servindo à próxima pessoa que cadastrar o mesmo papel.

## O dinheiro é guardado em BRL

`current_value` e `invested_amount` são `NUMERIC(14,2)` **em real**, inclusive para ação americana e
cripto. Um total que mistura moedas não soma, e o patrimônio é justamente um total.

A moeda de origem fica em `investment_assets.currency`, e a conversão é da parte 2, que lê `USD/BRL`
na mesma rodada — um crédito a mais, uma vez por rodada, não um por ativo.

`invested_amount` é **quanto saiu do bolso**, e não `quantity x average_price`. Para ativo em dólar
esse produto está em USD, e compará-lo com um `current_value` em real produziria um "lucro" que é só
a variação do câmbio. Por isso:

- **ação brasileira**: `invested_amount` é opcional e o produto serve de default — ele já está em real;
- **ação americana e cripto**: `invested_amount` é **obrigatório**. Quem comprou AAPL pagou um valor
  em reais, e esse valor é a única fonte honesta para uma compra passada: a taxa daquele dia não
  está em lugar nenhum, e usar a de hoje inventaria um número.

`quantity` e `average_price` são `NUMERIC(24,8)`: `0,01234567 BTC` e uma ação a `R$ 48,6137` não
cabem em duas casas.

## Cadastrar não lança; aportar lança

Esta é a fronteira que liga investimentos ao resto do sistema.

- **`POST /investments`** declara uma posição que **já existe**. Quem comprou ITSA4 há dois anos não
  pode ver essa compra cair como despesa do mês corrente — o saldo do mês passaria a mentir por
  causa de um cadastro.
- **`POST /investments/{id}/contributions`** é dinheiro saindo da conta **agora**. Grava uma
  **despesa** em `transactions` e aumenta a posição, no mesmo commit.
- **`POST /investments/{id}/earnings`** é dinheiro entrando. Grava uma **receita** e **não** mexe na
  posição: o dividendo caiu na conta, não virou cota. Reinvestir é um aporte, e é uma segunda
  chamada de propósito — os dois fatos aconteceram.

O que os dois criam é lançamento **comum**, na mesma tabela de sempre. Saldo, extrato e PDF continuam
somando uma tabela só, e nada daquele lado sabe que investimentos existem. A única marca é
`transactions.investment_id`, que é a volta: permite listar o que uma posição movimentou.

Como o `kind` vem da categoria (ver `models.transaction`), a categoria escolhida precisa ser do tipo
certo — categoria de receita num aporte faria o dinheiro investido *entrar* no saldo. A recusa é
`422 invalid_category_kind`; categoria de outro usuário e categoria inexistente saem as duas como
`422 invalid_category`, pela mesma razão que credencial inválida tem mensagem única.

Excluir a posição **não** apaga o que ela movimentou: `transactions.investment_id` é
`ON DELETE SET NULL`. O aporte saiu da conta de verdade, e apagá-lo para remover um rótulo
falsificaria o saldo do mês em que aconteceu.

## Preço médio

Média **ponderada**, recalculada a cada aporte:

```
novo_médio = (quantidade_antiga x médio_antigo + quantidade_nova x preço_pago) / quantidade_total
```

É o que permite ao lucro ser `current_value - invested_amount` sem guardar aqui o histórico de
compras — ele já está em `transactions`.

O `unit_price` do aporte fica na **moeda do ativo**. Em ativo cotado em real ele pode ser derivado de
`amount / quantity`; em dólar não pode, porque `amount` está em BRL e a divisão daria um preço em
moeda nenhuma. Por isso ele é exigido lá.

A posição é **travada** (`SELECT ... FOR UPDATE`) durante o aporte: dois aportes simultâneos sem
trava calculariam a média sobre a mesma quantidade antiga, e o segundo sobrescreveria o primeiro.

## O agendador (parte 2)

Roda a cada **15 minutos**, dentro do processo da API, sobre o mesmo `PeriodicJob` das recorrências
(`core/scheduler.py`) — e pela mesma razão: o trabalho é idempotente, então dispensa um cron à parte.

O orçamento é o que o plano gratuito permite. Com 800 créditos por dia e 96 rodadas
(24 h / 15 min), sobram ~8 créditos de Twelve Data por rodada. Daí o desenho:

1. a fila é `investment_assets` ordenada por `quoted_at` (nulos primeiro): o mais desatualizado é o
   primeiro a ser corrigido, e um ativo nunca fica para trás indefinidamente;
2. cada rodada consome um **teto configurável** de símbolos por provedor, e nunca monta um lote
   maior que 8 — o lote que estoura o limite gasta crédito e não devolve nada;
3. a cotação é gravada no catálogo, **uma vez por símbolo**, e o valor das posições daquele ativo é
   recalculado a partir dela;
4. renda fixa não gasta crédito nenhum: os índices do Banco Central são quatro chamadas públicas,
   guardadas numa tabela de taxas, e o acrual é aritmética local sobre `applied_on`;
5. uma rodada que falha só vai para o log — a seguinte tenta de novo, e `current_value` continua
   valendo o que valia. Nunca zera: não saber quanto vale é diferente de valer zero.

## Configuração

| Variável | O que é |
|---|---|
| `BRAPI_TOKEN` | Token da BRAPI (ações brasileiras) |
| `TWELVE_DATA_API_KEY` | Chave da Twelve Data (ações americanas, cripto e `USD/BRL`) |

As duas já estão no `.env.example`. O Banco Central não pede credencial.

As variáveis do agendador (intervalo, tetos por provedor, ligar/desligar) entram na parte 2, junto do
código que as lê — variável de configuração sem quem a consuma é documentação que envelhece.
