"""Correção do shapefile em etapas: simplificar, corrigir erros e gerar o .zip.

Nada muda no arquivo sem estar no `plano` que o usuário aprovou na tela. O plano é sempre
aplicado sobre o arquivo original, então voltar uma etapa e mudar de ideia não deixa sujeira.
Talhões que não estão no plano são copiados byte a byte (desenho e tabela de atributos).
"""
import csv
import io
import re
import struct
import threading
import time
import uuid
import zipfile
from collections import defaultdict

import numpy as np
import shapely
from shapely import MultiPolygon, Polygon

from analise import ErroArquivo, anel_ha, area_assinada

MUITOS_VERTICES = 1000      # acima disso o talhão é marcado como pesado
ESPACAMENTO_MIN = 5         # m: vértices mais próximos que isso, em média, também contam como "muitos"
VARIACAO_MAX = 0.5          # %: se a área mudar mais que isso a simplificação não é recomendada
AREA_MIN = 1                # m²: desenho com menos que isso é tratado como sem área
VALIDADE = 3600             # s que a correção fica guardada no servidor
MAX_SESSOES = 6

_sessoes, _trava = {}, threading.Lock()


# ---------- geometria ----------
def poligonos(g):
    """Só as partes de área (descarta linhas e pontos que o make_valid às vezes cria)."""
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'Polygon':
        return [g]
    if g.geom_type not in ('MultiPolygon', 'GeometryCollection'):
        return []
    return [p for parte in shapely.get_parts(g) for p in poligonos(parte)]


def limpar(g, sobra=0.0):
    if g is None or g.is_empty:
        return None
    if not g.is_valid:
        g = shapely.make_valid(g)
    partes = [p for p in poligonos(g) if p.area > sobra]
    if not partes:
        return None
    return partes[0] if len(partes) == 1 else MultiPolygon(partes)


def vertices(g):
    return int(shapely.get_num_coordinates(g)) if g is not None else 0


def ler_registro(shp, p):
    """Anéis (em graus/coordenadas do arquivo) de um registro do .shp a partir do seu offset."""
    c = p + 8
    if struct.unpack('<i', shp[c:c + 4])[0] not in (5, 15, 25):
        return []
    nparts, npts = struct.unpack('<ii', shp[c + 36:c + 44])
    inicios = list(struct.unpack('<%di' % nparts, shp[c + 44:c + 44 + 4 * nparts])) + [npts]
    off = c + 44 + 4 * nparts
    pts = np.frombuffer(shp, dtype='<f8', count=2 * npts, offset=off).reshape(-1, 2)
    return [pts[inicios[i]:inicios[i + 1]] for i in range(nparts)]


def ha_real(aneis_graus, geografico):
    """Área em ha calculada como o verificador (contornos menos buracos)."""
    return sum(-anel_ha(r, geografico) if area_assinada(r) > 0 else anel_ha(r, geografico) for r in aneis_graus if len(r) >= 4)


