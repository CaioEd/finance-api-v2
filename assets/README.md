# assets

A logo que aparece no topo dos relatórios em PDF mora aqui.

```bash
cp ~/onde/estiver/a-sua-logo.png assets/logo.png
```

e no `.env`:

```
REPORT_LOGO_PATH=assets/logo.png
```

PNG, JPEG ou GIF. A imagem é reduzida para 12 mm de altura, mantendo a proporção
e até 55 mm de largura — uma logo horizontal, com fundo transparente ou branco, é
o que cabe melhor na faixa. Arquivo grande não ajuda: 600 px de largura já sobram
para impressão.

Enquanto não houver logo — variável vazia, arquivo ausente ou imagem que o gerador
não consiga ler — o cabeçalho sai com o `APP_NAME` em texto, e o relatório é
gerado do mesmo jeito. Logo é aparência; o relatório é o dado.

O diretório é copiado para a imagem Docker (`/app/assets`) e montado pelo
`docker-compose.yml`, então trocar a logo não pede rebuild — basta reiniciar o
container.
