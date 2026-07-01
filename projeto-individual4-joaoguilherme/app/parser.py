"""
parser.py — Ingestão de PDF, cálculo de hash e segmentação semântica.

Responsabilidades:
- Cálculo de hash MD5/SHA-256 para idempotência
- Extração de texto de PDFs via pdfplumber
- Chunking semântico por seções relevantes (Resultados, Vendas, Lançamentos)
- Decisão automática: Full-Scan vs. Chunking baseada no tamanho do documento
"""

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

import pdfplumber

logger = logging.getLogger(__name__)

# Seções de interesse para filtragem semântica
SECOES_RELEVANTES = [
    r"resultado.*operac",
    r"pr[eé]via.*operac",
    r"vendas",
    r"lan[cç]amento",
    r"vso",
    r"venda.*oferta",
    r"indicador",
    r"desempenho.*operac",
    r"dado.*operac",
    r"unidade.*lan[cç]",
    r"unidade.*vend",
    r"vgv",
    r"receita",
    r"comercializa",
]

# Limiar de páginas para decidir entre Full-Scan e Chunking
LIMIAR_FULL_SCAN = 5


def calcular_hash_md5(filepath: str) -> str:
    """
    Calcula o hash MD5 do conteúdo de um arquivo.

    Args:
        filepath: Caminho absoluto para o arquivo PDF.

    Returns:
        String hexadecimal do hash MD5.
    """
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    hash_resultado = hasher.hexdigest()
    logger.info(f"Hash MD5 calculado para '{Path(filepath).name}': {hash_resultado}")
    return hash_resultado


def calcular_hash_sha256(filepath: str) -> str:
    """
    Calcula o hash SHA-256 do conteúdo de um arquivo.

    Args:
        filepath: Caminho absoluto para o arquivo PDF.

    Returns:
        String hexadecimal do hash SHA-256.
    """
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def extrair_texto_pdf(filepath: str) -> dict:
    """
    Extrai texto de todas as páginas de um PDF usando pdfplumber.

    Args:
        filepath: Caminho absoluto para o arquivo PDF.

    Returns:
        Dict com:
            - 'paginas': lista de strings (texto de cada página)
            - 'num_paginas': número total de páginas
            - 'texto_completo': texto concatenado de todas as páginas
    """
    paginas = []
    with pdfplumber.open(filepath) as pdf:
        num_paginas = len(pdf.pages)
        logger.info(f"PDF '{Path(filepath).name}' possui {num_paginas} página(s)")

        for i, page in enumerate(pdf.pages):
            texto = page.extract_text() or ""
            # Também tenta extrair tabelas para PDFs com dados tabulares
            tabelas = page.extract_tables()
            texto_tabelas = ""
            if tabelas:
                for tabela in tabelas:
                    linhas_tabela = []
                    for linha in tabela:
                        # Limpa células None e junta com separador
                        celulas = [str(c).strip() if c else "" for c in linha]
                        linhas_tabela.append(" | ".join(celulas))
                    texto_tabelas += "\n" + "\n".join(linhas_tabela)

            texto_pagina = texto
            if texto_tabelas:
                texto_pagina += "\n\n[TABELA EXTRAÍDA]\n" + texto_tabelas

            paginas.append(texto_pagina)

    return {
        "paginas": paginas,
        "num_paginas": num_paginas,
        "texto_completo": "\n\n--- PÁGINA ---\n\n".join(paginas),
    }


def _secao_relevante(texto: str) -> bool:
    """Verifica se um bloco de texto contém seções de interesse."""
    texto_lower = texto.lower()
    return any(re.search(padrao, texto_lower) for padrao in SECOES_RELEVANTES)


