"""API do Verificador de Talhões v2 (FastAPI). Também serve o frontend compilado."""
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import auth, config, estados, log_auditoria, regras, relatorios, servico
from .db import Execucao, Inconsistencia, Lote, Log, Regra, Sessao, Usuario, agora, criar_tabelas
from .importacao import ErroArquivo
from .log_auditoria import registrar, trava_escrita

log = logging.getLogger('verificador')


@asynccontextmanager
async def iniciar(_):
    criar_tabelas()
    auth.criar_admin_inicial()
    regras.garantir_padroes()
    yield


app = FastAPI(title='Verificador de Talhões', version='2.0.0', lifespan=iniciar)
# GeoJSON comprime ~5x: o mapa de uma fazenda grande cai de ~900 KB para ~180 KB na rede
app.add_middleware(GZipMiddleware, minimum_size=2000)


@app.exception_handler(ErroArquivo)
def erro_arquivo(_, e: ErroArquivo):
    return JSONResponse({'detail': str(e)}, status_code=400)


def pegar_lote(s, lote_id) -> Lote:
    lote = s.get(Lote, lote_id)
    if not lote:
        raise HTTPException(404, 'Lote não encontrado.')
    return lote


def baixar(nome, dados, tipo):
    return Response(dados, media_type=tipo, headers={'Content-Disposition': f'attachment; filename="{nome}"'})


@app.get('/api/saude', include_in_schema=False)
def saude():
    """Para o healthcheck do Docker: responde quando a API e o banco estão de pé."""
    with Sessao() as s:
        s.execute(select(Usuario.id).limit(1))
    return {'ok': True}


# ---------- autenticação ----------
class Login(BaseModel):
    usuario: str
    senha: str


@app.post('/api/auth/login')
def login(d: Login):
    with trava_escrita, Sessao() as s:
        u = auth.autenticar(s, d.usuario, d.senha)
        if not u:
            raise HTTPException(401, 'Usuário ou senha incorretos.')
        registrar(s, u.usuario, 'login', 'Entrou no sistema.')
        s.commit()
        return {'token': auth.gerar_token(u), 'usuario': auth.usuario_json(u)}


@app.get('/api/auth/eu')
def eu(u: Usuario = Depends(auth.usuario_atual)):
    return auth.usuario_json(u)


class TrocaSenha(BaseModel):
    atual: str
    nova: str = Field(min_length=6)


@app.post('/api/auth/senha')
def trocar_senha(d: TrocaSenha, u: Usuario = Depends(auth.usuario_atual)):
    with trava_escrita, Sessao() as s:
        u = s.get(Usuario, u.id)
        if not auth.conferir_senha(d.atual, u.senha_hash):
            raise HTTPException(400, 'A senha atual não confere.')
        u.senha_hash, u.trocar_senha = auth.hash_senha(d.nova), False
        registrar(s, u.usuario, 'usuario', 'Trocou a própria senha.')
        s.commit()
        return auth.usuario_json(u)


# ---------- admin: usuários ----------
class NovoUsuario(BaseModel):
    usuario: str = Field(min_length=2, max_length=80)
    nome: str = ''
    email: str = ''
    perfil: Literal['admin', 'analista'] = 'analista'
    provedor: Literal['local', 'ad'] = 'local'
    senha: str = ''
    ativo: bool = True


class EdicaoUsuario(BaseModel):
    nome: str | None = None
    email: str | None = None
    perfil: Literal['admin', 'analista'] | None = None
    ativo: bool | None = None
    senha: str | None = None


@app.get('/api/admin/usuarios')
def listar_usuarios(_: Usuario = Depends(auth.exigir_admin)):
    with Sessao() as s:
        return [auth.usuario_json(u) for u in s.scalars(select(Usuario).order_by(Usuario.usuario))]


@app.post('/api/admin/usuarios')
def criar_usuario(d: NovoUsuario, adm: Usuario = Depends(auth.exigir_admin)):
    login_ = d.usuario.strip().lower()
    if d.provedor == 'local' and len(d.senha) < 6:
        raise HTTPException(400, 'A senha precisa ter pelo menos 6 caracteres.')
    with trava_escrita, Sessao() as s:
        if s.scalar(select(Usuario.id).where(Usuario.usuario == login_)):
            raise HTTPException(400, 'Já existe um usuário com esse login.')
        u = Usuario(usuario=login_, nome=d.nome, email=d.email, perfil=d.perfil, provedor=d.provedor, ativo=d.ativo,
                    senha_hash=auth.hash_senha(d.senha) if d.provedor == 'local' else '', trocar_senha=d.provedor == 'local')
        s.add(u)
        registrar(s, adm.usuario, 'usuario', f'Criou o usuário {login_} (perfil {d.perfil}, {d.provedor}).')
        s.commit()
        return auth.usuario_json(u)


