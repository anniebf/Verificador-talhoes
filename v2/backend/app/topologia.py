"""Etapa 1 (detecção) e as operações de correção usadas nas etapas 2 e 3.

As geometrias ficam no sistema de coordenadas do lote; o `Medidor` converte para metros só para
medir (área, largura, tolerância). Cada inconsistência leva uma `op`: a receita da correção, que é
aplicada sobre a geometria atual na hora (não uma geometria pronta), então várias correções no
mesmo talhão se somam sem uma desfazer a outra.
"""
import re

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely import MultiPoint, MultiPolygon, Point, Polygon

from .regras import ESTRADA, PADRAO

ORDEM_OPS = ['remover_feicao', 'tornar_valida', 'remover_repetidos', 'preencher_buraco', 'remover_parte',
             'simplificar', 'subtrair', 'anexar']


# ---------- geometria ----------
def poligonos(g):
    """Só as partes de área (make_valid e interseções às vezes trazem linhas e pontos junto)."""
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'Polygon':
        return [g]
    if g.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [p for parte in shapely.get_parts(g) for p in poligonos(parte)]
    return []


def juntar(partes):
    partes = [p for p in partes if not p.is_empty]
    if not partes:
        return None
    return partes[0] if len(partes) == 1 else MultiPolygon(partes)


def limpar(g):
    if g is None or g.is_empty:
        return None
    if not g.is_valid:
        g = shapely.make_valid(g)
    return juntar(poligonos(g))


def wkb_hex(g):
    return shapely.to_wkb(g, hex=True, output_dimension=2)


def de_hex(h):
    return shapely.from_wkb(bytes.fromhex(h))


class Medidor:
    """Converte geometrias do lote para metros (UTM estimado) quando o sistema não é métrico."""

    def __init__(self, crs_wkt, crs_metrico):
        self.ida = self.volta = None
        self.fator = 1.0  # unidades do lote por metro (aproximado; só para tolerâncias)
        src = CRS.from_user_input(crs_wkt) if crs_wkt else None
        if src is not None and not _metrico(src) and crs_metrico:
            dst = CRS.from_user_input(crs_metrico)
            self.ida = Transformer.from_crs(src, dst, always_xy=True)
            self.volta = Transformer.from_crs(dst, src, always_xy=True)
            self.fator = 1 / 111320 if src.is_geographic else 1.0

    def _tr(self, t, g):
        if t is None or g is None:
            return g
        return shapely.transform(g, lambda c: np.column_stack(t.transform(c[:, 0], c[:, 1])))

    def m(self, g):
        return self._tr(self.ida, g)

    def de_m(self, g):
        return self._tr(self.volta, g)

    def area(self, g):
        return float(shapely.area(self.m(g))) if g is not None else 0.0

    def tol(self, metros):
        return metros * self.fator

    @property
    def grade(self):
        """Precisão de 1 mm para recortes e uniões: sem ela, contas em graus deixam espinhos e lascas finíssimas."""
        return self.tol(0.001)


def _metrico(crs):
    if not crs.is_projected:
        return False
    try:
        return crs.axis_info[0].unit_name in ('metre', 'meter', 'm')
    except (IndexError, AttributeError):
        return False


def crs_metrico_para(crs_wkt, bounds):
    """EPSG UTM do centro dos dados (ou '' quando o lote já está em metros)."""
    src = CRS.from_user_input(crs_wkt) if crs_wkt else None
    if src is None or _metrico(src):
        return ''
    from pyproj.aoi import AreaOfInterest
    from pyproj.database import query_utm_crs_info
    x0, y0, x1, y1 = bounds
    if not src.is_geographic:
        t = Transformer.from_crs(src, 'EPSG:4326', always_xy=True)
        x0, y0, x1, y1 = t.transform_bounds(x0, y0, x1, y1)
    lon, lat = (x0 + x1) / 2, (y0 + y1) / 2
    info = query_utm_crs_info(datum_name='SIRGAS 2000', area_of_interest=AreaOfInterest(lon, lat, lon, lat)) or \
        query_utm_crs_info(datum_name='WGS 84', area_of_interest=AreaOfInterest(lon, lat, lon, lat))
    return f'EPSG:{info[0].code}' if info else 'EPSG:3857'


def estrada(gm):
    """Faixa estreita e comprida (em metros)?"""
    p = gm.length
    if not p:
        return False
    largura, comprimento = 2 * gm.area / p, p / 2
    return (ESTRADA['largura_min'] <= largura <= ESTRADA['largura_max'] and comprimento >= ESTRADA['comprimento_min']
            and comprimento >= ESTRADA['alongamento_min'] * largura)


