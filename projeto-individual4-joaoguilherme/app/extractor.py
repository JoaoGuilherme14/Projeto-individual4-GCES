"""
extractor.py — Integração com Google Gemini para extração estruturada.

Responsabilidades:
- Configuração do cliente Gemini
- System prompt blindado contra alucinações
- Extração de métricas com saída estruturada (JSON → Pydantic)
- Retry com backoff em caso de falhas
"""

import json
import logging
import os
import time
from typing import Optional

import google.generativeai as genai
from dotenv import load_dotenv

from app.models import MetricasExtracao, MetricasTrimestrais

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração do Gemini
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

MODEL_NAME = "gemini-2.0-flash"

# ---------------------------------------------------------------------------
# System Prompt Blindado (Prompt Engineering)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Você é um extrator especializado de dados operacionais do setor imobiliário/habitacional brasileiro.
Sua ÚNICA função é extrair métricas numéricas de relatórios trimestrais de construtoras (prévias operacionais, boletins de conjuntura, resultados trimestrais).

## REGRAS OBRIGATÓRIAS — SIGA RIGOROSAMENTE:

### 1. VALORES ABSOLUTOS APENAS
- Extraia SOMENTE valores absolutos (em Reais R$ ou unidades).
- IGNORE completamente:
  - Porcentagens de crescimento/variação (ex: "+25% em relação ao ano anterior")
  - Variações percentuais em títulos de marketing (ex: "Aumento de 45% nas Vendas")
  - Comparativos percentuais entre trimestres
- Se um campo mostra apenas uma porcentagem de variação SEM o valor absoluto, retorne null para esse campo.

### 2. CONVERSÃO DE ESCALAS NUMÉRICAS
- Se o documento indica "em milhões de R$" ou "R$ milhões" e mostra 150.4, retorne 150400000.0
- Se indica "em milhares" e mostra 25.3, retorne 25300.0
- Se indica "em bilhões" e mostra 1.5, retorne 1500000000.0
- Se indica "em unidades" e mostra 3.500, retorne 3500.0
- SEMPRE converta para o valor real absoluto, sem escalas.

### 3. CAMPOS AUSENTES = null
- Se um indicador NÃO é mencionado no documento, retorne null para o campo.
- NUNCA invente, estime ou duplique dados de outro trimestre/empresa.
- É MELHOR retornar null do que inventar um número.

### 4. IDENTIFICAÇÃO DE EMPRESA E PERÍODO
- Identifique o nome da construtora mencionada no documento.
- Padronize nomes: "MRV Engenharia" → "MRV", "Direcional Engenharia" → "Direcional", "Construtora Tenda" → "Tenda".
- Identifique o ano (4 dígitos) e o trimestre (1, 2, 3 ou 4).
- Se houver dados de múltiplas empresas ou trimestres, retorne cada um como item separado na lista.

### 5. DEFINIÇÃO DOS CAMPOS
- **vendas_brutas_valor**: Vendas Brutas totais (VGV ou unidades vendidas), em R$. Inclui distratos.
- **vendas_liquidas_valor**: Vendas Líquidas (Vendas Brutas - Distratos), em R$.
- **lancamentos_valor**: Valor total de lançamentos (VGV de novos empreendimentos), em R$.
- **vso**: Venda Sobre Oferta, como decimal (15% = 0.15). Este campo É uma porcentagem — é o único que aceita valor percentual.

### 6. FORMATO DE SAÍDA
Retorne EXCLUSIVAMENTE um JSON válido no seguinte formato, sem texto adicional:

{
  "metricas": [
    {
      "empresa": "NomeDaEmpresa",
      "ano": 2025,
      "trimestre": 3,
      "vendas_brutas_valor": 1500000000.0,
      "vendas_liquidas_valor": 1200000000.0,
      "lancamentos_valor": 800000000.0,
      "vso": 0.15
    }
  ]
}
"""


def _construir_prompt_extracao(texto_chunk: str, paginas: list[int], estrategia: str) -> str:
    """Constrói o prompt de extração para um chunk específico."""
    return f"""Analise o seguinte trecho de um relatório/prévia operacional do setor habitacional brasileiro.

Informações sobre o trecho:
- Páginas incluídas: {paginas}
- Estratégia de segmentação utilizada: {estrategia}

TEXTO DO DOCUMENTO:
---
{texto_chunk}
---

