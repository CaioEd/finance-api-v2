# Rate limit no login

`POST /api/v1/auth/login` tem dois limites de tentativas, contra força bruta e *password spraying*:

| Limite | Padrão | Contra o quê |
|---|---|---|
| **por IP** | 5 tentativas a cada 5 minutos | um endereço testando senhas, numa conta ou em muitas |
| **por e-mail** | 10 tentativas a cada 10 minutos | muitos endereços (botnet) atacando a mesma conta |

Estourou qualquer um: `429`, no envelope de erro de sempre, com `Retry-After` em segundos. Nenhuma
outra rota tem limite.

```json
{ "error": { "code": "too_many_attempts", "message": "Muitas tentativas. Aguarde antes de tentar de novo.", "details": [] } }
```

## Como funciona

```
POST /auth/login
 ├─ rota: client_ip = Depends(get_client_ip) ──► ClientIpResolver (CLIENT_IP_HEADER)
 └─ AuthService.login(email, senha, client_ip)
      1. rate_limiter.hit(por IP,     "login:ip:<ip>")
      2. rate_limiter.hit(por e-mail, "login:email:<sha256 do e-mail>")
         não coube? ──► TooManyAttemptsError ──► 429 + Retry-After
      3. só então: busca o usuário, argon2, emite os tokens
```

`core/rate_limit.py` tem as peças, cada uma trocável sem mexer nas outras:

| Peça | Papel |
|---|---|
| `RateLimiter` (Protocol) | o contrato: `async hit(limit, key) -> RateLimitResult`. É só isso que o serviço conhece |
| `SlowapiRateLimiter` | a implementação atual, e o **único** lugar que importa o slowapi |
| `UnlimitedRateLimiter` | usada com `RATE_LIMIT_ENABLED=false` |
| `build_rate_limiter(settings)` | o único ponto que escolhe a implementação |
| `RateLimit` / `LoginRateLimits` | a regra (N tentativas em X segundos), sem notação de biblioteca |
| `ClientIpResolver` | de quem é a requisição, atrás ou não de proxy |

O limitador nasce em `create_app` e mora em `app.state`, como o `Clock`. Nenhuma rota usa os
decoradores do slowapi: é isso que mantém a troca de biblioteca dentro de um arquivo.

### Decisões

- **Toda tentativa conta, a certa inclusive.** Contar só as erradas pede saber o resultado antes de
  contar, e uma rajada simultânea passaria inteira pela checagem antes de a primeira falha ser
  gravada. O custo: quem entra com sucesso seis vezes em cinco minutos do mesmo IP espera.
- **Conta antes da senha.** A tentativa barrada não chega ao banco nem ao argon2: não gasta CPU e
  não revela se a senha estava certa — nem a senha certa passa.
- **IP primeiro; barrada no IP, a tentativa não gasta a cota do e-mail.** Sem isso, uma rajada de
  um endereço só trancaria a conta alheia na hora.
- **O e-mail entra como SHA-256**, normalizado: `ANA@` e `ana@` são a mesma cota, e o storage não
  guarda a lista de quem tentou entrar. O IP entra puro, porque é o que o log mostra.
- **Mensagem única** para os dois limites: dizer qual estourou ensinaria qual chave trocar.
- **IPv6 conta por /64**, a faixa que um assinante recebe inteira; contar por endereço daria 2^64
  contadores zerados.
- **Janela móvel** (`moving-window`): "5 em quaisquer 5 minutos". Na janela fixa cabem 10 em dois
  segundos — 5 no fim de uma janela e 5 no começo da seguinte.
- **Limite conhecido:** o limite por e-mail permite a um atacante trancar o login de uma conta alheia
  por 10 minutos. É o preço de proteger a conta de ataque distribuído; a sessão aberta (refresh
  token) continua valendo.

## Configuração

Tudo por ambiente (`core/config.py`; exemplos em `.env.example`):

| Variável | Padrão | O que faz |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | `false` troca o limitador por um que nunca recusa |
| `RATE_LIMIT_LOGIN_IP_ATTEMPTS` / `..._IP_WINDOW_SECONDS` | `5` / `300` | limite por IP |
| `RATE_LIMIT_LOGIN_EMAIL_ATTEMPTS` / `..._EMAIL_WINDOW_SECONDS` | `10` / `600` | limite por e-mail |
| `RATE_LIMIT_STORAGE_URL` | `memory://` | onde os contadores vivem |
| `RATE_LIMIT_STRATEGY` | `moving-window` | ou `fixed-window`, `sliding-window-counter` |
| `CLIENT_IP_HEADER` | vazio | cabeçalho de onde ler o IP do cliente; vazio é o IP do socket |
| `TRUSTED_PROXY_COUNT` | `1` | posição do IP nesse cabeçalho, contada da direita |

Número zero ou negativo, estratégia desconhecida e storage inválido **impedem a subida** — não o
primeiro login. Não use variáveis `RATELIMIT_*` (sem o segundo `_`): esse prefixo é do próprio
slowapi, que o lê do ambiente e do `.env`.

## IP do cliente em cada provedor

Atrás de proxy, o socket mostra o proxy; sem `CLIENT_IP_HEADER`, todo cliente dividiria um contador
só, e cinco senhas erradas de quem quer que seja barrariam o login de todos. Por isso, em
`ENVIRONMENT=production` com o cabeçalho vazio, a subida deixa um aviso no log.

