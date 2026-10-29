"""Linha de comando.

    python -m fenabrave baixar   [--anos ... | --desde ...] [--forcar]
    python -m fenabrave recortar [--forcar]
    python -m fenabrave extrair  [pdf|pasta] [--csv saida/]
    python -m fenabrave carregar [pdf|pasta] [--db ARQUIVO] [--forcar]
    python -m fenabrave pipeline [--anos ... | --desde ...] [--db ARQUIVO]
                                 [--permitir-erros]

`pipeline` encadeia baixar -> recortar -> carregar e para na primeira
etapa que falhar.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Permite rodar o arquivo direto pela IDE, sem `python -m`.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "fenabrave"

from . import baixar as _baixar  # noqa: E402
from . import recortar as _recortar  # noqa: E402
from .db import (  # noqa: E402
    carregar, criar_schema, engine_de, linhas_emplacamento,
)
from .extract import extrair  # noqa: E402
from .validate import validar  # noqa: E402

PASTA_INPUT_PADRAO = _recortar.PASTA_SAIDA_PADRAO
DB_PADRAO = "emplacamentos.db"


def _pdfs(alvo: str | None) -> list[Path]:
    p = Path(alvo) if alvo else PASTA_INPUT_PADRAO
    if not p.exists():
        sys.exit(f"Caminho não encontrado: {p}")
    arquivos = sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    if not arquivos:
        sys.exit(f"Nenhum PDF encontrado em {p}")
    return arquivos


def _salvar_csv(info, destino: Path) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    prefixo = destino / f"ed{info.edicao}"
    linhas_emplacamento(info).to_csv(f"{prefixo}_emplacamento.csv",
                                     index=False)
    info.regional.to_csv(f"{prefixo}_participacao_regional.csv",
                         index=False)
    info.tabela_volumes.to_csv(f"{prefixo}_tabela_pdf.csv", index=False)
    print(f"   CSVs salvos em {destino}")


def _extrair_e_carregar(pdfs: list[Path], gravar: bool, engine=None,
                        csv: str | None = None,
                        permitir_erros: bool = False) -> int:
    """Extrai, valida e, se `gravar`, carrega cada PDF.

    Retorna o número de falhas.
    """
    falhas = 0
    for pdf in pdfs:
        print(f"\n== {pdf.name}")
        try:
            info = extrair(pdf)
        except Exception as e:
            print(f"   ERRO: falha inesperada ao processar o arquivo: {e}")
            falhas += 1
            continue
        print(f"   Ed. {info.edicao}, "
              f"referência {info.mes_ref:02d}/{info.ano_ref}")
        for aviso in info.avisos:
            print(f"   AVISO: {aviso}")
        estimados = info.regional[info.regional.origem_valor != "ROTULO"]
        if not estimados.empty:
            print(f"   {len(estimados)} percentuais regionais sem rótulo "
                  "(calculados/estimados)")

        erros = validar(info)
        for e in erros:
            print(f"   ERRO: {e}")
        resumo = f"{len(erros)} erro(s)" if erros else "OK"
        print(f"   Validação: {resumo}")

        if not gravar:
            if csv and not info.tabela_volumes.empty:
                _salvar_csv(info, Path(csv))
            if erros:
                falhas += 1
            continue

        if erros and not permitir_erros:
            print("   Não gravado (use --forcar/--permitir-erros para "
                  "gravar mesmo assim)")
            falhas += 1
            continue
        print(f"   {carregar(engine, info)}")
    return falhas


def _argumento_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db", default=DB_PADRAO, metavar="ARQUIVO",
        help=f"arquivo SQLite (padrão: {DB_PADRAO})")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fenabrave", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)

    sub.add_parser("baixar", help="baixa os PDFs completos da Fenabrave "
                                  "(ver python -m fenabrave.baixar -h)")
    sub.add_parser("recortar", help="recorta as páginas de interesse "
                                    "(ver python -m fenabrave.recortar -h)")

    ajuda_alvo = f"PDF ou pasta (padrão: {PASTA_INPUT_PADRAO})"

    cmd_extrair = sub.add_parser("extrair", help="extrai e valida, sem gravar")
    cmd_extrair.add_argument("alvo", nargs="?", help=ajuda_alvo)
    cmd_extrair.add_argument("--csv",
                             help="pasta para salvar os CSVs extraídos")

    cmd_carregar = sub.add_parser("carregar",
                                  help="extrai, valida e grava no SQLite")
    cmd_carregar.add_argument("alvo", nargs="?", help=ajuda_alvo)
    _argumento_db(cmd_carregar)
    cmd_carregar.add_argument("--forcar", action="store_true",
                              help="grava mesmo com erros de validação")

    cmd_pipeline = sub.add_parser(
        "pipeline", help="encadeia baixar -> recortar -> carregar")
    cmd_pipeline.add_argument("--anos", type=int, nargs="+",
                              help="anos específicos a baixar")
    cmd_pipeline.add_argument("--desde", type=int,
                              help="baixa de um ano até o corrente")
    cmd_pipeline.add_argument("--forcar-download", action="store_true",
                              help="rebaixa mesmo se já existir")
    cmd_pipeline.add_argument("--forcar-recorte", action="store_true",
                              help="refaz o recorte mesmo se já existir")
    cmd_pipeline.add_argument("--permitir-erros", action="store_true",
                              help="grava mesmo com erros de validação")
    _argumento_db(cmd_pipeline)
    return ap


def _pipeline(args: argparse.Namespace) -> int:
    anos = _baixar.anos_alvo(args.anos, args.desde)

    print("== 1/3 baixar ==")
    if _baixar.baixar_anos(anos, _baixar.PASTA_DESTINO_PADRAO,
                           args.forcar_download):
        print("\nPipeline interrompido: falha ao baixar um ou mais PDFs.")
        return 1

    print("\n== 2/3 recortar ==")
    if _recortar.recortar_pasta(_recortar.PASTA_ENTRADA_PADRAO,
                                _recortar.PASTA_SAIDA_PADRAO,
                                args.forcar_recorte):
        print("\nPipeline interrompido: falha ao recortar um ou mais PDFs.")
        return 1

    print("\n== 3/3 carregar ==")
    engine = engine_de(args.db)
    criar_schema(engine)
    falhas = _extrair_e_carregar(_pdfs(None), gravar=True, engine=engine,
                                 permitir_erros=args.permitir_erros)
    return 1 if falhas else 0


def main(argv: list[str] | None = None) -> int:
    # `baixar` e `recortar` repassam os argumentos para a CLI própria.
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "baixar":
        return _baixar.main(argv[1:])
    if argv and argv[0] == "recortar":
        return _recortar.main(argv[1:])

    args = _parser().parse_args(argv)

    if args.comando == "pipeline":
        return _pipeline(args)

    engine = None
    if args.comando == "carregar":
        engine = engine_de(args.db)
        criar_schema(engine)

    falhas = _extrair_e_carregar(
        _pdfs(args.alvo),
        gravar=(args.comando == "carregar"),
        engine=engine,
        csv=getattr(args, "csv", None),
        permitir_erros=getattr(args, "forcar", False),
    )
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
