"""Baixa os PDFs mensais de emplacamentos do site da Fenabrave.

    python -m fenabrave.baixar                  # ano corrente e anterior
    python -m fenabrave.baixar --anos 2024 2025
    python -m fenabrave.baixar --desde 2020
    python -m fenabrave.baixar --forcar         # rebaixa os existentes
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_EMPLACAMENTOS = "https://www.fenabrave.org.br/portalv2/api/Emplacamentos"
BASE_PDF = "https://www.fenabrave.org.br/portal/files/"
USER_AGENT = "Mozilla/5.0 (compatible; fenabrave-pipeline/1.0)"

PASTA_DESTINO_PADRAO = Path(__file__).resolve().parent.parent / "entrada_dataset"

MESES = {
    1: "JANEIRO",
    2: "FEVEREIRO",
    3: "MARÇO",
    4: "ABRIL",
    5: "MAIO",
    6: "JUNHO",
    7: "JULHO",
    8: "AGOSTO",
    9: "SETEMBRO",
    10: "OUTUBRO",
    11: "NOVEMBRO",
    12: "DEZEMBRO",
}


def _sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(total=3, backoff_factor=1,
                  status_forcelist=[429, 500, 502, 503, 504])
    sessao.mount("https://", HTTPAdapter(max_retries=retry))
    return sessao


def listar_ano(sessao: requests.Session, ano: int) -> list[dict]:
    """Consulta a API e retorna os informes publicados no ano."""
    resp = sessao.post(API_EMPLACAMENTOS, data={"ano": ano}, timeout=30)
    resp.raise_for_status()
    blocos = resp.json().get("data", {}).get("blocos") or []
    informes = []
    for bloco in blocos:
        link = bloco.get("link")
        data_ref = bloco.get("data")
        if not link or not data_ref:
            continue
        mes = int(data_ref[5:7])
        informes.append({"ano": ano, "mes": mes, "arquivo_remoto": link})
    return informes


def nome_local(informe: dict) -> str:
    mes_nome = MESES[informe["mes"]]
    return f"Informativo - Emplacamentos {mes_nome} {informe['ano']}_.pdf"


def baixar_informe(sessao: requests.Session, informe: dict, destino: Path,
                   forcar: bool) -> str:
    caminho = destino / nome_local(informe)
    if caminho.exists() and not forcar:
        return "já existia"

    url = BASE_PDF + informe["arquivo_remoto"]
    resp = sessao.get(url, timeout=60)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        return f"ERRO: resposta de {url} não é um PDF"

    destino.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(resp.content)
    return "baixado"


def baixar_anos(anos: list[int], destino: Path, forcar: bool = False) -> int:
    sessao = _sessao()
    falhas = 0
    for ano in sorted(set(anos)):
        informes = listar_ano(sessao, ano)
        if not informes:
            print(f"{ano}: nenhum informe encontrado")
            continue
        for informe in sorted(informes, key=lambda i: i["mes"]):
            status = baixar_informe(sessao, informe, destino, forcar)
            print(f"{ano}-{informe['mes']:02d}: {status}")
            if status.startswith("ERRO"):
                falhas += 1
    return falhas


def anos_alvo(anos: list[int] | None, desde: int | None) -> list[int]:
    ano_atual = date.today().year
    if anos:
        return anos
    if desde:
        return list(range(desde, ano_atual + 1))
    return [ano_atual - 1, ano_atual]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fenabrave.baixar", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anos", type=int, nargs="+", help="anos específicos a baixar")
    ap.add_argument("--desde", type=int, help="baixa de um ano até o corrente")
    ap.add_argument("--destino", default=str(PASTA_DESTINO_PADRAO),help="pasta onde salvar os PDFs " f"(padrão: {PASTA_DESTINO_PADRAO})")
    ap.add_argument("--forcar", action="store_true", help="rebaixa mesmo se o arquivo já existir")
    args = ap.parse_args(argv)

    anos = anos_alvo(args.anos, args.desde)
    falhas = baixar_anos(anos, Path(args.destino), args.forcar)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
