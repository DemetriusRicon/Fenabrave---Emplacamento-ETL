"""Validações cruzadas com os próprios números impressos no PDF."""
from __future__ import annotations

import pandas as pd

from .extract import Informativo

SEGMENTOS_BASE = [
    "AUTOS", "COM_LEVES", "CAMINHOES", "ONIBUS", "MOTOS", "IMPL_ROD",
    "OUTROS",
]
AGREGADOS = {
    "AUTOS_COM_LEVES": ["AUTOS", "COM_LEVES"],
    "CAMINHOES_ONIBUS": ["CAMINHOES", "ONIBUS"],
    "SUBTOTAL": ["AUTOS", "COM_LEVES", "CAMINHOES", "ONIBUS"],
}
COLS_VOLUME = [
    "mes_atual", "mes_anterior", "acum_atual", "mes_ano_ant", "acum_ano_ant",
]
# Coluna impressa -> (numerador, denominador).
VARIACOES = {
    "var_mes_anterior": ("mes_atual", "mes_anterior"),
    "var_mes_ano_ant": ("mes_atual", "mes_ano_ant"),
    "var_acum": ("acum_atual", "acum_ano_ant"),
}
PARTICIPACOES = {
    "part_acum_atual": "acum_atual",
    "part_acum_ano_ant": "acum_ano_ant",
}


def volumes_consolidados(info: Informativo) -> pd.DataFrame:
    """Uma linha por segmento (algumas páginas repetem a tabela)."""
    vol = info.tabela_volumes.drop_duplicates("segmento")
    return vol.set_index("segmento")


def validar(info: Informativo) -> list[str]:
    erros: list[str] = []
    vol = info.tabela_volumes
    if vol.empty:
        return ["Nenhuma tabela de volumes encontrada"]

    cols = COLS_VOLUME + list(VARIACOES)
    for seg, grupo in vol.groupby("segmento"):
        if len(grupo[cols].drop_duplicates()) > 1:
            erros.append(f"{seg}: valores divergentes entre as páginas "
                         f"{sorted(grupo.pagina)}")

    v = volumes_consolidados(info)
    faltando = set(SEGMENTOS_BASE) - set(v.index)
    if faltando:
        erros.append(f"Segmentos ausentes na tabela: {sorted(faltando)}")
        return erros

    for agregado, partes in AGREGADOS.items():
        if agregado not in v.index:
            continue
        for c in COLS_VOLUME:
            soma = v.loc[partes, c].sum()
            impresso = v.at[agregado, c]
            if soma != impresso:
                erros.append(f"{agregado}.{c}: impresso {impresso} "
                             f"!= soma {soma}")

    for seg, linha in v.iterrows():
        for col, (num, den) in VARIACOES.items():
            if linha[den] == 0:
                continue
            calc = (linha[num] / linha[den] - 1) * 100
            if abs(calc - linha[col]) > 0.011:
                erros.append(f"{seg}.{col}: impresso {linha[col]} "
                             f"!= calculado {calc:.2f}")

    total = {c: v.loc[SEGMENTOS_BASE, c].sum()
             for c in PARTICIPACOES.values()}
    for _, linha in info.tabela_participacao.iterrows():
        for col, base in PARTICIPACOES.items():
            if total[base] == 0:
                continue
            calc = 100 * v.at[linha.segmento, base] / total[base]
            if abs(calc - linha[col]) > 0.051:
                erros.append(f"p{linha.pagina} participação "
                             f"{linha.segmento}.{col}: impresso {linha[col]} "
                             f"!= calculado {calc:.2f}")

    reg = info.regional
    if reg.empty:
        erros.append("Nenhum gráfico regional encontrado")
        return erros

    somas = reg.groupby(["segmento", "mes"]).percentual.sum()
    for (seg, mes), s in somas.items():
        if abs(s - 100) > 0.1:
            erros.append(f"Regional {seg} mês {mes}: soma {s:.2f}%")
    esperado = set(range(1, info.mes_ref + 1))
    for seg, meses in reg.groupby("segmento").mes:
        if set(meses) != esperado:
            erros.append(f"Regional {seg}: meses {sorted(set(meses))}, "
                         f"esperado 1..{info.mes_ref}")

    return erros
