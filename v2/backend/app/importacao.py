"""Leitura dos arquivos enviados: .zip, .rar ou os arquivos soltos do shapefile (.shp/.dbf/.shx/.prj/.cpg)."""
import collections
import re
import math
import shutil
import subprocess
import tempfile
import unicodedata
import warnings
import zipfile
from datetime import date, datetime
from pathlib import Path

import pyogrio
import shapely

from . import config

EXT_SHAPE = {'.shp', '.dbf', '.shx', '.prj', '.cpg', '.qix', '.sbn', '.sbx', '.qmd'}


class ErroArquivo(Exception):
    """Erro com mensagem pronta para o usuário."""


def preparar_pasta(arquivos):
    """arquivos: [(nome, bytes)]. Extrai tudo numa pasta temporária e devolve (pasta, [caminhos .shp])."""
    pasta = Path(tempfile.mkdtemp(prefix='vt_'))
    try:
        for nome, dados in arquivos:
            nome = Path(nome).name
            ext = Path(nome).suffix.lower()
            if ext == '.zip':
                _extrair_zip(dados, pasta / _seguro(Path(nome).stem))
            elif ext == '.rar':
                _extrair_rar(dados, nome, pasta / _seguro(Path(nome).stem))
            elif ext in EXT_SHAPE:
                (pasta / 'soltos').mkdir(exist_ok=True)
                (pasta / 'soltos' / _seguro(nome)).write_bytes(dados)
            else:
                raise ErroArquivo(f'"{nome}" não é um tipo aceito. Envie .zip, .rar ou os arquivos .shp/.dbf/.shx/.prj.')
        shps = sorted(p for p in pasta.rglob('*') if p.suffix.lower() == '.shp' and '__MACOSX' not in p.parts)
        if not shps:
            raise ErroArquivo('Não achei nenhum .shp nos arquivos enviados. Confira se a camada foi compactada com todos os arquivos dela.')
        # 3.1.1: .shp, .shx e .dbf são obrigatórios
        for shp in shps:
            for ext, papel in (('.shx', 'o índice das geometrias'), ('.dbf', 'a tabela de atributos')):
                if irmao(shp, ext) is None:
                    raise ErroArquivo(f'Achei {shp.name}, mas falta o {ext} com o mesmo nome ({papel}). '
                                      'Envie a camada com todos os arquivos (.shp, .shx, .dbf e, de preferência, .prj).')
        return pasta, shps
    except Exception:
        shutil.rmtree(pasta, ignore_errors=True)
        raise


def _seguro(nome):
    return ''.join(c if c.isalnum() or c in '._- ' else '_' for c in nome) or 'arquivo'


def _extrair_zip(dados, destino):
    import io
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        raise ErroArquivo('Não consegui abrir o .zip. Confira se ele não está corrompido.')
    if sum(i.file_size for i in z.infolist()) > config.LIMITE_DESCOMPACTADO:
        raise ErroArquivo('O .zip é grande demais depois de descompactado.')
    destino.mkdir(parents=True, exist_ok=True)
    raiz = destino.resolve()
    for info in z.infolist():
        if info.is_dir():
            continue
        alvo = (destino / info.filename).resolve()
        if not alvo.is_relative_to(raiz):  # zip slip
            continue
        if alvo.suffix.lower() == '.zip':  # zip dentro de zip, comum quando exportam várias camadas
            _extrair_zip(z.read(info), alvo.with_suffix(''))
            continue
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(z.read(info))


def _extrair_rar(dados, nome, destino):
    """O .rar é aberto com bsdtar (o tar do Windows), 7-Zip ou unrar, o que existir."""
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino.parent / (_seguro(nome))
    arq.write_bytes(dados)
    # bsdtar/tar do Windows (libarchive) abrem .rar; o tar do GNU não, então ele fica por último
    tentativas = [['bsdtar', '-xf', str(arq), '-C', str(destino)], ['7z', 'x', '-y', f'-o{destino}', str(arq)],
                  ['tar', '-xf', str(arq), '-C', str(destino)],
                  ['unrar', 'x', '-o+', str(arq), str(destino) + '/']]
    for cmd in tentativas:
        if not shutil.which(cmd[0]):
            continue
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode == 0 and any(destino.rglob('*')):
            arq.unlink(missing_ok=True)
            break
    else:
        raise ErroArquivo('Não consegui abrir o .rar neste servidor. Envie em .zip ou os arquivos soltos.')
    raiz = destino.resolve()
    for p in destino.rglob('*'):
        if not p.resolve().is_relative_to(raiz):
            p.unlink(missing_ok=True)


