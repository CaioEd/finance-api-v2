"""Trabalhos em segundo plano: a montagem, fora de requisição, do que os serviços fazem.

Irmão de `dependencies/`: lá a sessão e os repositórios chegam por `Depends`, a
cada requisição; aqui não há requisição, e cada trabalho abre a própria sessão
a partir do `Database` do `app.state`. A regra continua nos serviços — este
pacote só liga as pontas, e quem o chama no tempo certo é `core.scheduler`.
"""
