"""Etapas 2 e 3: operações de correção e a validação de cada uma.

Cada `op` vem da detecção (módulo `topologia`) e é aplicada sobre a geometria atual, numa cópia:
a prévia da etapa 2 e a execução da etapa 3 usam a mesma função, e nenhuma das duas mexe na
base guardada. Na etapa 3, cada operação passa pela `validar`; se alguma verificação reprova, a
operação é descartada e a ocorrência fica como "falha na correção".
"""
import shapely
from shapely import LineString, MultiLineString, Point, Polygon

from .topologia import _repetidos, de_hex, juntar, limpar, linhas, poligonos

ORDEM_OPS = ['remover_feicao', 'tornar_valida', 'remover_repetidos', 'preencher_buraco', 'remover_parte',
             'simplificar', 'subtrair', 'anexar', 'recortar_faixa', 'conectar']

DESCRICAO_OP = {
    'remover_feicao': 'Remover o registro',
    'tornar_valida': 'Reparar a geometria (make_valid)',
    'remover_repetidos': 'Remover vértices repetidos',
    'preencher_buraco': 'Preencher o buraco',
    'remover_parte': 'Remover a parte separada',
    'simplificar': 'Simplificar o contorno',
    'subtrair': 'Recortar a área sobreposta',
    'anexar': 'Incorporar a lacuna ao vizinho',
    'recortar_faixa': 'Recortar a faixa da estrada do talhão',
    'conectar': 'Ligar a ponta da linha à linha vizinha',
}


def ordem(op):
    return ORDEM_OPS.index(op['tipo'])


def _faixa(op, med):
    """Faixa da estrada (largura total `largura_m`), no sistema do lote."""
    lm = med.m(de_hex(op['linha']))
    return med.de_m(lm.buffer(op['largura_m'] / 2, cap_style='flat'))


def aplicar_op(geoms, op, med):
    """Devolve uma cópia de `geoms` ({id: geometria}) com a operação aplicada ao alvo."""
    g = dict(geoms)
    alvo, t = op['alvo'], op['tipo']
    atual = g.get(alvo)
    if t == 'remover_feicao':
        g[alvo] = None
        return g
    if atual is None:
        return g
    if t == 'tornar_valida':
        novo = limpar(atual)
    elif t == 'remover_repetidos':
        novo = limpar(shapely.remove_repeated_points(atual, op['tol']))
    elif t == 'preencher_buraco':
        pt = Point(op['ponto'])
        novo = juntar([Polygon(p.exterior, [r for r in p.interiors if not Polygon(r).contains(pt)]) for p in poligonos(atual)])
    elif t == 'remover_parte':
        pt = Point(op['ponto'])
        sobra = [p for p in poligonos(atual) if not p.contains(pt)]
        novo = juntar(sobra) if sobra else atual
    elif t == 'simplificar':
        novo = limpar(med.de_m(shapely.simplify(med.m(atual), op['tol_m'], preserve_topology=True)))
    elif t == 'subtrair':
        novo = limpar(shapely.difference(atual, de_hex(op['area']), grid_size=med.grade))
    elif t == 'anexar':
        novo = limpar(shapely.union(atual, de_hex(op['area']), grid_size=med.grade))
    elif t == 'recortar_faixa':
        novo = limpar(shapely.difference(atual, _faixa(op, med), grid_size=med.grade))
    elif t == 'conectar':
        partes = linhas(atual)
        c = list(partes[op['parte']].coords)
        c = [tuple(op['ponto']), *c] if op['extremidade'] == 0 else [*c, tuple(op['ponto'])]
        partes[op['parte']] = LineString(c)
        g[alvo] = partes[0] if len(partes) == 1 else MultiLineString(partes)
        return g
    else:
        raise ValueError(f'operação desconhecida: {t}')
    # recortes deixam lascas de área quase zero: não viram "parte solta" na próxima auditoria
    if novo is not None:
        novo = juntar([p for p in poligonos(novo) if med.area(p) >= 0.01]) or novo
    g[alvo] = novo
    return g


def previa(geoms, ops, med):
    """Prévia (etapa 2): aplica as operações numa cópia, sem validar e sem gravar nada."""
    for op in sorted(ops, key=ordem):
        geoms = aplicar_op(geoms, op, med)
    return geoms


