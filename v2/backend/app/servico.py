"""Regras de negócio sobre o banco: importar, auditar, revisar, executar correções e exportar.

A base original nunca é alterada: cada correção grava linhas novas em `geometrias` com o número da
versão seguinte, e a versão 1 continua disponível para download.
"""
import io
import json
import shutil
import tempfile
import threading
import traceback
import zipfile
from collections import Counter
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import shapely
from pyproj import CRS, Transformer
from sqlalchemy import and_, delete, func, select

from . import correcao, estados, importacao, regras as regras_mod, topologia
from .db import Camada, Execucao, Feicao, Geometria, Historico, Inconsistencia, Lote, Sessao, Usuario, Versao, agora
from .importacao import ErroArquivo, texto, valor
from .log_auditoria import registrar, trava_escrita
from .regras import PADRAO

VAZIO = shapely.to_wkb(shapely.Polygon())  # geometria vazia guardada como está; wkb None = feição removida
NIVEIS = ('fazenda', 'fazenda_nome', 'fazenda_detalhe', 'aglomerado', 'talhao', 'area_ha')


# ---------- leitura das geometrias versionadas ----------
def geometrias(s, lote_id, versao, fazendas=None, ids=None, com_linhas=False):
    """{feicao_id: geometria} na versão pedida (sem as feições removidas).

    Com `fazendas`, traz as feições dessas fazendas (e, com `com_linhas`, todas as linhas do lote)."""
    sub = select(Geometria.feicao_id, func.max(Geometria.versao).label('v')).join(Feicao, Feicao.id == Geometria.feicao_id) \
        .where(Feicao.lote_id == lote_id, Geometria.versao <= versao)
    if fazendas is not None:
        cond = Feicao.fazenda.in_(list(fazendas))
        sub = sub.where(cond | Feicao.linha if com_linhas else cond)
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

    def geojson(self, g, casas=7, simplificar=0.0):
        """`simplificar` (unidades do lote) deixa o desenho mais leve: só para exibir, nunca para medir."""
        if g is None or g.is_empty:
            return None
        if simplificar:
            g = shapely.simplify(g, simplificar, preserve_topology=False) or g
            if g.is_empty:
                return None
        return json.loads(shapely.to_geojson(shapely.set_precision(self(g), 10 ** -casas, mode='pointwise')))


def feicao_json(f, g, mapa, extra=None, leve=0.0):
    return {'type': 'Feature', 'geometry': mapa.geojson(g, casas=6, simplificar=leve) if leve else mapa.geojson(g),
            'properties': {'id': f.id, 'fazenda': f.fazenda, 'fazendaNome': f.fazenda_nome, 'aglomerado': f.aglomerado,
                           'talhao': f.talhao, 'linha': f.linha, 'indice': f.indice_origem, **(extra or {})}}


def dados_auditoria(f, g, camadas=None):
    return {'id': f.id, 'fazenda': f.fazenda, 'aglomerado': f.aglomerado, 'talhao': f.talhao, 'linha': f.linha,
            'indice': f.indice_origem, 'camada': (camadas or {}).get(f.camada_id, ''), 'geom': g}


def historico(s, inc, estado, usuario, execucao_id=None, nota=''):
    s.add(Historico(inconsistencia_id=inc.id, lote_id=inc.lote_id, execucao_id=execucao_id, estado=estado,
                    usuario=usuario, nota=nota))


# ---------- importação (3.1.1) ----------
def importar(s, usuario, arquivos, nome_lote='', lote=None, verificacoes=None):
    """Cria um lote (ou mescla num existente) com todos os shapefiles enviados e roda a auditoria."""
    pasta, shps = importacao.preparar_pasta(arquivos)
    try:
        aliases = regras_mod.carregar(s)['campos']
        camadas = [(shp, *importacao.ler_shapefile(shp)) for shp in shps]
        if not any(tipo == 'poligono' for _, _, tipo, _ in camadas) and (lote is None):
            raise ErroArquivo('Nenhuma camada de polígonos (talhões) nos arquivos enviados. Linhas de estrada entram junto com os talhões.')
        novo = lote is None
        if novo:
            crs = next((g.crs for _, g, _, _ in camadas if g.crs is not None), None)
            if crs is None:  # sem .prj: coordenadas em graus viram SIRGAS 2000
                x0, y0, x1, y1 = camadas[0][1].total_bounds
                crs = CRS.from_epsg(4674) if -180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90 else None
            nome_lote = nome_lote.strip() or Path(arquivos[0][0]).stem
            lote = Lote(nome=nome_lote[:200], crs=crs.to_wkt() if crs else '', crs_nome=crs.name if crs else 'Sem sistema (.prj)',
                        criado_por=usuario.usuario, versao_atual=1, verificacoes=list(verificacoes or []))
            s.add(lote)
            s.flush()
        else:
            lote.versao_atual += 1
        versao = lote.versao_atual
        colunas = list(lote.colunas or [])
        campos_lote = dict(lote.campos or {})
        resumo, limites, avisos_todos = [], [], []
        for shp, gdf, tipo, avisos in camadas:
            if gdf.crs is not None and lote.crs and not gdf.crs.equals(CRS.from_user_input(lote.crs)):
                avisos.append(f'{shp.name}: reprojetado de {gdf.crs.name} para {lote.crs_nome}.')
                gdf = gdf.to_crs(lote.crs)
            elif gdf.crs is None and lote.crs:
                avisos.append(f'{shp.name}: sem sistema; considerado o mesmo do lote ({lote.crs_nome}).')
            cols = [c for c in gdf.columns if c != 'geometry']
            for c in cols:
                if c not in colunas:
                    colunas.append(c)
            campos = importacao.detectar_campos(cols, aliases) if tipo == 'poligono' else {}
            if tipo == 'poligono':
                for k, v in campos.items():
                    campos_lote.setdefault(k, v)
                for nivel, rot in (('fazenda', 'fazenda'), ('talhao', 'talhão')):
                    if nivel not in campos and nivel not in campos_lote:
                        avisos.append(f'{shp.name}: não identifiquei a coluna de {rot}. Escolha-a em "Campos" na tela do lote.')
            cam = Camada(lote_id=lote.id, nome=shp.stem, arquivo=str(shp.relative_to(pasta)), n_feicoes=len(gdf), geometria=tipo,
                         colunas=cols, avisos=avisos, crs_original=gdf.crs.name if gdf.crs is not None else '', criado_por=usuario.usuario)
            s.add(cam)
            s.flush()
            registros = gdf.drop(columns='geometry').to_dict('records')
            attrs = [{c: valor(r[c]) for c in cols} for r in registros]
            usa = campos_lote if tipo == 'poligono' else campos_lote  # linhas também usam a fazenda, quando tiverem a coluna
            fs = [Feicao(lote_id=lote.id, camada_id=cam.id, linha=tipo == 'linha', indice_origem=i, atributos=a) for i, a in enumerate(attrs)]
            preencher_hierarquia(fs, usa)
            s.add_all(fs)
            s.flush()
            wkbs = shapely.to_wkb(gdf.geometry.values, output_dimension=2)
            s.add_all(Geometria(feicao_id=f.id, versao=versao, wkb=w if w is not None else VAZIO) for f, w in zip(fs, wkbs))
            if len(gdf) and not np.isnan(gdf.total_bounds).any():
                limites.append(gdf.total_bounds)
            resumo.append(f'{shp.name} ({len(gdf)} {"linhas" if tipo == "linha" else "feições"})')
            avisos_todos += avisos
        lote.colunas, lote.campos = colunas, campos_lote
        txt_avisos = f' Avisos: {" ".join(avisos_todos)}' if avisos_todos else ''
        if novo:
            b = np.array(limites) if limites else np.array([[0, 0, 0, 0]])
            lote.crs_metrico = topologia.crs_metrico_para(lote.crs, (b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()))
            s.add(Versao(lote_id=lote.id, numero=1, descricao=f'Importação (base original): {", ".join(resumo)}', criado_por=usuario.usuario))
            registrar(s, usuario.usuario, 'importacao', f'Lote "{lote.nome}" criado com {", ".join(resumo)}. '
                      f'Sistema: {lote.crs_nome}. Campos: {_campos_txt(campos_lote)}.{txt_avisos}', lote_id=lote.id, versao=versao)
        else:
            s.add(Versao(lote_id=lote.id, numero=versao, descricao=f'Mesclagem: {", ".join(resumo)}', criado_por=usuario.usuario))
            registrar(s, usuario.usuario, 'mesclagem', f'Mesclado no lote: {", ".join(resumo)}.{txt_avisos}', lote_id=lote.id, versao=versao)
        s.flush()
        auditar(s, lote, usuario, entrada=', '.join(resumo))
        return lote
    finally:
        shutil.rmtree(pasta, ignore_errors=True)  # os arquivos enviados são temporários; os dados ficam no banco


