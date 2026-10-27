"""Banco de dados (SQLAlchemy). Geometrias ficam em WKB, no sistema de coordenadas do lote.

Versionamento: cada feição tem uma linha em `geometrias` por versão em que mudou. A geometria
de uma feição na versão N é a da maior versão <= N (wkb vazio = feição removida).
"""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text,
                        create_engine, event, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DB_URL

engine = create_engine(DB_URL, connect_args={'check_same_thread': False} if DB_URL.startswith('sqlite') else {})
Sessao = sessionmaker(engine, expire_on_commit=False)


if DB_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def _pragmas(con, _):
        cur = con.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA foreign_keys=ON')
        cur.close()


def agora():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = 'usuarios'
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario: Mapped[str] = mapped_column(String(80), unique=True)
    nome: Mapped[str] = mapped_column(String(200), default='')
    email: Mapped[str] = mapped_column(String(200), default='')
    senha_hash: Mapped[str] = mapped_column(String(300), default='')
    perfil: Mapped[str] = mapped_column(String(20), default='analista')     # admin | analista
    provedor: Mapped[str] = mapped_column(String(20), default='local')      # local | ad
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    trocar_senha: Mapped[bool] = mapped_column(Boolean, default=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Regra(Base):
    """Parâmetros de detecção e correção de cada tipo de inconsistência (editados pelo admin)."""
    __tablename__ = 'regras'
    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    atualizado_por: Mapped[str] = mapped_column(String(80), default='')
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Lote(Base):
    """Um conjunto de shapefiles importados juntos (e os que forem mesclados depois)."""
    __tablename__ = 'lotes'
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    crs: Mapped[str] = mapped_column(Text, default='')            # WKT do sistema do lote
    crs_nome: Mapped[str] = mapped_column(String(200), default='')
    crs_metrico: Mapped[str] = mapped_column(String(40), default='')  # EPSG usado para medir em metros
    campos: Mapped[dict] = mapped_column(JSON, default=dict)      # mapeamento fazenda/aglomerado/talhão
    verificacoes: Mapped[list] = mapped_column(JSON, default=list)  # regras escolhidas para o lote ([] = todas as ativas)
    colunas: Mapped[list] = mapped_column(JSON, default=list)     # colunas da tabela, na ordem original
    versao_atual: Mapped[int] = mapped_column(Integer, default=1)
    criado_por: Mapped[str] = mapped_column(String(80), default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Camada(Base):
    __tablename__ = 'camadas'
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey('lotes.id', ondelete='CASCADE'), index=True)
    nome: Mapped[str] = mapped_column(String(300))
    arquivo: Mapped[str] = mapped_column(String(300), default='')
    n_feicoes: Mapped[int] = mapped_column(Integer, default=0)
    crs_original: Mapped[str] = mapped_column(String(200), default='')
    geometria: Mapped[str] = mapped_column(String(20), default='poligono')  # poligono | linha
    colunas: Mapped[list] = mapped_column(JSON, default=list)
    avisos: Mapped[list] = mapped_column(JSON, default=list)               # avisos da validação de entrada
    criado_por: Mapped[str] = mapped_column(String(80), default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Feicao(Base):
    __tablename__ = 'feicoes'
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey('lotes.id', ondelete='CASCADE'), index=True)
    camada_id: Mapped[int] = mapped_column(ForeignKey('camadas.id', ondelete='CASCADE'))
    fazenda: Mapped[str] = mapped_column(String(120), default='', index=True)
    fazenda_nome: Mapped[str] = mapped_column(String(300), default='')
    aglomerado: Mapped[str] = mapped_column(String(120), default='')
    talhao: Mapped[str] = mapped_column(String(120), default='')
    linha: Mapped[bool] = mapped_column(Boolean, default=False)          # feição de estrada/linha
    indice_origem: Mapped[int] = mapped_column(Integer, default=0)       # posição (FID) na camada de origem
    atributos: Mapped[dict] = mapped_column(JSON, default=dict)


class Geometria(Base):
    __tablename__ = 'geometrias'
    id: Mapped[int] = mapped_column(primary_key=True)
    feicao_id: Mapped[int] = mapped_column(ForeignKey('feicoes.id', ondelete='CASCADE'), index=True)
    versao: Mapped[int] = mapped_column(Integer, default=1)
    wkb: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class Versao(Base):
    __tablename__ = 'versoes'
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey('lotes.id', ondelete='CASCADE'), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    descricao: Mapped[str] = mapped_column(Text, default='')
    fazendas: Mapped[list] = mapped_column(JSON, default=list)
    n_alteradas: Mapped[int] = mapped_column(Integer, default=0)
    criado_por: Mapped[str] = mapped_column(String(80), default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Inconsistencia(Base):
    __tablename__ = 'inconsistencias'
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey('lotes.id', ondelete='CASCADE'), index=True)
    versao: Mapped[int] = mapped_column(Integer)                  # versão do lote em que foi detectada
    fazenda: Mapped[str] = mapped_column(String(120), default='', index=True)
    tipo: Mapped[str] = mapped_column(String(40), index=True)
    severidade: Mapped[str] = mapped_column(String(10), default='media')
    feicoes: Mapped[list] = mapped_column(JSON, default=list)     # ids das feições envolvidas
    descricao: Mapped[str] = mapped_column(Text, default='')
    area_m2: Mapped[float] = mapped_column(default=0.0)
    destaque: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # WKB do trecho com erro
    op: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # correção proposta (None = só manual)
    assinatura: Mapped[str] = mapped_column(String(200), default='')
    classificacao: Mapped[str] = mapped_column(String(15), default='confirmado')  # confirmado | suspeito | inconclusivo
    motivo: Mapped[str] = mapped_column(Text, default='')          # por que a regra apontou
    limitacoes: Mapped[str] = mapped_column(Text, default='')      # riscos da correção proposta
    caracteristicas: Mapped[dict] = mapped_column(JSON, default=dict)
    execucao_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # auditoria que detectou
    # ver estados.py: identificada, em_revisao, aprovada, rejeitada, pendente, corrigida, falha, nao_corrigivel
    status: Mapped[str] = mapped_column(String(20), default='identificada', index=True)
    decidido_por: Mapped[str] = mapped_column(String(80), default='')
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validacao: Mapped[list] = mapped_column(JSON, default=list)    # verificações feitas após a correção
    correcao_execucao_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Execucao(Base):
    """Uma rodada de auditoria ou de correção, com identificador próprio e o resumo do que aconteceu."""
    __tablename__ = 'execucoes'
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(Integer, index=True)
    tipo: Mapped[str] = mapped_column(String(20))                  # auditoria | correcao
    status: Mapped[str] = mapped_column(String(20), default='executando')  # executando | concluida | erro
    usuario: Mapped[str] = mapped_column(String(80), default='')
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    fim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versao_entrada: Mapped[int] = mapped_column(Integer, default=1)
    versao_saida: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entrada: Mapped[str] = mapped_column(Text, default='')         # arquivos / camadas analisadas
    saida: Mapped[str] = mapped_column(Text, default='')           # versão e arquivo gerados
    regras: Mapped[dict] = mapped_column(JSON, default=dict)       # regras executadas, com os parâmetros
    fazendas: Mapped[list] = mapped_column(JSON, default=list)
    resumo: Mapped[dict] = mapped_column(JSON, default=dict)
    erro: Mapped[str] = mapped_column(Text, default='')


class Historico(Base):
    """Cada mudança de estado de uma ocorrência (sem chave estrangeira: sobrevive à reauditoria)."""
    __tablename__ = 'historico_ocorrencias'
    id: Mapped[int] = mapped_column(primary_key=True)
    inconsistencia_id: Mapped[int] = mapped_column(Integer, index=True)
    lote_id: Mapped[int] = mapped_column(Integer, index=True)
    execucao_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estado: Mapped[str] = mapped_column(String(20))
    usuario: Mapped[str] = mapped_column(String(80), default='')
    data_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    nota: Mapped[str] = mapped_column(Text, default='')


class Log(Base):
    """Log de auditoria. Imutável: o banco recusa UPDATE e DELETE, e cada linha leva o hash da
    anterior, então qualquer alteração por fora quebra a corrente (ver auditoria_log.verificar)."""
    __tablename__ = 'log_auditoria'
    id: Mapped[int] = mapped_column(primary_key=True)
    data_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora, index=True)
    usuario: Mapped[str] = mapped_column(String(80))
    acao: Mapped[str] = mapped_column(String(40), index=True)
    lote_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    versao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fazenda: Mapped[str] = mapped_column(String(300), default='')
    talhao: Mapped[str] = mapped_column(String(300), default='')
    descricao: Mapped[str] = mapped_column(Text, default='')
    geom_antes: Mapped[str | None] = mapped_column(Text, nullable=True)   # WKT
    geom_depois: Mapped[str | None] = mapped_column(Text, nullable=True)  # WKT
    hash_anterior: Mapped[str] = mapped_column(String(64), default='')
    hash: Mapped[str] = mapped_column(String(64), default='')


def criar_tabelas():
    Base.metadata.create_all(engine)
    if DB_URL.startswith('sqlite'):
        with engine.begin() as con:
            for op in ('UPDATE', 'DELETE'):
                con.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS log_sem_{op.lower()} BEFORE {op} ON log_auditoria "
                    f"BEGIN SELECT RAISE(ABORT, 'O log de auditoria é imutável'); END"))
