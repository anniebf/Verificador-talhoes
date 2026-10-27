"""Fluxo completo pela API: login, importação, auditoria, revisão, correção, exportação e log.

    python -m pytest tests -q
"""
import io
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix='vt_teste_'))
os.environ['VT_DADOS'] = str(TMP)
os.environ['VT_FRONTEND'] = str(TMP / 'sem_frontend')
RAIZ = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, str(RAIZ / 'scripts' / 'gerar_exemplo.py'), str(TMP / 'exemplo')], check=True, capture_output=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope='module')
def c():
    with TestClient(app) as cliente:
        r = cliente.post('/api/auth/login', json={'usuario': 'admin', 'senha': 'admin'})
        assert r.status_code == 200, r.text
        assert r.json()['usuario']['trocarSenha'] is True
        cliente.headers['Authorization'] = 'Bearer ' + r.json()['token']
        assert cliente.post('/api/auth/senha', json={'atual': 'admin', 'nova': 'nova-senha'}).status_code == 200
        yield cliente


def enviar(c, url, *nomes, **form):
    arqs = [('arquivos', (n, (TMP / 'exemplo' / n).read_bytes())) for n in nomes]
    r = c.post(url, files=arqs, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def test_sem_login_bloqueia():
    with TestClient(app) as anon:
        assert anon.get('/api/lotes').status_code == 401


def test_fluxo_completo(c):
    lote = enviar(c, '/api/lotes', 'fazendas_exemplo.zip', nome='Teste')
    lid = lote['id']
    assert lote['campos']['fazenda'] == 'FAZ' and lote['campos']['talhao'] == 'TALHAO' and lote['campos']['aglomerado'] == 'AGLOMERADO'
    assert {f['codigo'] for f in lote['fazendas']} == {'5004', '5010'}
    erros = lote['totais']['erros']
    for tipo in ('sobreposicao', 'lacuna', 'buraco_interno', 'parte_solta', 'autointersecao', 'vertices_duplicados',
                 'talhao_duplicado', 'atributo_ausente', 'excesso_vertices'):
        assert erros.get(tipo), f'{tipo} não detectado: {erros}'
    incs = c.get(f'/api/lotes/{lid}/inconsistencias', params={'fazenda': '5010'}).json()
    assert not any(i['tipo'] == 'buraco_interno' for i in incs), 'buraco em forma de estrada deveria ser ignorado'

    # mapa da fazenda e detalhe (original x proposta)
    mapa = c.get(f'/api/lotes/{lid}/mapa', params={'fazenda': '5004'}).json()
    assert len(mapa['features']) == 12
    lon, lat = mapa['features'][0]['geometry']['coordinates'][0][0]
    assert -60 < lon < -40 and -30 < lat < -10
    incs = c.get(f'/api/lotes/{lid}/inconsistencias', params={'fazenda': '5004'}).json()
    for i in c.get(f'/api/lotes/{lid}/inconsistencias').json():  # inclusive as que envolvem geometria inválida
        r = c.get(f'/api/lotes/{lid}/inconsistencias/{i["id"]}')
        assert r.status_code == 200, (i['tipo'], r.text)
    sob = next(i for i in incs if i['tipo'] == 'sobreposicao')
    d = c.get(f'/api/lotes/{lid}/inconsistencias/{sob["id"]}').json()
    alvo = next(e for e in d['envolvidas'] if e['alvo'])
    assert alvo['depois']['areaHa'] < alvo['antes']['areaHa']
    assert abs((alvo['antes']['areaHa'] - alvo['depois']['areaHa']) - 0.8) < 0.01  # 20 m x 400 m

    # aplicar sem decidir é recusado
    ids = [i['id'] for i in incs]
    assert c.post(f'/api/lotes/{lid}/aplicar', json={'ids': ids}).status_code == 400

    manter = [i['id'] for i in incs if not i['temCorrecao'] or i['tipo'] == 'vertices_duplicados']
    aceitar = [i for i in ids if i not in manter]
    assert c.post(f'/api/lotes/{lid}/decisoes', json={'ids': aceitar, 'decisao': 'aceitar'}).status_code == 200
    if manter:
        assert c.post(f'/api/lotes/{lid}/decisoes', json={'ids': manter, 'decisao': 'manter'}).status_code == 200
    r = c.post(f'/api/lotes/{lid}/aplicar', json={'ids': ids})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res['versao'] == 2 and res['aplicadas'] == len(aceitar)

    # depois da correção, só sobram na 5004 os que foram mantidos (que não voltam)
    depois = c.get(f'/api/lotes/{lid}/inconsistencias', params={'fazenda': '5004'}).json()
    tipos_restantes = {i['tipo'] for i in depois}
    assert not tipos_restantes & {'sobreposicao', 'lacuna', 'buraco_interno', 'parte_solta', 'autointersecao'}, depois

    # exportação da nova versão e da original
    r = c.get(f'/api/lotes/{lid}/exportar', params={'formato': 'shp'})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {Path(n).suffix for n in z.namelist()} >= {'.shp', '.dbf', '.shx', '.prj'}
    assert c.get(f'/api/lotes/{lid}/exportar', params={'formato': 'geojson', 'versao': 1}).status_code == 200

    # mesclagem de outro shapefile no mesmo lote
    m = enviar(c, f'/api/lotes/{lid}/camadas', 'fazenda_extra.zip')
    assert len(m['camadas']) == 2 and m['versaoAtual'] == 3
    assert any(f['codigo'] == '5020' and f['erros'].get('sobreposicao') for f in m['fazendas'])

    # log: completo, encadeado e imutável
    log = c.get('/api/auditoria', params={'lote_id': lid}).json()['linhas']
    acoes = {l['acao'] for l in log}
    assert {'importacao', 'auditoria', 'correcao', 'versao', 'exportacao', 'mesclagem'} <= acoes
    assert c.get('/api/auditoria/integridade').json()['ok'] is True
    corr = next(l for l in log if l['acao'] == 'correcao')
    det = c.get(f'/api/auditoria/registro/{corr["id"]}').json()
    assert det['geomAntes'] and det['geomDepois']
    assert c.get('/api/auditoria/exportar/csv', params={'lote_id': lid}).content.startswith('﻿'.encode())
    assert c.get('/api/auditoria/exportar/pdf', params={'lote_id': lid}).content.startswith(b'%PDF')

    from sqlalchemy import text
    from app.db import engine
    with pytest.raises(Exception):
        with engine.begin() as con:
            con.execute(text('UPDATE log_auditoria SET descricao = "x"'))


def test_admin_usuarios_e_regras(c):
    r = c.post('/api/admin/usuarios', json={'usuario': 'Maria', 'nome': 'Maria', 'perfil': 'analista', 'senha': '123456'})
    assert r.status_code == 200 and r.json()['usuario'] == 'maria'
    regras = c.get('/api/admin/regras').json()
    assert any(x['chave'] == 'lacuna' for x in regras['regras'])
    r = c.put('/api/admin/regras/lacuna', json={'params': {'area_max_m2': 100}})
    assert r.status_code == 200
    assert next(x for x in r.json()['regras'] if x['chave'] == 'lacuna')['params']['area_max_m2'] == 100
    assert c.put('/api/admin/regras/lacuna', json={'params': {'xyz': 1}}).status_code == 400

    # analista não entra no admin
    with TestClient(app) as outro:
        tk = outro.post('/api/auth/login', json={'usuario': 'maria', 'senha': '123456'}).json()['token']
        assert outro.get('/api/admin/usuarios', headers={'Authorization': f'Bearer {tk}'}).status_code == 403
