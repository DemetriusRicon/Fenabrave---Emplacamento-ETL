"""Extração do Informativo de Emplacamentos Fenabrave (PDF) com PyMuPDF.

Tabelas são lidas pelas palavras e suas coordenadas. Gráficos de barras
são lidos pelos retângulos coloridos (cor = região) e pelos rótulos.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import pandas as pd
import pymupdf

MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
MESES_ABREV = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

# Cor de preenchimento (RGB 0-1, 2 casas) -> região.
CORES_REGIAO = {
    (0.25, 1.0, 0.45): "Norte",
    (1.0, 0.58, 0.0): "Nordeste",
    (1.0, 0.79, 0.0): "Centro-Oeste",
    (0.8, 0.59, 1.0): "Sudeste",
    (0.59, 0.8, 1.0): "Sul",
}
REGIOES = ["Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"]

SEGMENTOS_TABELA = {
    "autos": "AUTOS",
    "com leves": "COM_LEVES",
    "a + b": "AUTOS_COM_LEVES",
    "caminhoes": "CAMINHOES",
    "onibus": "ONIBUS",
    "c + d": "CAMINHOES_ONIBUS",
    "subtotal": "SUBTOTAL",
    "motos": "MOTOS",
    "impl rod": "IMPL_ROD",
    "outros": "OUTROS",
}
SEGMENTOS_GRAFICO = {
    "automoveis": "AUTOS",
    "comerciais leves": "COM_LEVES",
    "automoveis + comerciais leves": "AUTOS_COM_LEVES",
    "caminhoes": "CAMINHOES",
    "onibus": "ONIBUS",
    "motocicletas": "MOTOS",
    "implementos rodoviarios": "IMPL_ROD",
}

RE_DECIMAL = re.compile(r"^[-−]?\d+,\d+$")
X_ROTULO = 140  # coluna onde termina o rótulo e começam os números


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, sem prefixo 'A)' e sem pontos."""
    t = unicodedata.normalize("NFKD", texto)
    t = t.encode("ascii", "ignore").decode()
    t = re.sub(r"^[A-Z]\)\s*", "", t.strip())
    t = t.lower().replace(".", "")
    return re.sub(r"\s+", " ", t).strip()


def para_int(s: str) -> int:
    return int(s.replace(".", ""))


def para_float(s: str) -> float:
    return float(s.replace("−", "-").replace(".", "").replace(",", "."))


@dataclass
class Palavra:
    x0: float
    y0: float
    x1: float
    y1: float
    texto: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Informativo:
    arquivo: str
    sha256: str
    edicao: int
    ano_ref: int
    mes_ref: int
    tabela_volumes: pd.DataFrame = field(default_factory=pd.DataFrame)
    tabela_participacao: pd.DataFrame = field(default_factory=pd.DataFrame)
    regional: pd.DataFrame = field(default_factory=pd.DataFrame)
    avisos: list[str] = field(default_factory=list)


def _palavras(page: pymupdf.Page) -> list[Palavra]:
    return [Palavra(*w[:5]) for w in page.get_text("words")]


def _linhas(palavras: list[Palavra],
            tol: float = 4.0) -> list[list[Palavra]]:
    """Agrupa palavras cujo centro vertical está a menos de `tol` pt."""
    linhas: list[list[Palavra]] = []
    for p in sorted(palavras, key=lambda p: p.cy):
        if linhas and abs(linhas[-1][0].cy - p.cy) <= tol:
            linhas[-1].append(p)
        else:
            linhas.append([p])
    return [sorted(linha, key=lambda p: p.x0) for linha in linhas]


def _cabecalho(doc: pymupdf.Document) -> tuple[int, int, int]:
    texto = "\n".join(page.get_text() for page in doc)
    edicao = re.search(r"Ed\.\s*(\d+)", texto)
    norm = normalizar(texto)
    ref = (re.search(r"resumo mensal (\w+) de (\d{4})", norm)
           or re.search(r"ate (\w+)/(\d{4})", norm))
    if not (edicao and ref and ref.group(1) in MESES):
        raise ValueError("Não encontrei edição/mês de referência no PDF")
    return int(edicao.group(1)), int(ref.group(2)), MESES[ref.group(1)]


