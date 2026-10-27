"""Fluxo completo pela API sobre a base controlada (scripts/gerar_exemplo.py), com os resultados esperados
de cada regra. Critérios de aceite da documentação funcional (seção 8).

    python -m pytest tests -q
"""
import io
import os
import subprocess
import sys
import tempfile
import time
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

# resultado esperado da auditoria da base de exemplo: (fazenda, regra) -> (quantidade, classificação, tem correção)
ESPERADO = {
    ('5004', 'autointersecao'): (1, 'confirmado', True),
    ('5004', 'vertices_duplicados'): (1, 'confirmado', True),
    ('5004', 'parte_solta'): (1, 'suspeito', True),        # 0,25 ha = 1,5% do talhão: dentro dos critérios
    ('5004', 'buraco_interno'): (1, 'suspeito', True),     # 1,00 ha: no limite de preenchimento
    ('5004', 'sobreposicao'): (1, 'confirmado', True),
    ('5004', 'lacuna'): (1, 'confirmado', True),           # vão de 2 m: lasca
    ('5010', 'parte_solta'): (1, 'suspeito', False),       # duas partes de 9 ha: multiparte legítimo, sem remoção
    ('5010', 'corte_estrada'): (3, 'suspeito', None),      # faixa dentro do 005 (manual) + estrada cruzando 001 e 004 (recorte)
    ('5010', 'excesso_vertices'): (1, 'suspeito', True),
    ('5010', 'atributo_ausente'): (1, 'confirmado', False),
    ('5010', 'talhao_duplicado'): (1, 'suspeito', False),
    ('', 'estrada_desconectada'): (1, 'suspeito', True),   # ponta a 6 m da estrada principal
    ('', 'segmento_isolado'): (1, 'inconclusivo', False),
}


@pytest.fixture(scope='module')
def c():
    with TestClient(app) as cliente:
        r = cliente.post('/api/auth/login', json={'usuario': 'admin', 'senha': 'admin'})
        assert r.status_code == 200, r.text
        assert r.json()['usuario']['trocarSenha'] is True
        cliente.headers['Authorization'] = 'Bearer ' + r.json()['token']
        assert cliente.post('/api/auth/senha', json={'atual': 'admin', 'nova': 'nova-senha'}).status_code == 200
        yield cliente


def enviar(c, url, *nomes, status=200, **form):
    arqs = [('arquivos', (n, (TMP / 'exemplo' / n).read_bytes() if isinstance(n, str) and (TMP / 'exemplo' / n).exists() else b'x'))
            for n in nomes]
    r = c.post(url, files=arqs, data=form)
    assert r.status_code == status, r.text
    return r.json()


def executar(c, lid, ids):
    eid = c.post(f'/api/lotes/{lid}/correcao', json={'ids': ids}).json()['execucaoId']
    for _ in range(300):
        e = c.get(f'/api/execucoes/{eid}').json()
        if e['status'] != 'executando':
            return e
        time.sleep(0.1)
    raise AssertionError('execução não terminou')


def geoms(fc):
    return {f['properties']['id']: f['geometry'] for f in fc['features']}


def test_sem_login_bloqueia():
    with TestClient(app) as anon:
        assert anon.get('/api/lotes').status_code == 401


def test_validacao_da_entrada(c):
    # zip sem .shx é recusado com mensagem clara
    buf = io.BytesIO()
    with zipfile.ZipFile(TMP / 'exemplo' / 'fazenda_extra.zip') as z, zipfile.ZipFile(buf, 'w') as novo:
        for n in z.namelist():
            if not n.endswith('.shx'):
                novo.writestr(n, z.read(n))
    r = c.post('/api/lotes', files=[('arquivos', ('sem_shx.zip', buf.getvalue()))])
    assert r.status_code == 400 and '.shx' in r.json()['detail']
    # tipo de arquivo não aceito
    r = c.post('/api/lotes', files=[('arquivos', ('planilha.xlsx', b'abc'))])
    assert r.status_code == 400 and 'não é um tipo aceito' in r.json()['detail']
    # zip corrompido
    r = c.post('/api/lotes', files=[('arquivos', ('quebrado.zip', b'nao sou zip'))])
    assert r.status_code == 400 and 'corrompido' in r.json()['detail']


