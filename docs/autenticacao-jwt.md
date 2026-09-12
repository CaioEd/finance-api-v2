# Autenticação e tokens JWT

Como a API autentica, quanto dura cada token, o que encerra uma sessão e o que o front precisa fazer
com isso.

O código de referência está em três arquivos: `core/security.py` emite e valida os tokens,
`services/auth_service.py` faz login, rotação e revogação, e `dependencies/auth.py` resolve quem é o
portador de cada requisição. Os comportamentos descritos aqui estão fixados em teste — ver
[Testes](#testes).

## Resumo

A API emite **dois tokens de naturezas diferentes**, e a diferença é de propósito:

| | Access token | Refresh token |
|---|---|---|
| Formato | JWT assinado (HS256) | string aleatória opaca, 256 bits |
| Duração | **15 minutos** (`ACCESS_TOKEN_TTL_SECONDS=900`) | **30 dias** (`REFRESH_TOKEN_TTL_DAYS=30`), recontados a cada renovação |
| Guardado no servidor | não — é autocontido | sim, só o SHA-256, na tabela `refresh_tokens` |
| Vai em | `Authorization: Bearer <token>`, em toda rota protegida | corpo JSON de `/auth/refresh` e `/auth/logout` |
| Serve para | provar quem é, a cada requisição | obter um par novo quando o access expira |
| Revogável | **não** — vale até o `exp` | sim, na hora |

O access token é curto porque não tem como ser revogado. O refresh token é revogável porque vive no
banco — e, como consultar o banco já é obrigatório para ele, não haveria ganho em fazê-lo JWT.

## Quanto dura uma sessão

- **Access token: 15 minutos** a partir da emissão. É o `expires_in`, em segundos, de toda resposta
  de `register`, `login` e `refresh`.
- **Refresh token: 30 dias** a partir da emissão — e cada `POST /auth/refresh` emite um token novo,
  com mais 30 dias. A validade é uma **janela deslizante**: quem abre o app ao menos uma vez a cada
  30 dias não volta a digitar a senha; quem passa 30 dias sem abrir volta ao login.
- **Não existe teto absoluto de sessão.** Uma sessão renovada com frequência vive até ser revogada.
- A validade do refresh token não é devolvida ao cliente: a resposta traz só o `expires_in` do access.

Os dois números vêm do ambiente (`core/config.py`). Os valores acima são os defaults.

## O access token

### O que vai dentro

| Claim | Valor | Para quê |
|---|---|---|
| `sub` | id (UUID) do usuário | quem é o portador |
| `role` | `user` ou `admin` | informativo — a autorização **não** o lê (ver abaixo) |
| `jti` | UUID aleatório | identidade única do token |
| `iat` | instante da emissão, em segundos desde a época | |
| `exp` | `iat` + 900 | a expiração |
| `typ` | `"access"` | impede que outro token assinado com a mesma chave passe por access |

Nome e e-mail ficam de fora de propósito: dado de perfil dentro do token envelhece no primeiro
`PATCH /users/me` e continua circulando até o `exp`.

O JWT é **assinado, não cifrado**. Qualquer um com o token lê o conteúdo — é base64 —, então nada ali
pode ser segredo.

### Emissão e validação

`TokenCodec` (`core/security.py`) faz as duas pontas com a chave `JWT_SECRET_KEY`, que é obrigatória,
tem no mínimo 32 caracteres e não tem default.

Na validação (`decode_access`):

1. o algoritmo aceito é só o de `JWT_ALGORITHM` (HS256) — um token com `alg: none`, ou assinado com
   outro algoritmo, é recusado;
2. `sub`, `exp` e `typ` são obrigatórios; `typ` precisa ser `access`, e `sub` e `jti` precisam ser
   UUID;
3. o PyJWT confere `exp` e `iat` contra o relógio do **sistema**, sem tolerância.

Assinatura, formato ou tipo errados: `401 invalid_token`. Passou do `exp`: `401 token_expired` — o
código que diz ao front "renove e tente de novo".

> **Relógio.** O `iat` vem do `Clock` da aplicação, mas quem o confere é o PyJWT, pelo relógio do
> sistema. Um `Clock` adiantado emitiria tokens com `iat` no futuro, recusados como "not yet valid" —
> é por isso que nem os testes deslocam o relógio da aplicação.

**Trocar a `JWT_SECRET_KEY`** invalida na hora todo access token em circulação. Os refresh tokens não
dependem da chave e continuam valendo: cada cliente renova e segue.

`JWT_ALGORITHM` é configurável, mas a mesma chave assina e valida: só a família HMAC (HS256, HS384,
HS512) funciona com ela.

### O que acontece a cada requisição

`get_current_user` (`dependencies/auth.py`) roda em todo router protegido — declarado no router
inteiro, nunca rota a rota:

| Situação | Resposta |
|---|---|
| sem `Authorization: Bearer` | `401 invalid_token` ("Credencial ausente.") |
| token malformado, com assinatura errada ou de outro tipo | `401 invalid_token` |
| token expirado | `401 token_expired` |
| `sub` de um usuário que não existe mais | `401 invalid_token` |
| conta desativada | `403 account_inactive` |
| papel insuficiente (rotas de `/admin`) | `403 forbidden` |

Validado o token, **o usuário é carregado do banco** — e é dele, não do token, que saem `is_active` e
`role`. Duas consequências:

- excluir ou desativar uma conta barra o acesso **na requisição seguinte**, mesmo com um access token
  válido na mão;
- promover ou rebaixar alguém vale na requisição seguinte. O claim `role` fica defasado até a próxima
  renovação, e o front não deve usá-lo para decidir nada que importe.

## O refresh token

### Formato e armazenamento

`secrets.token_urlsafe(32)`: 43 caracteres, 256 bits aleatórios. O banco guarda **só o SHA-256**
(`token_hash`), e um dump da tabela não dá sessão a ninguém. É SHA-256 puro, e não argon2, porque não
há o que um ataque de dicionário adivinhe num valor aleatório de 256 bits.

A tabela `refresh_tokens` tem uma linha por token emitido:

| Coluna | |
|---|---|
| `user_id` | o dono; `ON DELETE CASCADE` |
| `token_hash` | o SHA-256 do token, único |
| `family_id` | a sessão — um dispositivo — a que o token pertence |
| `replaced_by_id` | o sucessor, depois da rotação |
| `expires_at` | a emissão mais 30 dias |
| `revoked_at` | quando o token deixou de valer: rotação, logout ou revogação |

Revogar é preencher `revoked_at`. A linha não é apagada — só junto com a conta —, para que a cadeia
de rotação continue legível.

### Família: uma sessão por dispositivo

Cada `register` ou `login` abre uma **família** nova. Cada renovação cria o próximo token da mesma
família e marca o anterior como revogado e substituído. Logar no celular e no notebook são duas
famílias independentes: sair de um não derruba o outro.

### Rotação e detecção de reuso

`POST /auth/refresh` (`AuthService.refresh`) confere, nesta ordem:

| # | Checagem | Se falhar |
|---|---|---|
| 1 | o token existe | `401 invalid_refresh_token` |
| 2 | não foi revogado | **a família inteira é revogada**, e `401 token_reuse_detected` |
| 3 | não expirou | `401 invalid_refresh_token` |
| 4 | o usuário existe | `401 invalid_refresh_token` |
| 5 | a conta está ativa | `403 account_inactive` |

Passou em tudo: emite um par novo — access e refresh, da mesma família — e revoga o token
apresentado. **Todo refresh token é de uso único.**

A checagem 2 é a detecção de roubo. Um token já rotacionado só reaparece se alguém guardou uma cópia:
ou quem roubou está usando a cópia antiga, ou o dono está, depois de o ladrão renovar primeiro. O
servidor não tem como saber qual dos dois é o legítimo, então derruba a família, e os dois voltam ao
login.

O servidor guarda **que** o token foi revogado, não **por quê**. Um token revogado por logout, por
"sair de todos os dispositivos" ou por troca de senha, quando reapresentado ao `/auth/refresh`, também
sai como `token_reuse_detected`. `POST /auth/logout`, ao contrário, nunca derruba família: token
desconhecido ou já revogado é só `204`.

## Revogação: o que encerra o quê

| Gesto | Rota | Refresh tokens | Access tokens já emitidos |
|---|---|---|---|
| Sair deste dispositivo | `POST /auth/logout` | o enviado | valem até o `exp` |
| **Sair de todos os dispositivos** | `POST /users/me/logout-all` | todos os do usuário | valem até o `exp` |
| Trocar a senha | `POST /users/me/password` | todos os do usuário | valem até o `exp` |
| Reuso detectado | `POST /auth/refresh` com token revogado | os da família | valem até o `exp` |
| Admin desativa a conta | `PATCH /admin/users/{user_id}` com `is_active: false` | todos os do usuário | **barrados na hora** (`403`) |
| Excluir a conta | `DELETE /users/me` ou `DELETE /admin/users/{user_id}` | apagados (CASCADE) | **barrados na hora** (`401`) |
| Trocar a `JWT_SECRET_KEY` | deploy | intactos | **todos inválidos** |

"Valem até o `exp`" quer dizer que, por até 15 minutos, o dispositivo continua usando a API; quando o
access token expira, a renovação falha e ele volta ao login.

Desativar **encerra** as sessões, e não só as suspende: reativar a conta não devolve as sessões de
antes, e a pessoa precisa entrar de novo. Sem isso, reativar devolveria a sessão a quem estivesse com
um refresh token antigo — inclusive a quem motivou a desativação.

### Por que o access token não é revogável

Revogar exigiria consultar estado a cada requisição, e a razão de ser do JWT é dispensar essa
consulta. A janela de exposição fica limitada ao TTL: 15 minutos.

Uma ressalva: `get_current_user` **já** carrega o usuário do banco a cada requisição, para checar
`is_active` e `role`. Tornar o access revogável não custaria consulta a mais — ver
[Evoluções possíveis](#evoluções-possíveis).

## Sair de todos os dispositivos

```http
POST /api/v1/users/me/logout-all
Authorization: Bearer <access_token>
```

Sem corpo; responde `204` sem conteúdo.

- **Revoga todos os refresh tokens do usuário, inclusive o do dispositivo que chamou.** Não existe
  "todos menos este".
- **É idempotente:** chamar de novo, já sem sessão viva, também responde `204`.
- **Exige o access token**, e é por isso que mora em `/users/me` e não em `/auth`:
  `api/routes/auth.py` é o único router sem `get_current_user`, e a regra do projeto é declarar a
  autenticação no router inteiro, nunca rota a rota.
- Os access tokens já emitidos — deste e dos outros dispositivos — continuam valendo até expirar.

A implementação é `AuthService.logout_all`, que chama `RefreshTokenRepository.revoke_all_for_user` —
o mesmo `UPDATE` da troca de senha e da desativação.

## Guia para o front

### Guardar

A API devolve os dois tokens no corpo JSON. O access token pode ficar só em memória. O refresh token
precisa sobreviver ao recarregamento da página para a sessão durar, e em qualquer lugar em que o
JavaScript o leia, um XSS também lê. Tirá-lo do alcance do JavaScript — um cookie `HttpOnly` — é
mudança na API, não no front.

### Renovar

- **Depois de toda renovação, troque os dois tokens.** O refresh token antigo deixou de valer no
  instante em que foi usado.
- Renove quando uma chamada voltar `401 token_expired`, e repita a chamada uma vez. Ou renove antes,
  usando o `expires_in`.
- **Uma renovação por vez.** Se duas requisições — ou duas abas — renovarem com o mesmo refresh token,
  a segunda apresenta um token que a primeira acabou de revogar. Para o servidor isso é reuso: a
  família cai e o usuário é deslogado. Centralize a renovação numa promessa compartilhada e, entre
  abas, sincronize (com `BroadcastChannel` ou o evento `storage`, por exemplo).

### Reagir aos erros

| Resposta | O que fazer |
|---|---|
| `401 token_expired` numa rota protegida | renovar e repetir a chamada uma vez |
| `401 invalid_token` | descartar os tokens e ir para o login |
| qualquer `401` de `/auth/refresh` | descartar os tokens e ir para o login — inclusive `token_reuse_detected`, que é também o que chega depois de um "sair de todos os dispositivos" feito em outro aparelho |
| `403 account_inactive` | avisar que a conta está desativada e descartar os tokens |

### Sair

- **Deste dispositivo:** `POST /auth/logout` com `{"refresh_token": "..."}`, e descarte os tokens
  locais sem depender da resposta — a rota é idempotente e não falha por token desconhecido.
- **De todos:** `POST /users/me/logout-all` com o `Authorization`. Se o access token tiver expirado,
  renove antes. No `204`, descarte os tokens locais e vá para o login: este dispositivo também foi
  desconectado.

## Limitações conhecidas

- **Até 15 minutos de acesso depois de uma revogação.** Logout, "sair de todos os dispositivos" e
  troca de senha não alcançam os access tokens já emitidos. Desativação e exclusão alcançam, porque a
  checagem é do estado da conta, não do token.
- **Sessão sem teto absoluto.** A janela de 30 dias desliza a cada renovação.
- **Renovações simultâneas não são serializadas no servidor.** `AuthService.refresh` lê o token sem
  `SELECT ... FOR UPDATE`. Duas requisições com o mesmo token *ao mesmo tempo* podem passar as duas e
  deixar dois tokens vivos na mesma família; *em sequência*, a segunda é tratada como reuso. A regra
  de uma renovação por vez, no front, evita os dois casos.
- **O motivo da revogação não é guardado:** todo token revogado, reapresentado ao refresh, sai como
  `token_reuse_detected`.
- **A tabela `refresh_tokens` só cresce.** Nenhuma rotina apaga token expirado ou revogado.
- **Não há lista de sessões.** A tabela não guarda dispositivo, IP nem último uso: dá para sair de
  todos os dispositivos, mas não de um específico à distância.

## Evoluções possíveis

- **Access token revogável sem consulta a mais.** Uma coluna `users.sessions_revoked_at`, preenchida
  por "sair de todos os dispositivos", troca de senha e desativação, e comparada com o `iat` em
  `get_current_user` — que já lê o usuário. Fecharia a janela de 15 minutos nesses três gestos. É
  mudança de decisão de arquitetura, e não foi feita.
- **Teto absoluto de sessão:** guardar o início da família e recusar a renovação depois de N dias.
- **Serializar a rotação** com `SELECT ... FOR UPDATE` na leitura do token.
- **Limpeza periódica** de tokens expirados há mais de N dias.

## Onde está no código

| Arquivo (`src/`) | O que faz |
|---|---|
| `core/security.py` | `TokenCodec` (emite e valida o JWT), `generate_opaque_token`, `fingerprint` |
| `core/config.py` | `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_TTL_SECONDS`, `REFRESH_TOKEN_TTL_DAYS` |
| `core/errors.py` | os códigos de erro citados aqui |
| `dependencies/auth.py` | `get_current_user` e `require_role` |
| `api/routes/auth.py` | registro, login, renovação e logout — as rotas sem autenticação |
| `api/routes/users.py` | `logout-all`, troca de senha e exclusão da conta |
| `services/auth_service.py` | registro, login, rotação, logout e logout em todos os dispositivos |
| `services/user_service.py` | troca de senha (revoga tudo) e exclusão da conta |
| `services/admin_user_service.py` | desativação pelo admin (revoga tudo) |
| `repositories/refresh_token_repository.py` | busca por hash; revogação da família e de todas as sessões |
| `models/refresh_token.py` | a tabela `refresh_tokens` |

## Testes

| Onde | O que prova |
|---|---|
| `tests/unit/test_security.py` | o JWT: ida e volta, expiração, chave errada, `typ` errado, `alg: none`, adulteração |
| `tests/api/test_sessions.py` | a duração dos dois tokens e cada caminho de revogação — "sair de todos os dispositivos", desativação, expiração |
| `tests/unit/test_admin_user_service.py` | a desativação revoga as sessões; as outras alterações, não |
| `tests/integration/test_auth.py` | registro, login, rotação, detecção de reuso e logout, contra Postgres |
| `tests/integration/test_users.py` | a troca de senha revogando tudo; a exclusão da conta |
