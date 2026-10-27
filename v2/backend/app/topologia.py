"""Etapa 1: detecção das inconsistências. Este módulo só lê geometrias, nunca altera dados.

As geometrias ficam no sistema de coordenadas do lote; o `Medidor` converte para metros só para
medir (área, largura, tolerância). Cada ocorrência leva:
- `classificacao`: confirmado, suspeito ou inconclusivo;
- `motivo`: por que a regra apontou, com os números que ela usou;
- `caracteristicas`: medidas da geometria relevantes para a revisão;
- `op`: a receita da correção proposta (aplicada pelo módulo `correcao`), ou None quando só cabe
  intervenção manual.
"""
import re

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely import LineString, MultiPoint, MultiPolygon, Point, Polygon

from .regras import ALONGAMENTO_ESTRADA, PADRAO


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


def linhas(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'LineString':
        return [g]
    if g.geom_type in ('MultiLineString', 'GeometryCollection'):
        return [p for parte in shapely.get_parts(g) for p in linhas(parte)]
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

    def comprimento(self, g):
        return float(shapely.length(self.m(g))) if g is not None else 0.0

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


def forma(gm):
    """Largura média (2·área/perímetro) e comprimento (perímetro/2) de uma área, em metros."""
    p = gm.length
    if not p:
        return 0.0, 0.0
    return 2 * gm.area / p, p / 2


def eh_estrada(largura, comprimento, p):
    return (p['largura_min_m'] <= largura <= p['largura_max_m'] and comprimento >= p['comprimento_min_m']
            and comprimento >= ALONGAMENTO_ESTRADA * largura)


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


def fmt_m(m):
    return f'{m:,.1f} m'.replace(',', 'X').replace('.', ',').replace('X', '.')


def rotulo(f):
    if f.get('linha'):
        return f"Linha #{f['indice'] + 1}" + (f" ({f['camada']})" if f.get('camada') else '')
    return f"Talhão {f['talhao'] or '?'}"


def _ponto(g):
    p = g.representative_point()
    return [p.x, p.y]


# borda a menos disso de outro talhão é divisa (inclui vãos finos entre talhões, que são lacunas)
DIVISA_M = 3.0


def _corredor(p0, p1, largura_m, centro_furo):
    """Faixa de `largura_m` de p0 (no buraco) a p1 (na borda), passando um pouco das duas pontas para o corte
    abrir de fato (borda inclinada não deixa lasca). Em metros."""
    (x0, y0), (x1, y1) = p0, p1
    if float(np.hypot(x1 - x0, y1 - y0)) < 1e-6:  # buraco encostado na borda: sai do centro do buraco para fora
        x0, y0 = centro_furo
    dx, dy = x1 - x0, y1 - y0
    n = float(np.hypot(dx, dy)) or 1.0
    ux, uy = dx / n, dy / n
    ext = largura_m + 1.0
    return LineString([(x0 - ux * ext, y0 - uy * ext), (x1 + ux * ext, y1 + uy * ext)]).buffer(largura_m / 2, cap_style='flat')


PONTA_M2 = 5.0  # pedacinho que o corte separa numa borda inclinada: sai junto com o corredor


def abre(parte_m, furo_m, canal_m):
    """O corredor abre o buraco sem dividir o talhão? (mesma conta da correção, em metros)"""
    novo = poligonos(shapely.difference(parte_m, canal_m))
    novo = [x for x in novo if x.area >= PONTA_M2] if len(novo) > 1 else novo
    pt = furo_m.representative_point()
    return len(novo) == 1 and not any(Polygon(r).contains(pt) for r in novo[0].interiors) and not novo[0].contains(pt)


def escape(parte, anel, vizinhos, med, largura_m, fechados=None, ja=()):
    """Corredor (no sistema do lote) que liga o buraco `anel` à borda de `parte` pelo caminho mais curto.

    Só sai pela borda livre: que não encosta em outro talhão nem dá para um vão cercado de talhões
    (`fechados`), para o escape não abrir dentro do vizinho nem virar lacuna. O caminho tem de ficar dentro
    do talhão e abrir o buraco sem dividir o talhão, contando os escapes já propostos nele (`ja`, em metros).
    Devolve (corredor, comprimento em metros), ou (None, motivo) quando não há saída automática.
    """
    pm, am = med.m(parte), med.m(anel)
    furo_m = Polygon(am)
    borda = pm.exterior
    dentro = Polygon(borda).buffer(0.01)
    centro = shapely.centroid(furo_m).coords[0]
    # os buracos do próprio talhão também são vãos fechados da fazenda: só contam os que ficam fora dele
    fora = shapely.difference(med.m(fechados), Polygon(borda).buffer(-0.5)) if fechados is not None else None
    tampa = [med.m(v).boundary for v in vizinhos] + ([fora] if fora is not None and not fora.is_empty else [])
    if tampa:
        divisa = shapely.union_all(np.array(tampa, dtype=object)).buffer(DIVISA_M)
        solta = shapely.difference(borda, divisa)
        if solta.is_empty:
            return None, 'o talhão não tem borda livre (toda a borda encosta em outro talhão)'
    else:
        solta = borda
    caminho = shapely.shortest_line(am, solta)
    if not dentro.covers(caminho):
        return None, 'o caminho mais curto até a borda livre passa por fora do talhão'
    canal = _corredor(*caminho.coords, largura_m, centro)
    atual = shapely.difference(pm, shapely.union_all(list(ja))) if ja else pm
    if not abre(atual, furo_m, canal):
        return None, 'o corredor até a borda livre dividiria o talhão em partes'
    return canal, float(caminho.length)


# ---------- detecção ----------
def auditar(feicoes, med, regras, fazendas_alvo=None):
    """feicoes: [{'id','fazenda','aglomerado','talhao','linha','indice','camada','geom'}].

    `fazendas_alvo`: numa reauditoria parcial, as linhas de estrada entram todas como contexto,
    mas só geram ocorrências próprias quando a fazenda delas está no alvo.
    """
    ativa = lambda k: regras[k]['params'] if regras.get(k, {}).get('ativo') else None
    out = []
    nome = {f['id']: rotulo(f) for f in feicoes}
    no_alvo = lambda f: fazendas_alvo is None or f['fazenda'] in fazendas_alvo

    def add(tipo, f, descricao, motivo, classificacao, destaque=None, area=0.0, op=None, outros=(), carac=None, extra_assin=''):
        ids = [f['id'], *[o['id'] for o in outros]]
        out.append({'tipo': tipo, 'severidade': PADRAO[tipo]['severidade'], 'fazenda': f['fazenda'], 'feicoes': ids,
                    'descricao': descricao, 'motivo': motivo, 'classificacao': classificacao,
                    'caracteristicas': {k: v for k, v in (carac or {}).items() if v is not None},
                    'area_m2': float(area), 'destaque': destaque, 'op': op,
                    'assinatura': f"{tipo}|{','.join(map(str, sorted(ids)))}|{round(area)}{extra_assin}"})

    pol = [f for f in feicoes if not f.get('linha')]
    lin = [f for f in feicoes if f.get('linha')]
    est = ativa('corte_estrada')

    # ---- A. vazias e inválidas; G = versão utilizável para as contas topológicas
    G = [None] * len(pol)
    limpas = [limpar(f['geom']) for f in pol]
    areas_m = shapely.area(med.m(np.array(limpas, dtype=object))) if pol else []
    for k, f in enumerate(pol):
        g = f['geom']
        if g is None or g.is_empty or limpas[k] is None or not (areas_m[k] >= 0.01):
            if ativa('geometria_vazia') is not None:
                vazio = g is None or g.is_empty
                add('geometria_vazia', f, f'{nome[f["id"]]}: {"sem desenho" if vazio else "desenho sem área"}.',
                    'A geometria está vazia.' if vazio else f'A área válida do desenho é {fmt_area(float(areas_m[k] or 0))} (menos de 0,01 m²): é um risco ou um ponto.',
                    'confirmado', destaque=g.centroid if not vazio else None,
                    op={'tipo': 'remover_feicao', 'alvo': f['id']}, carac={'vertices': int(shapely.get_num_coordinates(g)) if g is not None else 0})
            continue
        if not g.is_valid:
            motivo, onde = _motivo(g)
            if ativa('autointersecao') is not None:
                add('autointersecao', f, f'{nome[f["id"]]}: {motivo}.',
                    f'O validador de geometrias (GEOS) recusou o desenho: "{shapely.is_valid_reason(g)}".', 'confirmado',
                    destaque=onde, op={'tipo': 'tornar_valida', 'alvo': f['id']},
                    carac={'motivo_geos': shapely.is_valid_reason(g), 'partes_apos_reparo': len(poligonos(limpas[k])),
                           'area_apos_reparo_ha': round(float(areas_m[k]) / 1e4, 4)})
            G[k] = limpas[k]
        else:
            G[k] = g

    validos = [k for k, g in enumerate(G) if g is not None]
    # partes e buracos de um desenho inválido só fazem sentido depois do reparo (a reauditoria pega)
    integros = [k for k in validos if pol[k]['geom'].is_valid]
    GM = {k: gm for k, gm in zip(validos, med.m(np.array([G[k] for k in validos], dtype=object)))} if validos else {}

    if (p := ativa('vertices_duplicados')) is not None:
        tol = med.tol(p['tolerancia_m'])
        for f in pol:
            if f['geom'] is None or f['geom'].is_empty:
                continue
            pts = _repetidos(f['geom'], tol)
            if pts:
                add('vertices_duplicados', f, f'{nome[f["id"]]}: {len(pts)} vértice(s) repetido(s).',
                    f'Há {len(pts)} vértice(s) a menos de {p["tolerancia_m"]} m do vértice anterior.', 'confirmado',
                    destaque=MultiPoint(pts) if len(pts) > 1 else Point(pts[0]),
                    op={'tipo': 'remover_repetidos', 'alvo': f['id'], 'tol': tol},
                    carac={'repetidos': len(pts), 'vertices': int(shapely.get_num_coordinates(f['geom']))})

    # ---- B. múltiplas partes (e faixas de estrada entre elas)
    if (p := ativa('parte_solta')) is not None:
        for k in integros:
            partes = poligonos(G[k])
            if len(partes) < 2:
                continue
            am = [med.area(x) for x in partes]
            total = sum(am)
            principal = int(np.argmax(am))
            pm = med.m(partes[principal])
            for i, (parte, a) in enumerate(zip(partes, am)):
                if i == principal:
                    continue
                f = pol[k]
                xm = med.m(parte)
                vao = float(xm.distance(pm))
                if est is not None and est['largura_min_m'] <= vao <= est['largura_max_m']:
                    junto = float(shapely.length(shapely.intersection(xm.exterior, pm.buffer(vao + 1))))
                    if junto >= est['comprimento_min_m']:
                        add('corte_estrada', f, f'{nome[f["id"]]}: parte separada do resto por uma faixa de {fmt_m(vao)} ao longo de {fmt_m(junto)}.',
                            f'O vão entre a parte e o contorno principal tem largura entre {est["largura_min_m"]} e {est["largura_max_m"]} m '
                            f'e acompanha a borda por pelo menos {est["comprimento_min_m"]} m: formato de estrada.', 'suspeito',
                            destaque=parte, area=a, carac={'largura_m': round(vao, 1), 'comprimento_m': round(junto), 'onde': 'entre partes'})
                        continue
                razao = a / total * 100 if total else 0
                pode = a <= p['area_max_ha'] * 1e4 and razao <= p['razao_max_pct']
                motivo = (f'O talhão tem {len(partes)} componentes separados; este tem {fmt_area(a)} ({razao:.1f}% do talhão). ')
                motivo += (f'Está dentro dos critérios para propor a remoção (até {p["area_max_ha"]} ha e {p["razao_max_pct"]}%).' if pode
                           else f'Fora dos critérios de remoção (até {p["area_max_ha"]} ha e {p["razao_max_pct"]}%): avaliar à mão.')
                add('parte_solta', f, f'{nome[f["id"]]}: parte separada de {fmt_area(a)} ({razao:.1f}% do talhão).', motivo, 'suspeito',
                    destaque=parte, area=a, op={'tipo': 'remover_parte', 'alvo': f['id'], 'ponto': _ponto(parte)} if pode else None,
                    carac={'partes': len(partes), 'area_parte_ha': round(a / 1e4, 4), 'percentual_do_talhao': round(razao, 2),
                           'distancia_ao_principal_m': round(vao, 1)})

    arr = np.array([G[k] for k in validos], dtype=object)
    arvore = shapely.STRtree(arr) if validos else None

    # vãos cercados pelos talhões de uma fazenda (como na regra de lacuna), para o escape não sair neles
    cache_vaos = {}

    def vaos_fechados(faz):
        if faz not in cache_vaos:
            ks = [k for k in validos if pol[k]['fazenda'] == faz]
            uniao = shapely.union_all(np.array([G[k] for k in ks], dtype=object)) if len(ks) > 1 else None
            aneis = [a for pt in poligonos(uniao) for a in pt.interiors]
            cache_vaos[faz] = shapely.multilinestrings(aneis) if aneis else None
        return cache_vaos[faz]

    # ---- C. buracos (os com formato de estrada vão para D). Todo buraco precisa de um escape até a borda
    if (p := ativa('buraco_interno')) is not None:
        for k in integros:
            f = pol[k]
            vizinhos = [G[validos[i]] for i in arvore.query(G[k], predicate='dwithin', distance=med.tol(DIVISA_M)) if validos[i] != k]
            fechados = vaos_fechados(f['fazenda'])
            ja = []  # escapes já propostos neste talhão (em metros)
            for parte in poligonos(G[k]):
                for anel in parte.interiors:
                    furo = Polygon(anel)
                    fm = med.m(furo)
                    larg, comp = forma(fm)
                    if est is not None and eh_estrada(larg, comp, est):
                        add('corte_estrada', f, f'{nome[f["id"]]}: faixa vazia de {fmt_m(larg)} x {fmt_m(comp)} atravessando o talhão.',
                            f'Buraco estreito e comprido (largura {larg:.1f} m, comprimento {comp:.0f} m, {comp / max(larg, .1):.0f}x mais comprido que largo).',
                            'suspeito', destaque=furo, area=fm.area,
                            carac={'largura_m': round(larg, 1), 'comprimento_m': round(comp), 'area_ha': round(fm.area / 1e4, 4), 'onde': 'dentro do talhão'})
                        continue
                    canal_m, comp_esc = escape(parte, anel, vizinhos, med, p['largura_escape_m'], fechados, ja)
                    if canal_m is None:
                        canal = None
                        motivo = (f'Anel interno de {fmt_area(fm.area)} fechado dentro do talhão. Todo buraco precisa de um escape, mas '
                                  f'{comp_esc}: abrir o escape à mão.')
                    else:
                        ja.append(canal_m)
                        canal = med.de_m(canal_m)
                        motivo = (f'Anel interno de {fmt_area(fm.area)} fechado dentro do talhão. Todo buraco precisa de um escape: '
                                  f'proposta de abrir um corredor de {fmt_m(p["largura_escape_m"])} por {fmt_m(comp_esc)} até a borda, '
                                  'pelo caminho mais curto, saindo numa borda livre (que não encosta em outro talhão).')
                    add('buraco_interno', f, f'{nome[f["id"]]}: buraco de {fmt_area(fm.area)} sem escape.', motivo,
                        'suspeito', destaque=furo, area=fm.area,
                        op={'tipo': 'abrir_escape', 'alvo': f['id'], 'ponto': _ponto(furo), 'faixa': wkb_hex(canal)} if canal is not None else None,
                        carac={'area_ha': round(fm.area / 1e4, 4), 'perimetro_m': round(fm.length), 'largura_media_m': round(larg, 1),
                               'percentual_do_talhao': round(fm.area / GM[k].area * 100, 2) if GM[k].area else None,
                               'comprimento_escape_m': round(comp_esc, 1) if canal is not None else None})

    # ---- desempenho: excesso de vértices
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
            f = pol[k]
            add('excesso_vertices', f, f'{nome[f["id"]]}: {nv:,} vértices → {nv1:,} com tolerância de {p["tolerancia_m"]} m (área {var:+.2f}%).'.replace(',', '.'),
                f'{nv} vértices, um a cada {gm.length / nv:.1f} m (limite: {p["max_vertices"]} vértices ou {p["espacamento_min_m"]} m). '
                + ('A simplificação muda a área dentro do limite.' if ok else f'A simplificação mudaria a área em {var:+.2f}% (limite {p["variacao_area_max_pct"]}%): avaliar à mão.'),
                'suspeito', area=gm.area,
                op={'tipo': 'simplificar', 'alvo': f['id'], 'tol_m': p['tolerancia_m'], 'var_max': p['variacao_area_max_pct']} if ok else None,
                carac={'vertices': nv, 'vertices_depois': nv1, 'espacamento_medio_m': round(gm.length / nv, 2), 'variacao_area_pct': round(var, 3)})

    # ---- E. sobreposições
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
                fa, fb = pol[alvo], pol[outro]
                add('sobreposicao', fa, f'{nome[fa["id"]]} e {nome[fb["id"]]} se sobrepõem em {fmt_area(a)}. Proposta: recortar de {nome[fa["id"]]}.',
                    f'A interseção entre os dois tem {fmt_area(a)} (mínimo para apontar: {p["area_min_m2"]} m²).', 'confirmado',
                    destaque=inter, area=a, outros=[fb], op={'tipo': 'subtrair', 'alvo': fa['id'], 'area': wkb_hex(inter), 'outro': fb['id']},
                    carac={'area_m2': round(float(a), 2), 'percentual_alvo': round(a / GM[alvo].area * 100, 2),
                           'percentual_outro': round(a / GM[outro].area * 100, 2),
                           'fazendas': sorted({fa['fazenda'], fb['fazenda']})})

    # ---- E. lacunas (vão fechado entre vizinhos; faixas de estrada vão para D)
    if (p := ativa('lacuna')) is not None and validos:
        eps = med.tol(0.05)
        por_faz = {}
        for k in validos:
            por_faz.setdefault(pol[k]['fazenda'], []).append(k)
        for faz, ks in por_faz.items():
            if len(ks) < 2:
                continue
            uniao = shapely.union_all(np.array([G[k] for k in ks], dtype=object))
            for parte in poligonos(uniao):
                for anel in parte.interiors:
                    vao = Polygon(anel)
                    vm = med.m(vao)
                    if not (p['area_min_m2'] <= vm.area <= p['area_max_m2']):
                        continue
                    viz = [validos[i] for i in arvore.query(vao.buffer(eps), predicate='intersects')]
                    viz = [k for k in viz if pol[k]['fazenda'] == faz]
                    if len(viz) < 2:
                        continue  # vão cercado por um talhão só: é buraco interno
                    borda = vao.exterior.buffer(eps)
                    divisa = shapely.length(shapely.intersection(borda, np.array([G[k].boundary for k in viz], dtype=object)))
                    # quem só encosta na ponta do vão não conta como vizinho
                    ordem = [viz[i] for i in np.argsort(-divisa) if divisa[i] >= med.tol(0.5)]
                    if len(ordem) < 2:
                        continue
                    # buraco com escape aberto na divisa: quase todo o contorno é de um talhão só
                    if sorted(divisa, reverse=True)[1] <= med.tol(5.0) and sorted(divisa, reverse=True)[0] >= 0.9 * vao.exterior.length:
                        continue
                    larg, comp = forma(vm)
                    alvo = pol[ordem[0]]
                    vizinhos = ', '.join(nome[pol[k]['id']] for k in ordem[:4]) + ('…' if len(ordem) > 4 else '')
                    if est is not None and eh_estrada(larg, comp, est):
                        add('corte_estrada', alvo, f'Faixa de {fmt_m(larg)} x {fmt_m(comp)} entre {vizinhos}.',
                            'Vão entre talhões estreito e comprido: formato de estrada ou carreador.', 'suspeito',
                            destaque=vao, area=vm.area, outros=[pol[k] for k in ordem[1:]],
                            carac={'largura_m': round(larg, 1), 'comprimento_m': round(comp), 'onde': 'entre talhões'})
                        continue
                    fino = larg < (est['largura_min_m'] if est else 3)
                    add('lacuna', alvo, f'Lacuna de {fmt_area(vm.area)} entre {vizinhos}. Proposta: juntar a {nome[alvo["id"]]}.',
                        f'Vão de {fmt_area(vm.area)} totalmente cercado por {len(ordem)} talhões da fazenda; largura média {larg:.1f} m. '
                        + ('Faixa muito fina: típico de desenho que não fechou.' if fino else 'Pode ser área sem plantio: confira.'),
                        'confirmado' if fino else 'suspeito', destaque=vao, area=vm.area, outros=[pol[k] for k in ordem[1:]],
                        op={'tipo': 'anexar', 'alvo': alvo['id'], 'area': wkb_hex(vao)},
                        carac={'area_m2': round(vm.area, 2), 'largura_media_m': round(larg, 2), 'comprimento_m': round(comp),
                               'vizinhos': len(ordem)})

    # ---- D. estradas da camada de linhas atravessando talhões
    if est is not None and lin and validos:
        for f in lin:
            g = f['geom']
            if g is None or g.is_empty:
                continue
            for i in arvore.query(g, predicate='intersects'):
                k = validos[i]
                dentro = shapely.intersection(g, G[k])
                comp = med.comprimento(dentro)
                if comp < est['cruzamento_min_m']:
                    continue
                t = pol[k]
                add('corte_estrada', t, f'{nome[f["id"]]} atravessa {nome[t["id"]]} por {fmt_m(comp)}.',
                    f'A linha da camada de estradas passa {comp:.0f} m por dentro do talhão (mínimo para apontar: {est["cruzamento_min_m"]} m).',
                    'suspeito', destaque=dentro, outros=[f],
                    op={'tipo': 'recortar_faixa', 'alvo': t['id'], 'linha': wkb_hex(dentro), 'largura_m': est['largura_faixa_m']}
                    if est['propor_recorte'] else None,
                    carac={'comprimento_dentro_m': round(comp), 'largura_faixa_m': est['largura_faixa_m'], 'onde': 'camada de estradas'},
                    extra_assin=f'|{round(comp)}')

    # ---- F. linhas: pontas desconectadas e segmentos isolados
    pd_, pi_ = ativa('estrada_desconectada'), ativa('segmento_isolado')
    if lin and (pd_ is not None or pi_ is not None):
        partes = [(f, j, ln) for f in lin for j, ln in enumerate(linhas(f['geom']))]
        if len(partes) > 1:
            arv = shapely.STRtree(np.array([ln for _, _, ln in partes], dtype=object))
            tol = med.tol(max((pd_ or pi_)['tolerancia_m'], (pi_ or pd_)['tolerancia_m']))
            alcance = med.tol(pd_['distancia_max_m']) if pd_ is not None else tol

            def vizinhas(g, raio, idx):
                return [(partes[x][2].distance(g), x) for x in arv.query(g.buffer(raio)) if x != idx]

            for idx, (f, j, ln) in enumerate(partes):
                if not no_alvo(f):
                    continue
                desconexoes = []
                if pd_ is not None and not ln.is_closed:
                    for ext, pt in ((0, Point(ln.coords[0])), (-1, Point(ln.coords[-1]))):
                        perto = vizinhas(pt, alcance, idx)
                        if not perto or min(perto)[0] <= tol:
                            continue  # ponta ligada, ou estrada sem saída longe de tudo
                        d, x = min(perto)
                        if d <= alcance:
                            desconexoes.append((ext, pt, d, x))
                for ext, pt, d, x in desconexoes:
                    dm = d / med.fator
                    alvo_ln = partes[x][2]
                    destino = alvo_ln.interpolate(alvo_ln.project(pt))
                    outro = partes[x][0]
                    add('estrada_desconectada', f, f'{nome[f["id"]]}: ponta a {fmt_m(dm)} de {nome[outro["id"]]} sem encostar.',
                        f'A ponta para a {dm:.1f} m de outra linha (tolerância {pd_["tolerancia_m"]} m, procura até {pd_["distancia_max_m"]} m).',
                        'suspeito', destaque=LineString([pt, destino]), outros=[outro] if outro['id'] != f['id'] else (),
                        op={'tipo': 'conectar', 'alvo': f['id'], 'parte': j, 'extremidade': ext, 'ponto': [destino.x, destino.y]},
                        carac={'distancia_m': round(dm, 2)}, extra_assin=f'|{j}|{ext}')
                # isolada: nada encosta nem está perto o bastante para ser uma desconexão (circuitos fechados contam)
                if pi_ is not None and not desconexoes and not any(d <= alcance for d, _ in vizinhas(ln, alcance, idx)):
                    add('segmento_isolado', f, f'{nome[f["id"]]}: segmento de {fmt_m(med.comprimento(ln))} sem ligação com outra linha.',
                        f'Nenhuma outra linha a menos de {alcance / med.fator:.1f} m deste segmento.', 'inconclusivo', destaque=ln,
                        carac={'comprimento_m': round(med.comprimento(ln)), 'fechado': ln.is_closed}, extra_assin=f'|{j}')

    # ---- atributos (só talhões)
    if ativa('atributo_ausente') is not None:
        for f in pol:
            # "#nome": tem o nome da fazenda, mas falta o código; continua sendo atributo ausente
            sem_codigo = f['fazenda'].startswith('#')
            faltam = [n for n, falta in (('código da fazenda' if sem_codigo else 'fazenda', not f['fazenda'] or sem_codigo),
                                         ('talhão', not f['talhao'])) if falta]
            if faltam:
                g = f['geom']
                add('atributo_ausente', f, f'{nome[f["id"]]}: sem {" e sem ".join(faltam)}.',
                    f'Os campos escolhidos para {" e ".join(faltam)} estão vazios ou não foram definidos para o lote.', 'confirmado',
                    destaque=g.representative_point() if g is not None and not g.is_empty else None)

    if ativa('talhao_duplicado') is not None:
        grupos = {}
        for f in pol:
            if f['talhao']:
                grupos.setdefault((f['fazenda'], f['aglomerado'], f['talhao']), []).append(f)
        for (faz, agl, tal), fs in grupos.items():
            if len(fs) > 1:
                rot_faz = f'{faz[1:]} (sem código)' if faz.startswith('#') else (faz or '?')
                add('talhao_duplicado', fs[0], f'Talhão {tal} aparece {len(fs)} vezes na fazenda {rot_faz}{f" (aglomerado {agl})" if agl else ""}.',
                    f'{len(fs)} registros com a mesma combinação fazenda/aglomerado/talhão. Pode ser talhão dividido de propósito.',
                    'suspeito', outros=fs[1:], carac={'registros': len(fs)})
    return out
