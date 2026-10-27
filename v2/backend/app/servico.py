"""Regras de negócio sobre o banco: importar, auditar, revisar, aplicar e exportar."""
import io
import json
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import shapely
from pyproj import CRS, Transformer
from sqlalchemy import and_, delete, func, select

from . import importacao, regras as regras_mod, topologia
from .db import Camada, Feicao, Geometria, Inconsistencia, Lote, Versao, agora
from .importacao import ErroArquivo, texto, valor
from .log_auditoria import registrar
from .regras import PADRAO

VAZIO = shapely.to_wkb(shapely.Polygon())  # geometria vazia guardada como está; wkb None = feição removida


# ---------- leitura das geometrias versionadas ----------
def geometrias(s, lote_id, versao, fazendas=None, ids=None):
    """{feicao_id: geometria} na versão pedida (sem as feições removidas)."""
    sub = select(Geometria.feicao_id, func.max(Geometria.versao).label('v')).join(Feicao, Feicao.id == Geometria.feicao_id) \
        .where(Feicao.lote_id == lote_id, Geometria.versao <= versao)
    if fazendas is not None:
        sub = sub.where(Feicao.fazenda.in_(list(fazendas)))
    if ids is not None:
        sub = sub.where(Geometria.feicao_id.in_(list(ids)))
    sub = sub.group_by(Geometria.feicao_id).subquery()
    linhas = s.execute(select(Geometria.feicao_id, Geometria.wkb)
                       .join(sub, and_(Geometria.feicao_id == sub.c.feicao_id, Geometria.versao == sub.c.v))).all()
    return {fid: shapely.from_wkb(w) for fid, w in linhas if w is not None}


def medidor(lote):
    return topologia.Medidor(lote.crs, lote.crs_metrico)


class ParaMapa:
    """Converte do sistema do lote para lon/lat (o que o Leaflet desenha)."""

    def __init__(self, lote):
        src = CRS.from_user_input(lote.crs) if lote.crs else None
        self.t = None
        if src is not None and not (src.is_geographic and src.equals(CRS.from_epsg(4326), ignore_axis_order=True)):
            self.t = Transformer.from_crs(src, 'EPSG:4326', always_xy=True)

    def __call__(self, g):
        if g is None or self.t is None:
            return g
        return shapely.transform(g, lambda c: np.column_stack(self.t.transform(c[:, 0], c[:, 1])))

    def geojson(self, g, casas=7):
        if g is None or g.is_empty:
            return None
        return json.loads(shapely.to_geojson(shapely.set_precision(self(g), 10 ** -casas, mode='pointwise')))


def feicao_json(f, g, mapa, extra=None):
    return {'type': 'Feature', 'geometry': mapa.geojson(g),
            'properties': {'id': f.id, 'fazenda': f.fazenda, 'fazendaNome': f.fazenda_nome, 'aglomerado': f.aglomerado,
                           'talhao': f.talhao, **(extra or {})}}