def preencher_hierarquia(fs, campos):
    """Fazenda/aglomerado/talhão vêm só das colunas escolhidas; nível sem coluna fica vazio (nada é presumido).
    Talhão sem código mas com nome de fazenda fica na fazenda "#nome" (ver importacao.chave_fazenda)."""
    pega = lambda f, nivel: texto(f.atributos.get(campos[nivel])) if campos.get(nivel) else ''
    nomes_lidos = []  # lista, não dict por f.id: na importação as feições ainda não têm id
    for f in fs:
        nome, detalhe = pega(f, 'fazenda_nome'), pega(f, 'fazenda_detalhe')
        f.fazenda = importacao.chave_fazenda(pega(f, 'fazenda'), nome, detalhe)
        f.aglomerado, f.talhao = pega(f, 'aglomerado'), pega(f, 'talhao')
        nomes_lidos.append((f.fazenda, nome, detalhe))
    nomes = importacao.nomes_de_fazenda(nomes_lidos)
    for f in fs:
        f.fazenda_nome = nomes.get(f.fazenda, '')


def exibir_fazenda(chave, nome=''):
    """Fazenda para textos (log, relatórios): "5004 · MALU", "Mutum (sem código)" ou "Sem fazenda"."""
    if chave.startswith(importacao.SEM_CODIGO):
        return f'{nome or chave[1:]} (sem código)'
    if not chave:
        return 'Sem fazenda'
    return f'{chave} · {nome}' if nome else chave


def _campos_txt(c):
    rot = {'fazenda': 'fazenda', 'fazenda_nome': 'nome da fazenda', 'fazenda_detalhe': 'detalhe do nome', 'aglomerado': 'aglomerado',
           'talhao': 'talhão', 'area_ha': 'área'}
    return ', '.join(f'{rot[k]}={c[k]}' for k in rot if c.get(k)) or 'nenhum reconhecido'


def remapear_campos(s, lote, campos, usuario):
    """O usuário confirma/troca as colunas da hierarquia; refaz fazenda/talhão e reaudita o lote."""
    novos = {k: v for k, v in campos.items() if k in NIVEIS and v}
    for k, v in novos.items():
        if v not in (lote.colunas or []):
            raise ErroArquivo(f'A coluna "{v}" não existe no lote.')
    antes = _campos_txt(lote.campos or {})
    lote.campos = novos
    fs = list(s.scalars(select(Feicao).where(Feicao.lote_id == lote.id)))
    preencher_hierarquia(fs, novos)
    registrar(s, usuario.usuario, 'lote', f'Campos do lote alterados: {antes} → {_campos_txt(novos)}.', lote_id=lote.id, versao=lote.versao_atual)
    s.flush()
    auditar(s, lote, usuario, entrada='reauditoria após troca de campos')


# ---------- auditoria (3.1.2 a 3.1.4) ----------
def regras_do_lote(s, lote, verificacoes=None):
    """Regras ativas (pelo admin) e escolhidas para este lote."""
    todas = regras_mod.carregar(s)
    escolhidas = set(verificacoes if verificacoes is not None else (lote.verificacoes or [])) or set(PADRAO)
    for k in PADRAO:
        if k not in escolhidas:
            todas[k] = {**todas[k], 'ativo': False}
    return todas


