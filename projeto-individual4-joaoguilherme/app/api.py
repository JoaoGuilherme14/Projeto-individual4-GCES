"""
api.py — Endpoints FastAPI para a camada de serviço do Pipeline UDA.

Endpoints:
- GET  /api/conjuntura  — Consulta métricas com filtros (empresa, ano, trimestre)
- GET  /api/catalogo    — Lista PDFs processados (linhagem)
- POST /api/processar   — Upload manual de PDF para processamento
"""

import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.database import (
    LinhagemDadosDB,
    MetricasTrimestraisDB,
    get_session,
    init_db,
)
from app.models import (
    CatalogoResponse,
    ConjunturaResponse,
    LinhagemResponse,
    MetricaResponse,
    ProcessamentoResponse,
)
from app.scheduler import iniciar_scheduler, processar_arquivo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ---------------------------------------------------------------------------
# Lifespan — inicializa banco e scheduler ao iniciar a app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o banco de dados e o scheduler ao iniciar a aplicação."""
    logger.info("🚀 Inicializando Pipeline UDA...")
    init_db()
    logger.info("✅ Banco de dados inicializado")

    scheduler = iniciar_scheduler()
    logger.info("✅ Scheduler de polling ativo")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("🛑 Pipeline UDA encerrado")


# ---------------------------------------------------------------------------
# Aplicação FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Pipeline UDA — Setor Habitacional",
    description=(
        "API REST para consulta de métricas operacionais de construtoras "
        "brasileiras, extraídas automaticamente de relatórios trimestrais de RI "
        "via LLM com contrato semântico Pydantic."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# GET /api/conjuntura — Consulta de métricas com filtros
# ---------------------------------------------------------------------------
@app.get(
    "/api/conjuntura",
    response_model=list[ConjunturaResponse],
    summary="Consultar métricas operacionais",
    description=(
        "Retorna métricas trimestrais extraídas dos relatórios de RI, "
        "com a linhagem do arquivo fonte para auditoria. "
        "Suporta filtros opcionais por empresa, ano e trimestre."
    ),
)
def consultar_conjuntura(
    empresa: Optional[str] = Query(None, description="Filtro por nome da empresa (ex: MRV, Direcional)"),
    ano: Optional[int] = Query(None, description="Filtro por ano fiscal (ex: 2025)"),
    trimestre: Optional[int] = Query(None, ge=1, le=4, description="Filtro por trimestre (1-4)"),
):
    session = get_session()
    try:
        query = session.query(MetricasTrimestraisDB)

        if empresa:
            query = query.filter(
                MetricasTrimestraisDB.empresa.ilike(f"%{empresa}%")
            )
        if ano:
            query = query.filter(MetricasTrimestraisDB.ano == ano)
        if trimestre:
            query = query.filter(MetricasTrimestraisDB.trimestre == trimestre)

        resultados = query.all()

        response = []
        for metrica in resultados:
            # Buscar linhagem associada
            linhagem = (
                session.query(LinhagemDadosDB)
                .filter_by(id=metrica.linhagem_id)
                .first()
            )

            response.append(
                ConjunturaResponse(
                    metrica=MetricaResponse.model_validate(metrica),
                    linhagem=LinhagemResponse.model_validate(linhagem),
                )
            )

        return response

    finally:
        session.close()


# ---------------------------------------------------------------------------
# GET /api/catalogo — Lista de PDFs processados
# ---------------------------------------------------------------------------
@app.get(
    "/api/catalogo",
    response_model=CatalogoResponse,
    summary="Catálogo de PDFs processados",
    description=(
        "Retorna a lista completa de PDFs já processados pelo pipeline, "
        "incluindo hash, nome do arquivo e data de ingestão."
    ),
)
def listar_catalogo():
    session = get_session()
    try:
        registros = session.query(LinhagemDadosDB).order_by(
            LinhagemDadosDB.data_processamento.desc()
        ).all()

        return CatalogoResponse(
            total_arquivos=len(registros),
            arquivos=[LinhagemResponse.model_validate(r) for r in registros],
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# POST /api/processar — Upload manual de PDF
# ---------------------------------------------------------------------------
@app.post(
    "/api/processar",
    response_model=ProcessamentoResponse,
    summary="Processar PDF manualmente",
    description=(
        "Faz upload de um arquivo PDF para processamento imediato pelo pipeline. "
        "O arquivo passa por hash, idempotência, parsing, LLM e persistência."
    ),
)
async def processar_pdf_upload(
    arquivo: UploadFile = File(..., description="Arquivo PDF para processamento"),
    url_origem: Optional[str] = Query(None, description="URL de origem do PDF"),
):
    if not arquivo.filename or not arquivo.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="O arquivo deve ser um PDF (.pdf)",
        )

    # Salvar arquivo temporariamente
    downloads_dir = Path(os.getenv("DOWNLOADS_DIR", "app/downloads"))
    downloads_dir.mkdir(parents=True, exist_ok=True)

    filepath = downloads_dir / arquivo.filename

    try:
        with open(filepath, "wb") as f:
            content = await arquivo.read()
            f.write(content)

        # Processar pelo pipeline completo
        resultado = processar_arquivo(str(filepath), url_origem=url_origem)

        return ProcessamentoResponse(**resultado)

    except Exception as e:
        logger.error(f"Erro no processamento manual: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao processar o arquivo: {str(e)}",
        )


# ---------------------------------------------------------------------------
# GET / — Health check
# ---------------------------------------------------------------------------
@app.get("/", summary="Health Check")
def health_check():
    return {
        "status": "online",
        "servico": "Pipeline UDA — Setor Habitacional",
        "versao": "1.0.0",
        "docs": "/docs",
    }
