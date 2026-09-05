"""Base dos contratos de entrada de PATCH.

Existe porque "atualizar só o que foi escolhido" é uma decisão de contrato que
os quatro schemas de update compartilham, e cada serviço a reimplementava na
mão — três deles com um `model_dump(exclude_unset=True)` que deixava passar o
caso de baixo.

**Campo ausente e campo nulo significam a mesma coisa: não mexa.** Um PATCH
raramente é montado à mão. Vem de um formulário que serializa o objeto inteiro,
do "Try it out" do Swagger, de um cliente gerado do OpenAPI — e todos eles
mandam `null` no que o usuário não preencheu. Tratar esse `null` como valor a
gravar transformava a edição de um campo em `UPDATE` de todos os outros para
NULL: as colunas são `NOT NULL`, o banco recusava, e a resposta saía como
`409 conflict` — que não diz nada a quem só queria trocar o primeiro nome.

A equivalência é segura enquanto **nenhuma coluna editável for anulável**, e
hoje nenhuma é. No dia em que existir uma em que `null` queira dizer "limpe
este campo", ela não pode ser expressa assim: vai precisar de um sentinela que
distinga "ausente" de "nulo" (um `Field` com default próprio, não `None`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PatchIn(BaseModel):
    """Corpo de um PATCH: todo campo é opcional, e o que não veio não muda.

    `extra="forbid"` fica aqui e não em cada subclasse: sem ele, um campo
    escrito errado — ou um que o contrato não expõe de propósito, como `role`
    no perfil próprio — seria descartado em silêncio, e a resposta `200` diria
    que a mudança valeu.
    """

    model_config = ConfigDict(extra="forbid")

    def changes(self) -> dict[str, Any]:
        """Só os campos que o cliente pediu para mudar, prontos para atribuição.

        `exclude_unset` tira o que nem veio no corpo; `exclude_none` tira o que
        veio nulo. Um corpo vazio devolve `{}` — o recurso fica como está, e a
        requisição continua sendo um sucesso.
        """
        return self.model_dump(exclude_unset=True, exclude_none=True)
