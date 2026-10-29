"""Modelo de dados e carga (SQLAlchemy Core sobre um arquivo SQLite).

Só os dados brutos são gravados; Total e variações devem ser derivados na consulta.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer,
    MetaData, Numeric, String, Table, create_engine, func, select,
)
from sqlalchemy.engine import Connection, Engine

from .extract import Informativo
from .validate import SEGMENTOS_BASE, volumes_consolidados

metadata = MetaData()

informativo = Table(
    "informativo", metadata,
    Column("id", Integer, primary_key=True),
    Column("edicao", Integer, nullable=False, unique=True),
    Column("ano_ref", Integer, nullable=False),
    Column("mes_ref", Integer, nullable=False),
    Column("arquivo_nome", String(255), nullable=False),
    Column("arquivo_sha256", String(64), nullable=False, unique=True),
    Column("carregado_em", DateTime, server_default=func.now()),
)

dim_segmento = Table(
    "dim_segmento", metadata,
    Column("id", Integer, primary_key=True),
    Column("codigo", String(30), nullable=False, unique=True),
    Column("nome", String(60), nullable=False),
    Column("agregado", Boolean, nullable=False),
)

dim_regiao = Table(
    "dim_regiao", metadata,
    Column("id", Integer, primary_key=True),
    Column("nome", String(20), nullable=False, unique=True),
)

fato_emplacamento = Table(
    "fato_emplacamento", metadata,
    Column("informativo_id", ForeignKey("informativo.id"), primary_key=True),
    Column("segmento_id", ForeignKey("dim_segmento.id"), primary_key=True),
    Column("ano", Integer, primary_key=True),
    Column("mes", Integer, primary_key=True),
    Column("tipo_periodo", String(10), primary_key=True),
    Column("quantidade", Integer, nullable=False),
    CheckConstraint("tipo_periodo IN ('MENSAL', 'ACUMULADO')",
                    name="ck_tipo_periodo"),
)

fato_participacao_regional = Table(
    "fato_participacao_regional", metadata,
    Column("informativo_id", ForeignKey("informativo.id"), primary_key=True),
    Column("segmento_id", ForeignKey("dim_segmento.id"), primary_key=True),
    Column("regiao_id", ForeignKey("dim_regiao.id"), primary_key=True),
    Column("ano", Integer, primary_key=True),
    Column("mes", Integer, primary_key=True),
    Column("percentual", Numeric(5, 2), nullable=False),
    Column("origem_valor", String(20), nullable=False),
)

# (codigo, nome, agregado)
SEGMENTOS = [
    ("AUTOS", "Automóveis", False),
    ("COM_LEVES", "Comerciais Leves", False),
    ("CAMINHOES", "Caminhões", False),
    ("ONIBUS", "Ônibus", False),
    ("MOTOS", "Motocicletas", False),
    ("IMPL_ROD", "Implementos Rodoviários", False),
    ("OUTROS", "Outros", False),
    ("AUTOS_COM_LEVES", "Automóveis + Comerciais Leves", True),
]
REGIOES = ["Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"]

def criar_schema(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as conexao:
        contagem = conexao.execute(
            select(func.count()).select_from(dim_segmento))
        if contagem.scalar() == 0:
            conexao.execute(dim_segmento.insert(), [
                {"codigo": codigo, "nome": nome, "agregado": agregado}
                for codigo, nome, agregado in SEGMENTOS
            ])
            conexao.execute(dim_regiao.insert(),
                            [{"nome": regiao} for regiao in REGIOES])


def linhas_emplacamento(info: Informativo) -> pd.DataFrame:
    """Converte a tabela larga do PDF em (segmento, ano, mês, período)."""
    ano, mes = info.ano_ref, info.mes_ref
    ano_mes_ant, mes_ant = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    colunas = {
        "mes_atual": (ano, mes, "MENSAL"),
        "mes_anterior": (ano_mes_ant, mes_ant, "MENSAL"),
        "acum_atual": (ano, mes, "ACUMULADO"),
        "mes_ano_ant": (ano - 1, mes, "MENSAL"),
        "acum_ano_ant": (ano - 1, mes, "ACUMULADO"),
    }
    v = volumes_consolidados(info).loc[SEGMENTOS_BASE]
    return pd.DataFrame([
        {
            "segmento": seg,
            "ano": a,
            "mes": m,
            "tipo_periodo": t,
            "quantidade": int(v.at[seg, col]),
        }
        for seg in SEGMENTOS_BASE
        for col, (a, m, t) in colunas.items()
    ])


def _ids(conexao: Connection, tabela: Table, chave: str) -> dict[str, int]:
    linhas = conexao.execute(select(tabela.c.id, tabela.c[chave]))
    return {valor: id_ for id_, valor in linhas}


def carregar(engine: Engine, info: Informativo) -> str:
    """Grava um informativo em uma transação e retorna o status."""
    with engine.begin() as conexao:
        existente = conexao.execute(
            select(informativo.c.id, informativo.c.arquivo_sha256)
            .where(informativo.c.edicao == info.edicao)
        ).first()
        if existente and existente.arquivo_sha256 == info.sha256:
            return (f"Ed. {info.edicao}: já carregada (mesmo arquivo), "
                    "nada a fazer")
        if existente:
            for tabela in (fato_emplacamento, fato_participacao_regional):
                conexao.execute(tabela.delete().where(
                    tabela.c.informativo_id == existente.id))
            conexao.execute(
                informativo.delete().where(informativo.c.id == existente.id))

        informativo_id = conexao.execute(informativo.insert().values(
            edicao=info.edicao,
            ano_ref=info.ano_ref,
            mes_ref=info.mes_ref,
            arquivo_nome=info.arquivo,
            arquivo_sha256=info.sha256,
        )).inserted_primary_key[0]

        ids_segmento = _ids(conexao, dim_segmento, "codigo")
        ids_regiao = _ids(conexao, dim_regiao, "nome")

        emplacamentos = linhas_emplacamento(info)
        conexao.execute(fato_emplacamento.insert(), [
            {
                "informativo_id": informativo_id,
                "segmento_id": ids_segmento[linha.segmento],
                "ano": linha.ano,
                "mes": linha.mes,
                "tipo_periodo": linha.tipo_periodo,
                "quantidade": linha.quantidade,
            }
            for linha in emplacamentos.itertuples()
        ])

        regional = info.regional
        conexao.execute(fato_participacao_regional.insert(), [
            {
                "informativo_id": informativo_id,
                "segmento_id": ids_segmento[linha.segmento],
                "regiao_id": ids_regiao[linha.regiao],
                "ano": linha.ano,
                "mes": linha.mes,
                "percentual": round(float(linha.percentual), 2),
                "origem_valor": linha.origem_valor,
            }
            for linha in regional.itertuples()
        ])

    acao = "substituída" if existente else "carregada"
    return (f"Ed. {info.edicao} ({info.mes_ref:02d}/{info.ano_ref}) {acao}: "
            f"{len(emplacamentos)} linhas de emplacamento, "
            f"{len(regional)} de participação regional")


def engine_de(arquivo: str | Path) -> Engine:
    """Abre (ou cria) o banco SQLite no arquivo indicado."""
    caminho = Path(arquivo).resolve()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{caminho.as_posix()}", future=True)
