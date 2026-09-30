"""Conceito TUSS normalizado e o hash que define "mudou ou não mudou".

Não importa banco, HTTP nem framework: recebe o dicionário que a ANS devolve
(igual na API e no arquivo do portal) e devolve um objeto validado.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Campos de `extras` que viram colunas próprias; o resto vai para `atributos`.
_CAMPOS_DE_DATA = ("inicio_vigencia", "fim_vigencia", "fim_implantacao")
# A ANS usa "-" para "sem data" (período aberto).
_SEM_DATA = {"", "-"}
_RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_ESPACOS = re.compile(r"\s+")


class ConceitoInvalido(ValueError):
    """O registro da fonte não tem o formato esperado. A carga deve falhar alto."""


@dataclass(frozen=True, slots=True)
class Conceito:
    tabela: str
    codigo: str
    descricao: str
    inicio_vigencia: date | None
    fim_vigencia: date | None
    fim_implantacao: date | None
    atributos: dict[str, str] = field(default_factory=dict)

    @property
    def hash_conteudo(self) -> str:
        """SHA-256 dos campos normalizados, em ordem fixa.

        Espaço extra e diferença de maiúsculas/minúsculas não contam como mudança;
        acento conta (é outra palavra).
        """
        partes = {
            "descricao": chave_texto(self.descricao),
            "inicio_vigencia": _data_iso(self.inicio_vigencia),
            "fim_vigencia": _data_iso(self.fim_vigencia),
            "fim_implantacao": _data_iso(self.fim_implantacao),
            "atributos": {k: chave_texto(v) for k, v in sorted(self.atributos.items())},
        }
        bruto = json.dumps(partes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(bruto.encode("utf-8")).hexdigest()

    def como_dict(self) -> dict[str, Any]:
        """Conteúdo do conceito em JSON (datas em AAAA-MM-DD), como vai para os eventos."""
        return {
            "descricao": self.descricao,
            "inicio_vigencia": _data_iso(self.inicio_vigencia),
            "fim_vigencia": _data_iso(self.fim_vigencia),
            "fim_implantacao": _data_iso(self.fim_implantacao),
            "atributos": dict(sorted(self.atributos.items())),
        }


def normalizar_texto(valor: str) -> str:
    """Forma canônica para guardar: Unicode NFC, sem espaços repetidos nem nas pontas."""
    return _RE_ESPACOS.sub(" ", unicodedata.normalize("NFC", valor)).strip()


def conceito_da_fonte(registro: Any) -> Conceito:
    """Converte um registro da ANS em `Conceito`, validando tipos e datas."""
    if not isinstance(registro, dict):
        raise ConceitoInvalido(f"registro não é objeto JSON: {type(registro).__name__}")

    codigo = _texto_obrigatorio(registro, "id")
    tabela = _texto_obrigatorio(registro, "source")
    descricao = _texto_obrigatorio(registro, "display_name")

    extras = registro.get("extras") or {}
    if not isinstance(extras, dict):
        raise ConceitoInvalido(f"{tabela}/{codigo}: 'extras' não é objeto")

    datas = {nome: _data(extras.get(nome), tabela, codigo, nome) for nome in _CAMPOS_DE_DATA}
    atributos: dict[str, str] = {}
    for nome, valor in extras.items():
        if nome in _CAMPOS_DE_DATA or valor is None:
            continue
        if not isinstance(valor, str):
            raise ConceitoInvalido(f"{tabela}/{codigo}: extras.{nome} não é texto")
        atributos[nome] = normalizar_texto(valor)

    return Conceito(
        tabela=tabela,
        codigo=codigo,
        descricao=descricao,
        inicio_vigencia=datas["inicio_vigencia"],
        fim_vigencia=datas["fim_vigencia"],
        fim_implantacao=datas["fim_implantacao"],
        atributos=atributos,
    )


def _texto_obrigatorio(registro: dict[str, Any], campo: str) -> str:
    valor = registro.get(campo)
    # Código é texto: um número aqui indicaria que alguém perdeu zeros à esquerda.
    if not isinstance(valor, str) or not valor.strip():
        raise ConceitoInvalido(f"campo '{campo}' ausente, vazio ou não é texto: {valor!r}")
    return normalizar_texto(valor)


def _data(valor: Any, tabela: str, codigo: str, nome: str) -> date | None:
    if valor is None or (isinstance(valor, str) and valor.strip() in _SEM_DATA):
        return None
    if not isinstance(valor, str) or not _RE_DATA.match(valor.strip()):
        raise ConceitoInvalido(f"{tabela}/{codigo}: extras.{nome} não é data AAAA-MM-DD: {valor!r}")
    try:
        return date.fromisoformat(valor.strip())
    except ValueError as exc:
        raise ConceitoInvalido(
            f"{tabela}/{codigo}: extras.{nome} é data inexistente: {valor!r}"
        ) from exc


def chave_texto(valor: str) -> str:
    """Forma usada para comparar: a canônica, sem diferença de maiúsculas/minúsculas."""
    return normalizar_texto(valor).casefold()


def _data_iso(valor: date | None) -> str | None:
    return valor.isoformat() if valor else None