# ---------- importação ----------
def importar(s, usuario, arquivos, nome_lote='', lote=None):
    """Cria um lote (ou mescla num existente) com todos os shapefiles enviados."""
    pasta, shps = importacao.preparar_pasta(arquivos)
    try:
        aliases = regras_mod.carregar(s)['campos']
        camadas = [(shp, importacao.ler_shapefile(shp)) for shp in shps]
        novo = lote is None
        if novo:
            crs = next((g.crs for _, g in camadas if g.crs is not None), None)
            if crs is None:  # sem .prj: coordenadas em graus viram SIRGAS 2000
                x0, y0, x1, y1 = camadas[0][1].total_bounds
                crs = CRS.from_epsg(4674) if -180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90 else None
            nome_lote = nome_lote.strip() or Path(arquivos[0][0]).stem
            lote = Lote(nome=nome_lote[:200], crs=crs.to_wkt() if crs else '', crs_nome=crs.name if crs else 'Sem sistema (.prj)',
                        criado_por=usuario.usuario, versao_atual=1)
            s.add(lote)
            s.flush()
        else:
            lote.versao_atual += 1
        versao = lote.versao_atual
        colunas = list(lote.colunas or [])
        campos_lote = dict(lote.campos or {})
        resumo = []
        limites = []
        for shp, gdf in camadas:
            if gdf.crs is not None and lote.crs and not gdf.crs.equals(CRS.from_user_input(lote.crs)):
                gdf = gdf.to_crs(lote.crs)
            cols = [c for c in gdf.columns if c != 'geometry']
            for c in cols:
                if c not in colunas:
                    colunas.append(c)
            campos = importacao.detectar_campos(cols, aliases)
            for k, v in campos.items():
                campos_lote.setdefault(k, v)
            cam = Camada(lote_id=lote.id, nome=shp.stem, arquivo=str(shp.relative_to(pasta)), n_feicoes=len(gdf),
                         crs_original=gdf.crs.name if gdf.crs is not None else '', criado_por=usuario.usuario)
            s.add(cam)
            s.flush()
            registros = gdf.drop(columns='geometry').to_dict('records')
            attrs = [{c: valor(r[c]) for c in cols} for r in registros]
            cods = [texto(a.get(campos.get('fazenda'))) for a in attrs]
            nomes = importacao.nomes_de_fazenda(zip(cods, attrs), campos.get('fazenda_nome'))
            fs = [Feicao(lote_id=lote.id, camada_id=cam.id, fazenda=cod, fazenda_nome=nomes.get(cod, ''),
                         aglomerado=texto(a.get(campos.get('aglomerado'))), talhao=texto(a.get(campos.get('talhao'))),
                         atributos=a) for cod, a in zip(cods, attrs)]
            s.add_all(fs)
            s.flush()
            wkbs = shapely.to_wkb(gdf.geometry.values, output_dimension=2)
            s.add_all(Geometria(feicao_id=f.id, versao=versao, wkb=w if w is not None else VAZIO) for f, w in zip(fs, wkbs))
            limites.append(gdf.total_bounds)
            resumo.append(f'{shp.name} ({len(gdf)} feições)')
        lote.colunas, lote.campos = colunas, campos_lote
        if novo:
            b = np.array(limites)
            lote.crs_metrico = topologia.crs_metrico_para(lote.crs, (b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()))
            registrar(s, usuario.usuario, 'importacao', f'Lote "{lote.nome}" criado com {", ".join(resumo)}. '
                      f'Sistema: {lote.crs_nome}. Campos: {_campos_txt(campos_lote)}.', lote_id=lote.id, versao=versao)
        else:
            s.add(Versao(lote_id=lote.id, numero=versao, descricao=f'Mesclagem: {", ".join(resumo)}', criado_por=usuario.usuario))
            registrar(s, usuario.usuario, 'mesclagem', f'Mesclado no lote: {", ".join(resumo)}.', lote_id=lote.id, versao=versao)
        if novo:
            s.add(Versao(lote_id=lote.id, numero=1, descricao=f'Importação: {", ".join(resumo)}', criado_por=usuario.usuario))
        s.flush()
        auditar(s, lote, usuario)
        return lote
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def _campos_txt(c):
    rot = {'fazenda': 'fazenda', 'fazenda_nome': 'nome da fazenda', 'aglomerado': 'aglomerado', 'talhao': 'talhão', 'area_ha': 'área'}
    return ', '.join(f'{rot[k]}={c[k]}' for k in rot if c.get(k)) or 'nenhum reconhecido'