def test_fluxo_completo(c):
    lote = enviar(c, '/api/lotes', 'fazendas_exemplo.zip', nome='Teste')
    lid = lote['id']
    assert {x['geometria'] for x in lote['camadas']} == {'poligono', 'linha'}
    assert lote['campos']['fazenda'] == 'FAZ' and lote['campos']['talhao'] == 'TALHAO' and lote['campos']['aglomerado'] == 'AGLOMERADO'
    assert lote['totais']['talhoes'] == 20 and lote['totais']['linhas'] == 3
    aud = lote['ultimaAuditoria']
    assert aud['tipo'] == 'auditoria' and aud['resumo']['feicoes'] == 23

    # Etapa 1: cada regra encontra exatamente o esperado
    v1 = c.get(f'/api/lotes/{lid}/mapa', params={'versao': 1}).json()
    incs = c.get(f'/api/lotes/{lid}/inconsistencias').json()
    achado = {}
    for i in incs:
        achado.setdefault((i['fazenda'], i['tipo']), []).append(i)
    assert set(achado) == set(ESPERADO), set(achado) ^ set(ESPERADO)
    for chave, (n, classe, auto) in ESPERADO.items():
        lista = achado[chave]
        assert len(lista) == n, (chave, len(lista))
        assert all(i['classificacao'] == classe for i in lista), chave
        if auto is not None:
            assert all(i['temCorrecao'] == auto for i in lista), chave
            assert all(i['status'] == ('identificada' if auto else 'nao_corrigivel') for i in lista), chave
    cortes = achado[('5010', 'corte_estrada')]
    assert sum(i['temCorrecao'] for i in cortes) == 2  # só os cruzamentos da camada de estradas têm recorte proposto

    # cada ocorrência abre com detalhe (identificação, motivo, limitações) e vira "em revisão"
    for i in incs:
        r = c.get(f'/api/lotes/{lid}/inconsistencias/{i["id"]}')
        assert r.status_code == 200, (i['tipo'], r.text)
        d = r.json()
        assert d['motivo'] and d['envolvidas'] and 'camada' in d['envolvidas'][0] and 'indiceOrigem' in d['envolvidas'][0]
        assert d['status'] == ('em_revisao' if i['temCorrecao'] else 'nao_corrigivel')
    # a prévia não alterou nada: o mapa continua idêntico
    assert c.get(f'/api/lotes/{lid}/mapa', params={'versao': 1}).json() == v1
    sob = achado[('5004', 'sobreposicao')][0]
    d = c.get(f'/api/lotes/{lid}/inconsistencias/{sob["id"]}').json()
    alvo = next(e for e in d['envolvidas'] if e['alvo'])
    assert abs((alvo['antes']['areaHa'] - alvo['depois']['areaHa']) - 0.8) < 0.01  # prévia: 20 m x 400 m a menos

    # filtros da revisão
    assert all(i['temCorrecao'] for i in c.get(f'/api/lotes/{lid}/inconsistencias', params={'auto': True}).json())
    assert [i['tipo'] for i in c.get(f'/api/lotes/{lid}/inconsistencias', params={'talhao': '009'}).json()] == ['autointersecao']

    # Etapa 2: decisões. Em lote, só as elegíveis são aprovadas; as outras voltam com o motivo
    todas = [i['id'] for i in incs]
    r = c.post(f'/api/lotes/{lid}/decisoes', json={'ids': todas, 'decisao': 'aprovar'}).json()
    assert len(r['recusadas']) == sum(not i['temCorrecao'] for i in incs)
    pendente = achado[('5010', 'excesso_vertices')][0]['id']
    rejeitar = [i['id'] for i in cortes if i['temCorrecao']]
    assert c.post(f'/api/lotes/{lid}/decisoes', json={'ids': [pendente], 'decisao': 'pendente'}).status_code == 200
    assert c.post(f'/api/lotes/{lid}/decisoes', json={'ids': rejeitar, 'decisao': 'rejeitar'}).status_code == 200

    # força uma falha: sobreposição com área errada no recorte -> a validação tem que reprovar e descartar
    from app.db import Inconsistencia, Sessao
    from app.topologia import wkb_hex
    import shapely
    with Sessao() as s:
        inc = s.get(Inconsistencia, sob['id'])
        g = shapely.from_wkb(inc.destaque)
        inc.op = {**inc.op, 'area': wkb_hex(shapely.affinity.scale(g, 0.3, 0.3))}  # recorta só um pedaço
        s.commit()

    prep = c.post(f'/api/lotes/{lid}/correcao/preparar', json={'ids': todas}).json()
    assert len(prep['aprovadas']) == 7 and len(prep['pendentes']) == 1 and len(prep['rejeitadas']) == 2
    assert prep['naoCorrigiveis'] and prep['avisos'] and 'versão 2' in prep['saida']

    # Etapa 3: execução com validação
    e = executar(c, lid, todas)
    assert e['status'] == 'concluida', e
    r = e['resumo']
    assert r['corrigidas'] == 6 and r['falhas'] == 1 and r['novaVersao'] and e['versaoSaida'] == 2
    falha = next(x for x in r['resultados'] if x['status'] == 'falha')
    assert falha['tipo'] == 'sobreposicao' and any(not v['ok'] and v['nome'] == 'Problema resolvido' for v in falha['validacao'])
    assert all(v['ok'] for x in r['resultados'] if x['status'] == 'corrigida' for v in x['validacao'])

    # a falha não entrou na nova versão: o talhão 002 continua com a área original
    v2 = {f['properties']['talhao']: f for f in c.get(f'/api/lotes/{lid}/mapa', params={'fazenda': '5004'}).json()['features']}
    assert abs(v2['002']['properties']['areaHa'] - 16.8) < 0.001
    assert abs(v2['007']['properties']['areaHa'] - 16.0) < 0.001  # buraco preenchido

    depois = c.get(f'/api/lotes/{lid}/inconsistencias').json()
    est = {(i['fazenda'], i['tipo']): i['status'] for i in depois}
    assert est[('5004', 'sobreposicao')] == 'falha'          # continua em aberto, marcada como falha
    assert est[('5010', 'excesso_vertices')] == 'pendente'   # decisão mantida na reauditoria
    assert est[('5010', 'corte_estrada')] in ('rejeitada', 'nao_corrigivel')
    assert ('5004', 'lacuna') not in est and ('', 'estrada_desconectada') not in est
    corrigidas = c.get(f'/api/lotes/{lid}/inconsistencias', params={'status': 'corrigida'}).json()
    assert len(corrigidas) == 6

    # base original preservada: a versão 1 continua igual
    assert geoms(c.get(f'/api/lotes/{lid}/mapa', params={'versao': 1}).json()) == geoms(v1)

    # exportação: shapefile com talhões e estradas
    r = c.get(f'/api/lotes/{lid}/exportar', params={'formato': 'shp'})
    assert r.status_code == 200
    nomes = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith('_estradas.shp') for n in nomes) and sum(n.endswith('.shp') for n in nomes) == 2
    assert c.get(f'/api/lotes/{lid}/exportar', params={'formato': 'geojson', 'versao': 1}).status_code == 200

    # relatórios das execuções
    for eid in (aud['id'], e['id']):
        assert c.get(f'/api/execucoes/{eid}/relatorio/pdf').content.startswith(b'%PDF')
        assert c.get(f'/api/execucoes/{eid}/relatorio/csv').content.startswith('﻿'.encode())
    execs = c.get('/api/execucoes', params={'lote_id': lid}).json()
    assert {x['tipo'] for x in execs} == {'auditoria', 'correcao'}

    # histórico separa detecção, aprovação e confirmação
    hist = c.get(f'/api/lotes/{lid}/inconsistencias/{corrigidas[0]["id"]}').json()['historico']
    assert [h['estado'] for h in hist][:1] == ['Identificada'] and hist[-1]['estado'] == 'Corrigida'
    assert any(h['estado'] == 'Aprovada' for h in hist)

    # mesclagem e troca de campos
    m = enviar(c, f'/api/lotes/{lid}/camadas', 'fazenda_extra.zip')
    assert len(m['camadas']) == 3 and m['versaoAtual'] == 3
    assert any(f['codigo'] == '5020' and f['erros'].get('sobreposicao') for f in m['fazendas'])
    r = c.put(f'/api/lotes/{lid}/campos', json={'fazenda': 'FAZ', 'talhao': 'SAFRA', 'aglomerado': 'AGLOMERADO'})
    assert r.status_code == 200 and r.json()['campos']['talhao'] == 'SAFRA'
    assert c.put(f'/api/lotes/{lid}/campos', json={'fazenda': 'NAO_EXISTE'}).status_code == 400

    # log: completo, encadeado e imutável
    log = c.get('/api/auditoria', params={'lote_id': lid}).json()['linhas']
    acoes = {l['acao'] for l in log}
    assert {'importacao', 'auditoria', 'decisao', 'correcao', 'falha', 'versao', 'exportacao', 'mesclagem', 'lote'} <= acoes
    assert c.get('/api/auditoria/integridade').json()['ok'] is True
    corr = next(l for l in log if l['acao'] == 'correcao' and l['temGeometria'])
    det = c.get(f'/api/auditoria/registro/{corr["id"]}').json()
    assert det['geomAntes'] and det['geomDepois']
    assert c.get('/api/auditoria/exportar/pdf', params={'lote_id': lid}).content.startswith(b'%PDF')

    from sqlalchemy import text
    from app.db import engine
    with pytest.raises(Exception):
        with engine.begin() as con:
            con.execute(text('UPDATE log_auditoria SET descricao = "x"'))


