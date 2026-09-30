#!/usr/bin/env bash
# Mede o desempenho da API contra as metas do planejamento (ver tests/carga/api.js).
#
# Sobe uma cópia temporária da API, com um token temporário e sem os limites por
# minuto, na rede interna do compose. A API do dia a dia não é tocada e nenhuma
# porta nova é publicada. Tudo é removido no fim, mesmo se o teste falhar.
#
# Uso (na raiz do repositório, com o banco no ar e tabelas importadas):
#   scripts/teste_de_carga.sh

set -euo pipefail

CONTAINER=tuss-api-carga
K6_IMAGEM=grafana/k6:2.3.0

limpar() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap limpar EXIT

TOKEN=$(uv run python -c "from tuss.api.token import gerar_token; print(gerar_token())")
HASH=$(uv run python -c "import sys; from tuss.api.token import hash_token; print(hash_token(sys.argv[1]))" "$TOKEN")

echo "Subindo cópia temporária da API..."
docker compose build -q api
docker compose run -d --rm --no-deps --name "$CONTAINER" \
  -e TUSS_API_TOKENS_SHA256="$HASH" \
  -e TUSS_LIMITE_CONSULTAS_POR_MINUTO=1000000 \
  -e TUSS_LIMITE_BUSCAS_POR_MINUTO=1000000 \
  api >/dev/null

for _ in $(seq 30); do
  if docker exec "$CONTAINER" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)" \
    2>/dev/null; then
    break
  fi
  sleep 1
done

docker run --rm --network tuss-api_default \
  -v "$PWD/tests/carga:/scripts:ro" \
  -e URL="http://$CONTAINER:8000" -e TOKEN="$TOKEN" \
  "$K6_IMAGEM" run --quiet /scripts/api.js