class Sessao:
    def __init__(self, ctx, resultado):
        self.criada = time.time()
        self.cache_simp, self.cache_ha = {}, {}  # cada etapa refaz o plano; não precisa refazer as contas
        self.nome_zip, self.nome_shp = ctx['nome_zip'], ctx['nome_shp']
        self.shp, self.dbf, self.prj, self.cpg = ctx['shp'], ctx['dbf'], ctx['prj'], ctx['cpg']
        self.campos, self.geografico = ctx['campos'], ctx['geografico']
        self.tipo = struct.unpack('<i', self.shp[32:36])[0]  # 5 = Polygon, 15 = PolygonZ, 25 = PolygonM
        self.offsets = []
        p = 100
        while p + 8 <= len(self.shp):
            self.offsets.append(p)
            p += 8 + struct.unpack('>i', self.shp[p + 4:p + 8])[0] * 2
        # PolygonZ pode vir com ou sem M; descobre pelo tamanho do primeiro registro
        self.tem_m = self.tipo == 25
        for p in self.offsets:
            c, clen = p + 8, struct.unpack('>i', self.shp[p + 4:p + 8])[0] * 2
            if self.tipo == 15 and struct.unpack('<i', self.shp[c:c + 4])[0] == 15:
                np_, npts = struct.unpack('<ii', self.shp[c + 36:c + 44])
                self.tem_m = clen > 44 + 4 * np_ + 16 * npts + 16 + 8 * npts
                break
        geoms = ctx['geoms']
        # tudo em metros: x e y multiplicados por um fator fixo para o arquivo inteiro
        if self.geografico:
            lats = [g[0][0][1] for g in geoms if g and len(g[0])]
            lat0 = sum(lats) / len(lats) if lats else 0
            self.kx, self.ky = 111320 * np.cos(np.radians(lat0)), 110574.0
        else:
            self.kx = self.ky = 1.0
        self.G0 = [self.montar(g) if g else None for g in geoms]
        self.nv = [sum(len(r) for r in g) for g in geoms]
        self.muitos = [i for i, g in enumerate(self.G0) if g is not None and (
            self.nv[i] > MUITOS_VERTICES or (self.nv[i] > 50 and g.length / self.nv[i] < ESPACAMENTO_MIN))]
        # nomes para mostrar
        nomes = resultado.get('nomesFazendas', {})
        safra = next((c['name'] for c in self.campos if c['name'].upper() == 'SAFRA'), None)
        self.rot = {}
        for it in resultado['items'] + resultado['outros']:
            self.rot[it['idx']] = {'idx': it['idx'], 'faz': it['faz'], 'fazNome': it.get('fazNome') or nomes.get(it['faz'], ''),
                                   'tal': it['tal'], 'safra': analise_limpo(ctx['linhas'][it['idx']].get(safra, '')) if safra else ''}

    # anéis do registro original
    def aneis(self, i):
        return ler_registro(self.shp, self.offsets[i])

    def montar(self, aneis_graus):
        escala = np.array([self.kx, self.ky])
        rs = [np.asarray(r, dtype=float) * escala for r in aneis_graus if len(r) >= 4]
        ext = [r for r in rs if area_assinada(r) < 0]
        furos = [r for r in rs if area_assinada(r) > 0]
        if not ext:  # contorno no sentido invertido
            ext, furos = furos, []
        if not ext:
            return None
        cascas = [Polygon(e) for e in ext]
        dos = [[] for _ in ext]
        for f in furos:
            pt = Polygon(f).representative_point()
            dono = next((k for k, c in enumerate(cascas) if c.is_valid and c.contains(pt)), None)
            dos[dono if dono is not None else max(range(len(cascas)), key=lambda k: cascas[k].area)].append(f)
        ps = [Polygon(e, dos[k]) for k, e in enumerate(ext)]
        return limpar(ps[0] if len(ps) == 1 else MultiPolygon(ps))

    def graus(self, g):
        return shapely.transform(g, lambda c: c / np.array([self.kx, self.ky]))

    def simplificado(self, i, g, tol, mudancas=()):
        """Simplificação do talhão i, guardada: a prévia e todas as etapas usam a mesma conta."""
        chave = (i, tol, mudancas)
        if chave not in self.cache_simp:
            if len(self.cache_simp) > 20000:
                self.cache_simp.clear()
            self.cache_simp[chave] = limpar(shapely.simplify(g, tol, preserve_topology=True)) if g is not None else None
        return self.cache_simp[chave]

    def aneis_graus(self, g):
        return [np.asarray(r.coords) for p in poligonos(self.graus(g)) for r in [p.exterior, *p.interiors]] if g is not None else []

    def ha_antes(self, i):
        if i not in self.cache_ha:
            self.cache_ha[i] = ha_real(self.aneis(i), self.geografico)
        return self.cache_ha[i]

    def ha_de(self, g):
        return ha_real(self.aneis_graus(g), self.geografico) if g is not None else 0.0

    def para_json(self, g, tol=0.0):
        """Polígonos em [lon, lat] para o mapa; `tol` (m) deixa o desenho mais leve."""
        if g is None:
            return []
        if tol:  # só para desenhar: o modo rápido basta
            g = shapely.simplify(g, tol, preserve_topology=False)
        casas = 7 if self.geografico else 2
        out = []
        for p in poligonos(self.graus(g)):
            out.append([np.round(np.asarray(r.coords), casas).tolist() for r in [p.exterior, *p.interiors]])
        return out

    def rotulo(self, i):
        return self.rot.get(i) or {'idx': i, 'faz': '', 'fazNome': '', 'tal': f'Linha {i + 1}', 'safra': ''}

    def nome_talhao(self, i):
        r = self.rotulo(i)
        return f"talhão {r['tal']} ({r['fazNome'] or 'fazenda ' + r['faz']})"


