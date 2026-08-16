#!/bin/sh
# Entrypoint do container da API.
#
# Em local/test: espera o banco aceitar conexão, aplica as migrations e semeia
# a conta de desenvolvimento. É o que faz `make up` deixar o ambiente pronto
# para usar, sem passo manual.
#
# Em staging/produção não faz nada disso e apenas sobe o servidor: migration é
# passo deliberado, não efeito colateral de subir um container — várias
# réplicas subindo juntas correriam entre si pela mesma migration. O seed é
# barrado por dentro, no próprio comando (`cli.refuse_outside_dev`), e não só
# por este `case`.
set -eu

MAX_ATTEMPTS=${DB_WAIT_ATTEMPTS:-30}

case "${ENVIRONMENT:-}" in
    local | test)
        echo "[entrypoint] aplicando migrations (ENVIRONMENT=${ENVIRONMENT})"
        attempt=1
        until alembic upgrade head >/dev/null 2>&1; do
            if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
                echo "[entrypoint] banco não respondeu em ${MAX_ATTEMPTS}s; erro real abaixo:" >&2
                alembic upgrade head # sem silenciar, para a causa aparecer no log
                exit 1
            fi
            attempt=$((attempt + 1))
            sleep 1
        done
        echo "[entrypoint] migrations aplicadas"

        python -m cli seed-dev
        ;;
    *)
        echo "[entrypoint] ENVIRONMENT=${ENVIRONMENT:-<vazio>}: sem migration automática e sem seed"
        ;;
esac

exec "$@"
