"""Recorta dos informativos completos só as páginas usadas na extração.

As páginas são escolhidas pelo conteúdo, não pelo número:

    1. barras coloridas de região          -> gráficos regionais
    2. título "Resumo Mensal <mês>"        -> tabela mestra
    3. >= 3 linhas de volume e de part.    -> tabela do segmento

    python -m fenabrave.recortar            # entrada_dataset -> recortados
    python -m fenabrave.recortar --forcar   # refaz os recortes existentes
    python -m fenabrave.recortar --entrada pasta --saida outra
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .extract import (
    SEGMENTOS_GRAFICO, barras_regiao, extrair_tabelas, normalizar,
    titulos_graficos,
)

PASTA_ENTRADA_PADRAO = Path(__file__).resolve().parent.parent / "entrada_dataset"
PASTA_SAIDA_PADRAO = PASTA_ENTRADA_PADRAO / "recortados"

RE_RESUMO = re.compile(r"resumo mensal \w+ de \d{4}")
RODAPES_ESPERADOS = {1, 2, 5, 34, 37, 45, 52}
PAGINAS_GRAFICO_ESPERADAS = 4


@dataclass
class PaginaInfo:
    indice: int
    rodape: int | None
    regra: str | None  # GRAFICO, RESUMO, TABELA ou None (descartada)
    titulo: str
    segmentos: list[str] = field(default_factory=list)
    n_volumes: int = 0
    n_participacao: int = 0


@dataclass
class Resultado:
    origem: Path
    destino: Path
    status: str
    paginas: list[PaginaInfo] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def mantidas(self) -> list[PaginaInfo]:
        return [p for p in self.paginas if p.regra]


def _rodape(page: pymupdf.Page) -> int | None:
    """Número de página impresso no rodapé."""
    limite = page.rect.height - 70
    numeros = [w[4] for w in page.get_text("words")
               if w[3] > limite and w[0] > 400 and w[4].isdigit()]
    return int(numeros[0]) if numeros else None


def _titulo(page: pymupdf.Page) -> str:
    linhas = [t.strip() for t in page.get_text().splitlines() if t.strip()]
    if len(linhas) > 3:
        return linhas[3]
    return linhas[0] if linhas else ""


def analisar_pagina(page: pymupdf.Page, indice: int) -> PaginaInfo:
    volumes, participacao = extrair_tabelas(page, indice)
    info = PaginaInfo(
        indice=indice,
        rodape=_rodape(page),
        regra=None,
        titulo=_titulo(page),
        n_volumes=len(volumes),
        n_participacao=len(participacao),
    )
    segs_volume = {v["segmento"] for v in volumes}

    if barras_regiao(page):
        info.regra = "GRAFICO"
        info.segmentos = sorted({c for _, c in titulos_graficos(page)})
    elif RE_RESUMO.search(normalizar(page.get_text())):
        info.regra = "RESUMO"
        info.segmentos = sorted(segs_volume)
    elif len(volumes) >= 3 and len(participacao) >= 3:
        info.regra = "TABELA"
        info.segmentos = sorted(segs_volume)
    else:
        segs_part = {p["segmento"] for p in participacao}
        info.segmentos = sorted(segs_volume | segs_part)
    return info


def analisar(doc: pymupdf.Document) -> list[PaginaInfo]:
    return [analisar_pagina(page, i) for i, page in enumerate(doc, start=1)]


def conferir(paginas: list[PaginaInfo]) -> tuple[list[str], list[str]]:
    """Confere a seleção antes de gravar. Retorna (erros, avisos)."""
    erros, avisos = [], []
    mantidas = [p for p in paginas if p.regra]
    resumos = [p for p in mantidas if p.regra == "RESUMO"]
    graficos = [p for p in mantidas if p.regra == "GRAFICO"]

    if not mantidas:
        erros.append("Nenhuma página de interesse encontrada")
    if len(resumos) != 1:
        erros.append(
            f"Esperava 1 página 'Resumo Mensal', achei {len(resumos)}")

    segmentos = {s for p in graficos for s in p.segmentos}
    faltando = set(SEGMENTOS_GRAFICO.values()) - segmentos
    if faltando:
        erros.append(f"Gráficos regionais ausentes: {sorted(faltando)}")

    for p in paginas:
        if p.regra:
            continue
        if p.n_volumes >= 2 or p.n_participacao >= 2:
            erros.append(f"p{p.indice} descartada com tabela relevante "
                         f"({p.n_volumes} volume, {p.n_participacao} "
                         f"participação): {p.titulo}")
        elif p.n_volumes or p.n_participacao:
            n = p.n_volumes + p.n_participacao
            avisos.append(f"p{p.indice} descartada com {n} linha solta "
                          f"({p.segmentos}): {p.titulo}")

    if len(mantidas) != len(RODAPES_ESPERADOS):
        avisos.append(f"{len(mantidas)} páginas mantidas, "
                      f"esperava {len(RODAPES_ESPERADOS)}")
    if len(graficos) != PAGINAS_GRAFICO_ESPERADAS:
        avisos.append(f"{len(graficos)} páginas de gráfico, "
                      f"esperava {PAGINAS_GRAFICO_ESPERADAS}")
    rodapes = {p.rodape for p in mantidas}
    if rodapes != RODAPES_ESPERADOS:
        avisos.append(f"rodapés mantidos {sorted(r for r in rodapes if r)}, "
                      f"esperava {sorted(RODAPES_ESPERADOS)}")
    return erros, avisos


def recortar_pdf(origem: Path, destino: Path,
                 forcar: bool = False) -> Resultado:
    atualizado = (destino.exists()
                  and destino.stat().st_mtime >= origem.stat().st_mtime)
    if atualizado and not forcar:
        return Resultado(origem, destino, "já recortado")

    doc = pymupdf.open(origem)
    paginas = analisar(doc)
    erros, avisos = conferir(paginas)
    status = "erro" if erros else "recortado"
    res = Resultado(origem, destino, status, paginas, erros, avisos)
    if erros:
        return res

    novo = pymupdf.open()
    for p in res.mantidas:
        novo.insert_pdf(doc, from_page=p.indice - 1, to_page=p.indice - 1)
    destino.parent.mkdir(parents=True, exist_ok=True)
    novo.save(destino, garbage=4, deflate=True)
    return res


def _imprimir(res: Resultado) -> None:
    print(f"\n== {res.origem.name}")
    if res.status == "já recortado":
        print(f"   {res.status} (use --forcar para refazer)")
        return
    for p in res.mantidas:
        segs = ", ".join(p.segmentos) or "-"
        print(f"   p{p.rodape or p.indice:<3} {p.regra:<8} "
              f"{p.titulo[:46]:<46} [{segs}]")
    for aviso in res.avisos:
        print(f"   AVISO: {aviso}")
    for erro in res.erros:
        print(f"   ERRO: {erro}")
    gravado = res.destino.name if res.status == "recortado" else "NÃO GRAVADO"
    print(f"   {len(res.mantidas)} de {len(res.paginas)} páginas -> {gravado}")


def recortar_pasta(entrada: Path, saida: Path, forcar: bool = False) -> int:
    pdfs = sorted(entrada.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"Nenhum PDF encontrado em {entrada}")
    falhas = 0
    for pdf in pdfs:
        try:
            res = recortar_pdf(pdf, saida / pdf.name, forcar)
        except Exception as e:
            print(f"\n== {pdf.name}")
            print(f"   ERRO: falha inesperada ao processar o arquivo: {e}")
            falhas += 1
            continue
        _imprimir(res)
        if res.erros:
            falhas += 1
    return falhas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fenabrave.recortar", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entrada", default=str(PASTA_ENTRADA_PADRAO),
                    help="pasta com os PDFs completos "
                         f"(padrão: {PASTA_ENTRADA_PADRAO})")
    ap.add_argument("--saida", default=str(PASTA_SAIDA_PADRAO),
                    help=f"pasta dos recortes (padrão: {PASTA_SAIDA_PADRAO})")
    ap.add_argument("--forcar", action="store_true",
                    help="refaz mesmo se o recorte já existir")
    args = ap.parse_args(argv)

    falhas = recortar_pasta(Path(args.entrada), Path(args.saida), args.forcar)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