def analise_limpo(v):
    return '' if not v or v.upper() == 'NULL' else v


# ---------- sessões ----------
def criar_sessao(ctx, resultado):
    s = Sessao(ctx, resultado)
    s.outros = resultado['outros']  # contornos dos talhões sem problema: vão para a tela depois, à parte
    sid = uuid.uuid4().hex
    with _trava:
        agora = time.time()
        for k in [k for k, v in _sessoes.items() if agora - v.criada > VALIDADE]:
            del _sessoes[k]
        while len(_sessoes) >= MAX_SESSOES:
            del _sessoes[min(_sessoes, key=lambda k: _sessoes[k].criada)]
        _sessoes[sid] = s
    return sid, {
        'total': sum(s.nv),
        'muitos': len(s.muitos),
        'muitosIdx': {i: s.nv[i] for i in s.muitos},
        # desenhos sem área: não dá para consertar, só apagar o registro
        'semArea': [i for i, g in enumerate(s.G0) if g is None or g.area < AREA_MIN],
        'limite': MUITOS_VERTICES,
        'espacamento': ESPACAMENTO_MIN,
        'podeCorrigir': s.tipo in (5, 15, 25),
        'motivo': '' if s.tipo in (5, 15, 25) else 'A correção só funciona com camadas de polígono.',
    }


def pegar(sid):
    with _trava:
        s = _sessoes.get(sid)
    if not s:
        raise ErroArquivo('A correção deste arquivo expirou (ela fica guardada por 1 hora). Envie o .zip de novo para continuar.')
    return s


# ---------- plano aprovado ----------
def ler_plano(p):
    p = p or {}
    ids = lambda k: {int(x) for x in p.get(k) or []}
    mapa = lambda k: {int(i): {int(x) for x in v} for i, v in (p.get(k) or {}).items() if v}
    return {
        'tol': max(0.1, min(float(p.get('tolerancia') or 1), 50)),
        'simplificar': ids('simplificar'),
        'buracos': mapa('buracos'),       # idx -> anéis (ri) de buracos para preencher
        'partes': mapa('partes'),         # idx -> anéis (ri) de partes soltas para apagar
        'invertidos': ids('invertidos'),  # idx com contorno invertido para regravar
        'remover': ids('remover'),        # idx de registros sem desenho para apagar
        'recalcularArea': bool(p.get('recalcularArea')),
    }


def aplicar(s, plano):
    """Geometrias (em metros) depois do plano e a lista do que mudou em cada talhão."""
    G, mudou = list(s.G0), defaultdict(list)
    for i in plano['remover']:
        G[i] = None
        mudou[i].append('Registro sem desenho apagado')
    for i in set(plano['buracos']) | set(plano['partes']) | plano['invertidos']:
        if i in plano['remover']:
            continue
        tirar = plano['buracos'].get(i, set()) | plano['partes'].get(i, set())
        G[i] = s.montar([r for k, r in enumerate(s.aneis(i)) if k not in tirar])
        if plano['buracos'].get(i):
            n = len(plano['buracos'][i])
            mudou[i].append(f'{n} buraco{"s" if n > 1 else ""} preenchido{"s" if n > 1 else ""}')
        if plano['partes'].get(i):
            n = len(plano['partes'][i])
            mudou[i].append(f'{n} parte{"s" if n > 1 else ""} solta{"s" if n > 1 else ""} apagada{"s" if n > 1 else ""}')
        if i in plano['invertidos']:
            mudou[i].append('Sentido do contorno corrigido')
    for i in plano['simplificar']:
        if G[i] is not None:
            antes = vertices(G[i])
            mexido = (tuple(sorted(plano['buracos'].get(i, ()))), tuple(sorted(plano['partes'].get(i, ()))), i in plano['invertidos'])
            G[i] = s.simplificado(i, G[i], plano['tol'], mexido if any(mexido) else ())
            mudou[i].append(f'Simplificado ({antes} → {vertices(G[i])} vértices)')
    return G, mudou


