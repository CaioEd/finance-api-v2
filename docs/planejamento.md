# Orçamentos e objetivos

`/api/v1/spending-limits` guarda tetos de despesas. Cada limite tem nome, valor
positivo e duas datas inclusivas (`starts_on`, `ends_on`). `category_id` é
opcional: sem ele, o teto soma todas as despesas do usuário; com ele, soma só
as despesas daquela categoria. A categoria precisa ser de despesa e visível ao
usuário. `GET` devolve `spent` e `exceeded` (`spent > amount`) calculados a
partir dos lançamentos atuais. Uma categoria usada por um orçamento não pode
ser excluída antes de desvinculá-lo ou removê-lo.

`/api/v1/investment-goals` guarda metas de valor. `investment_id` é opcional:
sem ele, o progresso considera a carteira inteira; com ele, considera o valor
atual da posição escolhida. `GET` devolve `current_amount`, `progress_percent`
e `achieved`, sempre calculados dos investimentos atuais. A meta pode ter uma
data desejada (`target_on`).

Os dois recursos aceitam `GET` da coleção, `POST`, `PATCH` e `DELETE` por id.
No `PATCH`, campos nulos seguem a convenção da API e não alteram o registro.
Para limpar referências opcionais, use `clear_category`, `clear_investment` ou
`clear_target_on` com `true`. Todos os valores monetários saem como string de
duas casas. O acesso é restrito à conta autenticada.
