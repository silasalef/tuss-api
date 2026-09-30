import json
from pathlib import Path
from typing import Any

import pytest

from tuss.ingestion.lote import MAX_ERROS_GUARDADOS, ArquivoMudou, ler_lote

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ans"


def _registro(codigo: str, tabela: str = "tuss-22") -> dict[str, Any]:
    return {"id": codigo, "source": tabela, "display_name": f"Procedimento {codigo}", "extras": {}}


def _json(destino: Path, registros: list[Any]) -> Path:
    destino.write_text(json.dumps(registros), encoding="utf-8")
    return destino


def test_fixture_real_nao_tem_problemas() -> None:
    lote = ler_lote(FIXTURES / "concepts_tuss-22_page1.json")
    assert lote.problemas == []
    assert lote.tabela == "tuss-22"
    assert lote.total_registros == lote.total_validos == len(list(lote.conceitos())) == 25
    assert (str(lote.inicio_vigencia_min), str(lote.inicio_vigencia_max)) == (
        "2010-06-09",
        "2026-08-01",
    )


def test_registro_invalido_impede_importacao(tmp_path: Path) -> None:
    lote = ler_lote(_json(tmp_path / "t.json", [_registro("1"), {"id": 2}]))
    assert lote.total_registros == 2
    assert (lote.total_validos, lote.total_erros) == (1, 1)
    assert lote.problemas == ["1 registro(s) inválido(s)"]


def test_codigo_duplicado_impede_importacao(tmp_path: Path) -> None:
    lote = ler_lote(_json(tmp_path / "t.json", [_registro("1"), _registro("1")]))
    assert lote.duplicados == [("tuss-22", "1")]
    assert lote.problemas == ["1 código(s) duplicado(s)"]


def test_arquivo_com_duas_tabelas_impede_importacao(tmp_path: Path) -> None:
    lote = ler_lote(_json(tmp_path / "t.json", [_registro("1"), _registro("1", "tuss-20")]))
    assert lote.problemas == ["esperada uma tabela por arquivo, veio 2"]


def test_arquivo_vazio_impede_importacao(tmp_path: Path) -> None:
    lote = ler_lote(_json(tmp_path / "t.json", []))
    assert lote.problemas == ["esperada uma tabela por arquivo, veio 0"]


def test_muitos_erros_sao_contados_mas_so_os_primeiros_guardados(tmp_path: Path) -> None:
    lote = ler_lote(_json(tmp_path / "t.json", [{"id": n} for n in range(500)]))
    assert (lote.total_erros, len(lote.erros)) == (500, MAX_ERROS_GUARDADOS)


def test_arquivo_alterado_depois_de_validado_e_recusado(tmp_path: Path) -> None:
    caminho = _json(tmp_path / "t.json", [_registro("1")])
    lote = ler_lote(caminho)
    _json(caminho, [_registro("2")])
    with pytest.raises(ArquivoMudou):
        list(lote.conceitos())
