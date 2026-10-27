"""Leitura dos arquivos enviados: .zip, .rar ou os arquivos soltos do shapefile (.shp/.dbf/.shx/.prj/.cpg)."""
import collections
import math
import shutil
import subprocess
import tempfile
import unicodedata
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
        for shp in shps:
            if not any(shp.with_suffix(e).exists() for e in ('.dbf', '.DBF')):
                raise ErroArquivo(f'Achei {shp.name}, mas falta o .dbf com o mesmo nome (a tabela de atributos).')
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
    """O .rar é aberto com o tar do Windows (libarchive), 7-Zip ou unrar, o que existir."""
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino.parent / (_seguro(nome))
    arq.write_bytes(dados)
    tentativas = [['tar', '-xf', str(arq), '-C', str(destino)], ['7z', 'x', '-y', f'-o{destino}', str(arq)],
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


def ler_shapefile(shp: Path):
    """GeoDataFrame só com polígonos 2D. O encoding vem do .cpg; sem ele, latin1 (padrão do ArcGIS/QGIS antigos)."""
    tem_cpg = any(shp.with_suffix(e).exists() for e in ('.cpg', '.CPG'))
    try:
        gdf = pyogrio.read_dataframe(shp, encoding=None if tem_cpg else 'latin1')
    except Exception as e:
        raise ErroArquivo(f'Não consegui ler {shp.name}: {e}')
    if gdf.geometry.name != 'geometry':
        gdf = gdf.rename_geometry('geometry')
    tipos = set(gdf.geometry.dropna().geom_type)
    if tipos and not tipos & {'Polygon', 'MultiPolygon'}:
        raise ErroArquivo(f'{shp.name} não é uma camada de polígonos ({", ".join(sorted(tipos))}).')
    gdf['geometry'] = shapely.force_2d(gdf.geometry.values)
    return gdf


def simples(texto):
    sem = ''.join(c for c in unicodedata.normalize('NFKD', str(texto)) if not unicodedata.combining(c))
    return ' '.join(sem.casefold().split())


def detectar_campos(colunas, aliases):
    """Escolhe a coluna de cada nível da hierarquia pelo primeiro nome da lista que existir."""
    por_nome = {simples(c).upper(): c for c in colunas}
    escolhido = {}
    for nivel in ('fazenda', 'aglomerado', 'talhao', 'fazenda_nome', 'area_ha'):
        for alias in aliases.get(nivel, []):
            c = por_nome.get(simples(alias).upper())
            if c and (nivel != 'fazenda_nome' or c != escolhido.get('fazenda')):
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


def nomes_de_fazenda(linhas, campo_nome):
    """Em cada código de fazenda vale o nome que mais aparece (como na v1)."""
    if not campo_nome:
        return {}
    conta = collections.defaultdict(collections.Counter)
    for cod, attrs in linhas:
        n = texto(attrs.get(campo_nome))
        if n and '�' not in n:
            conta[cod][n] += 1
    return {cod: c.most_common(1)[0][0] for cod, c in conta.items()}