def extrair_tabelas(page: pymupdf.Page,pagina: int) -> tuple[list[dict], list[dict]]:
    """Retorna (linhas de volume, linhas de participação) da página."""
    palavras = _palavras(page)
    titulo_part = next(
        (p.y0 for p in palavras if p.texto == "Participação"), None)
    volumes, participacao = [], []

    for linha in _linhas(palavras):
        rotulo = " ".join(p.texto for p in linha if p.x1 < X_ROTULO)
        codigo = SEGMENTOS_TABELA.get(normalizar(rotulo))
        if not codigo:
            continue
        numeros = [p.texto for p in linha if p.x0 >= X_ROTULO]
        na_participacao = (titulo_part is not None
                           and linha[0].y0 > titulo_part)

        if na_participacao and len(numeros) == 2:
            participacao.append({
                "pagina": pagina,
                "segmento": codigo,
                "part_acum_atual": para_float(numeros[0]),
                "part_acum_ano_ant": para_float(numeros[1]),
            })
        elif not na_participacao and len(numeros) == 8:
            v = [para_int(n) for n in numeros[:5]]
            var = [para_float(n) for n in numeros[5:]]
            volumes.append({
                "pagina": pagina,
                "segmento": codigo,
                "mes_atual": v[0],
                "mes_anterior": v[1],
                "acum_atual": v[2],
                "mes_ano_ant": v[3],
                "acum_ano_ant": v[4],
                "var_mes_anterior": var[0],
                "var_mes_ano_ant": var[1],
                "var_acum": var[2],
            })
    return volumes, participacao