def auditar(s, lote, usuario, fazendas=None, entrada='', verificacoes=None):
    """Roda a detecção (no lote todo ou só nas fazendas dadas) e troca as ocorrências em aberto.

    Não mexe em geometria nenhuma. As decisões já tomadas (aprovada, rejeitada, pendente, falha) são
    mantidas quando a mesma ocorrência volta a ser detectada (mesma assinatura)."""
    if verificacoes is not None:
        lote.verificacoes = list(verificacoes)
    regras = regras_do_lote(s, lote)
    exe = Execucao(lote_id=lote.id, tipo='auditoria', usuario=usuario.usuario, versao_entrada=lote.versao_atual,
                   versao_saida=lote.versao_atual, fazendas=sorted(fazendas) if fazendas else [],
                   entrada=entrada or f'Lote {lote.id} "{lote.nome}", versão {lote.versao_atual}',
                   regras={k: v['params'] for k, v in regras.items() if k in PADRAO and v['ativo']})
    s.add(exe)
    s.flush()
    q = select(Feicao).where(Feicao.lote_id == lote.id)
    if fazendas is not None:
        q = q.where(Feicao.fazenda.in_(list(fazendas)) | Feicao.linha)
    fs = {f.id: f for f in s.scalars(q)}
    nomes_cam = dict(s.execute(select(Camada.id, Camada.nome).where(Camada.lote_id == lote.id)).all())
    gs = geometrias(s, lote.id, lote.versao_atual, fazendas=fazendas, com_linhas=True)
    lista = [dados_auditoria(fs[fid], g, nomes_cam) for fid, g in gs.items() if fid in fs]
    achados = topologia.auditar(lista, medidor(lote), regras, fazendas_alvo=set(fazendas) if fazendas is not None else None)

    antigas = select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.status.in_(estados.ABERTOS))
    if fazendas is not None:
        antigas = antigas.where(Inconsistencia.fazenda.in_(list(fazendas)))
    antigas = list(s.scalars(antigas))
    conhecidas = {i.assinatura: i for i in antigas}
    if antigas:
        s.execute(delete(Inconsistencia).where(Inconsistencia.id.in_([i.id for i in antigas])))
    novas = 0
    for a in achados:
        antes = conhecidas.get(a['assinatura'])
        inicial = estados.inicial(a['op'])
        status = antes.status if antes and antes.status not in ('identificada', 'em_revisao', 'nao_corrigivel') else inicial
        if status == 'aprovada' and a['op'] is None:
            status = inicial
        inc = Inconsistencia(lote_id=lote.id, versao=lote.versao_atual, fazenda=a['fazenda'], tipo=a['tipo'],
                             severidade=a['severidade'], feicoes=a['feicoes'], descricao=a['descricao'], area_m2=a['area_m2'],
                             destaque=shapely.to_wkb(a['destaque'], output_dimension=2) if a['destaque'] is not None else None,
                             op=a['op'], assinatura=a['assinatura'], classificacao=a['classificacao'], motivo=a['motivo'],
                             limitacoes=PADRAO[a['tipo']]['limitacoes'], caracteristicas=a['caracteristicas'], execucao_id=exe.id,
                             status=status, decidido_por=antes.decidido_por if antes else '',
                             decidido_em=antes.decidido_em if antes else None, validacao=antes.validacao if antes else [])
        s.add(inc)
        s.flush()
        historico(s, inc, status, usuario.usuario, exe.id,
                  'detectada de novo; decisão anterior mantida' if antes else 'registrada pela auditoria')
        novas += antes is None
    pol = [x for x in lista if not x['linha']]
    exe.resumo = {
        'feicoes': len(lista), 'talhoes': len(pol), 'linhas': len(lista) - len(pol), 'ocorrencias': len(achados), 'novas': novas,
        'porCategoria': dict(Counter(a['tipo'] for a in achados)),
        'porFazenda': dict(Counter(a['fazenda'] for a in achados)),
        'porClassificacao': dict(Counter(a['classificacao'] for a in achados)),
        'comCorrecao': sum(a['op'] is not None for a in achados),
    }
    exe.status, exe.fim = 'concluida', agora()
    alvo = f'fazenda(s) {", ".join(sorted(fazendas))}' if fazendas is not None else 'lote inteiro'
    cont = exe.resumo['porCategoria']
    registrar(s, usuario.usuario, 'auditoria', f'Auditoria #{exe.id} do {alvo}: {len(lista)} feições, {len(achados)} ocorrência(s)'
              + (': ' + ', '.join(f'{PADRAO[t]["nome"]} {c}' for t, c in Counter(cont).most_common()) if cont else '.')
              + f' Regras: {", ".join(PADRAO[k]["nome"] for k in exe.regras)}.',
              lote_id=lote.id, versao=lote.versao_atual, fazenda=', '.join(sorted(fazendas)) if fazendas else '')
    s.flush()
    return exe