MOTIVOS = {
    'Self-intersection': 'Autointerseção no contorno',
    'Ring Self-intersection': 'Anel que toca a si mesmo',
    'Too few points in geometry component': 'Contorno com pontos de menos',
    'Hole lies outside shell': 'Buraco fora do contorno',
    'Nested holes': 'Buracos um dentro do outro',
    'Interior is disconnected': 'Interior desconectado (buraco corta o talhão)',
    'Nested shells': 'Contorno dentro de outro contorno',
    'Duplicate Rings': 'Anel repetido',
    'Invalid Coordinate': 'Coordenada inválida',
    'Ring is not closed': 'Contorno não fechado',
}


def _motivo(g):
    r = shapely.is_valid_reason(g)
    m = re.match(r'([^\[]+)\[([-\d.eE+]+) ([-\d.eE+]+)', r)
    if not m:
        return r, None
    nome = m.group(1).strip()
    return MOTIVOS.get(nome, nome), Point(float(m.group(2)), float(m.group(3)))


def _repetidos(g, tol):
    """Pontos onde há vértices seguidos a menos de `tol` um do outro."""
    pts = []
    for p in poligonos(g) or ([g] if g is not None else []):
        aneis = [p.exterior, *p.interiors] if p.geom_type == 'Polygon' else []
        for r in aneis:
            c = np.asarray(r.coords)[:, :2]
            if len(c) < 3:
                continue
            d = np.hypot(*np.diff(c, axis=0).T)
            pts.extend(c[1:][d <= tol].tolist())
    return pts


def fmt_area(m2):
    return f'{m2 / 1e4:,.2f} ha'.replace(',', 'X').replace('.', ',').replace('X', '.') if m2 >= 1000 else \
        f'{m2:,.1f} m²'.replace(',', 'X').replace('.', ',').replace('X', '.')