# ---------- auditoria ----------
def auditar(s, lote, usuario, fazendas=None):
    """Roda a detecção (no lote todo ou só nas fazendas dadas) e troca as inconsistências em aberto."""
    q = select(Feicao).where(Feicao.lote_id == lote.id)
    if fazendas is not None:
        q = q.where(Feicao.fazenda.in_(list(fazendas)))
    fs = {f.id: f for f in s.scalars(q)}
    gs = geometrias(s, lote.id, lote.versao_atual, fazendas=fazendas)
    lista = [{'id': fid, 'fazenda': fs[fid].fazenda, 'aglomerado': fs[fid].aglomerado, 'talhao': fs[fid].talhao, 'geom': g}
             for fid, g in gs.items() if fid in fs]
    achados = topologia.auditar(lista, medidor(lote), regras_mod.carregar(s))

    antigas = select(Inconsistencia).where(Inconsistencia.lote_id == lote.id)
    if fazendas is not None:
        antigas = antigas.where(Inconsistencia.fazenda.in_(list(fazendas)))
    antigas = list(s.scalars(antigas))
    mantidas = {i.assinatura for i in antigas if i.status == 'mantida'}
    decididas = {i.assinatura: i for i in antigas if i.status in ('aceita', 'rejeitada')}
    abertas = [i.id for i in antigas if i.status in ('pendente', 'aceita', 'rejeitada')]
    if abertas:
        s.execute(delete(Inconsistencia).where(Inconsistencia.id.in_(abertas)))
    n = 0
    for a in achados:
        if a['assinatura'] in mantidas:
            continue
        antes = decididas.get(a['assinatura'])
        s.add(Inconsistencia(lote_id=lote.id, versao=lote.versao_atual, fazenda=a['fazenda'], tipo=a['tipo'],
                             severidade=a['severidade'], feicoes=a['feicoes'], descricao=a['descricao'], area_m2=a['area_m2'],
                             destaque=shapely.to_wkb(a['destaque'], output_dimension=2) if a['destaque'] is not None else None,
                             op=a['op'], assinatura=a['assinatura'],
                             status=antes.status if antes else 'pendente',
                             decidido_por=antes.decidido_por if antes else '', decidido_em=antes.decidido_em if antes else None))
        n += 1
    cont = Counter(a['tipo'] for a in achados if a['assinatura'] not in mantidas)
    alvo = f'fazenda(s) {", ".join(sorted(fazendas))}' if fazendas is not None else 'lote inteiro'
    registrar(s, usuario.usuario, 'auditoria', f'Auditoria do {alvo}: {len(lista)} talhões, {n} inconsistência(s)'
              + (': ' + ', '.join(f'{PADRAO[t]["nome"]} {c}' for t, c in cont.most_common()) if cont else '.'),
              lote_id=lote.id, versao=lote.versao_atual, fazenda=', '.join(sorted(fazendas)) if fazendas else '')
    s.flush()
    return n