# ---------- resumo do lote (etapa 1) ----------
def resumo_lote(s, lote):
    fs = list(s.scalars(select(Feicao).where(Feicao.lote_id == lote.id)))
    gs = geometrias(s, lote.id, lote.versao_atual)
    med = medidor(lote)
    ids = [f.id for f in fs if f.id in gs and not f.linha]
    arr = np.array([gs[i] for i in ids], dtype=object)
    partes = dict(zip(ids, shapely.get_num_geometries(arr).tolist())) if ids else {}
    areas = dict(zip(ids, (shapely.area(med.m(arr)) / 1e4).tolist())) if ids else {}
    incs = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id)))
    abertas = [i for i in incs if i.status in estados.ABERTOS]

    faz = {}
    for f in fs:
        if f.id not in gs or f.linha:
            continue
        d = faz.setdefault(f.fazenda, {'codigo': f.fazenda, 'nome': f.fazenda_nome, 'talhoes': 0, 'poligonos': 0, 'areaHa': 0.0,
                                       'aglomerados': Counter(), 'erros': Counter(), 'pendentes': 0})
        d['talhoes'] += 1
        d['poligonos'] += partes.get(f.id, 0)
        d['areaHa'] += areas.get(f.id, 0) or 0
        d['aglomerados'][f.aglomerado] += 1
    for i in abertas:
        d = faz.setdefault(i.fazenda, {'codigo': i.fazenda, 'nome': 'Estradas / sem fazenda' if not i.fazenda else '', 'talhoes': 0,
                                       'poligonos': 0, 'areaHa': 0.0, 'aglomerados': Counter(), 'erros': Counter(), 'pendentes': 0})
        d['erros'][i.tipo] += 1
        d['pendentes'] += i.status in ('identificada', 'em_revisao', 'pendente')
    fazendas = []
    for d in sorted(faz.values(), key=lambda d: (len(d['codigo']), d['codigo'])):
        # aglomerado principal: o que mais aparece nos talhões da fazenda (a tela agrupa as fazendas por ele)
        d['aglomerado'] = d['aglomerados'].most_common(1)[0][0] if d['aglomerados'] else ''
        d['aglomerados'] = [{'nome': k, 'talhoes': v} for k, v in sorted(d['aglomerados'].items())]
        d['nErros'] = sum(d['erros'].values())
        d['erros'] = dict(d['erros'])
        d['areaHa'] = round(d['areaHa'], 2)
        fazendas.append(d)
    camadas = list(s.scalars(select(Camada).where(Camada.lote_id == lote.id).order_by(Camada.id)))
    versoes = list(s.scalars(select(Versao).where(Versao.lote_id == lote.id).order_by(Versao.numero)))
    ult = s.scalar(select(Execucao).where(Execucao.lote_id == lote.id, Execucao.tipo == 'auditoria').order_by(Execucao.id.desc()).limit(1))
    return {
        **lote_json(lote),
        'colunas': lote.colunas,
        'camadas': [{'id': c.id, 'nome': c.nome, 'arquivo': c.arquivo, 'feicoes': c.n_feicoes, 'crs': c.crs_original, 'geometria': c.geometria,
                     'avisos': c.avisos, 'criadoPor': c.criado_por, 'criadoEm': c.criado_em} for c in camadas],
        'versoes': [versao_json(v) for v in versoes],
        'fazendas': fazendas,
        'ultimaAuditoria': execucao_json(ult) if ult else None,
        'totais': {'talhoes': len(ids), 'linhas': sum(1 for f in fs if f.linha and f.id in gs), 'poligonos': sum(partes.values()),
                   'fazendas': len([d for d in fazendas if d['talhoes']]),
                   'erros': dict(Counter(i.tipo for i in abertas)), 'nErros': len(abertas),
                   'porEstado': dict(Counter(i.status for i in incs)),
                   'porClassificacao': dict(Counter(i.classificacao for i in abertas))},
        'tipos': [{'chave': k, 'nome': v['nome'], 'severidade': v['severidade'], 'grupo': v['grupo']} for k, v in PADRAO.items()],
        'estados': estados.ESTADOS,
    }


def feicao_detalhe(s, lote, fid):
    """Ficha de um talhão/linha clicado no mapa: identificação, medidas e todos os atributos originais."""
    f = s.get(Feicao, fid)
    if f is None or f.lote_id != lote.id:
        raise ErroArquivo('Feição não encontrada.')
    g = geometrias(s, lote.id, lote.versao_atual, ids=[fid]).get(fid)
    med = medidor(lote)
    cam = s.get(Camada, f.camada_id)
    abertas = [i for i in s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id,
                                                              Inconsistencia.status.in_(estados.ABERTOS)))
               if fid in i.feicoes]
    return {'id': f.id, 'fazenda': f.fazenda, 'fazendaNome': f.fazenda_nome, 'aglomerado': f.aglomerado, 'talhao': f.talhao,
            'linha': f.linha, 'camada': cam.nome if cam else '', 'registro': f.indice_origem + 1,
            'areaHa': round(med.area(g) / 1e4, 4) if g is not None and not f.linha else None,
            'comprimentoM': round(med.comprimento(g), 1) if g is not None and f.linha else None,
            'vertices': int(shapely.get_num_coordinates(g)) if g is not None else 0,
            'partes': int(shapely.get_num_geometries(g)) if g is not None else 0,
            'atributos': f.atributos,
            'ocorrencias': [{'id': i.id, 'nome': PADRAO[i.tipo]['nome'], 'status': i.status} for i in abertas]}


def lote_json(l):
    return {'id': l.id, 'nome': l.nome, 'crs': l.crs_nome, 'crsMetrico': l.crs_metrico, 'campos': l.campos,
            'verificacoes': l.verificacoes or [], 'versaoAtual': l.versao_atual, 'criadoPor': l.criado_por, 'criadoEm': l.criado_em}


def versao_json(v):
    return {'numero': v.numero, 'descricao': v.descricao, 'fazendas': v.fazendas, 'alteradas': v.n_alteradas,
            'criadoPor': v.criado_por, 'criadoEm': v.criado_em}


def execucao_json(e):
    d = {'id': e.id, 'loteId': e.lote_id, 'tipo': e.tipo, 'status': e.status, 'usuario': e.usuario, 'inicio': e.inicio, 'fim': e.fim,
         'versaoEntrada': e.versao_entrada, 'versaoSaida': e.versao_saida, 'entrada': e.entrada, 'saida': e.saida,
         'regras': e.regras, 'fazendas': e.fazendas, 'resumo': e.resumo, 'erro': e.erro}
    if e.status == 'executando':
        d['progresso'] = PROGRESSO.get(e.id, {'feito': 0, 'total': 0, 'etapa': 'Na fila'})
    return d


def mapa_fazenda(s, lote, fazenda, versao=None):
    """GeoJSON dos talhões (todas as fazendas se `fazenda` for None, com desenho simplificado) e das estradas perto."""
    versao = versao or lote.versao_atual
    fazendas = None if fazenda is None else [fazenda]
    fs = {f.id: f for f in s.scalars(select(Feicao).where(Feicao.lote_id == lote.id))}
    gs = geometrias(s, lote.id, versao, fazendas=fazendas, com_linhas=True)
    erros = Counter()
    for i in s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.status.in_(estados.ABERTOS))):
        for fid in i.feicoes:
            erros[fid] += 1
    mapa, med = ParaMapa(lote), medidor(lote)
    # desenho para o mapa: 0,5 m numa fazenda e 5 m no lote todo (invisível na tela, ~5x menos dados)
    tol = med.tol(5.0) if fazenda is None else med.tol(0.5)
    talhoes = [fid for fid in gs if fid in fs and not fs[fid].linha]
    caixa = None
    if fazenda is not None and talhoes:
        b = shapely.bounds(np.array([gs[f] for f in talhoes], dtype=object))
        caixa = shapely.box(np.nanmin(b[:, 0]), np.nanmin(b[:, 1]), np.nanmax(b[:, 2]), np.nanmax(b[:, 3])).buffer(med.tol(200))
    feats = []
    for fid, g in gs.items():
        f = fs.get(fid)
        if f is None:
            continue
        if f.linha and fazenda is not None and f.fazenda != fazenda and (caixa is None or not g.intersects(caixa)):
            continue
        extra = {'erros': erros[fid], 'vertices': int(shapely.get_num_coordinates(g))}
        extra.update({'comprimentoM': round(med.comprimento(g))} if f.linha else {'areaHa': round(med.area(g) / 1e4, 4)})
        feats.append(feicao_json(f, g, mapa, extra, leve=tol))
    return {'type': 'FeatureCollection', 'features': feats}