@app.patch('/api/admin/usuarios/{uid}')
def editar_usuario(uid: int, d: EdicaoUsuario, adm: Usuario = Depends(auth.exigir_admin)):
    with trava_escrita, Sessao() as s:
        u = s.get(Usuario, uid)
        if not u:
            raise HTTPException(404, 'Usuário não encontrado.')
        mudancas = []
        if u.id == adm.id and (d.ativo is False or (d.perfil and d.perfil != 'admin')):
            raise HTTPException(400, 'Você não pode desativar nem tirar o perfil de admin de si mesmo.')
        for campo in ('nome', 'email', 'perfil', 'ativo'):
            v = getattr(d, campo)
            if v is not None and v != getattr(u, campo):
                mudancas.append(f'{campo}: {getattr(u, campo)} → {v}')
                setattr(u, campo, v)
        if d.senha:
            if len(d.senha) < 6:
                raise HTTPException(400, 'A senha precisa ter pelo menos 6 caracteres.')
            u.senha_hash, u.trocar_senha = auth.hash_senha(d.senha), True
            mudancas.append('senha redefinida')
        if mudancas:
            registrar(s, adm.usuario, 'usuario', f'Editou {u.usuario}: {"; ".join(mudancas)}.')
        s.commit()
        return auth.usuario_json(u)