# ---------- resumo do lote (etapa 1) ----------
def resumo_lote(s, lote):
    fs = list(s.scalars(select(Feicao).where(Feicao.lote_id == lote.id)))
    gs = geometrias(s, lote.id, lote.versao_atual)
    med = medidor(lote)
    ids = [f.id for f in fs if f.id in gs]
    arr = np.array([gs[i] for i in ids], dtype=object)
    partes = dict(zip(ids, shapely.get_num_geometries(arr).tolist())) if ids else {}
    areas = dict(zip(ids, (shapely.area(med.m(arr)) / 1e4).tolist())) if ids else {}
    incs = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id)))
    abertas = [i for i in incs if i.status in ('pendente', 'aceita', 'rejeitada')]

    faz = {}
    for f in fs:
        if f.id not in gs:
            continue
        d = faz.setdefault(f.fazenda, {'codigo': f.fazenda, 'nome': f.fazenda_nome, 'talhoes': 0, 'poligonos': 0, 'areaHa': 0.0,
                                       'aglomerados': Counter(), 'erros': Counter(), 'pendentes': 0})
        d['talhoes'] += 1
        d['poligonos'] += partes.get(f.id, 0)
        d['areaHa'] += areas.get(f.id, 0) or 0
        d['aglomerados'][f.aglomerado] += 1
    for i in abertas:
        if i.fazenda in faz:
            faz[i.fazenda]['erros'][i.tipo] += 1
            faz[i.fazenda]['pendentes'] += i.status == 'pendente'
    fazendas = []
    for d in sorted(faz.values(), key=lambda d: (len(d['codigo']), d['codigo'])):
        d['aglomerados'] = [{'nome': k, 'talhoes': v} for k, v in sorted(d['aglomerados'].items())]
        d['nErros'] = sum(d['erros'].values())
        d['erros'] = dict(d['erros'])
        d['areaHa'] = round(d['areaHa'], 2)
        fazendas.append(d)
    camadas = list(s.scalars(select(Camada).where(Camada.lote_id == lote.id).order_by(Camada.id)))
    versoes = list(s.scalars(select(Versao).where(Versao.lote_id == lote.id).order_by(Versao.numero)))
    return {
        **lote_json(lote),
        'camadas': [{'id': c.id, 'nome': c.nome, 'arquivo': c.arquivo, 'feicoes': c.n_feicoes, 'crs': c.crs_original,
                     'criadoPor': c.criado_por, 'criadoEm': c.criado_em} for c in camadas],
        'versoes': [versao_json(v) for v in versoes],
        'fazendas': fazendas,
        'totais': {'talhoes': len(gs), 'poligonos': sum(partes.values()), 'fazendas': len(fazendas),
                   'erros': dict(Counter(i.tipo for i in abertas)), 'nErros': len(abertas),
                   'aplicadas': sum(i.status == 'aplicada' for i in incs), 'mantidas': sum(i.status == 'mantida' for i in incs)},
        'tipos': [{'chave': k, 'nome': v['nome'], 'severidade': v['severidade']} for k, v in PADRAO.items()],
    }


def lote_json(l):
    return {'id': l.id, 'nome': l.nome, 'crs': l.crs_nome, 'crsMetrico': l.crs_metrico, 'campos': l.campos,
            'versaoAtual': l.versao_atual, 'criadoPor': l.criado_por, 'criadoEm': l.criado_em}


def versao_json(v):
    return {'numero': v.numero, 'descricao': v.descricao, 'fazendas': v.fazendas, 'alteradas': v.n_alteradas,
            'criadoPor': v.criado_por, 'criadoEm': v.criado_em}


def mapa_fazenda(s, lote, fazenda, versao=None):
    """GeoJSON dos talhões (todas as fazendas se `fazenda` for None, com desenho simplificado)."""
    versao = versao or lote.versao_atual
    fazendas = None if fazenda is None else [fazenda]
    fs = {f.id: f for f in s.scalars(select(Feicao).where(Feicao.lote_id == lote.id, *( [Feicao.fazenda == fazenda] if fazenda is not None else [])))}
    gs = geometrias(s, lote.id, versao, fazendas=fazendas)
    erros = Counter()
    for i in s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id,
                                                    Inconsistencia.status.in_(('pendente', 'aceita', 'rejeitada')))):
        for fid in i.feicoes:
            erros[fid] += 1
    mapa, med = ParaMapa(lote), medidor(lote)
    tol = med.tol(2.0) if fazenda is None else 0
    feats = []
    for fid, g in gs.items():
        f = fs.get(fid)
        if f is None:
            continue
        gd = shapely.simplify(g, tol, preserve_topology=False) if tol else g
        feats.append(feicao_json(f, gd, mapa, {'erros': erros[fid], 'areaHa': round(med.area(g) / 1e4, 4),
                                               'vertices': int(shapely.get_num_coordinates(g))}))
    return {'type': 'FeatureCollection', 'features': feats}


# ---------- inconsistências (etapa 2) ----------
def nomes_feicoes(s, ids):
    return {f.id: f for f in s.scalars(select(Feicao).where(Feicao.id.in_(list(ids))))} if ids else {}