# ---------- ocorrências (etapa 2) ----------
def nomes_feicoes(s, ids):
    return {f.id: f for f in s.scalars(select(Feicao).where(Feicao.id.in_(list(ids))))} if ids else {}


def inconsistencia_json(i, feicoes, mapa, leve=0.0):
    def ident(fid):
        f = feicoes.get(fid)
        if f is None:
            return {'id': fid, 'talhao': '?', 'aglomerado': '', 'linha': False}
        return {'id': fid, 'talhao': f.talhao, 'aglomerado': f.aglomerado, 'fazenda': f.fazenda, 'linha': f.linha, 'indice': f.indice_origem}
    return {'id': i.id, 'tipo': i.tipo, 'nome': PADRAO[i.tipo]['nome'], 'grupo': PADRAO[i.tipo]['grupo'], 'severidade': i.severidade,
            'fazenda': i.fazenda, 'descricao': i.descricao, 'areaM2': round(i.area_m2, 2), 'status': i.status,
            'estado': estados.ESTADOS.get(i.status, i.status), 'classificacao': i.classificacao, 'temCorrecao': i.op is not None,
            'versao': i.versao, 'execucaoId': i.execucao_id, 'decididoPor': i.decidido_por, 'decididoEm': i.decidido_em,
            'feicoes': [ident(fid) for fid in i.feicoes],
            'destaque': (mapa.geojson(shapely.from_wkb(i.destaque), casas=6, simplificar=leve) if leve
                         else mapa.geojson(shapely.from_wkb(i.destaque))) if i.destaque else None}


def listar_inconsistencias(s, lote, fazenda=None, tipo=None, status=None, talhao=None, auto=None, ids=None):
    q = select(Inconsistencia).where(Inconsistencia.lote_id == lote.id)
    if ids is not None:
        q = q.where(Inconsistencia.id.in_(ids))
    if fazenda is not None:
        q = q.where(Inconsistencia.fazenda == fazenda)
    if tipo:
        q = q.where(Inconsistencia.tipo == tipo)
    if status == 'abertas':
        q = q.where(Inconsistencia.status.in_(estados.ABERTOS))
    elif status:
        q = q.where(Inconsistencia.status == status)
    incs = list(s.scalars(q))
    if auto is not None:
        incs = [i for i in incs if (i.op is not None) == auto]
    fs = nomes_feicoes(s, {fid for i in incs for fid in i.feicoes})
    if talhao:
        t = talhao.strip().lower()
        incs = [i for i in incs if any(t in (fs[f].talhao or '').lower() for f in i.feicoes if f in fs)]
    ordem = {k: n for n, k in enumerate(PADRAO)}
    incs.sort(key=lambda i: (ordem[i.tipo], -i.area_m2, i.id))
    mapa = ParaMapa(lote)
    leve = medidor(lote).tol(0.5)  # só a lista/mapa; o detalhe da revisão manda o desenho exato
    return [inconsistencia_json(i, fs, mapa, leve) for i in incs]


def detalhe_inconsistencia(s, lote, inc, usuario=None):
    """Original x prévia da correção (numa cópia), com os vizinhos em volta para dar contexto.
    Abrir uma ocorrência identificada a coloca "em revisão"."""
    if usuario is not None and inc.status == 'identificada':
        inc.status = 'em_revisao'
        historico(s, inc, 'em_revisao', usuario.usuario, nota='aberta na revisão')
    med, mapa = medidor(lote), ParaMapa(lote)
    gs = geometrias(s, lote.id, lote.versao_atual, ids=inc.feicoes)
    proposta = correcao.previa(dict(gs), [inc.op], med) if inc.op else dict(gs)
    fs = nomes_feicoes(s, inc.feicoes)
    camadas = dict(s.execute(select(Camada.id, Camada.nome).where(Camada.lote_id == lote.id)).all())
    # vizinhos: só caixas envolventes, porque as geometrias podem ser inválidas (é justamente o erro em revisão)
    validas = [g for g in gs.values() if g is not None and not g.is_empty]
    viz = []
    if validas:
        b = shapely.bounds(np.array(validas, dtype=object))
        x0, y0, x1, y1 = b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()
        area = shapely.box(x0, y0, x1, y1).buffer(max(med.tol(150), (x1 - x0) * 0.3))
        todos = geometrias(s, lote.id, lote.versao_atual, fazendas=[inc.fazenda], com_linhas=True)
        perto = [fid for fid, g in todos.items() if fid not in gs and g is not None and shapely.envelope(g).intersects(area)][:150]
        fviz = nomes_feicoes(s, perto)
        viz = [feicao_json(fviz[fid], todos[fid], mapa) for fid in perto]

    def metricas(g, linha):
        if g is None:
            return {'areaHa': 0, 'comprimentoM': 0, 'vertices': 0, 'partes': 0, 'valida': True}
        return {'areaHa': round(med.area(g) / 1e4, 4) if not linha else 0, 'comprimentoM': round(med.comprimento(g), 1) if linha else 0,
                'vertices': int(shapely.get_num_coordinates(g)), 'partes': int(shapely.get_num_geometries(g)), 'valida': bool(g.is_valid)}

    envolvidas = []
    for fid in inc.feicoes:
        if fid not in fs:
            continue
        f, a, d = fs[fid], gs.get(fid), proposta.get(fid)
        envolvidas.append({'id': fid, 'talhao': f.talhao, 'aglomerado': f.aglomerado, 'fazenda': f.fazenda, 'fazendaNome': f.fazenda_nome,
                           'linha': f.linha, 'camada': camadas.get(f.camada_id, ''), 'indiceOrigem': f.indice_origem,
                           'alvo': bool(inc.op and inc.op.get('alvo') == fid), 'antes': metricas(a, f.linha), 'depois': metricas(d, f.linha),
                           'removida': a is not None and d is None, 'atributos': f.atributos})
    hist = list(s.scalars(select(Historico).where(Historico.inconsistencia_id == inc.id).order_by(Historico.id)))
    return {
        **inconsistencia_json(inc, fs, mapa),
        'motivo': inc.motivo, 'limitacoes': inc.limitacoes, 'caracteristicas': inc.caracteristicas,
        'manual': inc.op is None, 'operacao': correcao.DESCRICAO_OP.get(inc.op['tipo']) if inc.op else None,
        'validacao': inc.validacao,
        'original': {'type': 'FeatureCollection', 'features': [feicao_json(fs[fid], gs[fid], mapa) for fid in inc.feicoes if fid in gs and fid in fs]},
        'proposta': {'type': 'FeatureCollection', 'features': [feicao_json(fs[fid], proposta[fid], mapa) for fid in inc.feicoes
                                                                if proposta.get(fid) is not None and fid in fs]},
        'vizinhos': {'type': 'FeatureCollection', 'features': viz},
        'envolvidas': envolvidas,
        'historico': [{'estado': estados.ESTADOS.get(h.estado, h.estado), 'usuario': h.usuario, 'dataHora': h.data_hora,
                       'execucaoId': h.execucao_id, 'nota': h.nota} for h in hist],
    }


