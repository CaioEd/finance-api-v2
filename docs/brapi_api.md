Integre a API da brapi neste projeto.

Endpoint: GET https://brapi.dev/api/v2/stocks/quote?symbols=B3SA3
Autenticação: header Authorization: Bearer BRAPI_TOKEN.
Leia BRAPI_TOKEN de uma variável de ambiente; não exponha a chave no frontend ou no repositório.
Crie uma função tipada para buscar a cotação, trate respostas não-2xx e retorne results[0].data.
Use os padrões e o cliente HTTP que já existem no projeto.
Leia a documentação da API: https://brapi.dev/docs.mdx