# ---------- etapa 1: simplificar ----------
def previa_simplificacao(s, tol, escopo):
    tol = max(0.1, min(float(tol), 50))
    alvo = s.muitos if escopo == 'muitos' else [i for i, g in enumerate(s.G0) if g is not None]
    out = []
    for i in alvo:
        g = s.G0[i]
        gs = s.simplificado(i, g, tol)
        a0, a1 = g.area, gs.area if gs is not None else 0
        var = (a1 - a0) / a0 * 100 if a0 else 0
        nv1 = vertices(gs)
        out.append({**s.rotulo(i), 'nv0': s.nv[i], 'nv1': nv1, 'ha0': a0 / 1e4, 'ha1': a1 / 1e4, 'var': var,
                    'muitos': i in s.muitos,
                    'recomendado': gs is not None and abs(var) <= VARIACAO_MAX and nv1 < s.nv[i]})
    return {'tolerancia': tol, 'variacaoMax': VARIACAO_MAX, 'talhoes': out}


def previa_talhao(s, i, tol):
    g = s.G0[i]
    gs = s.simplificado(i, g, max(0.1, min(float(tol), 50)))
    return {'antes': s.para_json(g), 'depois': s.para_json(gs), 'nv0': s.nv[i], 'nv1': vertices(gs)}


# ---------- etapa 3: resumo e arquivo ----------
def resumo(s, plano):
    G, mudou = aplicar(s, plano)
    nv_depois = sum(vertices(G[i]) if i in mudou else s.nv[i] for i in range(len(G)) if i not in plano['remover'])
    lista = []
    for i in sorted(mudou, key=lambda k: (s.rotulo(k)['fazNome'] or s.rotulo(k)['faz'], s.rotulo(k)['tal'])):
        antes, depois = s.ha_antes(i), s.ha_de(G[i])
        # geo: como o talhão fica, para o mapa da revisão (um pouco mais leve que o arquivo)
        lista.append({**s.rotulo(i), 'mudancas': mudou[i], 'ha0': antes, 'ha1': depois, 'geo': s.para_json(G[i], 0.3)})
    return {
        'alterados': len(mudou), 'removidos': len(plano['remover']),
        'nvAntes': sum(s.nv), 'nvDepois': nv_depois,
        'nomeArquivo': nome_saida(s), 'lista': lista,
        'camposArea': [c['name'] for c in campos_area(s)],
    }


def nome_saida(s):
    base = re.sub(r'\.zip$', '', s.nome_zip, flags=re.I)
    return f'{base}_simplificado.zip'


def campos_area(s):
    return [c for c in s.campos if c['name'].upper() in ('HA', 'AREA', 'AREA_HA') and c['type'] in 'CNF']


def codificar(s, g):
    """Conteúdo de um registro do mesmo tipo do arquivo: contorno no sentido horário, buracos no
    anti-horário. Em PolygonZ/M os vértices novos ficam com Z = 0 e M = 0."""
    if g is None:
        return struct.pack('<i', 0)
    gg = shapely.orient_polygons(s.graus(g), exterior_cw=True)
    aneis = [np.asarray(r.coords, dtype='<f8') for p in poligonos(gg) for r in [p.exterior, *p.interiors]]
    pts = np.vstack(aneis)
    partes, n = [], 0
    for r in aneis:
        partes.append(n)
        n += len(r)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    corpo = (struct.pack('<i4d2i', s.tipo, x0, y0, x1, y1, len(aneis), len(pts))
             + struct.pack('<%di' % len(partes), *partes) + pts.astype('<f8').tobytes())
    zeros = struct.pack('<2d', 0, 0) + bytes(8 * len(pts))
    if s.tipo == 15:
        corpo += zeros + (zeros if s.tem_m else b'')
    elif s.tipo == 25:
        corpo += zeros
    return corpo


def formatar_area(campo, original, ha_antes, ha_depois):
    """Novo valor no mesmo formato e unidade do original; None se não der para saber a unidade."""
    texto = original.decode('latin1').strip()
    try:
        v = float(texto.replace(',', '.'))
    except ValueError:
        return None
    if not ha_antes:
        return None
    razao = v / ha_antes
    if 0.8 < razao < 1.25:
        novo = ha_depois
    elif 8000 < razao < 12500:
        novo = ha_depois * 1e4
    else:
        return None
    if campo['type'] in 'NF':
        txt = f"{novo:.{campo['dec']}f}"
        return txt.rjust(campo['len']).encode('latin1') if len(txt) <= campo['len'] else None
    m = re.match(r'^-?\d+([.,])(\d+)$', texto)
    sep, casas = (m.group(1), len(m.group(2))) if m else ('.', 2)
    txt = f'{novo:.{casas}f}'.replace('.', sep)
    return txt.ljust(campo['len']).encode('latin1') if len(txt) <= campo['len'] else None