def decidir(s, lote, ids, decisao, usuario):
    """aprovar | rejeitar | pendente (3.2.4). Devolve (alteradas, recusadas com motivo).

    Em lote (3.2.5), só as elegíveis recebem a decisão; as outras voltam com o motivo."""
    novo = estados.DECISOES[decisao]
    incs = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.id.in_(ids))))
    if not incs:
        raise ErroArquivo('Nenhuma ocorrência encontrada.')
    feitas, recusadas = [], []
    for i in incs:
        if i.status == 'corrigida':
            recusadas.append({'id': i.id, 'motivo': 'já foi corrigida'})
            continue
        if decisao == 'aprovar' and i.op is None:
            recusadas.append({'id': i.id, 'motivo': f'{PADRAO[i.tipo]["nome"]} não tem correção automática'})
            continue
        if decisao != 'aprovar' and i.op is None and decisao == 'rejeitar':
            pass  # rejeitar algo sem correção = ciente, não vai ser corrigido aqui
        i.status = novo
        i.decidido_por, i.decidido_em = usuario.usuario, agora()
        historico(s, i, novo, usuario.usuario, nota='decisão na revisão')
        feitas.append(i)
    if feitas:
        nomes = {'aprovar': 'aprovou a correção de', 'rejeitar': 'rejeitou a correção de', 'pendente': 'deixou pendente'}
        registrar(s, usuario.usuario, 'decisao', f'{usuario.usuario} {nomes[decisao]} {len(feitas)} ocorrência(s): '
                  + ', '.join(f'#{i.id} {PADRAO[i.tipo]["nome"]}' for i in feitas[:20]) + ('…' if len(feitas) > 20 else ''),
                  lote_id=lote.id, versao=lote.versao_atual, fazenda=', '.join(sorted({i.fazenda for i in feitas}))[:300])
    return feitas, recusadas


# ---------- etapa 3: preparação (3.3.1) ----------
AVISOS_OP = {
    'simplificar': 'A simplificação altera contornos e áreas dentro da tolerância configurada.',
    'subtrair': 'O recorte de sobreposição tira área de um dos talhões.',
    'anexar': 'A lacuna é somada inteira a um vizinho.',
    'remover_parte': 'Remover partes separadas apaga área do talhão.',
    'preencher_buraco': 'Preencher buracos soma área ao talhão.',
    'abrir_escape': 'O escape tira do talhão a área do corredor entre o buraco e a borda.',
    'recortar_faixa': 'O recorte da faixa de estrada pode dividir o talhão.',
    'remover_feicao': 'Registros removidos saem da nova versão (continuam na base original).',
    'tornar_valida': 'O reparo de geometria pode mudar a área de laços cruzados.',
}


def preparar_correcao(s, lote, ids):
    incs = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.id.in_(ids))))
    aprovadas = [i for i in incs if i.status == 'aprovada']
    ops = Counter(i.op['tipo'] for i in aprovadas)
    fs = nomes_feicoes(s, {fid for i in incs for fid in i.feicoes})
    mapa = ParaMapa(lote)
    return {
        'aprovadas': [inconsistencia_json(i, fs, mapa) for i in aprovadas],
        'operacoes': [{'tipo': k, 'nome': correcao.DESCRICAO_OP[k], 'quantidade': v} for k, v in ops.items()],
        'naoCorrigiveis': [inconsistencia_json(i, fs, mapa) for i in incs if i.status == 'nao_corrigivel'],
        'rejeitadas': [inconsistencia_json(i, fs, mapa) for i in incs if i.status == 'rejeitada'],
        'pendentes': [inconsistencia_json(i, fs, mapa) for i in incs if i.status in ('identificada', 'em_revisao', 'pendente', 'falha')],
        'saida': f'Lote "{lote.nome}", versão {lote.versao_atual + 1} (arquivo {_seguro(f"{lote.nome}_v{lote.versao_atual + 1}")}.zip). '
                 + ('A base original (v1) continua disponível.' if lote.versao_atual == 1
                    else f'A versão {lote.versao_atual} e a base original (v1) continuam disponíveis.'),
        'avisos': [AVISOS_OP[k] for k in ops if k in AVISOS_OP]
                  + ['Cada correção é validada depois de executada; as que não passarem ficam como "Falha na correção" e não entram na nova versão.'],
    }


# ---------- etapa 3: execução com validação (3.3.2 a 3.3.4) ----------
PROGRESSO = {}  # execucao_id -> {'feito', 'total', 'etapa'} enquanto roda


