#!/usr/bin/env python3
"""
test_pipeline.py — Testes completos do Pipeline UDA.

Executa os 3 cenários de validação exigidos pelo SKILL.md:
1. Teste de Idempotência
2. Teste de Variação de Layout (requer 2º PDF)
3. Teste de Valores Absolutos vs. Percentuais

Uso:
    # Com API key configurada:
    python test_pipeline.py

    # Apenas teste de parsing (sem LLM):
    python test_pipeline.py --parsing-only
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import (
    LinhagemDadosDB,
    MetricasTrimestraisDB,
    SessionLocal,
    init_db,
    hash_existe,
    engine,
    Base,
)
from app.models import MetricasTrimestrais
from app.parser import calcular_hash_md5, processar_pdf
from app.scheduler import processar_arquivo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PDF_EXEMPLO = "app/downloads/exemplo_Boletim_Conjuntura_2025_3T.pdf"
PDF_LAYOUT_B = "app/downloads/previa_operacional_layout_b.pdf"


def limpar_banco():
    """Remove e recria o banco para testes isolados."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    logger.info("Banco de dados limpo e recriado para testes")


def teste_parsing():
    """Teste básico de parsing sem LLM."""
    print("\n" + "=" * 70)
    print("TESTE DE PARSING (sem LLM)")
    print("=" * 70)

    if not os.path.exists(PDF_EXEMPLO):
        print(f"[ERRO] PDF não encontrado: {PDF_EXEMPLO}")
        return False

    resultado = processar_pdf(PDF_EXEMPLO)
    if not resultado:
        print("[ERRO] Falha no parsing")
        return False

    print(f"\n[OK] Arquivo: {resultado['nome_arquivo']}")
    print(f"[OK] Hash MD5: {resultado['hash_pdf']}")
    print(f"[OK] Páginas: {resultado['num_paginas']}")
    print(f"[OK] Chunks: {len(resultado['chunks'])}")
    print(f"[OK] Estratégia: {resultado['chunks'][0]['estrategia']}")
    print(f"[OK] Tamanho: {resultado['tamanho_bytes']} bytes")

    # Verificar que o texto contém dados relevantes
    texto = resultado["texto_completo"].lower()
    empresas = ["mrv", "cury", "tenda", "direcional", "plano", "pacaembu"]
    empresas_encontradas = [e for e in empresas if e in texto]
    print(f"[OK] Empresas detectadas no texto: {', '.join(empresas_encontradas)}")

    keywords = ["lançamento", "vendas", "vso", "trimestre"]
    keywords_encontradas = [k for k in keywords if k in texto]
    print(f"[OK] Keywords encontradas: {', '.join(keywords_encontradas)}")

    return True


def teste_idempotencia():
    """
    SKILL.md Teste 1: Idempotência
    - Processa PDF 1x → registro criado
    - Processa mesmo PDF 2x → duplicidade detectada, sem registro extra
    """
    print("\n" + "=" * 70)
    print("TESTE 1: IDEMPOTÊNCIA (SKILL.md §1)")
    print("=" * 70)

    limpar_banco()

    if not os.path.exists(PDF_EXEMPLO):
        print(f"[ERRO] PDF não encontrado: {PDF_EXEMPLO}")
        return False

    # 1ª execução — deve criar registro
    print("\n--- 1ª execução: deve processar normalmente ---")
    resultado1 = processar_arquivo(PDF_EXEMPLO)
    print(f"Status: {resultado1['status']}")
    print(f"Mensagem: {resultado1['mensagem']}")
    print(f"Métricas extraídas: {resultado1['metricas_extraidas']}")

    if resultado1["status"] != "sucesso":
        print(f"[ERRO] 1ª execução falhou: {resultado1['status']}")
        return False

    # Verificar banco
    session = SessionLocal()
    count_metricas_1 = session.query(MetricasTrimestraisDB).count()
    count_linhagem_1 = session.query(LinhagemDadosDB).count()
    session.close()
    print(f"[OK] Registros no banco: {count_metricas_1} métricas, {count_linhagem_1} linhagem")

    # 2ª execução — deve detectar duplicidade
    print("\n--- 2ª execução: deve detectar duplicidade ---")
    resultado2 = processar_arquivo(PDF_EXEMPLO)
    print(f"Status: {resultado2['status']}")
    print(f"Mensagem: {resultado2['mensagem']}")

    if resultado2["status"] != "duplicado":
        print(f"[ERRO] 2ª execução deveria retornar 'duplicado', got: {resultado2['status']}")
        return False

    # Verificar que NÃO criou registro extra
    session = SessionLocal()
    count_metricas_2 = session.query(MetricasTrimestraisDB).count()
    count_linhagem_2 = session.query(LinhagemDadosDB).count()
    session.close()

    if count_metricas_2 != count_metricas_1 or count_linhagem_2 != count_linhagem_1:
        print(f"[ERRO] Registros duplicados! Antes: {count_metricas_1}/{count_linhagem_1}, Depois: {count_metricas_2}/{count_linhagem_2}")
        return False

    print(f"[OK] Nenhum registro duplicado criado!")
    print(f"[OK] Registros mantidos: {count_metricas_2} métricas, {count_linhagem_2} linhagem")
    print("\n✅ TESTE DE IDEMPOTÊNCIA PASSOU!")
    return True