def segmentar_chunks(dados_pdf: dict, max_chars: int = 6000) -> list[dict]:
    """
    Implementa chunking semântico do PDF.

    Estratégia:
    - Se o documento tem <= LIMIAR_FULL_SCAN páginas: usa Full-Scan
    - Caso contrário: filtra apenas páginas com seções relevantes

    Args:
        dados_pdf: Resultado de extrair_texto_pdf().
        max_chars: Tamanho máximo por chunk (para controle de tokens).

    Returns:
        Lista de dicts com:
            - 'texto': conteúdo do chunk
            - 'paginas': lista de números de página incluídos
            - 'estrategia': 'full-scan' ou 'chunking-semantico'
    """
    num_paginas = dados_pdf["num_paginas"]
    paginas = dados_pdf["paginas"]

    # Decisão: Full-Scan ou Chunking Semântico
    if num_paginas <= LIMIAR_FULL_SCAN:
        estrategia = "full-scan"
        logger.info(
            f"Documento curto ({num_paginas} pág.) — usando estratégia Full-Scan"
        )
        # Retorna todo o texto como um único chunk (ou divide se muito grande)
        texto_completo = dados_pdf["texto_completo"]
        if len(texto_completo) <= max_chars:
            return [
                {
                    "texto": texto_completo,
                    "paginas": list(range(1, num_paginas + 1)),
                    "estrategia": estrategia,
                }
            ]
        else:
            # Divide em chunks respeitando o limite
            return _dividir_em_chunks(texto_completo, max_chars, estrategia)

    # Chunking Semântico: filtra páginas relevantes
    estrategia = "chunking-semantico"
    logger.info(
        f"Documento longo ({num_paginas} pág.) — usando Chunking Semântico"
    )

    chunks = []
    chunk_atual = ""
    paginas_chunk = []

    for i, texto_pagina in enumerate(paginas):
        if not _secao_relevante(texto_pagina):
            logger.debug(f"Página {i + 1} ignorada — sem seções relevantes")
            continue

        logger.debug(f"Página {i + 1} incluída — contém seção relevante")

        if len(chunk_atual) + len(texto_pagina) > max_chars:
            # Salva chunk atual e começa novo
            if chunk_atual:
                chunks.append(
                    {
                        "texto": chunk_atual,
                        "paginas": paginas_chunk,
                        "estrategia": estrategia,
                    }
                )
            chunk_atual = texto_pagina
            paginas_chunk = [i + 1]
        else:
            chunk_atual += "\n\n--- PÁGINA ---\n\n" + texto_pagina
            paginas_chunk.append(i + 1)

    # Último chunk
    if chunk_atual:
        chunks.append(
            {
                "texto": chunk_atual,
                "paginas": paginas_chunk,
                "estrategia": estrategia,
            }
        )

    # Fallback: se nenhuma página foi considerada relevante, usa full-scan
    if not chunks:
        logger.warning(
            "Nenhuma seção relevante encontrada — fallback para Full-Scan"
        )
        return [
            {
                "texto": dados_pdf["texto_completo"],
                "paginas": list(range(1, num_paginas + 1)),
                "estrategia": "full-scan-fallback",
            }
        ]

    logger.info(f"Gerados {len(chunks)} chunk(s) semântico(s)")
    return chunks


def _dividir_em_chunks(
    texto: str, max_chars: int, estrategia: str
) -> list[dict]:
    """Divide texto longo em chunks menores respeitando quebras de página."""
    partes = texto.split("\n\n--- PÁGINA ---\n\n")
    chunks = []
    chunk_atual = ""
    paginas_chunk = []

    for i, parte in enumerate(partes):
        if len(chunk_atual) + len(parte) > max_chars and chunk_atual:
            chunks.append(
                {
                    "texto": chunk_atual,
                    "paginas": paginas_chunk,
                    "estrategia": estrategia,
                }
            )
            chunk_atual = parte
            paginas_chunk = [i + 1]
        else:
            chunk_atual += ("\n\n--- PÁGINA ---\n\n" + parte) if chunk_atual else parte
            paginas_chunk.append(i + 1)

    if chunk_atual:
        chunks.append(
            {
                "texto": chunk_atual,
                "paginas": paginas_chunk,
                "estrategia": estrategia,
            }
        )

    return chunks


def obter_tamanho_arquivo(filepath: str) -> int:
    """Retorna o tamanho do arquivo em bytes."""
    return os.path.getsize(filepath)


def processar_pdf(filepath: str) -> Optional[dict]:
    """
    Pipeline completo de processamento de um PDF:
    1. Calcula hash MD5
    2. Extrai texto com pdfplumber
    3. Segmenta em chunks semânticos

    Args:
        filepath: Caminho absoluto para o arquivo PDF.

    Returns:
        Dict com hash, chunks e metadados, ou None se falhar.
    """
    try:
        filepath = str(Path(filepath).resolve())
        nome_arquivo = Path(filepath).name

        logger.info(f"=== Iniciando processamento de: {nome_arquivo} ===")

        # 1. Hash MD5
        hash_pdf = calcular_hash_md5(filepath)

        # 2. Extração de texto
        dados_pdf = extrair_texto_pdf(filepath)

        # 3. Chunking semântico
        chunks = segmentar_chunks(dados_pdf)

        resultado = {
            "hash_pdf": hash_pdf,
            "nome_arquivo": nome_arquivo,
            "filepath": filepath,
            "tamanho_bytes": obter_tamanho_arquivo(filepath),
            "num_paginas": dados_pdf["num_paginas"],
            "chunks": chunks,
            "texto_completo": dados_pdf["texto_completo"],
        }

        logger.info(
            f"Processamento concluído: {nome_arquivo} | "
            f"{dados_pdf['num_paginas']} pág. | "
            f"{len(chunks)} chunk(s) | "
            f"Estratégia: {chunks[0]['estrategia']}"
        )

        return resultado

    except Exception as e:
        logger.error(f"Erro ao processar PDF '{filepath}': {e}", exc_info=True)
        return None