def iniciar_correcao(lote_id, ids, usuario: Usuario):
    """Cria a execução e roda em segundo plano; a tela acompanha pelo GET da execução."""
    with trava_escrita, Sessao() as s:
        lote = s.get(Lote, lote_id)
        n = s.scalar(select(func.count()).select_from(Inconsistencia).where(
            Inconsistencia.lote_id == lote_id, Inconsistencia.id.in_(ids), Inconsistencia.status == 'aprovada'))
        if not n:
            raise ErroArquivo('Nenhuma correção aprovada nesta seleção. Aprove pelo menos uma na revisão.')
        exe = Execucao(lote_id=lote_id, tipo='correcao', usuario=usuario.usuario, versao_entrada=lote.versao_atual,
                       entrada=f'Lote {lote.id} "{lote.nome}", versão {lote.versao_atual}; {len(ids)} ocorrência(s) revisada(s), {n} aprovada(s)')
        s.add(exe)
        s.commit()
        eid = exe.id
    PROGRESSO[eid] = {'feito': 0, 'total': n, 'etapa': 'Iniciando'}
    threading.Thread(target=_executar, args=(eid, lote_id, list(ids), usuario.usuario), daemon=True).start()
    return eid


def _executar(eid, lote_id, ids, login):
    try:
        with trava_escrita, Sessao() as s:
            usuario = s.scalar(select(Usuario).where(Usuario.usuario == login))
            _corrigir(s, s.get(Execucao, eid), s.get(Lote, lote_id), ids, usuario)
            s.commit()
    except Exception as e:  # noqa: BLE001 - a execução registra a falha e a tela mostra
        traceback.print_exc()
        with trava_escrita, Sessao() as s:
            exe = s.get(Execucao, eid)
            exe.status, exe.fim, exe.erro = 'erro', agora(), f'{type(e).__name__}: {e}'
            registrar(s, login, 'correcao', f'Execução #{eid} interrompida por erro: {e}. Nenhuma alteração foi gravada.', lote_id=lote_id)
            s.commit()
    finally:
        PROGRESSO.pop(eid, None)


def _corrigir(s, exe, lote, ids, usuario):
    prog = PROGRESSO[exe.id]
    incs = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.id.in_(ids))))
    aprovadas = sorted([i for i in incs if i.status == 'aprovada'], key=lambda i: (correcao.ordem(i.op), i.id))
    regras = regras_mod.carregar(s)
    area_min = regras['sobreposicao']['params']['area_min_m2']
    exe.regras = {i.op['tipo']: correcao.DESCRICAO_OP[i.op['tipo']] for i in aprovadas}
    med = medidor(lote)
    fs = nomes_feicoes(s, {fid for i in incs for fid in i.feicoes})
    fazendas_afetadas = sorted({i.fazenda for i in aprovadas})
    prog['etapa'] = 'Carregando geometrias'
    estado = geometrias(s, lote.id, lote.versao_atual, fazendas=fazendas_afetadas, com_linhas=True)
    estado.update(geometrias(s, lote.id, lote.versao_atual, ids={fid for i in aprovadas for fid in i.feicoes}))
    original = dict(estado)
    nova = lote.versao_atual + 1
    corrigidas, falhas = [], []
    for n, i in enumerate(aprovadas, 1):
        prog.update(feito=n - 1, etapa=f'#{i.id} {PADRAO[i.tipo]["nome"]}')
        alvo = i.op['alvo']
        try:
            depois = correcao.aplicar_op(estado, i.op, med)
            ok, checks = correcao.validar(i.op, estado, depois, med, {k: v for k, v in estado.items() if k != alvo},
                                          area_esperada_m2=i.area_m2, area_min_m2=area_min)
        except Exception as e:  # noqa: BLE001 - erro numa operação vira falha dela, não da execução
            ok, checks, depois = False, [{'nome': 'Execução da operação', 'ok': False, 'detalhe': f'{type(e).__name__}: {e}'}], estado
        i.validacao, i.correcao_execucao_id = checks, exe.id
        f = fs.get(alvo)
        faz = exibir_fazenda(i.fazenda, f.fazenda_nome if f else '')
        tal = ', '.join(dict.fromkeys((fs[x].talhao or (f'linha #{fs[x].indice_origem + 1}' if fs[x].linha else '?')) for x in i.feicoes if x in fs))
        if ok:
            registrar(s, usuario.usuario, 'correcao', f'Execução #{exe.id}: {correcao.DESCRICAO_OP[i.op["tipo"]]} ({PADRAO[i.tipo]["nome"]}) — '
                      f'validada. {i.descricao}', lote_id=lote.id, versao=nova, fazenda=faz, talhao=tal,
                      antes=estado.get(alvo), depois=depois.get(alvo))
            estado = depois
            i.status = 'corrigida'
            corrigidas.append(i)
        else:
            motivo = '; '.join(c['detalhe'] for c in checks if not c['ok'])
            registrar(s, usuario.usuario, 'falha', f'Execução #{exe.id}: {correcao.DESCRICAO_OP[i.op["tipo"]]} ({PADRAO[i.tipo]["nome"]}) '
                      f'reprovada na validação e descartada: {motivo}', lote_id=lote.id, versao=nova, fazenda=faz, talhao=tal)
            i.status = 'falha'
            falhas.append(i)
        historico(s, i, i.status, usuario.usuario, exe.id, 'validação aprovada' if ok else 'validação reprovada')

    prog.update(feito=len(aprovadas), etapa='Gravando a nova versão')
    mudou = [fid for fid in estado if fid in original and not _igual(original[fid], estado.get(fid))]
    alteradas = []
    if mudou:
        for fid in mudou:
            g = estado.get(fid)
            s.add(Geometria(feicao_id=fid, versao=nova, wkb=shapely.to_wkb(g, output_dimension=2) if g is not None else None))
        lote.versao_atual = nova
        fazendas = sorted({fs[f].fazenda for f in mudou if f in fs} | set(fazendas_afetadas))
        s.add(Versao(lote_id=lote.id, numero=nova, fazendas=fazendas, n_alteradas=len(mudou), criado_por=usuario.usuario,
                     descricao=f'Execução #{exe.id}: {len(corrigidas)} correção(ões) validada(s), {len(falhas)} falha(s)'))
        fs.update(nomes_feicoes(s, [f for f in mudou if f not in fs]))
        for fid in mudou:
            a, d = original.get(fid), estado.get(fid)
            linha = fs[fid].linha if fid in fs else False
            alteradas.append({'id': fid, 'talhao': fs[fid].talhao if fid in fs else '?', 'fazenda': fs[fid].fazenda if fid in fs else '',
                              'linha': linha, 'areaAntes': round(med.area(a) / 1e4, 4) if not linha else 0,
                              'areaDepois': round(med.area(d) / 1e4, 4) if d is not None and not linha else 0,
                              'verticesAntes': int(shapely.get_num_coordinates(a)),
                              'verticesDepois': int(shapely.get_num_coordinates(d)) if d is not None else 0,
                              'removida': d is None,
                              'ajustes': [correcao.DESCRICAO_OP[i.op['tipo']] for i in corrigidas if i.op['alvo'] == fid]})
        registrar(s, usuario.usuario, 'versao', f'Versão {nova} criada pela execução #{exe.id}: {len(mudou)} feição(ões) alterada(s).',
                  lote_id=lote.id, versao=nova, fazenda=', '.join(fazendas))
    s.flush()

    # reauditoria das fazendas mexidas: confirma o que foi resolvido e acha problemas novos
    prog['etapa'] = 'Reauditando as fazendas'
    reaud = auditar(s, lote, usuario, fazendas=fazendas_afetadas or None, entrada=f'reauditoria após a correção #{exe.id}') \
        if aprovadas else None
    escopo = list(s.scalars(select(Inconsistencia).where(Inconsistencia.lote_id == lote.id, Inconsistencia.fazenda.in_(fazendas_afetadas),
                                                       Inconsistencia.status.in_(estados.ABERTOS))))
    exe.versao_saida = lote.versao_atual
    nome_arq = _seguro(f'{lote.nome}_v{lote.versao_atual}')
    exe.saida = (f'Versão {lote.versao_atual} do lote "{lote.nome}" ({nome_arq}.zip)' if mudou
                 else f'Nenhuma geometria alterada; o lote continua na versão {lote.versao_atual}.')
    exe.fazendas = fazendas_afetadas
    exe.resumo = {
        'aprovadas': len(aprovadas), 'corrigidas': len(corrigidas), 'falhas': len(falhas),
        'pendentes': sum(1 for i in escopo if i.status in ('identificada', 'em_revisao', 'pendente')),
        'semSolucao': sum(1 for i in escopo if i.status in ('nao_corrigivel', 'rejeitada', 'falha')),
        'novosProblemas': reaud.resumo.get('novas', 0) if reaud else 0, 'reauditoria': reaud.id if reaud else None,
        'arquivo': f'{nome_arq}.zip' if mudou else None, 'novaVersao': bool(mudou),
        'alteradas': alteradas,
        'resultados': [{'id': i.id, 'tipo': i.tipo, 'nome': PADRAO[i.tipo]['nome'], 'descricao': i.descricao, 'operacao':
                        correcao.DESCRICAO_OP[i.op['tipo']], 'status': i.status, 'validacao': i.validacao} for i in aprovadas],
    }
    exe.status, exe.fim = 'concluida', agora()
    registrar(s, usuario.usuario, 'correcao', f'Execução #{exe.id} concluída: {len(corrigidas)} corrigida(s), {len(falhas)} falha(s), '
              f'{exe.resumo["semSolucao"]} sem solução, {exe.resumo["pendentes"]} pendente(s). Saída: {exe.saida}',
              lote_id=lote.id, versao=lote.versao_atual, fazenda=', '.join(fazendas_afetadas))


