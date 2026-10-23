"""Leitura de shapefile (.shp/.dbf) dentro de um .zip e busca de talhões com
buracos internos, partes soltas ou geometria inválida.

Os anéis são arrays numpy (n, 2) e as contas pesadas usam numpy/Shapely: um arquivo com
centenas de milhares de vértices é lido e analisado em poucos segundos."""
import collections
import io
import math
import re
import struct
import unicodedata
import zipfile

import numpy as np
import shapely

LIMITE_DESCOMPACTADO = 1024 * 1024 * 1024  # 1 GB


class ErroArquivo(Exception):
    """Erro com mensagem pronta para mostrar ao usuário."""


def ler_dbf(dados, encoding):
    n, hlen, rlen = struct.unpack('<IHH', dados[4:12])
    campos = []
    p = 32
    while p < hlen - 1 and dados[p] != 0x0D:
        nome = dados[p:p + 11].split(b'\0')[0].decode('latin1')
        campos.append({'name': nome, 'type': chr(dados[p + 11]), 'len': dados[p + 16], 'dec': dados[p + 17]})
        p += 32
    linhas = []
    for r in range(n):
        pos = hlen + r * rlen + 1
        linha = {}
        for c in campos:
            linha[c['name']] = dados[pos:pos + c['len']].decode(encoding, errors='replace').strip()
            pos += c['len']
        linhas.append(linha)
    return campos, linhas


def ler_shp(dados):
    geoms = []
    p = 100
    while p + 8 <= len(dados):
        clen = struct.unpack('>i', dados[p + 4:p + 8])[0] * 2
        c = p + 8
        tipo = struct.unpack('<i', dados[c:c + 4])[0]
        if tipo not in (5, 15, 25):
            geoms.append([])
            p = c + clen
            continue
        nparts, npts = struct.unpack('<ii', dados[c + 36:c + 44])
        inicios = list(struct.unpack('<%di' % nparts, dados[c + 44:c + 44 + 4 * nparts])) + [npts]
        pts = np.frombuffer(dados, dtype='<f8', count=2 * npts, offset=c + 44 + 4 * nparts).reshape(-1, 2)
        geoms.append([pts[inicios[i]:inicios[i + 1]] for i in range(nparts)])
        p = c + clen
    return geoms


def area_assinada(r):
    r = np.asarray(r, dtype=float)
    if len(r) < 3:
        return 0.0
    x, y = r[:, 0], r[:, 1]
    return float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) / 2


def centro(r):
    r = np.asarray(r, dtype=float)
    return r[:max(len(r) - 1, 1)].mean(axis=0).tolist()


def anel_ha(r, geografico):
    if not geografico:
        return abs(area_assinada(r)) / 1e4
    lat = centro(r)[1]
    kx, ky = 111320 * math.cos(math.radians(lat)), 110574
    return abs(area_assinada(r)) * kx * ky / 1e4


# Possível estrada: faixa estreita e comprida. Buraco com até 20 m de largura, 100 m de
# comprimento e 20x mais comprido que largo; ou parte solta separada do talhão por um
# vão de 1 a 20 m ao longo de pelo menos 100 m.
ESTRADA_LARGURA_MAX = 20
ESTRADA_COMPRIMENTO_MIN = 100
ESTRADA_ALONGAMENTO_MIN = 20


def conversor_metros(geografico, ref):
    if not geografico:
        return lambda r: r
    lon0, lat0 = ref
    escala = np.array([111320 * math.cos(math.radians(lat0)), 110574])
    return lambda r: (np.asarray(r, dtype=float) - [lon0, lat0]) * escala


def perimetro(r):
    r = np.asarray(r, dtype=float)
    return float(np.hypot(*np.diff(r, axis=0).T).sum()) if len(r) > 1 else 0.0


def estrada_no_buraco(r_m):
    p = perimetro(r_m)
    if not p:
        return None
    largura, comprimento = 2 * abs(area_assinada(r_m)) / p, p / 2
    if (largura <= ESTRADA_LARGURA_MAX and comprimento >= ESTRADA_COMPRIMENTO_MIN
            and comprimento >= ESTRADA_ALONGAMENTO_MIN * max(largura, 0.1)):
        return {'largura': round(largura, 1), 'comprimento': round(comprimento)}
    return None