def irmao(shp, ext):
    """Arquivo da mesma camada com outra extensão (sem diferença de maiúscula)."""
    alvo = (shp.stem + ext).lower()
    return next((p for p in shp.parent.iterdir() if p.name.lower() == alvo), None)


TIPOS = {'Polygon': 'poligono', 'MultiPolygon': 'poligono', 'LineString': 'linha', 'MultiLineString': 'linha'}


def ler_shapefile(shp: Path):
    """Lê e valida uma camada (3.1.1). Devolve (GeoDataFrame 2D, 'poligono'|'linha', [avisos]).

    O encoding vem do .cpg; sem ele, latin1 (padrão do ArcGIS/QGIS antigos).
    """
    from pyproj import CRS
    avisos = []
    prj = irmao(shp, '.prj')
    if prj is None:
        avisos.append(f'{shp.name}: sem .prj (sistema de coordenadas não informado).')
    else:
        try:
            CRS.from_wkt(prj.read_text(encoding='latin1'))
        except Exception:  # noqa: BLE001
            avisos.append(f'{shp.name}: o .prj existe mas não é um sistema de coordenadas válido; ele foi ignorado.')
    cpg = irmao(shp, '.cpg')
    if cpg is None:
        avisos.append(f'{shp.name}: sem .cpg; textos lidos como Latin-1.')
    utf8 = cpg is not None and re.match(r'\s*utf-?8', cpg.read_text(errors='replace'), re.I) is not None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            # Latin-1 lê os bytes sem perder nada; com .cpg UTF-8, cada texto é decodificado depois (_utf8_misto)
            gdf = pyogrio.read_dataframe(shp, encoding='latin1' if cpg is None or utf8 else None)
        if utf8:
            # .cpg que diz UTF-8 com alguns textos gravados em Latin-1 (comum em bases editadas no ArcGIS/Excel):
            # cada texto vira UTF-8 quando é UTF-8 válido e Latin-1 (Windows-1252) quando não é. Ler o arquivo
            # inteiro de um jeito só estragava o outro tipo ("CLARÃO" virava "CLARÃƒO").
            gdf, n_latin = _utf8_misto(gdf)
            if n_latin:
                avisos.append(f'{shp.name}: o .cpg diz UTF-8, mas {n_latin} texto(s) estão em Latin-1; '
                              'esses foram lidos como Latin-1 para os acentos saírem certos.')
    except Exception as e:  # noqa: BLE001
        raise ErroArquivo(f'Não consegui ler {shp.name}: o arquivo parece corrompido ou incompleto ({e}).')
    if gdf.geometry.name != 'geometry':
        gdf = gdf.rename_geometry('geometry')
    if prj is not None and gdf.crs is None:
        avisos.append(f'{shp.name}: o .prj não pôde ser interpretado.')
    tipos = set(gdf.geometry.dropna().geom_type)
    if not tipos:
        avisos.append(f'{shp.name}: nenhuma geometria preenchida.')
    familias = {TIPOS.get(t) for t in tipos}
    if None in familias:
        raise ErroArquivo(f'{shp.name} tem geometrias de tipo não suportado ({", ".join(sorted(tipos))}). '
                          'As regras de auditoria valem para polígonos (talhões) e linhas (estradas).')
    if len(familias) > 1:
        raise ErroArquivo(f'{shp.name} mistura polígonos e linhas; separe em duas camadas.')
    if any(z for z in gdf.geometry.dropna().has_z):
        avisos.append(f'{shp.name}: coordenadas Z descartadas (a auditoria é 2D).')
    gdf['geometry'] = shapely.force_2d(gdf.geometry.values)
    return gdf, (familias.pop() if familias else 'poligono'), avisos