# ---------- detecção ----------
def auditar(feicoes, med, regras):
    """feicoes: [{'id','fazenda','aglomerado','talhao','geom'}]. Devolve a lista de inconsistências."""
    ativa = lambda k: regras[k]['params'] if regras.get(k, {}).get('ativo') else None
    out = []
    nome = {f['id']: rotulo(f) for f in feicoes}

    def add(tipo, f, descricao, destaque=None, area=0.0, op=None, outros=()):
        ids = [f['id'], *[o['id'] for o in outros]]
        out.append({'tipo': tipo, 'severidade': PADRAO[tipo]['severidade'], 'fazenda': f['fazenda'], 'feicoes': ids,
                    'descricao': descricao, 'area_m2': float(area), 'destaque': destaque, 'op': op,
                    'assinatura': f"{tipo}|{','.join(map(str, sorted(ids)))}|{round(area)}"})

    # geometria vazia / inválida; G = versão utilizável para as contas topológicas
    G = [None] * len(feicoes)
    # área medida já na versão válida: uma "gravata" tem área assinada zero, mas não é vazia
    limpas = [limpar(f['geom']) for f in feicoes]
    areas_m = shapely.area(med.m(np.array(limpas, dtype=object))) if feicoes else []
    for k, f in enumerate(feicoes):
        g = f['geom']
        if g is None or g.is_empty or limpas[k] is None or not (areas_m[k] >= 0.01):
            if ativa('geometria_vazia') is not None:
                add('geometria_vazia', f, f'{nome[f["id"]]}: sem desenho ou desenho sem área.',
                    destaque=g.centroid if g is not None and not g.is_empty else None,
                    op={'tipo': 'remover_feicao', 'alvo': f['id']})
            continue
        if not g.is_valid:
            motivo, onde = _motivo(g)
            if ativa('autointersecao') is not None:
                add('autointersecao', f, f'{nome[f["id"]]}: {motivo}.', destaque=onde,
                    op={'tipo': 'tornar_valida', 'alvo': f['id']})
            G[k] = limpas[k]
        else:
            G[k] = g

    validos = [k for k, g in enumerate(G) if g is not None]
    GM = {k: gm for k, gm in zip(validos, med.m(np.array([G[k] for k in validos], dtype=object)))} if validos else {}

    if (p := ativa('vertices_duplicados')) is not None:
        tol = med.tol(p['tolerancia_m'])
        for k, f in enumerate(feicoes):
            if f['geom'] is None or f['geom'].is_empty:
                continue
            pts = _repetidos(f['geom'], tol)
            if pts:
                add('vertices_duplicados', f, f'{nome[f["id"]]}: {len(pts)} vértice(s) repetido(s).',
                    destaque=MultiPoint(pts) if len(pts) > 1 else Point(pts[0]),
                    op={'tipo': 'remover_repetidos', 'alvo': f['id'], 'tol': tol})

    if (p := ativa('buraco_interno')) is not None:
        for k in validos:
            for parte in poligonos(G[k]):
                for anel in parte.interiors:
                    furo = Polygon(anel)
                    fm = med.m(furo)
                    if fm.area > p['area_max_ha'] * 1e4 or (p['ignorar_estradas'] and estrada(fm)):
                        continue
                    add('buraco_interno', feicoes[k], f'{nome[feicoes[k]["id"]]}: buraco de {fmt_area(fm.area)}.',
                        destaque=furo, area=fm.area,
                        op={'tipo': 'preencher_buraco', 'alvo': feicoes[k]['id'], 'ponto': _ponto(furo)})

    if (p := ativa('parte_solta')) is not None:
        for k in validos:
            partes = poligonos(G[k])
            if len(partes) < 2:
                continue
            am = [med.area(x) for x in partes]
            principal = int(np.argmax(am))
            for i, (parte, a) in enumerate(zip(partes, am)):
                if i != principal and a <= p['area_max_ha'] * 1e4:
                    add('parte_solta', feicoes[k], f'{nome[feicoes[k]["id"]]}: parte solta de {fmt_area(a)}.',
                        destaque=parte, area=a,
                        op={'tipo': 'remover_parte', 'alvo': feicoes[k]['id'], 'ponto': _ponto(parte)})

    if (p := ativa('excesso_vertices')) is not None:
        for k in validos:
            nv = int(shapely.get_num_coordinates(G[k]))
            gm = GM[k]
            if not (nv > p['max_vertices'] or (nv > 50 and gm.length / nv < p['espacamento_min_m'])):
                continue
            gs = limpar(shapely.simplify(gm, p['tolerancia_m'], preserve_topology=True))
            if gs is None:
                continue
            nv1 = int(shapely.get_num_coordinates(gs))
            var = (gs.area - gm.area) / gm.area * 100 if gm.area else 0
            if nv1 >= nv:
                continue
            ok = abs(var) <= p['variacao_area_max_pct']
            txt = (f'{nome[feicoes[k]["id"]]}: {nv:,} vértices → {nv1:,} simplificando com {p["tolerancia_m"]} m '
                   f'(área {var:+.2f}%).').replace(',', '.')
            if not ok:
                txt += ' A área mudaria acima do limite: revise à mão.'
            add('excesso_vertices', feicoes[k], txt, area=gm.area,
                op={'tipo': 'simplificar', 'alvo': feicoes[k]['id'], 'tol_m': p['tolerancia_m']} if ok else None)

    arr = np.array([G[k] for k in validos], dtype=object)
    arvore = shapely.STRtree(arr) if validos else None

    if (p := ativa('sobreposicao')) is not None and validos:
        a_idx, b_idx = arvore.query(arr, predicate='intersects')
        sel = a_idx < b_idx
        a_idx, b_idx = a_idx[sel], b_idx[sel]
        if len(a_idx):
            gma = np.array([GM[validos[i]] for i in a_idx], dtype=object)
            gmb = np.array([GM[validos[i]] for i in b_idx], dtype=object)
            areas = shapely.area(shapely.intersection(gma, gmb))
            for i, j, a in zip(a_idx, b_idx, areas):
                if a < p['area_min_m2']:
                    continue
                ka, kb = validos[i], validos[j]
                inter = limpar(shapely.intersection(G[ka], G[kb]))
                if inter is None:
                    continue
                maior = ka if GM[ka].area >= GM[kb].area else kb
                alvo = maior if p['recortar_de'] == 'maior' else (kb if maior == ka else ka)
                outro = kb if alvo == ka else ka
                fa, fb = feicoes[alvo], feicoes[outro]
                add('sobreposicao', fa, f'{nome[fa["id"]]} e {nome[fb["id"]]} se sobrepõem em {fmt_area(a)}. '
                    f'Proposta: recortar de {nome[fa["id"]]}.', destaque=inter, area=a, outros=[fb],
                    op={'tipo': 'subtrair', 'alvo': fa['id'], 'area': wkb_hex(inter)})

    if (p := ativa('lacuna')) is not None and validos:
        eps = med.tol(0.05)
        por_faz = {}
        for k in validos:
            por_faz.setdefault(feicoes[k]['fazenda'], []).append(k)
        for faz, ks in por_faz.items():
            if len(ks) < 2:
                continue
            uniao = shapely.union_all(np.array([G[k] for k in ks], dtype=object))
            for parte in poligonos(uniao):
                for anel in parte.interiors:
                    vao = Polygon(anel)
                    vm = med.m(vao)
                    if not (p['area_min_m2'] <= vm.area <= p['area_max_m2']) or (p['ignorar_estradas'] and estrada(vm)):
                        continue
                    viz = [validos[i] for i in arvore.query(vao.buffer(eps), predicate='intersects')]
                    viz = [k for k in viz if feicoes[k]['fazenda'] == faz]
                    if len(viz) < 2:
                        continue  # vão cercado por um talhão só: é buraco interno
                    borda = vao.exterior.buffer(eps)
                    divisa = shapely.length(shapely.intersection(borda, np.array([G[k].boundary for k in viz], dtype=object)))
                    # quem só encosta na ponta do vão não conta como vizinho
                    ordem = [viz[i] for i in np.argsort(-divisa) if divisa[i] >= med.tol(0.5)]
                    if len(ordem) < 2:
                        continue
                    alvo = feicoes[ordem[0]]
                    add('lacuna', alvo, f'Lacuna de {fmt_area(vm.area)} entre {", ".join(nome[feicoes[k]["id"]] for k in ordem[:4])}'
                        f'{"…" if len(ordem) > 4 else ""}. Proposta: juntar a {nome[alvo["id"]]}.',
                        destaque=vao, area=vm.area, outros=[feicoes[k] for k in ordem[1:]],
                        op={'tipo': 'anexar', 'alvo': alvo['id'], 'area': wkb_hex(vao)})

    if ativa('atributo_ausente') is not None:
        for f in feicoes:
            faltam = [n for n, v in (('fazenda', f['fazenda']), ('talhão', f['talhao'])) if not v]
            if faltam:
                add('atributo_ausente', f, f'{nome[f["id"]]}: sem {" e sem ".join(faltam)}.',
                    destaque=_ponto_g(f['geom']))

    if ativa('talhao_duplicado') is not None:
        grupos = {}
        for f in feicoes:
            if f['talhao']:
                grupos.setdefault((f['fazenda'], f['aglomerado'], f['talhao']), []).append(f)
        for (faz, agl, tal), fs in grupos.items():
            if len(fs) > 1:
                add('talhao_duplicado', fs[0], f'Talhão {tal} aparece {len(fs)} vezes na fazenda {faz or "?"}'
                    f'{f" (aglomerado {agl})" if agl else ""}.', outros=fs[1:])
    return out