def inconsistencia_json(i, feicoes, mapa):
    return {'id': i.id, 'tipo': i.tipo, 'nome': PADRAO[i.tipo]['nome'], 'severidade': i.severidade, 'fazenda': i.fazenda,
            'descricao': i.descricao, 'areaM2': round(i.area_m2, 2), 'status': i.status, 'temCorrecao': i.op is not None,
            'versao': i.versao, 'decididoPor': i.decidido_por, 'decididoEm': i.decidido_em,
            'feicoes': [{'id': fid, 'talhao': feicoes[fid].talhao if fid in feicoes else '?',
                         'aglomerado': feicoes[fid].aglomerado if fid in feicoes else ''} for fid in i.feicoes],
            'destaque': mapa.geojson(shapely.from_wkb(i.destaque)) if i.destaque else None}


def listar_inconsistencias(s, lote, fazenda=None, tipo=None, status=None):
    q = select(Inconsistencia).where(Inconsistencia.lote_id == lote.id)
    if fazenda is not None:
        q = q.where(Inconsistencia.fazenda == fazenda)
    if tipo:
        q = q.where(Inconsistencia.tipo == tipo)
    if status == 'abertas':
        q = q.where(Inconsistencia.status.in_(('pendente', 'aceita', 'rejeitada')))
    elif status:
        q = q.where(Inconsistencia.status == status)
    incs = list(s.scalars(q))
    ordem = {k: n for n, k in enumerate(PADRAO)}
    incs.sort(key=lambda i: (ordem[i.tipo], -i.area_m2, i.id))
    fs = nomes_feicoes(s, {fid for i in incs for fid in i.feicoes})
    mapa = ParaMapa(lote)
    return [inconsistencia_json(i, fs, mapa) for i in incs]


def detalhe_inconsistencia(s, lote, inc):
    """Original x proposta das feições envolvidas, com os vizinhos em volta para dar contexto."""
    med, mapa = medidor(lote), ParaMapa(lote)
    gs = geometrias(s, lote.id, lote.versao_atual, ids=inc.feicoes)
    proposta = topologia.aplicar_ops(gs, [inc.op], med) if inc.op else dict(gs)
    fs = nomes_feicoes(s, inc.feicoes)
    # vizinhos: talhões da mesma fazenda perto do erro
    # só caixas envolventes: as geometrias podem ser inválidas (é justamente o erro em revisão)
    validas = [g for g in gs.values() if g is not None and not g.is_empty]
    viz = []
    if validas:
        b = shapely.bounds(np.array(validas, dtype=object))
        x0, y0, x1, y1 = b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()
        area = shapely.box(x0, y0, x1, y1).buffer(max(med.tol(150), (x1 - x0) * 0.3))
        todos = geometrias(s, lote.id, lote.versao_atual, fazendas=[inc.fazenda])
        perto = [fid for fid, g in todos.items() if fid not in gs and g is not None and shapely.envelope(g).intersects(area)][:150]
        fviz = nomes_feicoes(s, perto)
        viz = [feicao_json(fviz[fid], todos[fid], mapa) for fid in perto]

    def metricas(g):
        return {'areaHa': round(med.area(g) / 1e4, 4) if g is not None else 0,
                'vertices': int(shapely.get_num_coordinates(g)) if g is not None else 0,
                'partes': int(shapely.get_num_geometries(g)) if g is not None else 0,
                'valida': bool(g.is_valid) if g is not None else True}

    envolvidas = []
    for fid in inc.feicoes:
        if fid not in fs:
            continue
        a, d = gs.get(fid), proposta.get(fid)
        envolvidas.append({'id': fid, 'talhao': fs[fid].talhao, 'aglomerado': fs[fid].aglomerado,
                           'alvo': bool(inc.op and inc.op.get('alvo') == fid), 'antes': metricas(a), 'depois': metricas(d),
                           'removida': a is not None and d is None})
    return {
        **inconsistencia_json(inc, fs, mapa),
        'operacao': topologia.DESCRICAO_OP.get(inc.op['tipo']) if inc.op else None,
        'original': {'type': 'FeatureCollection', 'features': [feicao_json(fs[fid], gs[fid], mapa) for fid in inc.feicoes if fid in gs and fid in fs]},
        'proposta': {'type': 'FeatureCollection', 'features': [feicao_json(fs[fid], proposta[fid], mapa) for fid in inc.feicoes
                                                                if proposta.get(fid) is not None and fid in fs]},
        'vizinhos': {'type': 'FeatureCollection', 'features': viz},
        'envolvidas': envolvidas,
    }


