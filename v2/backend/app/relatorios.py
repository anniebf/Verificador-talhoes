"""Relatórios de execução (seções 3.1.4, 3.3.4 e 7): auditoria e correção, em CSV e PDF."""
import csv
import io

from sqlalchemy import select

from . import estados
from .correcao import DESCRICAO_OP
from .db import Execucao, Feicao, Historico, Inconsistencia, Lote
from .log_auditoria import _latin
from .regras import PADRAO


def _faz(chave):
    """Fazenda sem código (chave "#nome") aparece como "nome (sem código)"."""
    return f'{chave[1:]} (sem código)' if chave.startswith('#') else chave


def _ocorrencias(s, exe):
    """Ocorrências ligadas à execução: as detectadas (auditoria) ou as executadas (correção)."""
    if exe.tipo == 'auditoria':
        ids = set(s.scalars(select(Historico.inconsistencia_id).where(Historico.execucao_id == exe.id)))
    else:
        ids = {r['id'] for r in (exe.resumo or {}).get('resultados', [])}
    incs = {i.id: i for i in s.scalars(select(Inconsistencia).where(Inconsistencia.id.in_(ids)))} if ids else {}
    fs = {f.id: f for f in s.scalars(select(Feicao).where(Feicao.id.in_({x for i in incs.values() for x in i.feicoes})))} if incs else {}
    return incs, fs


def _talhoes(i, fs):
    return ', '.join(dict.fromkeys((fs[x].talhao or (f'linha #{fs[x].indice_origem + 1}' if fs[x].linha else '?')) for x in i.feicoes if x in fs))


def _data(d):
    return d.strftime('%d/%m/%Y %H:%M:%S') if d else ''


def _cabecalho(exe, lote):
    r = exe.resumo or {}
    linhas = [
        ('Execução', f'#{exe.id} ({"auditoria" if exe.tipo == "auditoria" else "correção automática"})'),
        ('Lote', f'#{lote.id} {lote.nome}' if lote else str(exe.lote_id)),
        ('Usuário', exe.usuario), ('Início (UTC)', _data(exe.inicio)), ('Fim (UTC)', _data(exe.fim)),
        ('Situação', exe.status + (f' — {exe.erro}' if exe.erro else '')),
        ('Base de entrada', f'{exe.entrada} (versão {exe.versao_entrada})'),
        ('Base de saída', exe.saida or f'versão {exe.versao_saida}'),
        ('Fazendas', ', '.join(exe.fazendas) if exe.fazendas else 'todas'),
    ]
    if exe.tipo == 'auditoria':
        linhas += [
            ('Regras executadas', '; '.join(f'{PADRAO[k]["nome"]} {v}' if v else PADRAO[k]['nome'] for k, v in exe.regras.items() if k in PADRAO)),
            ('Feições analisadas', f'{r.get("feicoes", 0)} ({r.get("talhoes", 0)} talhões, {r.get("linhas", 0)} linhas)'),
            ('Ocorrências', f'{r.get("ocorrencias", 0)} ({r.get("comCorrecao", 0)} com correção automática)'),
            ('Por categoria', '; '.join(f'{PADRAO[k]["nome"]}: {v}' for k, v in r.get('porCategoria', {}).items())),
            ('Por fazenda', '; '.join(f'{k or "sem fazenda"}: {v}' for k, v in r.get('porFazenda', {}).items())),
            ('Por classificação', '; '.join(f'{estados.CLASSIFICACOES.get(k, k)}: {v}' for k, v in r.get('porClassificacao', {}).items())),
        ]
    else:
        linhas += [
            ('Operações', '; '.join(exe.regras.values())),
            ('Aprovadas', r.get('aprovadas', 0)), ('Corrigidas (validadas)', r.get('corrigidas', 0)),
            ('Falhas na correção', r.get('falhas', 0)), ('Pendentes', r.get('pendentes', 0)),
            ('Sem solução', r.get('semSolucao', 0)), ('Novos problemas na reauditoria', r.get('novosProblemas', 0)),
            ('Arquivo gerado', r.get('arquivo') or '—'),
        ]
    return linhas