def rotulo(f):
    return f"Talhão {f['talhao'] or '?'}" + (f" ({f['aglomerado']})" if f.get('aglomerado') else '')


def _ponto(g):
    p = g.representative_point()
    return [p.x, p.y]


def _ponto_g(g):
    return g.representative_point() if g is not None and not g.is_empty else None


# ---------- correção ----------
def aplicar_ops(geoms, ops, med):
    """geoms: {id: geometria}. Aplica as ops em ORDEM_OPS e devolve {id: nova geometria ou None}."""
    g = dict(geoms)
    for op in sorted(ops, key=lambda o: ORDEM_OPS.index(o['tipo'])):
        alvo = op['alvo']
        atual = g.get(alvo)
        if op['tipo'] == 'remover_feicao':
            g[alvo] = None
            continue
        if atual is None:
            continue
        t = op['tipo']
        if t == 'tornar_valida':
            novo = limpar(atual)
        elif t == 'remover_repetidos':
            novo = limpar(shapely.remove_repeated_points(atual, op['tol']))
        elif t == 'preencher_buraco':
            pt = Point(op['ponto'])
            novo = juntar([Polygon(p.exterior, [r for r in p.interiors if not Polygon(r).contains(pt)])
                           for p in poligonos(atual)])
        elif t == 'remover_parte':
            pt = Point(op['ponto'])
            sobra = [p for p in poligonos(atual) if not p.contains(pt)]
            novo = juntar(sobra) if sobra else atual
        elif t == 'simplificar':
            novo = limpar(med.de_m(shapely.simplify(med.m(atual), op['tol_m'], preserve_topology=True)))
        elif t == 'subtrair':
            novo = limpar(shapely.difference(atual, de_hex(op['area']), grid_size=med.grade))
        elif t == 'anexar':
            novo = limpar(shapely.union(atual, de_hex(op['area']), grid_size=med.grade))
        else:
            raise ValueError(f'operação desconhecida: {t}')
        # recortes deixam lascas de área quase zero: não viram "parte solta" na próxima auditoria
        if novo is not None:
            novo = juntar([p for p in poligonos(novo) if med.area(p) >= 0.01]) or novo
        g[alvo] = novo
    return g


DESCRICAO_OP = {
    'remover_feicao': 'Registro removido',
    'tornar_valida': 'Geometria refeita válida',
    'remover_repetidos': 'Vértices repetidos removidos',
    'preencher_buraco': 'Buraco preenchido',
    'remover_parte': 'Parte solta removida',
    'simplificar': 'Contorno simplificado',
    'subtrair': 'Área sobreposta recortada',
    'anexar': 'Lacuna incorporada',
}