def decidir(s, lote, ids, decisao, usuario):
    """decisao: 'aceitar' (usar a correção) ou 'manter' (ficar com o original)."""
    incs = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.id.in_(ids))))
    if not incs:
        raise ErroArquivo('Nenhuma inconsistência encontrada.')
    for i in incs:
        if i.status in ('aplicada', 'mantida'):
            raise ErroArquivo(f'A inconsistência #{i.id} já foi processada.')
        if decisao == 'aceitar' and i.op is None:
            raise ErroArquivo(f'#{i.id} ({PADRAO[i.tipo]["nome"]}) não tem correção automática: só dá para manter o original.')
        i.status = 'aceita' if decisao == 'aceitar' else 'rejeitada'
        i.decidido_por, i.decidido_em = usuario.usuario, agora()
    return incs


def aplicar(s, lote, ids, usuario):
    """Etapa 3: aplica as correções aceitas, grava nova versão e o log de cada decisão."""
    incs = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.id.in_(ids))))
    if len(incs) != len(set(ids)):
        raise ErroArquivo('Algumas inconsistências não existem mais (o lote foi auditado de novo). Volte e revise outra vez.')
    faltam = [i.id for i in incs if i.status not in ('aceita', 'rejeitada')]
    if faltam:
        raise ErroArquivo(f'Ainda falta decidir {len(faltam)} inconsistência(s): escolha manter o original ou aceitar a correção em cada uma.')
    med, aceitas = medidor(lote), [i for i in incs if i.status == 'aceita']
    alvos = {i.op['alvo'] for i in aceitas}
    antes = geometrias(s, lote.id, lote.versao_atual, ids=alvos)
    depois = topologia.aplicar_ops(antes, [i.op for i in aceitas], med)
    mudou = [fid for fid in alvos if fid in antes and not _igual(antes[fid], depois.get(fid))]
    fs = nomes_feicoes(s, {fid for i in incs for fid in i.feicoes})
    nova = lote.versao_atual + 1 if mudou else lote.versao_atual
    fazendas = sorted({i.fazenda for i in incs} | {fs[f].fazenda for f in mudou if f in fs})
    if mudou:
        for fid in mudou:
            g = depois.get(fid)
            s.add(Geometria(feicao_id=fid, versao=nova, wkb=shapely.to_wkb(g, output_dimension=2) if g is not None else None))
        lote.versao_atual = nova
        s.add(Versao(lote_id=lote.id, numero=nova, fazendas=fazendas, n_alteradas=len(mudou), criado_por=usuario.usuario,
                     descricao=f'{len(aceitas)} correção(ões) aplicada(s), {len(incs) - len(aceitas)} mantida(s) como original'))
    nome_faz = {f.fazenda: f.fazenda_nome for f in fs.values()}
    for i in incs:
        f = fs.get(i.op['alvo'] if i.op else i.feicoes[0]) if i.feicoes else None
        faz = f'{i.fazenda} · {nome_faz[i.fazenda]}' if nome_faz.get(i.fazenda) else i.fazenda
        tal = ', '.join(dict.fromkeys(fs[x].talhao or '?' for x in i.feicoes if x in fs))
        if i.status == 'aceita':
            alvo = i.op['alvo']
            registrar(s, usuario.usuario, 'correcao', f'{topologia.DESCRICAO_OP[i.op["tipo"]]} ({PADRAO[i.tipo]["nome"]}): {i.descricao}',
                      lote_id=lote.id, versao=nova, fazenda=faz, talhao=tal, antes=antes.get(alvo), depois=depois.get(alvo))
            i.status = 'aplicada'
        else:
            registrar(s, usuario.usuario, 'mantido', f'Original mantido ({PADRAO[i.tipo]["nome"]}): {i.descricao}',
                      lote_id=lote.id, versao=nova, fazenda=faz, talhao=tal)
            i.status = 'mantida'
    alteradas = []
    for fid in mudou:
        a, d = antes.get(fid), depois.get(fid)
        alteradas.append({'id': fid, 'talhao': fs[fid].talhao if fid in fs else '?', 'fazenda': fs[fid].fazenda if fid in fs else '',
                          'areaAntes': round(med.area(a) / 1e4, 4), 'areaDepois': round(med.area(d) / 1e4, 4) if d is not None else 0,
                          'verticesAntes': int(shapely.get_num_coordinates(a)),
                          'verticesDepois': int(shapely.get_num_coordinates(d)) if d is not None else 0,
                          'removida': d is None,
                          'ajustes': [topologia.DESCRICAO_OP[i.op['tipo']] for i in aceitas if i.op['alvo'] == fid]})
    if mudou:
        registrar(s, usuario.usuario, 'versao', f'Versão {nova} criada: {len(mudou)} talhão(ões) alterado(s) em {", ".join(fazendas)}.',
                  lote_id=lote.id, versao=nova, fazenda=', '.join(fazendas))
    s.flush()
    restantes = auditar(s, lote, usuario, fazendas=fazendas)
    return {'versao': nova, 'novaVersao': bool(mudou), 'aplicadas': len(aceitas), 'mantidas': len(incs) - len(aceitas),
            'alteradas': alteradas, 'fazendas': fazendas, 'restantes': restantes}


