# assets

A logo que aparece no topo dos relatórios em PDF mora aqui. `logo.png` é a marca
do FinanceHub, e o `.env.example` já aponta para ela:

```
REPORT_LOGO_PATH=assets/logo.png
```

Para trocar por outra, basta substituir o arquivo:

```bash
cp ~/onde/estiver/a-sua-logo.png assets/logo.png
```

PNG, JPEG ou GIF. A imagem é reduzida para 12 mm de altura, mantendo a proporção
e até 55 mm de largura. **Ela é o símbolo, não o letreiro**: o nome do produto
(`REPORT_BRAND_NAME`) é escrito ao lado dela, então uma logo que já traga o nome
desenhado sai repetida. Fundo transparente ou branco. Arquivo grande não ajuda:
600 px de largura já sobram para impressão.

Sem logo — variável vazia, arquivo ausente ou imagem que o gerador não consiga
ler — a faixa sai só com o nome, e o relatório é gerado do mesmo jeito. Logo é
aparência; o relatório é o dado.

O diretório é copiado para a imagem Docker (`/app/assets`) e montado pelo
`docker-compose.yml`, então trocar a logo não pede rebuild — basta reiniciar o
container.