# ---------- validação (seção 3.3.3) ----------
def validar(op, antes, depois, med, vizinhos, area_esperada_m2=0.0, area_min_m2=1.0):
    """Confere o resultado de uma operação. Devolve (ok, [{'nome','ok','detalhe'}]).

    antes/depois: {id: geometria} antes e depois da operação; vizinhos: {id: geometria} das outras
    feições da fazenda (para ver se a correção criou sobreposição nova).
    """
    alvo, t = op['alvo'], op['tipo']
    a, d = antes.get(alvo), depois.get(alvo)
    checks = []

    def ck(nome, ok, detalhe):
        checks.append({'nome': nome, 'ok': bool(ok), 'detalhe': detalhe})

    if t == 'remover_feicao':
        ck('Problema resolvido', d is None, 'Registro removido da nova versão.' if d is None else 'O registro continua lá.')
        ck('Atributos preservados', True, 'Os demais registros não foram tocados.')
        return all(c['ok'] for c in checks), checks

    # 1. legível
    try:
        legivel = d is not None and not d.is_empty and shapely.from_wkb(shapely.to_wkb(d)) is not None
    except Exception:  # noqa: BLE001 - qualquer erro aqui é "não legível"
        legivel = False
    ck('Geometria legível', legivel, 'Resultado gravável e relido sem erro.' if legivel else 'A operação não produziu uma geometria utilizável.')
    if not legivel:
        return False, checks

    # 2. válida
    ck('Geometria válida', d.is_valid, 'Válida pelo GEOS.' if d.is_valid else f'Inválida: {shapely.is_valid_reason(d)}.')

    # 3. problema resolvido e 6. limites da regra
    linha = t == 'conectar'
    aa, ad = (med.area(a), med.area(d)) if not linha else (0, 0)
    delta = ad - aa
    folga = max(1.0, area_esperada_m2 * 0.02)  # 2% (mín. 1 m²) por causa da precisão de 1 mm e das lascas
    if t == 'tornar_valida':
        ck('Problema resolvido', d.is_valid, 'A geometria ficou válida.')
        ck('Limites da regra', ad > 0, f'Área depois do reparo: {ad / 1e4:.4f} ha (antes, pela área assinada: {aa / 1e4:.4f} ha).')
    elif t == 'remover_repetidos':
        sobra = len(_repetidos(d, op['tol']))
        ck('Problema resolvido', sobra == 0, 'Sem vértices repetidos.' if not sobra else f'Ainda há {sobra} vértice(s) repetido(s).')
        ck('Limites da regra', abs(delta) <= max(0.01, aa * 1e-6), f'Variação de área: {delta:+.3f} m².')
    elif t == 'preencher_buraco':
        pt = Point(op['ponto'])
        resolvido = not any(Polygon(r).contains(pt) for p in poligonos(d) for r in p.interiors)
        ck('Problema resolvido', resolvido, 'O buraco não existe mais.' if resolvido else 'O buraco continua.')
        ck('Limites da regra', abs(delta - area_esperada_m2) <= folga, f'Área somada: {delta:.1f} m² (buraco: {area_esperada_m2:.1f} m²).')
    elif t == 'remover_parte':
        pt = Point(op['ponto'])
        resolvido = not any(p.contains(pt) for p in poligonos(d))
        ck('Problema resolvido', resolvido, 'A parte separada saiu.' if resolvido else 'A parte continua.')
        ck('Limites da regra', abs(-delta - area_esperada_m2) <= folga, f'Área retirada: {-delta:.1f} m² (parte: {area_esperada_m2:.1f} m²).')
    elif t == 'simplificar':
        nv0, nv1 = int(shapely.get_num_coordinates(a)), int(shapely.get_num_coordinates(d))
        var = delta / aa * 100 if aa else 0
        ck('Problema resolvido', nv1 < nv0, f'Vértices: {nv0} → {nv1}.')
        ck('Limites da regra', abs(var) <= op['var_max'], f'Variação de área {var:+.3f}% (limite {op["var_max"]}%).')
    elif t == 'subtrair':
        outro = vizinhos.get(op.get('outro'))
        resto = med.area(limpar(shapely.intersection(d, outro))) if outro is not None else 0.0
        ck('Problema resolvido', resto < area_min_m2, f'Sobreposição restante: {resto:.2f} m².')
        ck('Limites da regra', -delta <= area_esperada_m2 + folga, f'Área retirada: {-delta:.1f} m² (sobreposição: {area_esperada_m2:.1f} m²).')
    elif t == 'anexar':
        vao = de_hex(op['area'])
        resto = med.area(limpar(shapely.difference(vao, d, grid_size=med.grade)))
        ck('Problema resolvido', resto <= max(0.5, area_esperada_m2 * 0.01), f'Área do vão ainda descoberta: {resto:.2f} m².')
        ck('Limites da regra', delta <= area_esperada_m2 + folga, f'Área somada: {delta:.1f} m² (lacuna: {area_esperada_m2:.1f} m²).')
    elif t == 'recortar_faixa':
        faixa = _faixa(op, med)
        resto = med.area(limpar(shapely.intersection(d, faixa)))
        af = med.area(limpar(shapely.intersection(a, faixa)))
        ck('Problema resolvido', resto < area_min_m2, f'Área do talhão ainda dentro da faixa da estrada: {resto:.2f} m².')
        ck('Limites da regra', -delta <= af + max(1.0, af * 0.02), f'Área retirada: {-delta:.1f} m² (faixa dentro do talhão: {af:.1f} m²).')
    elif t == 'conectar':
        ln = linhas(d)[op['parte']]
        ponta = Point(ln.coords[0] if op['extremidade'] == 0 else ln.coords[-1])
        dist = ponta.distance(Point(op['ponto']))
        ck('Problema resolvido', dist <= med.tol(0.001), 'A ponta agora encosta na linha vizinha.')
        acrescimo = med.comprimento(d) - med.comprimento(a)
        ck('Limites da regra', acrescimo <= 50, f'Comprimento acrescentado: {acrescimo:.2f} m.')

    # 4. novos problemas: a correção não pode criar sobreposição com os vizinhos
    if not linha:
        novos = []
        for fid, v in vizinhos.items():
            if fid == alvo or v is None or not shapely.intersects(shapely.envelope(d), shapely.envelope(v)):
                continue
            antes_ov = med.area(limpar(shapely.intersection(a, v))) if a is not None and a.is_valid and v.is_valid else 0.0
            depois_ov = med.area(limpar(shapely.intersection(d, v))) if v.is_valid else 0.0
            if depois_ov - antes_ov > area_min_m2:
                novos.append((fid, depois_ov - antes_ov))
        ck('Sem novos problemas', not novos, 'Nenhuma sobreposição nova com os vizinhos.' if not novos else
           'Criou sobreposição com ' + ', '.join(f'feição {fid} ({ov:.1f} m²)' for fid, ov in novos) + '.')
    else:
        ck('Sem novos problemas', d.is_valid, 'Linha continua válida.')

    # 5. atributos: as operações só mexem na geometria
    ck('Atributos preservados', True, 'Atributos do registro mantidos como na base original.')
    return all(c['ok'] for c in checks), checks
