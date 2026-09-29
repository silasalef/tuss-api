import json
import zipfile
from pathlib import Path

import pytest

from tuss.ingestion import arquivo

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ans"


def _zip(destino: Path, membros: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(destino, "w") as zf:
        for nome, conteudo in membros.items():
            zf.writestr(nome, conteudo)
    return destino


def test_le_json_do_portal() -> None:
    fonte = arquivo.abrir(FIXTURES / "tuss-23_20260929.json")
    assert len(fonte.sha256) == 64
    assert [r["id"] for r in arquivo.registros(fonte)] == ["2", "1"]


def test_le_zip_com_varios_json_em_ordem_de_nome(tmp_path: Path) -> None:
    caminho = _zip(
        tmp_path / "t.zip",
        {
            "t(2).json": json.dumps([{"id": "b"}]).encode(),
            "t(1).json": json.dumps([{"id": "a"}]).encode(),
        },
    )
    ids = [r["id"] for r in arquivo.registros(arquivo.abrir(caminho))]
    assert ids == ["a", "b"]


def test_sha256_muda_com_o_conteudo(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text("[]")
    b.write_text("[ ]")
    assert arquivo.abrir(a).sha256 != arquivo.abrir(b).sha256


def test_recusa_extensao_desconhecida(tmp_path: Path) -> None:
    caminho = tmp_path / "t.csv"
    caminho.write_text("id;source")
    with pytest.raises(arquivo.ArquivoInvalido):
        arquivo.abrir(caminho)


def test_recusa_json_que_nao_e_lista(tmp_path: Path) -> None:
    caminho = tmp_path / "t.json"
    caminho.write_text('{"id": "1"}')
    with pytest.raises(arquivo.ArquivoInvalido):
        list(arquivo.registros(arquivo.abrir(caminho)))


def test_recusa_membro_que_nao_e_json(tmp_path: Path) -> None:
    caminho = _zip(tmp_path / "t.zip", {"leia-me.exe": b"MZ"})
    with pytest.raises(arquivo.ArquivoInvalido):
        list(arquivo.registros(arquivo.abrir(caminho)))


def test_recusa_zip_grande_demais(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arquivo, "MAX_BYTES_POR_JSON", 10)
    caminho = _zip(tmp_path / "t.zip", {"t.json": json.dumps([{"id": "x" * 50}]).encode()})
    with pytest.raises(arquivo.ArquivoInvalido):
        list(arquivo.registros(arquivo.abrir(caminho)))


def test_recusa_zip_corrompido(tmp_path: Path) -> None:
    caminho = tmp_path / "t.zip"
    caminho.write_bytes(b"isto nao e zip")
    with pytest.raises(arquivo.ArquivoInvalido):
        list(arquivo.registros(arquivo.abrir(caminho)))