def gerar_zip(s, plano):
    if s.tipo not in (5, 15, 25):
        raise ErroArquivo('A correção só funciona com camadas de polígono.')
    G, mudou = aplicar(s, plano)
    n = len(s.offsets)
    # .shp e .shx
    corpos = []
    for i in range(n):
        if i in plano['remover']:
            continue
        if i in mudou:
            corpos.append(codificar(s, G[i]))
        else:
            p = s.offsets[i]
            clen = struct.unpack('>i', s.shp[p + 4:p + 8])[0] * 2
            corpos.append(s.shp[p + 8:p + 8 + clen])
    caixas = [struct.unpack('<4d', c[4:36]) for c in corpos if len(c) >= 36 and struct.unpack('<i', c[:4])[0] != 0]
    caixa = (min(b[0] for b in caixas), min(b[1] for b in caixas), max(b[2] for b in caixas), max(b[3] for b in caixas)) if caixas else (0, 0, 0, 0)
    shp, shx, pos = io.BytesIO(), io.BytesIO(), 50  # posições em palavras de 16 bits
    tam_shp = 50 + sum(4 + len(c) // 2 for c in corpos)
    cab = lambda tam: (struct.pack('>7i', 9994, 0, 0, 0, 0, 0, tam) + struct.pack('<2i4d', 1000, s.tipo, *caixa)
                       + s.shp[68:100])  # faixas de Z e M como no original
    shp.write(cab(tam_shp))
    shx.write(cab(50 + 4 * len(corpos)))
    for k, c in enumerate(corpos, 1):
        shp.write(struct.pack('>2i', k, len(c) // 2) + c)
        shx.write(struct.pack('>2i', pos, len(c) // 2))
        pos += 4 + len(c) // 2
    # .dbf: cabeçalho e linhas originais; só muda a contagem e, se aprovado, a área
    hlen, rlen = struct.unpack('<HH', s.dbf[8:12])
    pos_campo, p = {}, 1
    for c in s.campos:
        pos_campo[c['name']] = p
        p += c['len']
    areas = campos_area(s) if plano['recalcularArea'] else []
    avisos_area = []
    linhas = []
    for i in range(n):
        if i in plano['remover']:
            continue
        reg = bytearray(s.dbf[hlen + i * rlen: hlen + (i + 1) * rlen])
        if areas and i in mudou and G[i] is not None:
            antes, depois = s.ha_antes(i), s.ha_de(G[i])
            for c in areas:
                a = pos_campo[c['name']]
                novo = formatar_area(c, bytes(reg[a:a + c['len']]), antes, depois)
                if novo is None:
                    avisos_area.append((i, c['name']))
                else:
                    reg[a:a + c['len']] = novo
        linhas.append(bytes(reg))
    cab_dbf = bytearray(s.dbf[:hlen])
    cab_dbf[4:8] = struct.pack('<I', len(linhas))
    dbf = bytes(cab_dbf) + b''.join(linhas) + b'\x1a'
    # relatório
    rel = io.StringIO()
    w = csv.writer(rel, delimiter=';')
    w.writerow(['Fazenda', 'Nome da fazenda', 'Talhão', 'Safra', 'Alterações', 'Área antes (ha)', 'Área depois (ha)'])
    for i in sorted(mudou):
        r = s.rotulo(i)
        antes, depois = s.ha_antes(i), s.ha_de(G[i])
        obs = list(mudou[i]) + [f'Campo {c} não recalculado (unidade desconhecida)' for k, c in avisos_area if k == i]
        w.writerow([r['faz'], r['fazNome'], r['tal'], r['safra'], ' | '.join(obs), f'{antes:.4f}'.replace('.', ','), f'{depois:.4f}'.replace('.', ',')])
    base = re.sub(r'\.shp$', '', s.nome_shp.split('/')[-1], flags=re.I) + '_simplificado'
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(base + '.shp', shp.getvalue())
        z.writestr(base + '.shx', shx.getvalue())
        z.writestr(base + '.dbf', dbf)
        if s.prj:
            z.writestr(base + '.prj', s.prj)
        if s.cpg:
            z.writestr(base + '.cpg', s.cpg)
        z.writestr('relatorio_alteracoes.csv', '﻿' + rel.getvalue())
    return nome_saida(s), saida.getvalue()