Com base EXCLUSIVAMENTE no texto acima, extraia as métricas operacionais trimestrais.
Siga TODAS as regras do sistema (valores absolutos, conversão de escalas, null para ausências).
Retorne o JSON estruturado conforme o formato especificado."""


def extrair_metricas(
    chunks: list[dict],
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> list[MetricasTrimestrais]:
    """
    Extrai métricas de uma lista de chunks usando o Google Gemini.

    Args:
        chunks: Lista de dicts com 'texto', 'paginas' e 'estrategia'.
        max_retries: Número máximo de tentativas por chunk.
        retry_delay: Delay entre tentativas (com backoff exponencial).

    Returns:
        Lista de MetricasTrimestrais validadas pelo Pydantic.
    """
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY não configurada. "
            "Defina a variável de ambiente ou crie um arquivo .env"
        )

    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            temperature=0.1,  # Baixa temperatura para maior precisão
            response_mime_type="application/json",
        ),
    )

    todas_metricas: list[MetricasTrimestrais] = []

    for i, chunk in enumerate(chunks):
        logger.info(
            f"Processando chunk {i + 1}/{len(chunks)} "
            f"(páginas: {chunk['paginas']}, estratégia: {chunk['estrategia']})"
        )

        prompt = _construir_prompt_extracao(
            chunk["texto"], chunk["paginas"], chunk["estrategia"]
        )

        metricas_chunk = _chamar_gemini_com_retry(
            model, prompt, max_retries, retry_delay
        )

        if metricas_chunk:
            todas_metricas.extend(metricas_chunk)
            logger.info(
                f"Chunk {i + 1}: {len(metricas_chunk)} métrica(s) extraída(s)"
            )
        else:
            logger.warning(f"Chunk {i + 1}: nenhuma métrica extraída")

    # Deduplicar métricas (mesmo empresa+ano+trimestre)
    metricas_unicas = _deduplicar_metricas(todas_metricas)

    logger.info(
        f"Total de métricas extraídas: {len(todas_metricas)} "
        f"→ {len(metricas_unicas)} após deduplicação"
    )

    return metricas_unicas


def _chamar_gemini_com_retry(
    model,
    prompt: str,
    max_retries: int,
    retry_delay: float,
) -> Optional[list[MetricasTrimestrais]]:
    """Chama o Gemini com retry e backoff exponencial."""
    for tentativa in range(1, max_retries + 1):
        try:
            response = model.generate_content(prompt)

            if not response.text:
                logger.warning(f"Tentativa {tentativa}: resposta vazia do Gemini")
                continue

            # Parse do JSON e validação Pydantic
            json_response = json.loads(response.text)
            extracao = MetricasExtracao(**json_response)

            return extracao.metricas

        except json.JSONDecodeError as e:
            logger.warning(
                f"Tentativa {tentativa}: JSON inválido do Gemini — {e}"
            )
        except Exception as e:
            logger.warning(
                f"Tentativa {tentativa}: erro na chamada Gemini — {e}"
            )

        if tentativa < max_retries:
            delay = retry_delay * (2 ** (tentativa - 1))
            logger.info(f"Aguardando {delay:.1f}s antes da próxima tentativa...")
            time.sleep(delay)

    logger.error("Todas as tentativas de extração falharam")
    return None


def _deduplicar_metricas(
    metricas: list[MetricasTrimestrais],
) -> list[MetricasTrimestrais]:
    """
    Remove métricas duplicadas (mesma empresa + ano + trimestre),
    mantendo a que tem mais campos preenchidos.
    """
    mapa: dict[tuple, MetricasTrimestrais] = {}

    for m in metricas:
        chave = (m.empresa.upper().strip(), m.ano, m.trimestre)

        if chave not in mapa:
            mapa[chave] = m
        else:
            # Mantém a versão com mais campos preenchidos
            existente = mapa[chave]
            campos_existente = sum(
                1
                for f in ["vendas_brutas_valor", "vendas_liquidas_valor", "lancamentos_valor", "vso"]
                if getattr(existente, f) is not None
            )
            campos_novo = sum(
                1
                for f in ["vendas_brutas_valor", "vendas_liquidas_valor", "lancamentos_valor", "vso"]
                if getattr(m, f) is not None
            )

            if campos_novo > campos_existente:
                mapa[chave] = m

    return list(mapa.values())
