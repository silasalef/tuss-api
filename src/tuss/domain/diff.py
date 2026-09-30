"""O que mudou entre duas cargas de uma tabela. Sem banco, HTTP nem framework.

Quatro tipos de mudança, sempre por código:

- `incluido`: código que nunca tinha aparecido;
- `alterado`: código que continua na lista, com conteúdo diferente (hash diferente);
- `removido`: código que estava publicado e não veio na carga nova (a ANS retira
  o código da lista em vez de preencher `fim_vigencia`);
- `reativado`: código que já tinha sido removido e voltou.

"Conteúdo diferente" é o mesmo critério do hash (`Conceito.hash_conteudo`): espaço
extra e maiúsculas/minúsculas não contam.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from typing import Literal

from tuss.domain.conceito import Conceito, chave_texto

TipoMudanca = Literal["incluido", "alterado", "removido", "reativado"]

_CAMPOS = ("descricao", "inicio_vigencia", "fim_vigencia", "fim_implantacao")


@dataclass(frozen=True, slots=True)
class Mudanca:
    tipo: TipoMudanca
    codigo: str
    antes: Conceito | None  # versão publicada até agora (nula em incluido/reativado)
    depois: Conceito | None  # versão da carga nova (nula em removido)
    campos_alterados: tuple[str, ...] = ()  # só em alterado


def comparar_conceito(
    antes: Conceito | None, depois: Conceito | None, *, ja_existiu: bool = False
) -> Mudanca | None:
    """Mudança de um código entre a versão publicada e a da carga nova, ou `None` se igual.

    `ja_existiu`: o código já apareceu numa carga anterior, mas hoje não está publicado
    (foi removido). Se ele voltar, é `reativado`, não `incluido`.
    """
    if antes is None:
        if depois is None:
            return None
        tipo: TipoMudanca = "reativado" if ja_existiu else "incluido"
        return Mudanca(tipo, depois.codigo, None, depois)
    if depois is None:
        return Mudanca("removido", antes.codigo, antes, None)
    if antes.codigo != depois.codigo:
        raise ValueError(f"códigos diferentes: {antes.codigo!r} e {depois.codigo!r}")
    if antes.hash_conteudo == depois.hash_conteudo:
        return None
    return Mudanca("alterado", antes.codigo, antes, depois, campos_alterados(antes, depois))


def comparar(
    publicados: Mapping[str, Conceito],
    novos: Mapping[str, Conceito],
    ja_existiram: Set[str] = frozenset(),
) -> list[Mudanca]:
    """Todas as mudanças de uma tabela, em ordem de código.

    `publicados` e `novos` são indexados por código. `ja_existiram`: códigos removidos
    em cargas anteriores (servem para distinguir `reativado` de `incluido`).
    """
    mudancas = []
    for codigo in sorted(publicados.keys() | novos.keys()):
        mudanca = comparar_conceito(
            publicados.get(codigo), novos.get(codigo), ja_existiu=codigo in ja_existiram
        )
        if mudanca is not None:
            mudancas.append(mudanca)
    return mudancas


def aplicar(publicados: Mapping[str, Conceito], mudancas: Iterable[Mudanca]) -> dict[str, Conceito]:
    """O estado depois das mudanças. É o que a publicação faz no banco.

    Garantia (testada com hypothesis): `aplicar(A, comparar(A, B))` tem os mesmos códigos
    de B, cada um com o mesmo hash que em B.
    """
    resultado = dict(publicados)
    for mudanca in mudancas:
        if mudanca.depois is None:
            del resultado[mudanca.codigo]
        else:
            resultado[mudanca.codigo] = mudanca.depois
    return resultado


def campos_alterados(antes: Conceito, depois: Conceito) -> tuple[str, ...]:
    """Nomes dos campos que mudaram; atributos extras aparecem como `atributos.<nome>`."""
    mudaram = [
        campo
        for campo in _CAMPOS
        if _chave(getattr(antes, campo)) != _chave(getattr(depois, campo))
    ]
    for nome in sorted(antes.atributos.keys() | depois.atributos.keys()):
        if _chave(antes.atributos.get(nome)) != _chave(depois.atributos.get(nome)):
            mudaram.append(f"atributos.{nome}")
    return tuple(mudaram)


def _chave(valor: object) -> object:
    return chave_texto(valor) if isinstance(valor, str) else valor
