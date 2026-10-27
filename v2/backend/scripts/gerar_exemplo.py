"""Gera shapefiles de exemplo (em SIRGAS 2000, graus) com um erro de cada tipo, para testar o sistema.

    python scripts/gerar_exemplo.py            -> dados/exemplo/fazendas_exemplo.zip e fazenda_extra.zip
"""
import io
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import shapely
from pyproj import Transformer
from shapely import MultiPolygon, Polygon, box

SAIDA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'dados' / 'exemplo'
SAIDA.mkdir(parents=True, exist_ok=True)
para_graus = Transformer.from_crs('EPSG:31982', 'EPSG:4674', always_xy=True)  # UTM 22S -> SIRGAS 2000
X0, Y0 = 600_000, 7_600_000


def graus(g):
    return shapely.transform(g, lambda c: __import__('numpy').column_stack(para_graus.transform(c[:, 0], c[:, 1])))


def grade(x, y, n_col, n_lin, lado=400, folga=0):
    """Talhões quadrados encostados (folga 0) numa grade."""
    return [box(x + i * (lado + folga), y + j * (lado + folga), x + i * (lado + folga) + lado, y + j * (lado + folga) + lado)
            for j in range(n_lin) for i in range(n_col)]


def fazenda_a():
    """Fazenda 5004 (MALU): sobreposição, lacuna, buraco, parte solta, autointerseção, vértices duplicados."""
    t = grade(X0, Y0, 4, 3)
    t[1] = box(X0 + 380, Y0, X0 + 800, Y0 + 400)                       # sobrepõe 20 m sobre o talhão 1
    t[5] = box(X0 + 400, Y0 + 400, X0 + 798, Y0 + 800)                  # deixa vão de 2 m com o talhão 7
    t[6] = Polygon(t[6].exterior.coords, [[(X0 + 950, Y0 + 550), (X0 + 1050, Y0 + 550), (X0 + 1050, Y0 + 650), (X0 + 950, Y0 + 650)]])
    t[3] = MultiPolygon([t[3], box(X0 + 1650, Y0 + 50, X0 + 1700, Y0 + 100)])  # pedacinho solto fora
    x, y = X0, Y0 + 800                                                  # "gravata": autointerseção
    t[8] = Polygon([(x, y), (x + 400, y + 400), (x + 400, y), (x, y + 400), (x, y)])
    c = list(t[9].exterior.coords)                                       # vértice repetido
    t[9] = Polygon([c[0], c[1], c[1], c[2], c[3], c[0]])
    return t


def fazenda_b():
    """Fazenda 5010 (SANTA RITA): talhão duplicado, sem número, excesso de vértices, buraco tipo estrada (ignorado)."""
    import numpy as np
    x, y = X0 + 3000, Y0
    t = grade(x, y, 3, 2, lado=500)
    ang = np.linspace(0, 2 * np.pi, 3000)                                # 3000 vértices num círculo de 250 m
    cx, cy = x + 250, y + 1250
    t.append(Polygon(np.column_stack([cx + 250 * np.cos(ang), cy + 250 * np.sin(ang)])))
    # estrada de 8 m atravessando o talhão 4 por dentro (vira buraco comprido): não deve aparecer
    t[4] = Polygon(t[4].exterior.coords, [[(x + 540, y + 746), (x + 960, y + 746), (x + 960, y + 754), (x + 540, y + 754)]])
    return t


def montar(talhoes, faz, nome, aglomerados, numeros):
    return gpd.GeoDataFrame({
        'FAZ': [faz] * len(talhoes), 'FAZENDA': [nome] * len(talhoes),
        'AGLOMERADO': aglomerados, 'TALHAO': numeros,
        'HA': [round(g.area / 1e4, 2) for g in talhoes], 'SAFRA': ['2026/27'] * len(talhoes),
    }, geometry=[graus(g) for g in talhoes], crs='EPSG:4674')


def zipar(gdf, nome):
    pasta = SAIDA / nome
    pasta.mkdir(exist_ok=True)
    gdf.to_file(pasta / f'{nome}.shp', engine='pyogrio', encoding='UTF-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in pasta.iterdir():
            z.write(p, p.name)
    (SAIDA / f'{nome}.zip').write_bytes(buf.getvalue())
    return SAIDA / f'{nome}.zip'


a = montar(fazenda_a(), '5004', 'MALU', ['A1'] * 8 + ['A2'] * 4, [f'{i:03d}' for i in range(1, 13)])
b_t = fazenda_b()
b = montar(b_t, '5010', 'SANTA RITA', ['B1'] * len(b_t), ['001', '002', '002', '004', '005', '', '007'])
print(zipar(gpd.GeoDataFrame(__import__('pandas').concat([a, b], ignore_index=True), crs='EPSG:4674'), 'fazendas_exemplo'))

# camada à parte para testar a mesclagem: fazenda 5020 com uma sobreposição
c_t = grade(X0, Y0 + 3000, 3, 1)
c_t[2] = box(X0 + 790, Y0 + 3000, X0 + 1200, Y0 + 3400)
print(zipar(montar(c_t, '5020', 'BOA VISTA', ['C1'] * 3, ['001', '002', '003']), 'fazenda_extra'))