def _igual(a, b):
    if a is None or b is None:
        return a is b
    return shapely.equals_exact(a, b, 0) and shapely.get_num_coordinates(a) == shapely.get_num_coordinates(b)


# ---------- exportação ----------
def exportar(s, lote, versao, formato='shp', fazenda=None, recalcular_area=True):
    versao = versao or lote.versao_atual
    q = select(Feicao).where(Feicao.lote_id == lote.id)
    if fazenda:
        q = q.where(Feicao.fazenda == fazenda)
    fs = list(s.scalars(q.order_by(Feicao.id)))
    gs = geometrias(s, lote.id, versao, fazendas=[fazenda] if fazenda else None)
    alteradas = set(s.scalars(select(Geometria.feicao_id).join(Feicao, Feicao.id == Geometria.feicao_id)
                              .where(Feicao.lote_id == lote.id, Geometria.versao > 1, Geometria.versao <= versao)))
    fs = [f for f in fs if f.id in gs]
    colunas = list(lote.colunas or [])
    campo_area = (lote.campos or {}).get('area_ha')
    med = medidor(lote)
    linhas = []
    for f in fs:
        a = {c: f.atributos.get(c) for c in colunas}
        if recalcular_area and campo_area and f.id in alteradas:
            a[campo_area] = round(med.area(gs[f.id]) / 1e4, 4)
        linhas.append(a)
    gdf = gpd.GeoDataFrame(linhas, columns=colunas, geometry=[gs[f.id] for f in fs], crs=lote.crs or None)
    base = _seguro(f'{lote.nome}_v{versao}' + (f'_faz{fazenda}' if fazenda else ''))
    if formato == 'geojson':
        if gdf.crs is not None:
            gdf = gdf.to_crs('EPSG:4326')
        return f'{base}.geojson', gdf.to_json(drop_id=True).encode('utf-8'), 'application/geo+json'
    pasta = Path(tempfile.mkdtemp(prefix='vt_exp_'))
    try:
        pyogrio.write_dataframe(gdf, pasta / f'{base}.shp', driver='ESRI Shapefile', encoding='UTF-8')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in sorted(pasta.iterdir()):
                z.write(p, p.name)
        return f'{base}.zip', buf.getvalue(), 'application/zip'
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def _seguro(nome):
    return ''.join(c if c.isalnum() or c in '._-' else '_' for c in nome)
