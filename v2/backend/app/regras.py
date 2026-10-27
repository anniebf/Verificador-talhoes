"""Regras de detecção e correção, com os valores padrão. O admin muda os parâmetros na tela."""
from sqlalchemy import select

from .db import Regra, Sessao

# ordem = ordem em que aparecem na tela. `grupo` segue a seção 3.1.2 da documentação funcional:
# A inválidas/vazias, B multipartes, C buracos, D estradas, E sobreposição/lacuna, F linhas; mais
# atributos e desempenho. `limitacoes` aparece na revisão como risco da correção proposta.
PADRAO = {
    'geometria_vazia': {
        'nome': 'Geometria vazia', 'severidade': 'alta', 'grupo': 'A',
        'descricao': 'Registro sem desenho ou com desenho sem área (risco ou ponto). Correção: remover o registro.',
        'limitacoes': 'O registro inteiro sai da base, junto com os atributos. Confira se não falta o desenho no arquivo de origem.',
        'params': {},
    },
    'autointersecao': {
        'nome': 'Geometria inválida', 'severidade': 'alta', 'grupo': 'A',
        'descricao': 'Autointerseção, anel inválido ou outra inconsistência estrutural apontada pelo GEOS. Correção: reparar (make_valid) e validar.',
        'limitacoes': 'O reparo pode dividir o talhão em partes ou mudar a área de laços cruzados; a validação confere o resultado.',
        'params': {},
    },
    'vertices_duplicados': {
        'nome': 'Vértices duplicados', 'severidade': 'baixa', 'grupo': 'A',
        'descricao': 'Vértices seguidos no mesmo lugar. Correção: remover as repetições.',
        'limitacoes': 'Sem efeito na forma; só tira pontos repetidos.',
        'params': {'tolerancia_m': 0.01},
    },
    'parte_solta': {
        'nome': 'Múltiplas partes', 'severidade': 'media', 'grupo': 'B',
        'descricao': 'Talhão com componentes separados. Multiparte pode ser legítimo, então é suspeito. '
                     'Só propõe remover a parte quando ela é pequena em área E em proporção ao talhão.',
        'limitacoes': 'Remover a parte apaga uma área que pode ser plantio real. Confira na imagem.',
        'params': {'area_max_ha': 2.0, 'razao_max_pct': 5.0},
    },
    'buraco_interno': {
        'nome': 'Buraco interno', 'severidade': 'media', 'grupo': 'C',
        'descricao': 'Anel interno fechado no talhão (área vazia). Todo buraco precisa de um escape: a correção proposta abre um '
                     'corredor estreito do buraco até a borda do talhão, pelo caminho mais curto (de preferência numa borda que não '
                     'encosta em outro talhão). Faixas com formato de estrada vão para "Possível corte por estrada".',
        'limitacoes': 'O escape tira do talhão a área do corredor (largura x comprimento). Confira na prévia por onde ele sai.',
        'params': {'largura_escape_m': 1.0},
    },
    'corte_estrada': {
        'nome': 'Possível corte por estrada', 'severidade': 'media', 'grupo': 'D',
        'descricao': 'Faixa estreita e comprida dentro do talhão, entre partes dele ou entre talhões; ou estrada da camada de linhas '
                     'atravessando o talhão. Regra indicativa. Com camada de estradas, propõe recortar a faixa da estrada.',
        'limitacoes': 'Decida se a estrada deve dividir o talhão ou só atravessá-lo como feição independente. '
                      'O recorte usa a largura configurada e pode dividir o talhão em partes.',
        'params': {'largura_min_m': 3.0, 'largura_max_m': 20.0, 'comprimento_min_m': 100.0, 'cruzamento_min_m': 50.0,
                   'largura_faixa_m': 8.0, 'propor_recorte': True},
    },
    'sobreposicao': {
        'nome': 'Sobreposição', 'severidade': 'alta', 'grupo': 'E',
        'descricao': 'Dois talhões ocupando a mesma área. Correção: recortar a área sobreposta de um deles.',
        'limitacoes': 'A área recortada sai de um talhão só (o maior, por padrão). Confira qual dos dois está certo na imagem.',
        'params': {'area_min_m2': 1.0, 'recortar_de': 'maior'},
    },
    'lacuna': {
        'nome': 'Lacuna', 'severidade': 'media', 'grupo': 'E',
        'descricao': 'Vão totalmente cercado por talhões vizinhos (a vizinhança indica cobertura contínua). '
                     'Correção: juntar o vão ao vizinho com a maior divisa.',
        'limitacoes': 'O vão vai inteiro para um vizinho. Vãos largos podem ser carreadores ou áreas sem plantio.',
        'params': {'area_min_m2': 1.0, 'area_max_m2': 5000.0},
    },
    'estrada_desconectada': {
        'nome': 'Estrada desconectada', 'severidade': 'media', 'grupo': 'F',
        'descricao': 'Ponta de uma linha que para perto de outra sem encostar (possível desconexão). Correção: ligar a ponta à linha mais próxima.',
        'limitacoes': 'Pontas soltas podem ser estradas sem saída legítimas; só as que param perto de outra linha são apontadas.',
        'params': {'tolerancia_m': 0.5, 'distancia_max_m': 15.0},
    },
    'segmento_isolado': {
        'nome': 'Segmento isolado', 'severidade': 'baixa', 'grupo': 'F',
        'descricao': 'Linha que não encosta em nenhuma outra. Circuitos fechados não contam. Correção manual.',
        'limitacoes': '',
        'params': {'tolerancia_m': 0.5},
    },
    'atributo_ausente': {
        'nome': 'Atributo ausente', 'severidade': 'media', 'grupo': 'Atributos',
        'descricao': 'Talhão sem código de fazenda ou sem número de talhão, nos campos escolhidos para o lote. Correção manual.',
        'limitacoes': '',
        'params': {},
    },
    'talhao_duplicado': {
        'nome': 'Talhão duplicado', 'severidade': 'media', 'grupo': 'Atributos',
        'descricao': 'Mesmo número de talhão repetido na mesma fazenda/aglomerado. Correção manual.',
        'limitacoes': '',
        'params': {},
    },
    'excesso_vertices': {
        'nome': 'Excesso de vértices', 'severidade': 'baixa', 'grupo': 'Desempenho',
        'descricao': 'Talhão com vértices demais (pesa no QGIS). Correção explícita: simplificar com a tolerância configurada.',
        'limitacoes': 'A simplificação mexe no contorno e na área; só é proposta se a área mudar menos que o limite.',
        'params': {'max_vertices': 1000, 'espacamento_min_m': 5.0, 'tolerancia_m': 1.0, 'variacao_area_max_pct': 0.5},
    },
}