# ---------- admin: regras ----------
@app.get('/api/admin/regras')
def ver_regras(_: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        return regras.como_json(s)


class EdicaoRegra(BaseModel):
    ativo: bool | None = None
    params: dict | None = None


@app.put('/api/admin/regras/{chave}')
def editar_regra(chave: str, d: EdicaoRegra, adm: Usuario = Depends(auth.exigir_admin)):
    if chave not in regras.PADRAO and chave != 'campos':
        raise HTTPException(404, 'Regra não encontrada.')
    padrao = regras.CAMPOS_PADRAO if chave == 'campos' else regras.PADRAO[chave]['params']
    with trava_escrita, Sessao() as s:
        r = s.get(Regra, chave) or Regra(chave=chave, params={})
        s.add(r)
        mudancas = []
        if d.ativo is not None and d.ativo != r.ativo:
            r.ativo = d.ativo
            mudancas.append('ativada' if d.ativo else 'desativada')
        if d.params is not None:
            novos = dict(r.params or {})
            for k, v in d.params.items():
                if k not in padrao:
                    raise HTTPException(400, f'Parâmetro desconhecido: {k}')
                ref = padrao[k]
                try:
                    v = list(map(str, v)) if isinstance(ref, list) else type(ref)(v) if not isinstance(ref, bool) else bool(v)
                except (TypeError, ValueError):
                    raise HTTPException(400, f'Valor inválido para {k}.')
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
                    raise HTTPException(400, f'{k} não pode ser negativo.')
                if novos.get(k, ref) != v:
                    mudancas.append(f'{k}: {novos.get(k, ref)} → {v}')
                novos[k] = v
            r.params = novos
        r.atualizado_por, r.atualizado_em = adm.usuario, agora()
        if mudancas:
            nome = 'Mapeamento de campos' if chave == 'campos' else regras.PADRAO[chave]['nome']
            registrar(s, adm.usuario, 'regra', f'{nome}: {"; ".join(mudancas)}.')
        s.commit()
        return regras.como_json(s)


@app.post('/api/admin/regras/{chave}/padrao')
def restaurar_regra(chave: str, adm: Usuario = Depends(auth.exigir_admin)):
    if chave not in regras.PADRAO and chave != 'campos':
        raise HTTPException(404, 'Regra não encontrada.')
    with trava_escrita, Sessao() as s:
        r = s.get(Regra, chave)
        if r:
            r.params = {k: list(v) for k, v in regras.CAMPOS_PADRAO.items()} if chave == 'campos' else dict(regras.PADRAO[chave]['params'])
            r.ativo, r.atualizado_por, r.atualizado_em = True, adm.usuario, agora()
            registrar(s, adm.usuario, 'regra', f'{chave}: valores padrão restaurados.')
        s.commit()
        return regras.como_json(s)


# ---------- lotes / etapa 1 ----------
async def ler_uploads(arquivos: list[UploadFile]):
    out, total = [], 0
    for a in arquivos:
        dados = await a.read()
        total += len(dados)
        if total > config.LIMITE_UPLOAD:
            raise ErroArquivo('Os arquivos passam de 500 MB.')
        out.append((a.filename or 'arquivo', dados))
    if not out:
        raise ErroArquivo('Nenhum arquivo enviado.')
    return out


@app.get('/api/lotes')
def listar_lotes(_: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        lotes = list(s.scalars(select(Lote).order_by(Lote.id.desc())))
        abertas = {}
        for lid, st in s.execute(select(Inconsistencia.lote_id, Inconsistencia.status)):
            d = abertas.setdefault(lid, {'abertas': 0, 'resolvidas': 0})
            d['abertas' if st in estados.ABERTOS else 'resolvidas'] += 1
        return [{**servico.lote_json(l), **abertas.get(l.id, {'abertas': 0, 'resolvidas': 0})} for l in lotes]


def _lista_regras(texto):
    """Verificações escolhidas na importação ('' = todas as ativas)."""
    escolhidas = [x.strip() for x in (texto or '').split(',') if x.strip()]
    desconhecidas = [x for x in escolhidas if x not in regras.PADRAO]
    if desconhecidas:
        raise HTTPException(400, f'Verificação desconhecida: {", ".join(desconhecidas)}')
    return escolhidas


@app.post('/api/lotes')
async def criar_lote(arquivos: list[UploadFile] = File(...), nome: str = Form(''), verificacoes: str = Form(''),
                     u: Usuario = Depends(auth.usuario_atual)):
    sel = _lista_regras(verificacoes)
    dados = await ler_uploads(arquivos)
    return await _rodar(lambda: _importar(dados, nome, None, u, sel))


@app.post('/api/lotes/{lote_id}/camadas')
async def mesclar(lote_id: int, arquivos: list[UploadFile] = File(...), u: Usuario = Depends(auth.usuario_atual)):
    dados = await ler_uploads(arquivos)
    return await _rodar(lambda: _importar(dados, '', lote_id, u))


async def _rodar(f):
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(f)


def _importar(dados, nome, lote_id, u, verificacoes=None):
    with trava_escrita, Sessao() as s:
        lote = pegar_lote(s, lote_id) if lote_id else None
        lote = servico.importar(s, u, dados, nome, lote, verificacoes)
        s.commit()
        return servico.resumo_lote(s, lote)


@app.get('/api/lotes/{lote_id}')
def ver_lote(lote_id: int, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        return servico.resumo_lote(s, pegar_lote(s, lote_id))


@app.delete('/api/lotes/{lote_id}')
def apagar_lote(lote_id: int, adm: Usuario = Depends(auth.exigir_admin)):
    with trava_escrita, Sessao() as s:
        lote = pegar_lote(s, lote_id)
        registrar(s, adm.usuario, 'lote', f'Lote "{lote.nome}" apagado (versão {lote.versao_atual}).', lote_id=lote.id)
        s.delete(lote)
        s.commit()
        return {'ok': True}


@app.get('/api/lotes/{lote_id}/mapa')
def mapa(lote_id: int, fazenda: str | None = None, versao: int | None = None, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        return servico.mapa_fazenda(s, pegar_lote(s, lote_id), fazenda, versao)


class Reauditoria(BaseModel):
    verificacoes: list[str] | None = None


@app.get('/api/lotes/{lote_id}/feicoes/{fid}')
def feicao(lote_id: int, fid: int, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        return servico.feicao_detalhe(s, pegar_lote(s, lote_id), fid)


@app.post('/api/lotes/{lote_id}/auditar')
def reauditar(lote_id: int, d: Reauditoria | None = None, u: Usuario = Depends(auth.usuario_atual)):
    sel = _lista_regras(','.join(d.verificacoes)) if d and d.verificacoes is not None else None
    with trava_escrita, Sessao() as s:
        lote = pegar_lote(s, lote_id)
        servico.auditar(s, lote, u, verificacoes=sel, entrada='reauditoria pedida pelo usuário')
        s.commit()
        return servico.resumo_lote(s, lote)


class Campos(BaseModel):
    fazenda: str = ''
    fazenda_nome: str = ''
    fazenda_detalhe: str = ''
    aglomerado: str = ''
    talhao: str = ''
    area_ha: str = ''


@app.put('/api/lotes/{lote_id}/campos')
def trocar_campos(lote_id: int, d: Campos, u: Usuario = Depends(auth.usuario_atual)):
    with trava_escrita, Sessao() as s:
        lote = pegar_lote(s, lote_id)
        servico.remapear_campos(s, lote, d.model_dump(), u)
        s.commit()
        return servico.resumo_lote(s, lote)


# ---------- etapa 2: revisão ----------
@app.get('/api/lotes/{lote_id}/inconsistencias')
def inconsistencias(lote_id: int, fazenda: str | None = None, tipo: str | None = None, status: str | None = 'abertas',
                    talhao: str | None = None, auto: bool | None = None, ids: str | None = None,
                    _: Usuario = Depends(auth.usuario_atual)):
    lista_ids = [int(x) for x in ids.split(',') if x.strip()] if ids else None
    with Sessao() as s:
        return servico.listar_inconsistencias(s, pegar_lote(s, lote_id), fazenda, tipo, status or None, talhao, auto, lista_ids)


@app.get('/api/lotes/{lote_id}/inconsistencias/{inc_id}')
def inconsistencia(lote_id: int, inc_id: int, u: Usuario = Depends(auth.usuario_atual)):
    # abrir a ocorrência a coloca "em revisão": por isso grava
    with trava_escrita, Sessao() as s:
        lote = pegar_lote(s, lote_id)
        inc = s.get(Inconsistencia, inc_id)
        if not inc or inc.lote_id != lote.id:
            raise HTTPException(404, 'Ocorrência não encontrada (o lote pode ter sido auditado de novo).')
        d = servico.detalhe_inconsistencia(s, lote, inc, u)
        s.commit()
        return d


class Decisao(BaseModel):
    ids: list[int]
    decisao: Literal['aprovar', 'rejeitar', 'pendente']


@app.post('/api/lotes/{lote_id}/decisoes')
def decidir(lote_id: int, d: Decisao, u: Usuario = Depends(auth.usuario_atual)):
    with trava_escrita, Sessao() as s:
        lote = pegar_lote(s, lote_id)
        feitas, recusadas = servico.decidir(s, lote, d.ids, d.decisao, u)
        s.commit()
        return {'alteradas': [{'id': i.id, 'status': i.status, 'estado': estados.ESTADOS[i.status], 'decididoPor': i.decidido_por,
                               'decididoEm': i.decidido_em} for i in feitas], 'recusadas': recusadas}


# ---------- etapa 3: correção e exportação ----------
class Selecao(BaseModel):
    ids: list[int]


@app.post('/api/lotes/{lote_id}/correcao/preparar')
def preparar(lote_id: int, d: Selecao, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        return servico.preparar_correcao(s, pegar_lote(s, lote_id), d.ids)


@app.post('/api/lotes/{lote_id}/correcao')
def executar(lote_id: int, d: Selecao, u: Usuario = Depends(auth.usuario_atual)):
    if not d.ids:
        raise HTTPException(400, 'Nada para executar.')
    with Sessao() as s:
        pegar_lote(s, lote_id)
    return {'execucaoId': servico.iniciar_correcao(lote_id, d.ids, u)}


@app.get('/api/execucoes')
def listar_execucoes(lote_id: int | None = None, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        return [servico.execucao_json(e) for e in relatorios.execucoes(s, lote_id)]


@app.get('/api/execucoes/{eid}')
def ver_execucao(eid: int, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        e = s.get(Execucao, eid)
        if not e:
            raise HTTPException(404, 'Execução não encontrada.')
        return servico.execucao_json(e)


@app.get('/api/execucoes/{eid}/relatorio/{formato}')
def relatorio_execucao(eid: int, formato: Literal['csv', 'pdf'], u: Usuario = Depends(auth.usuario_atual)):
    with trava_escrita, Sessao() as s:
        e = s.get(Execucao, eid)
        if not e:
            raise HTTPException(404, 'Execução não encontrada.')
        if e.status == 'executando':
            raise HTTPException(409, 'A execução ainda está rodando.')
        dados = relatorios.gerar_csv(s, e) if formato == 'csv' else relatorios.gerar_pdf(s, e)
        nome = f'relatorio_{e.tipo}_{e.id}.{formato}'
        registrar(s, u.usuario, 'exportacao', f'Baixou o relatório da execução #{e.id} ({formato.upper()}).', lote_id=e.lote_id)
        s.commit()
        return baixar(nome, dados, 'application/pdf' if formato == 'pdf' else 'text/csv; charset=utf-8')


@app.get('/api/lotes/{lote_id}/exportar')
def exportar(lote_id: int, formato: Literal['shp', 'geojson'] = 'shp', versao: int | None = None, fazenda: str | None = None,
             recalcular_area: bool = True, u: Usuario = Depends(auth.usuario_atual)):
    with trava_escrita, Sessao() as s:
        lote = pegar_lote(s, lote_id)
        if versao and not 1 <= versao <= lote.versao_atual:
            raise HTTPException(400, 'Versão inexistente.')
        nome, dados, tipo = servico.exportar(s, lote, versao, formato, fazenda, recalcular_area)
        registrar(s, u.usuario, 'exportacao', f'Exportou {nome} ({formato.upper()}).', lote_id=lote.id,
                  versao=versao or lote.versao_atual, fazenda=fazenda or '')
        s.commit()
        return baixar(nome, dados, tipo)


# ---------- log de auditoria ----------
def _filtro_log(q, lote_id, acao, usuario, fazenda, de, ate):
    if lote_id:
        q = q.where(Log.lote_id == lote_id)
    if acao:
        q = q.where(Log.acao == acao)
    if usuario:
        q = q.where(Log.usuario == usuario)
    if fazenda:
        q = q.where(Log.fazenda.contains(fazenda))
    if de:
        q = q.where(Log.data_hora >= de)
    if ate:
        q = q.where(Log.data_hora <= ate)
    return q


@app.get('/api/auditoria')
def ver_log(lote_id: int | None = None, acao: str | None = None, usuario: str | None = None, fazenda: str | None = None,
            de: datetime | None = None, ate: datetime | None = None, limite: int = 500, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        q = _filtro_log(select(Log), lote_id, acao, usuario, fazenda, de, ate).order_by(Log.id.desc()).limit(min(limite, 5000))
        return {'linhas': [log_auditoria.como_json(l) for l in s.scalars(q)], 'acoes': log_auditoria.ACOES}


@app.get('/api/auditoria/integridade')
def integridade(_: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        ok, onde = log_auditoria.verificar(s)
        total = s.query(Log).count()
        return {'ok': ok, 'quebraEm': onde, 'registros': total}


@app.get('/api/auditoria/registro/{log_id}')
def ver_registro(log_id: int, _: Usuario = Depends(auth.usuario_atual)):
    with Sessao() as s:
        l = s.get(Log, log_id)
        if not l:
            raise HTTPException(404, 'Registro não encontrado.')
        d = log_auditoria.como_json(l, com_geom=True)
        if l.lote_id and (lote := s.get(Lote, l.lote_id)):
            import shapely
            mapa = servico.ParaMapa(lote)
            d['geomAntes'] = mapa.geojson(shapely.from_wkt(l.geom_antes)) if l.geom_antes else None
            d['geomDepois'] = mapa.geojson(shapely.from_wkt(l.geom_depois)) if l.geom_depois else None
        return d


@app.get('/api/auditoria/exportar/{formato}')
def exportar_log(formato: Literal['csv', 'pdf'], lote_id: int | None = None, acao: str | None = None, usuario: str | None = None,
                 fazenda: str | None = None, de: datetime | None = None, ate: datetime | None = None,
                 u: Usuario = Depends(auth.usuario_atual)):
    with trava_escrita, Sessao() as s:
        linhas = list(s.scalars(_filtro_log(select(Log), lote_id, acao, usuario, fazenda, de, ate).order_by(Log.id)))
        filtros = ', '.join(f'{k}={v}' for k, v in (('lote', lote_id), ('ação', acao), ('usuário', usuario), ('fazenda', fazenda),
                                                     ('de', de), ('até', ate)) if v)
        nome = f'log_auditoria{f"_lote{lote_id}" if lote_id else ""}_{agora():%Y%m%d_%H%M}'
        registrar(s, u.usuario, 'exportacao', f'Exportou o log de auditoria em {formato.upper()} ({len(linhas)} registros'
                  f'{"; " + filtros if filtros else ""}).', lote_id=lote_id)
        s.commit()
        if formato == 'csv':
            return baixar(f'{nome}.csv', log_auditoria.gerar_csv(linhas), 'text/csv; charset=utf-8')
        titulo = 'Log de auditoria' + (f' - lote {lote_id}' if lote_id else '')
        return baixar(f'{nome}.pdf', log_auditoria.gerar_pdf(linhas, titulo, f'({filtros})' if filtros else ''), 'application/pdf')


# ---------- frontend ----------
if config.FRONTEND.exists():
    app.mount('/assets', StaticFiles(directory=config.FRONTEND / 'assets'), name='assets')

    @app.get('/{caminho:path}', include_in_schema=False)
    def spa(caminho: str):
        arq = config.FRONTEND / caminho
        if caminho and arq.is_file() and arq.resolve().is_relative_to(config.FRONTEND.resolve()):
            return FileResponse(arq)
        return FileResponse(config.FRONTEND / 'index.html')
