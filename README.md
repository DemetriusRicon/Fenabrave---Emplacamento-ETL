# Extrator — Informativo de Emplacamentos Fenabrave

Extrai tabelas e gráficos do PDF mensal da Fenabrave, valida e grava num banco SQLite local (`emplacamentos.db`).

## Uso

O pipeline tem três etapas independentes:

```bash
pip install -r requirements.txt

python -m fenabrave baixar      # 1. baixa os PDFs completos     -> entrada_dataset/
python -m fenabrave recortar    # 2. recorta as páginas de interesse -> entrada_dataset/recortados/
python -m fenabrave carregar    # 3. extrai, valida e grava no SQLite
```

`baixar` e `recortar` aceitam as mesmas opções que suas CLIs próprias
(`python -m fenabrave.baixar -h` / `python -m fenabrave.recortar -h`), por exemplo:

```bash
python -m fenabrave baixar --anos 2024 2025
python -m fenabrave recortar --forcar
```

Para rodar as três etapas encadeadas numa só chamada (útil para testar do zero ou como script
avulso), use `pipeline` — ele para na primeira etapa que falhar:

```bash
python -m fenabrave pipeline                          # ano corrente e anterior, idempotente
python -m fenabrave pipeline --anos 2024 2025 --permitir-erros
python -m fenabrave pipeline --desde 2020 --db dados/emplacamentos.db
```

Opções do `pipeline`:

| Opção | Efeito |
|---|---|
| `--anos A B ...` / `--desde A` | anos a baixar (padrão: ano corrente e anterior) |
| `--forcar-download` | rebaixa PDFs que já existem |
| `--forcar-recorte` | refaz recortes que já existem |
| `--permitir-erros` | grava mesmo com erros de validação |
| `--db ARQUIVO` | arquivo SQLite (padrão: `emplacamentos.db`) |

Sem argumentos, `extrair`/`carregar` processam tudo que está em `entrada_dataset/recortados`. Também
aceitam um PDF ou pasta específica, e `--csv` para salvar os dados extraídos:

```bash
python -m fenabrave extrair "Informativo - Emplacamentos AGOSTO 2026_.pdf" --csv saida
python -m fenabrave carregar pasta_dos_pdfs --db dados/emplacamentos.db
```

## Download automático dos PDFs

`fenabrave/baixar.py` é a primeira etapa do pipeline e também pode ser usada sozinha: consulta a API
pública da Fenabrave (`POST /portalv2/api/Emplacamentos`) e baixa os informes mensais para
`entrada_dataset`, prontos para `recortar`. Não sobrescreve arquivos já baixados (idempotente), a menos
que `--forcar` seja usado (no `pipeline`, `--forcar-download`).

```bash
python -m fenabrave.baixar                    # baixa os informes do ano corrente e anterior
python -m fenabrave.baixar --anos 2024 2025    # anos específicos
python -m fenabrave.baixar --desde 2020        # de um ano até o corrente
python -m fenabrave.baixar --forcar            # rebaixa mesmo se já existir
```

## Recorte das páginas de interesse

O informativo completo tem 52 páginas, mas só 7 interessam ao pipeline. `fenabrave/recortar.py` faz
essa seleção **por conteúdo**, não por número de página, e grava um PDF reduzido em
`entrada_dataset/recortados/`:

1. página com barras coloridas de região → gráficos regionais;
2. título `Resumo Mensal <mês> de <ano>` → tabela mestra;
3. ≥ 3 linhas de volume **e** de participação → tabela detalhada do segmento.

```bash
python -m fenabrave.recortar             # entrada_dataset -> entrada_dataset/recortados
python -m fenabrave.recortar --forcar    # refaz mesmo se o recorte já existir
```

A cada arquivo imprime as páginas mantidas com o título e os segmentos detectados, e confere a seleção
antes de gravar: é **erro** (não grava) não achar exatamente uma página de resumo, faltar algum dos 7
gráficos regionais, ou descartar uma página com tabela relevante. Divergências menores viram **aviso**.

Duas páginas do PDF completo são descartadas de propósito e aparecem como aviso em todo mês:
`Emplacamento Motocicletas` (p43), cuja participação acumulada está **errada na origem** — repete os
números de Caminhões —, e `Emplacamento Impl. Rodod` (p51), que só repete uma linha da página 1.

## Banco de dados

Os dados são gravados num arquivo SQLite — por padrão `emplacamentos.db` na pasta atual, ou o caminho
passado em `--db`. Não é preciso instalar nem configurar servidor de banco.

Se a validação falhar, o informativo **não é gravado** (use `--forcar` em `carregar`, ou `--permitir-erros` em `pipeline`, para gravar mesmo assim) e o comando sai com código 1.
Recarregar o mesmo arquivo não faz nada. Um PDF diferente para a mesma edição substitui a carga anterior.

## Como funciona

| Conteúdo do PDF | Técnica |
|---|---|
| Tabelas (volumes, variações, participação) | `page.get_text("words")`: palavras com coordenadas, agrupadas por linha |
| Gráficos de barras por região | `page.get_drawings()`: retângulos cuja cor identifica a região. Os rótulos são associados aos segmentos mantendo a ordem vertical e escolhendo a combinação mais próxima das alturas das barras |
| Cabeçalhos, linha "Total" e setas | São imagens no PDF, então não são lidos. O Total não é gravado: deve ser somado na consulta |

Quando um segmento não tem rótulo, o valor é `100 − soma dos demais` (`origem_valor = COMPLEMENTO`) ou é repartido pela altura das barras (`ESTIMADO_GEOMETRIA`).

### Validações (`fenabrave/validate.py`)
- Os subtotais impressos (A+B, C+D, Subtotal) batem com a soma dos segmentos.
- As variações impressas batem com as recalculadas (tolerância de 0,01 pp).
- A participação no acumulado bate com acumulado do segmento ÷ total.
- As páginas que repetem a tabela trazem os mesmos números.
- Cada barra regional soma 100%, e todos os meses de 1 até o mês de referência estão presentes.

## Modelo de dados

- `informativo`: uma linha por edição, com o hash SHA-256 do arquivo evitando duplicidade .
- `dim_segmento`, `dim_regiao`.
- `fato_emplacamento`: segmento × ano × mês × `MENSAL|ACUMULADO` → quantidade. Só os 7 segmentos base.
- `fato_participacao_regional`: segmento × região × ano × mês → percentual e `origem_valor`.
- Não há views: Total e variações são calculados nas consultas. Cada edição republica meses anteriores, então, para ter o valor mais recente, use a linha da maior `informativo.edicao` por segmento/ano/mês.

## Limitações conhecidas
- O layout é detectado pelos títulos ("Resumo Mensal", "AUTOMÓVEIS" etc.) e pelas cores das regiões. Se a Fenabrave mudar as cores ou os nomes, é necessario ajuste no `CORES_REGIAO`, `SEGMENTOS_TABELA` e `SEGMENTOS_GRAFICO` em `extract.py`.

DISCLAIMER : Ela ja alterou ao longo dos anos e esse pipeline não vai funcionar para todos os anos. 
- A posição das colunas da tabela é fixa (5 volumes + 3 variações), porque os cabeçalhos são imagem.