def test_verificacoes_escolhidas(c):
    lote = enviar(c, '/api/lotes', 'fazendas_exemplo.zip', verificacoes='sobreposicao,lacuna')
    tipos = {i['tipo'] for i in c.get(f'/api/lotes/{lote["id"]}/inconsistencias').json()}
    assert tipos == {'sobreposicao', 'lacuna'}
    assert set(lote['ultimaAuditoria']['regras']) == {'sobreposicao', 'lacuna'}
    assert c.post('/api/lotes', files=[('arquivos', ('a.zip', b''))], data={'verificacoes': 'xyz'}).status_code == 400


def test_admin_usuarios_e_regras(c):
    r = c.post('/api/admin/usuarios', json={'usuario': 'Maria', 'nome': 'Maria', 'perfil': 'analista', 'senha': '123456'})
    assert r.status_code == 200 and r.json()['usuario'] == 'maria'
    regras = c.get('/api/admin/regras').json()
    assert any(x['chave'] == 'estrada_desconectada' for x in regras['regras'])
    r = c.put('/api/admin/regras/lacuna', json={'params': {'area_max_m2': 100}})
    assert r.status_code == 200
    assert next(x for x in r.json()['regras'] if x['chave'] == 'lacuna')['params']['area_max_m2'] == 100
    assert c.put('/api/admin/regras/lacuna', json={'params': {'xyz': 1}}).status_code == 400

    with TestClient(app) as outro:
        tk = outro.post('/api/auth/login', json={'usuario': 'maria', 'senha': '123456'}).json()['token']
        assert outro.get('/api/admin/usuarios', headers={'Authorization': f'Bearer {tk}'}).status_code == 403
