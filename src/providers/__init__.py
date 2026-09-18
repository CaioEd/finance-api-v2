"""Clientes dos provedores de cotação — a única parte do sistema que fala com fora.

Um módulo por provedor, e cada um traduz o formato dele para os tipos deste
pacote (`Quote`, `AssetHit`, `IndexRate`): trocar de provedor é reescrever um
arquivo, não um domínio.

Nenhum cliente conhece `Investment` nem abre sessão de banco. Quem liga as duas
coisas é `jobs.investment_quotes`, onde mora também a política de orçamento.
"""
