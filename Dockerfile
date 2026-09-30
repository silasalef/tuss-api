# Imagem do projeto: roda o CLI e as migrations (e, nas próximas fases, a API e o worker).
# python:3.13-slim tem versão arm64 e amd64.

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM python:3.13-slim

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependências primeiro, em camada separada: mudar o código não reinstala tudo.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --locked --no-dev

# Usuário sem privilégios: o container não roda como root.
RUN useradd --system --uid 10001 --no-create-home tuss
USER tuss

ENTRYPOINT []
CMD ["tuss", "--help"]
