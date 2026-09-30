from pathlib import Path

from tuss.api.app import openapi_json

VERSIONADO = Path(__file__).parents[2] / "docs" / "openapi.json"


def test_openapi_versionado_esta_atualizado() -> None:
    # Falhou? Rode `uv run tuss openapi > docs/openapi.json` e faça commit do arquivo.
    assert VERSIONADO.read_text(encoding="utf-8") == openapi_json()