def estrada_entre_partes(parte_m, principal_m, passo=10, alcance=30):
    """Mede o vão entre a parte solta e o talhão principal ao longo da borda."""
    if len(parte_m) < 2 or len(principal_m) < 2:
        return None
    # pontos a cada `passo` m na borda da parte e a distância de cada um até o talhão principal
    borda = shapely.linestrings(parte_m)
    pontos = shapely.line_interpolate_point(borda, np.arange(0, borda.length, passo))
    d = shapely.distance(pontos, shapely.linestrings(principal_m))
    perto = np.sort(d[d <= alcance])
    if not len(perto):
        return None
    vao, junto = float(perto[len(perto) // 2]), len(perto) * passo
    if 1 <= vao <= ESTRADA_LARGURA_MAX and junto >= ESTRADA_COMPRIMENTO_MIN:
        return {'largura': round(vao, 1), 'comprimento': round(junto)}
    return None


def simplificar(r, tol):
    """Douglas-Peucker (feito pelo Shapely): tira vértices que mudam o desenho menos que `tol`.
    Só para mostrar no mapa; a análise usa sempre o desenho original."""
    r = np.asarray(r, dtype=float)
    if len(r) <= 5:
        return r
    novo = shapely.get_coordinates(shapely.simplify(shapely.linestrings(r), tol, preserve_topology=False))
    return novo if len(novo) >= 4 else r


def escolher_campo(campos, nomes):
    maiusc = [c['name'].upper() for c in campos]
    for n in nomes:
        if n in maiusc:
            return campos[maiusc.index(n)]
    return None


def simples(texto):
    """Para comparar nomes: sem acento, sem diferença de maiúscula e de espaços."""
    sem_acento = ''.join(c for c in unicodedata.normalize('NFKD', texto) if not unicodedata.combining(c))
    return ' '.join(sem_acento.casefold().split())


def limpar(v):
    return '' if v is None or v == '' or v.upper() == 'NULL' else v


def ler_tabela_fazendas(caminho):
    """Lê fazendas.csv (codigo;nome). Linhas sem nome são ignoradas."""
    nomes = {}
    try:
        with open(caminho, encoding='utf-8-sig') as f:
            for linha in f:
                partes = [p.strip() for p in linha.replace(',', ';', 1).split(';')]
                if len(partes) >= 2 and partes[0] and partes[1] and partes[0].lower() != 'codigo':
                    nomes[partes[0]] = partes[1]
    except FileNotFoundError:
        pass
    return nomes


def analisar(campos, linhas, geoms, geografico, nome_arquivo, nomes_fazendas=None):
    f_tal = escolher_campo(campos, ['TALHAO', 'TALHÃO', 'ID_TALHAO', 'COD_TALHAO', 'TAL', 'NOME']) or campos[0]
    f_faz = escolher_campo(campos, ['FAZ', 'COD_FAZ', 'CD_FAZ', 'ID_FAZ', 'FAZENDA', 'DE_FAZ'])
    # Nome da fazenda: FAZENDA é o nome principal (é a coluna que a equipe mantém); DE_FAZ vem
    # junto quando diz outra coisa, porque às vezes FAZENDA é o grupo e DE_FAZ a fazenda em si
    # (ex.: "MALU · EMA"). Sem FAZENDA, vale só o DE_FAZ.
    f_principal = [f for f in (escolher_campo(campos, [n]) for n in ['FAZENDA', 'NOME_FAZ', 'NM_FAZ']) if f and f is not f_faz]
    f_detalhe = escolher_campo(campos, ['DE_FAZ'])
    if f_detalhe is f_faz:
        f_detalhe = None
    f_setor = escolher_campo(campos, ['SETOR'])
    f_ha = escolher_campo(campos, ['HA', 'AREA', 'AREA_HA'])
    valor = lambda linha, f: limpar(linha.get(f['name'], '')) if f else ''

    # em cada código de fazenda vale o nome mais usado nos talhões dele; fazendas.csv passa na frente
    conta_principal, conta_detalhe = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for linha in linhas:
        cod = valor(linha, f_faz) or 'Sem fazenda'
        p = next((valor(linha, f) for f in f_principal if valor(linha, f)), '')
        d = valor(linha, f_detalhe)
        if p and '�' not in p:
            conta_principal[cod][p] += 1
        if d and '�' not in d:
            conta_detalhe[cod][d] += 1
    nomes_fazendas = nomes_fazendas or {}
    mais_usado = lambda c: c.most_common(1)[0][0] if c else ''

    def nome_da(cod):
        if nomes_fazendas.get(cod):
            return nomes_fazendas[cod]
        p, d = mais_usado(conta_principal[cod]), mais_usado(conta_detalhe[cod])
        if p and d and simples(p) != simples(d):
            return f'{p} · {d}'
        return p or d
    casas = 6 if geografico else 1  # ~10 cm; deixa a resposta bem menor
    tolerancia = 1.5 / 111320 if geografico else 1.5  # o desenho do mapa pode errar até ~1,5 m
    arred = lambda r, tol=tolerancia: np.round(simplificar(r, tol), casas).tolist()

    itens, outros = [], []
    for idx, linha in enumerate(linhas):
        g = geoms[idx] if idx < len(geoms) else []
        # ri = posição do anel no registro; a etapa de correção usa para saber qual anel mexer
        # anéis com menos de 0,01 m² são riscos/pontos: não contam como contorno nem buraco
        validos = [i for i, r in enumerate(g) if anel_ha(r, geografico) >= 1e-6]
        externos_i = [i for i in validos if area_assinada(g[i]) < 0]  # shapefile: sentido horário = contorno
        buracos_i = [i for i in validos if area_assinada(g[i]) > 0]
        invalido = ''
        if not g:
            invalido = 'Sem desenho (geometria vazia)'
        elif not validos:
            invalido = 'Desenho sem área (um risco ou um ponto)'
        elif not externos_i:
            invalido = 'Contorno com sentido invertido'
            externos_i, buracos_i = buracos_i, []
        externos, buracos = [g[i] for i in externos_i], [g[i] for i in buracos_i]
        areas = [anel_ha(r, geografico) for r in externos]
        principal = areas.index(max(areas)) if areas else -1
        extras = [(r, a, externos_i[i]) for i, (r, a) in enumerate(zip(externos, areas)) if i != principal]
        faz = valor(linha, f_faz) or 'Sem fazenda'
        if not buracos and not extras and not invalido:
            # talhão sem problema: vai só o contorno, para aparecer no mapa
            outros.append({'idx': idx, 'faz': faz, 'tal': valor(linha, f_tal) or f'Linha {idx + 1}',
                           'r': [arred(r, tolerancia * 2) for r in externos]})  # só contorno no mapa; pode ser mais leve
            continue
        m = conversor_metros(geografico, centro(externos[principal]) if externos else [0, 0])
        principal_m = m(externos[principal]) if externos else []
        itens.append({
            'idx': idx,
            'faz': faz,
            'fazNome': nome_da(faz),
            'tal': valor(linha, f_tal) or f'Linha {idx + 1}',
            'setor': valor(linha, f_setor),
            'ha': valor(linha, f_ha),
            'invalid': invalido,
            'outers': [arred(r) for r in externos],
            'main': principal,
            'holes': [{'r': arred(r), 'ri': ri, 'ha': anel_ha(r, geografico), 'c': centro(r), 'estrada': estrada_no_buraco(m(r))} for r, ri in zip(buracos, buracos_i)],
            'extra': [{'r': arred(r), 'ri': ri, 'ha': a, 'c': centro(r), 'estrada': estrada_entre_partes(m(r), principal_m)} for r, a, ri in extras],
        })

    fazendas = {valor(l, f_faz) or 'Sem fazenda' for l in linhas}
    return {
        'semNome': sorted({i['faz'] for i in itens if not i['fazNome']}, key=lambda c: (len(c), c)),
        'fileName': nome_arquivo,
        'total': len(linhas),
        'totalFarms': len(fazendas),
        'geographic': geografico,
        'fieldTal': {'name': f_tal['name'], 'type': f_tal['type']},
        'fieldFaz': {'name': f_faz['name'], 'type': f_faz['type']} if f_faz else None,
        'nHoles': sum(1 for i in itens for h in i['holes'] if not h['estrada']),
        'nExtra': sum(1 for i in itens for e in i['extra'] if not e['estrada']),
        'nRoads': sum(1 for i in itens for x in i['holes'] + i['extra'] if x['estrada']),
        'nInvalid': sum(1 for i in itens if i['invalid']),
        'items': itens,
        'outros': outros,
        'nomesFazendas': {f: nome_da(f) for f in fazendas if nome_da(f)},
    }


def verificar_zip(conteudo, nome_arquivo, nomes_fazendas=None):
    ctx = abrir_zip(conteudo, nome_arquivo)
    return analisar(ctx['campos'], ctx['linhas'], ctx['geoms'], ctx['geografico'], ctx['nome'], nomes_fazendas)


def abrir_zip(conteudo, nome_arquivo):
    """Lê o .zip e devolve tudo o que a análise e a correção precisam, inclusive os bytes originais."""
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile:
        raise ErroArquivo('Não consegui abrir esse .zip. Confira se ele não está corrompido.')
    nomes = [i.filename for i in z.infolist() if not i.is_dir() and not i.filename.startswith('__MACOSX')]
    if sum(i.file_size for i in z.infolist()) > LIMITE_DESCOMPACTADO:
        raise ErroArquivo('O .zip é grande demais (mais de 1 GB descompactado).')
    shps = [n for n in nomes if n.lower().endswith('.shp')]
    if not shps:
        raise ErroArquivo('Não achei nenhum arquivo .shp dentro do .zip. Confira se você compactou a camada com todos os arquivos dela.')
    shp = shps[0]
    base = shp[:-4].lower()
    achar = lambda ext: next((n for n in nomes if n.lower() == base + ext), None)
    dbf = achar('.dbf')
    if not dbf:
        raise ErroArquivo(f'Achei {shp}, mas falta o arquivo .dbf com o mesmo nome. Ele guarda a tabela de atributos.')
    prj = z.read(achar('.prj')).decode('latin1') if achar('.prj') else ''
    cpg = z.read(achar('.cpg')).decode('latin1').strip() if achar('.cpg') else ''
    encoding = 'utf-8' if re.match(r'utf-?8', cpg, re.I) else (cpg.lower() or 'latin1')
    try:
        ''.encode(encoding)
    except LookupError:
        encoding = 'latin1'
    try:
        dbf_bytes, shp_bytes = z.read(dbf), z.read(shp)
        campos, linhas = ler_dbf(dbf_bytes, encoding)
        geoms = ler_shp(shp_bytes)
    except (struct.error, IndexError):
        raise ErroArquivo('O .shp ou o .dbf parece estar corrompido. Exporte a camada de novo no QGIS e tente outra vez.')
    if not campos:
        raise ErroArquivo('A tabela de atributos (.dbf) está vazia.')
    if prj:
        geografico = prj.lstrip().upper().startswith('GEOGCS')
    else:
        primeiro = next((g for g in geoms if g), None)
        geografico = bool(primeiro) and abs(primeiro[0][0][0]) <= 180
    nome = nome_arquivo + (f' (camada {shp})' if len(shps) > 1 else '')
    return {'campos': campos, 'linhas': linhas, 'geoms': geoms, 'geografico': geografico, 'nome': nome,
            'nome_zip': nome_arquivo, 'nome_shp': shp, 'shp': shp_bytes, 'dbf': dbf_bytes,
            'prj': z.read(achar('.prj')) if achar('.prj') else None,
            'cpg': z.read(achar('.cpg')) if achar('.cpg') else None}
