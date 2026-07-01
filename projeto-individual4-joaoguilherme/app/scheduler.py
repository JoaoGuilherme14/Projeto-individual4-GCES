"""
scheduler.py — Agendador de polling para monitoramento de novos PDFs.

Responsabilidades:
- APScheduler com IntervalTrigger para polling periódico
- Escaneamento da pasta de downloads em busca de novos PDFs
- Orquestração do pipeline: hash → idempotência → parsing → LLM → persistência
- Logging detalhado de cada decisão
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from app.database import hash_existe, init_db, salvar_resultado
from app.extractor import extrair_metricas
from app.parser import calcular_hash_md5, obter_tamanho_arquivo, processar_pdf

load_dotenv()

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "app/downloads")


def processar_arquivo(filepath: str, url_origem: str = None) -> dict:
    """
    Pipeline completo para um único arquivo PDF.

    Fluxo:
    1. Calcula hash MD5
    2. Verifica idempotência no banco
    3. Se novo: parsing + chunking + LLM + persistência
    4. Se duplicado: log e retorno

    Args:
        filepath: Caminho do arquivo PDF.
        url_origem: URL de origem do PDF (opcional).

    Returns:
        Dict com status, mensagem e detalhes do processamento.
    """
    nome_arquivo = Path(filepath).name
    logger.info(f"{'='*60}")
    logger.info(f"Processando arquivo: {nome_arquivo}")
    logger.info(f"{'='*60}")

    # 1. Calcular hash
    hash_pdf = calcular_hash_md5(filepath)

    # 2. Verificar idempotência
    if hash_existe(hash_pdf):
        msg = (
            f"DUPLICIDADE DETECTADA: '{nome_arquivo}' (hash: {hash_pdf[:16]}...) "
            f"já foi processado anteriormente. Ingestão cancelada."
        )
        logger.info(f"⏭️  {msg}")
        return {
            "status": "duplicado",
            "mensagem": msg,
            "hash_pdf": hash_pdf,
            "metricas_extraidas": 0,
            "linhagem_id": None,
        }

    # 3. Parsing e chunking
    logger.info(f"📄 Novo arquivo detectado. Iniciando parsing...")
    dados_pdf = processar_pdf(filepath)

    if not dados_pdf:
        msg = f"Falha no parsing do PDF '{nome_arquivo}'"
        logger.error(msg)
        return {
            "status": "erro",
            "mensagem": msg,
            "hash_pdf": hash_pdf,
            "metricas_extraidas": 0,
            "linhagem_id": None,
        }

    # 4. Extração via LLM
    logger.info(f"🤖 Enviando {len(dados_pdf['chunks'])} chunk(s) para o LLM...")
    metricas = extrair_metricas(dados_pdf["chunks"])

    if not metricas:
        msg = f"Nenhuma métrica extraída de '{nome_arquivo}'"
        logger.warning(msg)
        return {
            "status": "sem_metricas",
            "mensagem": msg,
            "hash_pdf": hash_pdf,
            "metricas_extraidas": 0,
            "linhagem_id": None,
        }

    # 5. Persistência atômica
    logger.info(f"💾 Salvando {len(metricas)} métrica(s) com linhagem...")
    linhagem_dict = {
        "hash_pdf": hash_pdf,
        "url_origem": url_origem,
        "nome_arquivo": nome_arquivo,
        "tamanho_bytes": obter_tamanho_arquivo(filepath),
    }

    metricas_dicts = [m.model_dump() for m in metricas]
    linhagem_id = salvar_resultado(metricas_dicts, linhagem_dict)

    msg = (
        f"Processamento concluído com sucesso: '{nome_arquivo}' → "
        f"{len(metricas)} métrica(s) salva(s), linhagem_id={linhagem_id}"
    )
    logger.info(f"✅ {msg}")

    return {
        "status": "sucesso",
        "mensagem": msg,
        "hash_pdf": hash_pdf,
        "metricas_extraidas": len(metricas),
        "linhagem_id": linhagem_id,
    }


def escanear_pasta_downloads():
    """
    Escaneia a pasta de downloads e processa todos os PDFs encontrados.
    Esta função é chamada pelo APScheduler em intervalos configuráveis.
    """
    pasta = Path(DOWNLOADS_DIR)

    if not pasta.exists():
        logger.warning(f"Pasta de downloads não encontrada: {pasta}")
        pasta.mkdir(parents=True, exist_ok=True)
        logger.info(f"Pasta criada: {pasta}")
        return

    pdfs = list(pasta.glob("*.pdf"))

    if not pdfs:
        logger.info(f"Nenhum PDF encontrado em {pasta}")
        return

    logger.info(f"Encontrados {len(pdfs)} PDF(s) em {pasta}")

    resultados = {"sucesso": 0, "duplicado": 0, "erro": 0, "sem_metricas": 0}

    for pdf_path in pdfs:
        resultado = processar_arquivo(str(pdf_path))
        status = resultado["status"]
        resultados[status] = resultados.get(status, 0) + 1

    logger.info(
        f"Resumo do escaneamento: "
        f"{resultados['sucesso']} novo(s), "
        f"{resultados['duplicado']} duplicado(s), "
        f"{resultados['erro']} erro(s), "
        f"{resultados['sem_metricas']} sem métricas"
    )


def iniciar_scheduler():
    """
    Inicia o APScheduler com polling periódico da pasta de downloads.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    intervalo = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "86400"))

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        escanear_pasta_downloads,
        "interval",
        seconds=intervalo,
        id="polling_downloads",
        name="Polling de novos PDFs",
        replace_existing=True,
    )

    # Executa imediatamente na primeira vez
    scheduler.add_job(
        escanear_pasta_downloads,
        "date",  # Executa uma vez, agora
        id="polling_inicial",
        name="Escaneamento inicial de PDFs",
    )

    scheduler.start()
    logger.info(
        f"Scheduler iniciado — polling a cada {intervalo}s "
        f"na pasta '{DOWNLOADS_DIR}'"
    )

    return scheduler
