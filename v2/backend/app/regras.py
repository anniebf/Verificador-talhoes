"""Regras de detecção e correção, com os valores padrão. O admin muda os parâmetros na tela."""
from sqlalchemy import select

from .db import Regra, Sessao

# ordem = ordem em que aparecem na tela e em que as correções são aplicadas
PADRAO = {
    'geometria_vazia': {
        'nome': 'Geometria vazia', 'severidade': 'alta',
        'descricao': 'Registro sem desenho ou com desenho sem área (risco ou ponto). Correção: remover o registro.',
        'params': {},
    },
    'autointersecao': {
        'nome': 'Autointerseção', 'severidade': 'alta',
        'descricao': 'Contorno que cruza a si mesmo ou outra geometria inválida. Correção: refazer a geometria válida (make_valid).',
        'params': {},
    },
    'vertices_duplicados': {
        'nome': 'Vértices duplicados', 'severidade': 'baixa',
        'descricao': 'Vértices seguidos no mesmo lugar. Correção: remover as repetições.',
        'params': {'tolerancia_m': 0.01},
    },
    'buraco_interno': {
        'nome': 'Buraco interno', 'severidade': 'media',
        'descricao': 'Furo dentro do talhão. Correção: preencher. Buracos maiores que o limite e faixas com formato de estrada são ignorados.',
        'params': {'area_max_ha': 5.0, 'ignorar_estradas': True},
    },
    'parte_solta': {
        'nome': 'Parte solta', 'severidade': 'media',
        'descricao': 'Pedaço separado do contorno principal do talhão. Correção: remover o pedaço.',
        'params': {'area_max_ha': 2.0},
    },
    'excesso_vertices': {
        'nome': 'Excesso de vértices', 'severidade': 'baixa',
        'descricao': 'Talhão com vértices demais (pesa no QGIS). Correção: simplificar mantendo a forma.',
        'params': {'max_vertices': 1000, 'espacamento_min_m': 5.0, 'tolerancia_m': 1.0, 'variacao_area_max_pct': 0.5},
    },
    'sobreposicao': {
        'nome': 'Sobreposição', 'severidade': 'alta',
        'descricao': 'Dois talhões ocupando a mesma área. Correção: recortar a área sobreposta de um deles.',
        'params': {'area_min_m2': 1.0, 'recortar_de': 'maior'},
    },
    'lacuna': {
        'nome': 'Lacuna', 'severidade': 'media',
        'descricao': 'Vão fechado entre talhões vizinhos. Correção: juntar o vão ao vizinho com a maior divisa.',
        'params': {'area_min_m2': 1.0, 'area_max_m2': 5000.0, 'ignorar_estradas': True},
    },
    'atributo_ausente': {
        'nome': 'Atributo ausente', 'severidade': 'media',
        'descricao': 'Talhão sem código de fazenda ou sem número de talhão. Correção manual.',
        'params': {},
    },
    'talhao_duplicado': {
        'nome': 'Talhão duplicado', 'severidade': 'media',
        'descricao': 'Mesmo número de talhão repetido na mesma fazenda/aglomerado. Correção manual.',
        'params': {},
    },
}

# não é um tipo de erro: nomes de colunas aceitos para montar a hierarquia e recalcular área
CAMPOS_PADRAO = {
    'fazenda': ['FAZ', 'COD_FAZ', 'CD_FAZ', 'ID_FAZ', 'CODFAZ', 'FAZENDA', 'DE_FAZ'],
    'fazenda_nome': ['FAZENDA', 'NOME_FAZ', 'NM_FAZ', 'DE_FAZ', 'NOMEFAZ'],
    'aglomerado': ['AGLOMERADO', 'AGLOM', 'AGL', 'COD_AGLOM', 'CD_AGLOM', 'NUCLEO', 'SETOR'],
    'talhao': ['TALHAO', 'TALHÃO', 'ID_TALHAO', 'COD_TALHAO', 'CD_TALHAO', 'NUM_TALHAO', 'TAL', 'NOME'],
    'area_ha': ['HA', 'AREA_HA', 'AREA', 'AREAHA'],
}

# estrada: faixa estreita e comprida (mesmos números da v1)
ESTRADA = {'largura_min': 3, 'largura_max': 20, 'comprimento_min': 100, 'alongamento_min': 20}


def garantir_padroes():
    with Sessao() as s:
        tem = set(s.scalars(select(Regra.chave)))
        for chave, r in PADRAO.items():
            if chave not in tem:
                s.add(Regra(chave=chave, ativo=True, params=dict(r['params'])))
        if 'campos' not in tem:
            s.add(Regra(chave='campos', ativo=True, params={k: list(v) for k, v in CAMPOS_PADRAO.items()}))
        s.commit()


def carregar(s):
    """{chave: {'ativo', 'params'}} com os padrões preenchendo o que faltar."""
    guardadas = {r.chave: r for r in s.scalars(select(Regra))}
    out = {}
    for chave, r in PADRAO.items():
        g = guardadas.get(chave)
        out[chave] = {'ativo': g.ativo if g else True, 'params': {**r['params'], **((g.params if g else None) or {})}}
    g = guardadas.get('campos')
    out['campos'] = {**CAMPOS_PADRAO, **((g.params if g else None) or {})}
    return out


def como_json(s):
    regras = carregar(s)
    guardadas = {r.chave: r for r in s.scalars(select(Regra))}
    lista = []
    for chave, r in PADRAO.items():
        g = guardadas.get(chave)
        lista.append({'chave': chave, 'nome': r['nome'], 'descricao': r['descricao'], 'severidade': r['severidade'],
                      'ativo': regras[chave]['ativo'], 'params': regras[chave]['params'], 'padrao': r['params'],
                      'atualizadoPor': g.atualizado_por if g else '', 'atualizadoEm': g.atualizado_em if g else None})
    return {'regras': lista, 'campos': regras['campos'], 'camposPadrao': CAMPOS_PADRAO}