def gerar_csv(s, exe):
    lote = s.get(Lote, exe.lote_id)
    incs, fs = _ocorrencias(s, exe)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    for k, v in _cabecalho(exe, lote):
        w.writerow([k, v])
    w.writerow([])
    if exe.tipo == 'auditoria':
        w.writerow(['ocorrencia', 'categoria', 'regra', 'classificacao', 'fazenda', 'talhao', 'camada_id_origem', 'descricao', 'motivo',
                    'area_m2', 'correcao_automatica', 'estado_atual'])
        for i in sorted(incs.values(), key=lambda i: i.id):
            orig = ', '.join(f'{fs[x].camada_id}:{fs[x].indice_origem}' for x in i.feicoes if x in fs)
            w.writerow([i.id, PADRAO[i.tipo]['grupo'], PADRAO[i.tipo]['nome'], estados.CLASSIFICACOES.get(i.classificacao),
                        _faz(i.fazenda), _talhoes(i, fs), orig, i.descricao, i.motivo, round(i.area_m2, 2),
                        DESCRICAO_OP[i.op['tipo']] if i.op else 'não (manual)', estados.ESTADOS.get(i.status, i.status)])
    else:
        w.writerow(['ocorrencia', 'regra', 'operacao', 'resultado', 'verificacao', 'ok', 'detalhe'])
        for r in exe.resumo.get('resultados', []):
            for c in r['validacao'] or [{'nome': '', 'ok': '', 'detalhe': ''}]:
                w.writerow([r['id'], r['nome'], r['operacao'], estados.ESTADOS.get(r['status'], r['status']), c['nome'],
                            'sim' if c['ok'] else 'não', c['detalhe']])
        w.writerow([])
        w.writerow(['feicao', 'fazenda', 'talhao', 'area_antes_ha', 'area_depois_ha', 'vertices_antes', 'vertices_depois', 'ajustes'])
        for a in exe.resumo.get('alteradas', []):
            w.writerow([a['id'], _faz(a['fazenda']), a['talhao'], a['areaAntes'], 'removido' if a['removida'] else a['areaDepois'],
                        a['verticesAntes'], a['verticesDepois'], ', '.join(a['ajustes'])])
    return ('﻿' + buf.getvalue()).encode('utf-8')


def gerar_pdf(s, exe):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    lote = s.get(Lote, exe.lote_id)
    incs, fs = _ocorrencias(s, exe)
    titulo = f'Relatório de {"auditoria" if exe.tipo == "auditoria" else "correção automática"} - execução #{exe.id}'
    pdf = FPDF(orientation='L', format='A4')
    pdf.set_auto_page_break(True, 12)
    pdf.set_title(titulo)
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 8, _latin(titulo), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)
    cab = FontFace(emphasis='BOLD', fill_color=(230, 236, 230))
    pdf.set_font('Helvetica', '', 8.5)
    with pdf.table(col_widths=(50, 227), text_align='LEFT', line_height=4.6, first_row_as_headings=False) as t:
        for k, v in _cabecalho(exe, lote):
            t.row([_latin(k), _latin(str(v))])
    pdf.ln(4)
    pdf.set_font('Helvetica', 'B', 11)
    if exe.tipo == 'auditoria':
        pdf.cell(0, 6, 'Ocorrências identificadas', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 7.5)
        with pdf.table(col_widths=(10, 32, 20, 22, 22, 115, 30, 26), headings_style=cab, line_height=4, text_align='LEFT') as t:
            t.row(['#', 'Regra', 'Classif.', 'Fazenda', 'Talhão', 'Descrição', 'Correção', 'Estado atual'])
            for i in sorted(incs.values(), key=lambda i: i.id):
                t.row([str(i.id), _latin(PADRAO[i.tipo]['nome']), _latin(estados.CLASSIFICACOES.get(i.classificacao, '')),
                       _latin(_faz(i.fazenda)), _latin(_talhoes(i, fs)), _latin(i.descricao),
                       _latin(DESCRICAO_OP[i.op['tipo']] if i.op else 'manual'), _latin(estados.ESTADOS.get(i.status, i.status))])
    else:
        pdf.cell(0, 6, 'Correções executadas e validação', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 7.5)
        with pdf.table(col_widths=(10, 30, 45, 26, 166), headings_style=cab, line_height=4, text_align='LEFT') as t:
            t.row(['#', 'Regra', 'Operação', 'Resultado', 'Verificações'])
            for r in exe.resumo.get('resultados', []):
                ver = ' | '.join(f'{"OK" if c["ok"] else "FALHOU"} {c["nome"]}: {c["detalhe"]}' for c in r['validacao'])
                t.row([str(r['id']), _latin(r['nome']), _latin(r['operacao']), _latin(estados.ESTADOS.get(r['status'], r['status'])), _latin(ver)])
        if exe.resumo.get('alteradas'):
            pdf.ln(3)
            pdf.set_font('Helvetica', 'B', 11)
            pdf.cell(0, 6, _latin('Feições alteradas na nova versão'), new_x='LMARGIN', new_y='NEXT')
            pdf.set_font('Helvetica', '', 7.5)
            with pdf.table(col_widths=(16, 30, 30, 30, 30, 30, 111), headings_style=cab, line_height=4, text_align='LEFT') as t:
                t.row(['Feição', 'Fazenda', 'Talhão', 'Área antes (ha)', 'Área depois (ha)', 'Vértices', 'Ajustes'])
                for a in exe.resumo['alteradas']:
                    t.row([str(a['id']), _latin(_faz(a['fazenda'])), _latin(a['talhao']), f'{a["areaAntes"]:.4f}',
                           'removido' if a['removida'] else f'{a["areaDepois"]:.4f}', f'{a["verticesAntes"]} -> {a["verticesDepois"]}',
                           _latin(', '.join(a['ajustes']))])
    return bytes(pdf.output())


def execucoes(s, lote_id=None, limite=200):
    q = select(Execucao).order_by(Execucao.id.desc()).limit(limite)
    if lote_id:
        q = q.where(Execucao.lote_id == lote_id)
    return list(s.scalars(q))