O cabeçalho é lido como lista (`a, b, c`) **da direita para a esquerda**: cada proxy acrescenta à
direita o IP de quem falou com ele, e o que o cliente escreveu fica à esquerda. Ler o primeiro da
lista deixaria quem ataca escolher o próprio IP a cada requisição.

| Onde roda | `CLIENT_IP_HEADER` | `TRUSTED_PROXY_COUNT` |
|---|---|---|
| **Railway** | `X-Real-IP` | `1` |
| AWS ALB (ECS, EKS, EC2) · nginx/Traefik próprio | `X-Forwarded-For` | `1` |
| AWS CloudFront → ALB | `X-Forwarded-For` | `2` |
| Cloudflare na frente da origem | `CF-Connecting-IP` | `1` |
| Sem proxy (compose local, VM exposta) | vazio | — |

- **Railway:** a borda sobrescreve o `X-Real-IP` com o IP de quem conectou (o cliente não consegue
  escrevê-lo desde 08/2024). Para o `X-Forwarded-For` há relatos conflitantes sobre acrescentar ou
  limpar a lista; o `X-Real-IP` evita depender disso. Com Cloudflare na frente, a Railway grava nele
  o `CF-Connecting-IP`.
- **Cloudflare:** só é confiável se a origem recusa tráfego que não venha da Cloudflare — senão
  qualquer um manda o cabeçalho direto.
- **Sem cabeçalho**, vale `request.client.host`, que o uvicorn reescreve a partir do
  `X-Forwarded-For` quando a conexão vem de `FORWARDED_ALLOW_IPS` (padrão `127.0.0.1`). Não use `*`
  ali: o uvicorn passa a ler o primeiro da lista, que o cliente escolhe.

**Confira no deploy.** Faça seis logins errados da sua máquina. O log registra
`login barrado pelo limite por ip (ip=...)`, e esse IP precisa ser o seu IP público
(`curl ifconfig.me`), não um `10.x` ou `100.x` da rede interna. Se for do proxy, o cabeçalho ou a
contagem estão errados.

## Mais de uma réplica ou worker: Redis

`memory://` conta dentro do processo. Com duas réplicas na Railway, `uvicorn --workers 4` ou duas
tasks no ECS, cada processo conta sozinho e o limite efetivo multiplica. Para um contador só:

1. acrescente `"redis>=5"` em `dependencies` no `pyproject.toml` (o `limits` usa esse cliente);
2. `RATE_LIMIT_STORAGE_URL=redis://usuario:senha@host:6379/0` — na Railway, referencie a variável
   do serviço Redis (`${{Redis.REDIS_URL}}`); no ElastiCache com TLS, `rediss://`;
3. nenhuma linha de código muda.

Duas decisões ficam para esse dia:

- o `hit` do slowapi é síncrono por dentro; com Redis, cada tentativa é uma ida à rede com o event
  loop parado. Para tirá-la do loop, troque a implementação (abaixo) por uma com `limits.aio`;
- Redis fora do ar hoje vira `500` no login (falha fechada). Para deixar entrar sem limite durante a
  queda, trate a exceção dentro do `hit` e registre no log.

## Como alterar

**Os números:** só as variáveis acima.

**Trocar o slowapi** — por `limits.aio`, outra biblioteca ou Redis direto: escreva uma classe com o
mesmo `hit` e devolva-a em `build_rate_limiter`. Serviço, rota e testes de API não mudam. Um
esboço com `redis.asyncio`, em janela fixa:

```python
class RedisRateLimiter:
    def __init__(self, client: Redis) -> None:
        self._redis = client

    async def hit(self, limit: RateLimit, key: str) -> RateLimitResult:
        name = f"{KEY_NAMESPACE}:{limit.window_seconds}:{key}"
        async with self._redis.pipeline(transaction=True) as pipe:
            count, _ = await pipe.incr(name).expire(name, limit.window_seconds, nx=True).execute()
        if count <= limit.attempts:
            return ALLOWED
        return RateLimitResult(allowed=False, retry_after_seconds=max(1, await self._redis.ttl(name)))
```

**Limitar outra rota** (`/auth/register`, por exemplo): a rota recebe o IP com
`Depends(get_client_ip)`, o serviço chama `rate_limiter.hit(RateLimit(...), "register:ip:<ip>")` e
levanta `TooManyAttemptsError(retry_after_seconds=...)` quando não couber. Os números vão para
`Settings`, como os do login. Não use os decoradores do slowapi.

## Testes

| Arquivo | O que prova |
|---|---|
| `tests/unit/test_rate_limit.py` | leitura do IP pela direita, cabeçalho forjado ou repetido, IPv6 /64, porta; o contador nas três estratégias; a janela móvel com relógio parado; falha na subida; aviso de produção |
| `tests/unit/test_auth_service.py` | ordem IP → e-mail, e-mail em hash, tentativa barrada sem banco nem argon2, IP no log |
| `tests/api/test_login_rate_limit.py` | 429 com `Retry-After`, spraying, senha certa contando, limite por e-mail entre IPs, `X-Forwarded-For` forjado, só o login limitado, limite desligado, `Retry-After` exposto no CORS |
| `tests/unit/test_config.py` | números positivos, estratégia conhecida, leitura do ambiente |

```bash
make test-unit k="rate_limit or auth_service"
make test-api k=rate_limit
```

**No Swagger** (`/docs`), com `make api`: registre uma conta em `POST /auth/register`; em
`POST /auth/login`, mande a senha errada cinco vezes (`401`) e a certa na sexta — `429`, com
`retry-after` nos cabeçalhos da resposta. Reinicie a API para zerar os contadores.