def _decodificar(texto_latin1):
    """Texto lido como Latin-1 (bytes intactos) -> UTF-8 se os bytes forem UTF-8 válido; senão Windows-1252.
    Devolve (texto, veio_em_latin1)."""
    bruto = texto_latin1.encode('latin1')
    try:
        return bruto.decode('utf-8'), False
    except UnicodeDecodeError:
        return bruto.decode('cp1252', errors='replace'), True


def _utf8_misto(gdf):
    """Decodifica cada texto (e o nome de cada coluna) de um GeoDataFrame lido como Latin-1."""
    n_latin = 0

    def conv(v):
        nonlocal n_latin
        if not isinstance(v, str) or v.isascii():
            return v
        t, era_latin = _decodificar(v)
        n_latin += era_latin
        return t
    for c in gdf.columns:
        if c != 'geometry' and gdf[c].dtype.kind == 'O':
            gdf[c] = gdf[c].map(conv)
    gdf = gdf.rename(columns={c: _decodificar(c)[0] for c in gdf.columns if c != 'geometry' and not c.isascii()})
    return gdf, n_latin


def simples(texto):
    sem = ''.join(c for c in unicodedata.normalize('NFKD', str(texto)) if not unicodedata.combining(c))
    return ' '.join(sem.casefold().split())


def base_coluna(nome):
    """Nome sem o sufixo que o QGIS acrescenta em junções: FAZ_2 -> FAZ, FAZENDA_2_ -> FAZENDA, AREA_2_2 -> AREA."""
    return re.sub(r'(_\d*)+$', '', simples(nome).upper()) or simples(nome).upper()


def detectar_campos(colunas, aliases):
    """Sugere a coluna de cada nível da hierarquia: o primeiro nome da lista que existir, exato ou com o
    sufixo de junção (FAZ_2). O usuário confirma ou troca na tela do lote."""
    exatos = {simples(c).upper(): c for c in colunas}
    por_base = {}
    for c in colunas:
        por_base.setdefault(base_coluna(c), c)
    escolhido = {}
    for nivel in ('fazenda', 'aglomerado', 'talhao', 'fazenda_nome', 'fazenda_detalhe', 'area_ha'):
        usados = set(escolhido.values())
        for alias in aliases.get(nivel, []):
            a = simples(alias).upper()
            c = exatos.get(a) or por_base.get(a)
            if c and c not in usados:
                escolhido[nivel] = c
                break
    return escolhido


def valor(v):
    """Valor de atributo que cabe em JSON."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, 'item'):  # numpy
        return valor(v.item())
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return v


def texto(v):
    v = valor(v)
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = str(v).strip()
    return '' if t.upper() == 'NULL' else t


SEM_CODIGO = '#'  # chave de fazenda "#nome": talhão sem código, mas com nome de fazenda


def chave_fazenda(codigo, nome, detalhe):
    """Código da fazenda. Sem código, mas com nome, a chave é "#nome": cada fazenda fica separada pelo
    nome em vez de todas caírem num grupo só. Sem código e sem nome, "" (Sem fazenda)."""
    if codigo:
        return codigo
    n = nome or detalhe
    return SEM_CODIGO + n if n and '�' not in n else ''


def nomes_de_fazenda(linhas):
    """linhas: (chave da fazenda, nome, detalhe). Em cada fazenda vale o nome que mais aparece; o detalhe
    (DE_FAZ) vem junto quando diz outra coisa — "MALU · EMA" — e some quando é igual (acento e
    maiúscula não contam). Sem nome, vale só o detalhe (como na v1)."""
    conta_nome = collections.defaultdict(collections.Counter)
    conta_det = collections.defaultdict(collections.Counter)
    for chave, nome, detalhe in linhas:
        if nome and '�' not in nome:
            conta_nome[chave][nome] += 1
        if detalhe and '�' not in detalhe:
            conta_det[chave][detalhe] += 1
    saida = {}
    for chave in set(conta_nome) | set(conta_det):
        n = conta_nome[chave].most_common(1)[0][0] if conta_nome[chave] else ''
        d = conta_det[chave].most_common(1)[0][0] if conta_det[chave] else ''
        saida[chave] = f'{n} · {d}' if n and d and simples(n) != simples(d) else (n or d)
    return saida
