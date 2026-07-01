"""
database.py — Conexão com banco de dados, tabelas ORM e funções de persistência.

Responsabilidades:
- Definição das tabelas: metricas_trimestrais e linhagem_dados
- Engine e Session factory (SQLite)
- Inserção atômica com linhagem (transação única)
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pipeline_uda.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# Tabela de Linhagem de Dados (Data Lineage / Catálogo)
# ---------------------------------------------------------------------------
class LinhagemDadosDB(Base):
    """Registra a origem de cada PDF processado para rastreabilidade completa."""

    __tablename__ = "linhagem_dados"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hash_pdf = Column(String, unique=True, nullable=False, index=True)
    url_origem = Column(String, nullable=True)
    nome_arquivo = Column(String, nullable=False)
    data_processamento = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    tamanho_bytes = Column(Integer, nullable=True)

    # Relacionamento 1:N com métricas
    metricas = relationship("MetricasTrimestraisDB", back_populates="linhagem")

    def __repr__(self):
        return f"<Linhagem(hash={self.hash_pdf[:12]}..., arquivo={self.nome_arquivo})>"


# ---------------------------------------------------------------------------
# Tabela de Métricas Trimestrais (Dados Operacionais)
# ---------------------------------------------------------------------------
class MetricasTrimestraisDB(Base):
    """Métricas operacionais extraídas dos relatórios de RI das construtoras."""

    __tablename__ = "metricas_trimestrais"

    id = Column(Integer, primary_key=True, autoincrement=True)
    empresa = Column(String, nullable=False, index=True)
    ano = Column(Integer, nullable=False)
    trimestre = Column(Integer, nullable=False)
    vendas_brutas_valor = Column(Float, nullable=True)
    vendas_liquidas_valor = Column(Float, nullable=True)
    lancamentos_valor = Column(Float, nullable=True)
    vso = Column(Float, nullable=True)

    # FK para linhagem — cada métrica sabe de qual PDF foi extraída
    linhagem_id = Column(Integer, ForeignKey("linhagem_dados.id"), nullable=False)
    linhagem = relationship("LinhagemDadosDB", back_populates="metricas")

    __table_args__ = (
        UniqueConstraint("empresa", "ano", "trimestre", "linhagem_id", name="uq_metrica_por_fonte"),
    )

    def __repr__(self):
        return (
            f"<Metrica(empresa={self.empresa}, "
            f"{self.ano}T{self.trimestre}, "
            f"vendas_brutas={self.vendas_brutas_valor})>"
        )


# ---------------------------------------------------------------------------
# Inicialização e Funções Utilitárias
# ---------------------------------------------------------------------------
def init_db():
    """Cria todas as tabelas no banco se não existirem."""
    Base.metadata.create_all(bind=engine)


def get_session():
    """Retorna uma nova sessão do banco de dados."""
    return SessionLocal()


def hash_existe(hash_pdf: str) -> bool:
    """Verifica se um hash de PDF já existe no catálogo (idempotência)."""
    session = SessionLocal()
    try:
        existe = session.query(LinhagemDadosDB).filter_by(hash_pdf=hash_pdf).first() is not None
        return existe
    finally:
        session.close()


def salvar_resultado(metricas_list: list, linhagem_dict: dict) -> int:
    """
    Persiste métricas e linhagem em uma transação atômica.

    Args:
        metricas_list: Lista de dicts com os campos de MetricasTrimestraisDB.
        linhagem_dict: Dict com os campos de LinhagemDadosDB.

    Returns:
        ID da linhagem inserida.

    Raises:
        Exception: Faz rollback em caso de qualquer erro.
    """
    session = SessionLocal()
    try:
        # 1. Inserir registro de linhagem
        linhagem = LinhagemDadosDB(**linhagem_dict)
        session.add(linhagem)
        session.flush()  # Gera o ID sem comitar

        # 2. Inserir métricas associadas à linhagem
        for metrica_data in metricas_list:
            metrica = MetricasTrimestraisDB(
                **metrica_data,
                linhagem_id=linhagem.id,
            )
            session.add(metrica)

        # 3. Commit atômico — tudo ou nada
        session.commit()
        return linhagem.id

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