def teste_variacao_layout():
    """
    SKILL.md Teste 2: Variação de Layout (Resiliência)
    - Layout A: Boletim de exemplo
    - Layout B: Prévia operacional com layout diferente
    """
    print("\n" + "=" * 70)
    print("TESTE 2: VARIAÇÃO DE LAYOUT (SKILL.md §2)")
    print("=" * 70)

    limpar_banco()

    # Layout A
    print("\n--- Layout A: Boletim de Conjuntura ---")
    if not os.path.exists(PDF_EXEMPLO):
        print(f"[ERRO] PDF Layout A não encontrado: {PDF_EXEMPLO}")
        return False

    resultado_a = processar_arquivo(PDF_EXEMPLO)
    print(f"Status: {resultado_a['status']}")
    if resultado_a["status"] != "sucesso":
        print(f"[ERRO] Layout A falhou")
        return False
    print(f"[OK] Layout A processado: {resultado_a['metricas_extraidas']} métricas")

    # Layout B
    print("\n--- Layout B: Prévia Operacional ---")
    if not os.path.exists(PDF_LAYOUT_B):
        print(f"[AVISO] PDF Layout B não encontrado: {PDF_LAYOUT_B}")
        print(f"        Baixe uma prévia operacional e coloque em: {PDF_LAYOUT_B}")
        print(f"        Consulte o README para instruções de download.")
        return None  # Não falha, apenas avisa

    resultado_b = processar_arquivo(PDF_LAYOUT_B)
    print(f"Status: {resultado_b['status']}")
    if resultado_b["status"] != "sucesso":
        print(f"[ERRO] Layout B falhou")
        return False
    print(f"[OK] Layout B processado: {resultado_b['metricas_extraidas']} métricas")

    # Verificar banco
    session = SessionLocal()
    total = session.query(MetricasTrimestraisDB).count()
    session.close()
    print(f"\n[OK] Total de métricas no banco: {total}")
    print("\n✅ TESTE DE VARIAÇÃO DE LAYOUT PASSOU!")
    return True


def teste_valores_absolutos():
    """
    SKILL.md Teste 3: Valores Absolutos vs. Percentuais
    Verifica que o banco contém valores absolutos, não porcentagens.
    """
    print("\n" + "=" * 70)
    print("TESTE 3: VALORES ABSOLUTOS VS. PERCENTUAIS (SKILL.md §3)")
    print("=" * 70)

    session = SessionLocal()
    metricas = session.query(MetricasTrimestraisDB).all()
    session.close()

    if not metricas:
        print("[AVISO] Nenhuma métrica no banco. Execute o teste de idempotência primeiro.")
        return None

    problemas = []
    for m in metricas:
        # VSO é o único campo que pode ser uma porcentagem (como decimal)
        if m.vso is not None and m.vso > 1.0:
            problemas.append(f"{m.empresa} {m.ano}T{m.trimestre}: VSO={m.vso} (deveria ser decimal, ex: 0.15)")

        # Vendas e lançamentos não devem ser porcentagens
        for campo, valor in [
            ("vendas_brutas_valor", m.vendas_brutas_valor),
            ("vendas_liquidas_valor", m.vendas_liquidas_valor),
            ("lancamentos_valor", m.lancamentos_valor),
        ]:
            if valor is not None and 0 < valor < 100:
                problemas.append(
                    f"{m.empresa} {m.ano}T{m.trimestre}: {campo}={valor} "
                    f"(parece ser % — deveria ser valor absoluto em R$)"
                )

        print(f"[OK] {m.empresa} {m.ano}T{m.trimestre}: "
              f"vendas_brutas={m.vendas_brutas_valor}, "
              f"vendas_liquidas={m.vendas_liquidas_valor}, "
              f"lancamentos={m.lancamentos_valor}, "
              f"vso={m.vso}")

    if problemas:
        print("\n[AVISO] Possíveis problemas detectados:")
        for p in problemas:
            print(f"  ⚠️  {p}")
        return False

    print("\n✅ TESTE DE VALORES ABSOLUTOS PASSOU!")
    return True


def main():
    parser = argparse.ArgumentParser(description="Testes do Pipeline UDA")
    parser.add_argument(
        "--parsing-only",
        action="store_true",
        help="Executa apenas teste de parsing (sem LLM)",
    )
    args = parser.parse_args()

    init_db()

    if args.parsing_only:
        ok = teste_parsing()
        sys.exit(0 if ok else 1)

    # Execução completa
    resultados = {}

    resultados["parsing"] = teste_parsing()
    resultados["idempotencia"] = teste_idempotencia()
    resultados["variacao_layout"] = teste_variacao_layout()
    resultados["valores_absolutos"] = teste_valores_absolutos()

    # Resumo
    print("\n" + "=" * 70)
    print("RESUMO DOS TESTES")
    print("=" * 70)
    for teste, resultado in resultados.items():
        if resultado is True:
            status = "✅ PASSOU"
        elif resultado is False:
            status = "❌ FALHOU"
        else:
            status = "⚠️  PENDENTE (recurso não disponível)"
        print(f"  {teste}: {status}")

    if all(v is True for v in resultados.values() if v is not None):
        print("\n🎉 TODOS OS TESTES PASSARAM!")
    else:
        print("\n⚠️  Alguns testes falharam ou estão pendentes.")


if __name__ == "__main__":
    main()