def barras_regiao(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    """Retângulos das barras regionais da página, com a região de cada um."""
    barras = []
    for d in page.get_drawings():
        cor = d.get("fill")
        if not cor or len(d["items"]) != 1 or d["items"][0][0] != "re":
            continue
        regiao = CORES_REGIAO.get(tuple(round(c, 2) for c in cor))
        # Os marcadores da legenda têm a mesma cor, mas são estreitos.
        if not regiao or d["rect"].width < 15:
            continue
        barras.append((d["rect"], regiao))
    return barras


def titulos_graficos(page: pymupdf.Page) -> list[tuple[float, str]]:
    titulos = []
    for bloco in page.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            texto = "".join(s["text"] for s in linha["spans"]).strip()
            codigo = SEGMENTOS_GRAFICO.get(normalizar(texto))
            if codigo:
                titulos.append((linha["bbox"][3], codigo))
    return sorted(titulos)


def _associar_rotulos(valores: list[float], ordem: list[str],
                      geo: dict[str, float]) -> dict[str, float] | None:
    """Associa rótulos a segmentos, ambos ordenados de cima para baixo.

    Rótulos de segmentos finos costumam ficar fora do retângulo, então a
    posição não basta: entre as combinações que mantêm a ordem vertical,
    fica a mais próxima das alturas das barras.
    """
    if len(valores) > len(ordem):
        return None

    def distancia(regs):
        return sum(abs(v - geo[g]) for v, g in zip(valores, regs))

    melhor = min(combinations(ordem, len(valores)), key=distancia)
    return dict(zip(melhor, valores))


def _extrair_graficos(page: pymupdf.Page, pagina: int, ano: int,
                      avisos: list[str]) -> list[dict]:
    titulos = titulos_graficos(page)
    if not titulos:
        return []

    # Agrupa os retângulos por (gráfico, coluna).
    barras: dict[tuple[str, float], list[tuple[pymupdf.Rect, str]]] = {}
    for r, regiao in barras_regiao(page):
        acima = [c for y, c in titulos if y <= r.y0 + 1]
        if not acima:
            continue
        chave = (acima[-1], round(r.x0))
        barras.setdefault(chave, []).append((r, regiao))

    palavras = _palavras(page)
    rotulos = [p for p in palavras if RE_DECIMAL.match(p.texto)]
    meses = [p for p in palavras if normalizar(p.texto) in MESES_ABREV]

    registros = []
    for (segmento, _), segs in barras.items():
        x0 = min(r.x0 for r, _ in segs)
        x1 = max(r.x1 for r, _ in segs)
        topo = min(r.y0 for r, _ in segs)
        base = max(r.y1 for r, _ in segs)
        altura_total = base - topo

        mes_w = [m for m in meses
                 if x0 <= m.cx <= x1 and base <= m.cy <= base + 25]
        if len(mes_w) != 1:
            avisos.append(f"p{pagina} {segmento}: barra em x={x0:.0f} "
                          "sem mês identificável")
            continue
        mes = MESES_ABREV[normalizar(mes_w[0].texto)]

        alturas = {regiao: r.height for r, regiao in segs}
        geo = {g: 100 * h / altura_total for g, h in alturas.items()}
        na_barra = (p for p in rotulos
                    if x0 <= p.cx <= x1 and topo - 12 <= p.cy <= base + 3)
        valores = [para_float(p.texto)
                   for p in sorted(na_barra, key=lambda p: p.cy)]
        ordem = [g for _, g in sorted(segs, key=lambda s: s[0].y0)]
        lidos = _associar_rotulos(valores, ordem, geo)
        if lidos is None:
            avisos.append(f"p{pagina} {segmento} mês {mes}: "
                          f"{len(valores)} rótulos para {len(segs)} "
                          "segmentos, barra ignorada")
            continue
        for regiao, valor in lidos.items():
            if abs(valor - geo[regiao]) > 1.5:
                avisos.append(f"p{pagina} {segmento} mês {mes} {regiao}: "
                              f"rótulo {valor} difere da altura da barra "
                              f"({geo[regiao]:.2f})")

        # Segmentos sem rótulo recebem o que falta para 100%,
        # repartido pela altura.
        faltando = [g for g in REGIOES if g in alturas and g not in lidos]
        resto = round(100 - sum(lidos.values()), 2)
        soma_alt = sum(alturas[g] for g in faltando)

        for regiao in REGIOES:
            if regiao in lidos:
                valor, origem = lidos[regiao], "ROTULO"
            elif regiao not in alturas:
                valor, origem = 0.0, "SEM_SEGMENTO"
            elif len(faltando) == 1:
                valor, origem = resto, "COMPLEMENTO"
            else:
                valor = round(resto * alturas[regiao] / soma_alt, 2)
                origem = "ESTIMADO_GEOMETRIA"
            altura = alturas.get(regiao, 0)
            registros.append({
                "pagina": pagina,
                "segmento": segmento,
                "regiao": regiao,
                "ano": ano,
                "mes": mes,
                "percentual": valor,
                "origem_valor": origem,
                "percentual_geometria": round(100 * altura / altura_total, 2),
            })
    return registros


def extrair(caminho_pdf: str | Path) -> Informativo:
    caminho = Path(caminho_pdf)
    conteudo = caminho.read_bytes()
    doc = pymupdf.open(stream=conteudo, filetype="pdf")
    edicao, ano, mes = _cabecalho(doc)
    sha256 = hashlib.sha256(conteudo).hexdigest()
    info = Informativo(caminho.name, sha256, edicao, ano, mes)

    volumes, participacao, regional = [], [], []
    for i, page in enumerate(doc, start=1):
        v, p = extrair_tabelas(page, i)
        volumes += v
        participacao += p
        regional += _extrair_graficos(page, i, ano, info.avisos)

    info.tabela_volumes = pd.DataFrame(volumes)
    info.tabela_participacao = pd.DataFrame(participacao)
    info.regional = pd.DataFrame(regional)
    return info
