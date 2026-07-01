"""
models.py — Modelos Pydantic (Contrato Semântico).

Define os schemas de validação que blindam o banco de dados contra
alucinações do LLM e garantem tipagem segura na API.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Contrato Semântico — Schema de extração do LLM
# ---------------------------------------------------------------------------
class MetricasTrimestrais(BaseModel):
    """
    Contrato semântico para extração de métricas operacionais.

    Este modelo é enviado ao LLM como schema de saída estruturada.
    Todos os campos opcionais DEVEM ser None quando a informação
    não estiver presente no documento — nunca inventar dados.
    """

    empresa: str = Field(
        description=(
            "Nome padronizado da construtora "
            "(ex: MRV, Direcional, Tenda, Cury, Plano&Plano)"
        )
    )
    ano: int = Field(description="Ano fiscal com 4 dígitos (ex: 2025)")
    trimestre: int = Field(
        description="Número do trimestre correspondente (1, 2, 3 ou 4)"
    )
    vendas_brutas_valor: Optional[float] = Field(
        None,
        description=(
            "Valor absoluto de Vendas Brutas em Reais (R$). "
            "Converter para valor bruto real (ex: se o documento diz "
            "'R$ 1,5 bilhão', retornar 1500000000.0). "
            "Se o documento diz 'em milhões de R$' e mostra 150.4, "
            "retornar 150400000.0. "
            "Tratar ausências como None. "
            "IGNORAR porcentagens de crescimento/variação."
        ),
    )
    vendas_liquidas_valor: Optional[float] = Field(
        None,
        description=(
            "Valor absoluto de Vendas Líquidas em Reais (R$). "
            "Mesmas regras de conversão de escala. "
            "Tratar ausências como None. "
            "IGNORAR porcentagens de crescimento/variação."
        ),
    )
    lancamentos_valor: Optional[float] = Field(
        None,
        description=(
            "Valor absoluto de Lançamentos (VGV ou Unidades) em Reais (R$). "
            "Converter escalas para valor real absoluto. "
            "Tratar ausências como None."
        ),
    )
    vso: Optional[float] = Field(
        None,
        description=(
            "Venda sobre Oferta (VSO) expressa como float decimal "
            "(ex: 0.15 para 15%, 0.32 para 32%). "
            "Tratar ausências como None."
        ),
    )


class MetricasExtracao(BaseModel):
    """Container para múltiplas métricas extraídas de um único documento."""

    metricas: List[MetricasTrimestrais] = Field(
        description=(
            "Lista de métricas trimestrais extraídas do documento. "
            "Um documento pode conter dados de múltiplas empresas e/ou trimestres."
        )
    )


# ---------------------------------------------------------------------------
# Schemas de Resposta da API
# ---------------------------------------------------------------------------
class LinhagemResponse(BaseModel):
    """Schema de resposta para dados de linhagem na API."""

    id: int
    hash_pdf: str
    url_origem: Optional[str] = None
    nome_arquivo: str
    data_processamento: datetime
    tamanho_bytes: Optional[int] = None

    class Config:
        from_attributes = True


class MetricaResponse(BaseModel):
    """Schema de resposta para uma métrica individual na API."""

    id: int
    empresa: str
    ano: int
    trimestre: int
    vendas_brutas_valor: Optional[float] = None
    vendas_liquidas_valor: Optional[float] = None
    lancamentos_valor: Optional[float] = None
    vso: Optional[float] = None

    class Config:
        from_attributes = True


class ConjunturaResponse(BaseModel):
    """Schema combinado: métrica + linhagem do arquivo fonte (auditoria)."""

    metrica: MetricaResponse
    linhagem: LinhagemResponse


class CatalogoResponse(BaseModel):
    """Schema de resposta para o catálogo de PDFs processados."""

    total_arquivos: int
    arquivos: List[LinhagemResponse]


class ProcessamentoResponse(BaseModel):
    """Schema de resposta para o endpoint de processamento manual."""

    status: str
    mensagem: str
    hash_pdf: str
    metricas_extraidas: int
    linhagem_id: Optional[int] = None