# não é um tipo de erro: nomes de colunas aceitos para montar a hierarquia e recalcular área
CAMPOS_PADRAO = {
    'fazenda': ['FAZ', 'COD_FAZ', 'CD_FAZ', 'ID_FAZ', 'CODFAZ', 'FAZENDA', 'DE_FAZ'],
    'fazenda_nome': ['FAZENDA', 'NOME_FAZ', 'NM_FAZ', 'DE_FAZ', 'NOMEFAZ'],
    # detalhe do nome: quando FAZENDA é o grupo (MALU) e DE_FAZ a fazenda em si (EMA), aparece "MALU · EMA"
    'fazenda_detalhe': ['DE_FAZ', 'DESC_FAZ', 'DS_FAZ'],
    # aglomerado = grupo de fazendas (na base padrão, CLIENTE junta as fazendas 4301, 4302, 4303…)
    'aglomerado': ['AGLOMERADO', 'AGLOM', 'AGL', 'COD_AGLOM', 'CD_AGLOM', 'NUCLEO', 'CLIENTE'],
    'talhao': ['TALHAO', 'TALHÃO', 'ID_TALHAO', 'COD_TALHAO', 'CD_TALHAO', 'NUM_TALHAO', 'TAL'],
    'area_ha': ['HA', 'AREA_HA', 'AREA', 'AREAHA'],
}

# faixa estreita e comprida: comprimento ao menos 20x a largura (como na v1)
ALONGAMENTO_ESTRADA = 20


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
        guardado = (g.params if g else None) or {}
        # só os parâmetros que a regra usa hoje (um parâmetro antigo guardado no banco é ignorado)
        out[chave] = {'ativo': g.ativo if g else True, 'params': {k: guardado.get(k, v) for k, v in r['params'].items()}}
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
                      'grupo': r['grupo'], 'limitacoes': r['limitacoes'],
                      'ativo': regras[chave]['ativo'], 'params': regras[chave]['params'], 'padrao': r['params'],
                      'atualizadoPor': g.atualizado_por if g else '', 'atualizadoEm': g.atualizado_em if g else None})
    return {'regras': lista, 'campos': regras['campos'], 'camposPadrao': CAMPOS_PADRAO}