def _igual(a, b):
    if a is None or b is None:
        return a is b
    return shapely.equals_exact(a, b, 0) and shapely.get_num_coordinates(a) == shapely.get_num_coordinates(b)


# ---------- exportação (3.3.4) ----------
def exportar(s, lote, versao, formato='shp', fazenda=None, recalcular_area=True):
    """Shapefile (.zip com uma camada de talhões e, se houver, uma de estradas) ou GeoJSON da versão."""
    versao = versao or lote.versao_atual
    q = select(Feicao).where(Feicao.lote_id == lote.id)
    if fazenda:
        q = q.where(Feicao.fazenda == fazenda)
    fs = list(s.scalars(q.order_by(Feicao.camada_id, Feicao.indice_origem)))
    gs = geometrias(s, lote.id, versao, fazendas=[fazenda] if fazenda else None)
    alteradas = set(s.scalars(select(Geometria.feicao_id).join(Feicao, Feicao.id == Geometria.feicao_id)
                              .where(Feicao.lote_id == lote.id, Geometria.versao > 1, Geometria.versao <= versao)))
    fs = [f for f in fs if f.id in gs]
    camadas = {c.id: c for c in s.scalars(select(Camada).where(Camada.lote_id == lote.id))}
    campo_area = (lote.campos or {}).get('area_ha')
    med = medidor(lote)
    base = _seguro(f'{lote.nome}_v{versao}' + (f'_faz{fazenda}' if fazenda else ''))

    def quadro(sel, colunas):
        linhas = []
        for f in sel:
            a = {c: f.atributos.get(c) for c in colunas}
            if recalcular_area and campo_area in a and f.id in alteradas and not f.linha:
                a[campo_area] = round(med.area(gs[f.id]) / 1e4, 4)
            linhas.append(a)
        return gpd.GeoDataFrame(linhas, columns=colunas, geometry=[gs[f.id] for f in sel], crs=lote.crs or None)

    grupos = {}
    for tipo in ('poligono', 'linha'):
        sel = [f for f in fs if f.linha == (tipo == 'linha')]
        if sel:
            cols = list(dict.fromkeys(c for cid in dict.fromkeys(f.camada_id for f in sel) for c in (camadas[cid].colunas or [])))
            grupos[tipo] = quadro(sel, cols)
    if formato == 'geojson':
        gdf = gpd.GeoDataFrame(__import__('pandas').concat(list(grupos.values()), ignore_index=True), crs=lote.crs or None)
        if gdf.crs is not None:
            gdf = gdf.to_crs('EPSG:4326')
        return f'{base}.geojson', gdf.to_json(drop_id=True).encode('utf-8'), 'application/geo+json'
    pasta = Path(tempfile.mkdtemp(prefix='vt_exp_'))
    try:
        for tipo, gdf in grupos.items():
            nome = base if tipo == 'poligono' else f'{base}_estradas'
            pyogrio.write_dataframe(gdf, pasta / f'{nome}.shp', driver='ESRI Shapefile', encoding='UTF-8')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in sorted(pasta.iterdir()):
                z.write(p, p.name)
        return f'{base}.zip', buf.getvalue(), 'application/zip'
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def _seguro(nome):
    return ''.join(c if c.isalnum() or c in '._-' else '_' for c in nome)
