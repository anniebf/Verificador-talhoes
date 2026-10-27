"""Registro imutável de tudo o que muda no sistema, encadeado por hash (como um livro-razão)."""
import csv
import hashlib
import io
import json
import threading

from sqlalchemy import select

from .db import Log, agora

# um escritor por vez: a corrente de hash não pode bifurcar
trava_escrita = threading.RLock()


def _hash(anterior, campos):
    return hashlib.sha256((anterior + json.dumps(campos, sort_keys=True, default=str)).encode()).hexdigest()


def _campos(l: Log):
    # o SQLite devolve a data sem fuso; o hash usa sempre o mesmo formato (UTC, sem fuso)
    return {'data_hora': l.data_hora.strftime('%Y-%m-%dT%H:%M:%S') if l.data_hora else '','usuario': l.usuario, 'acao': l.acao,
            'lote_id': l.lote_id, 'versao': l.versao, 'fazenda': l.fazenda, 'talhao': l.talhao,
            'descricao': l.descricao, 'geom_antes': l.geom_antes, 'geom_depois': l.geom_depois}


def registrar(s, usuario, acao, descricao, lote_id=None, versao=None, fazenda='', talhao='', antes=None, depois=None):
    """Acrescenta uma linha ao log dentro da transação `s` (chamar com trava_escrita)."""
    ultimo = s.scalar(select(Log.hash).order_by(Log.id.desc()).limit(1)) or ''
    l = Log(data_hora=agora().replace(microsecond=0), usuario=usuario, acao=acao, lote_id=lote_id, versao=versao,
            fazenda=fazenda or '', talhao=talhao or '', descricao=descricao,
            geom_antes=antes.wkt if antes is not None else None, geom_depois=depois.wkt if depois is not None else None)
    l.hash_anterior = ultimo
    l.hash = _hash(ultimo, _campos(l))
    s.add(l)
    s.flush()
    return l


def verificar(s):
    """Refaz a corrente inteira. Devolve (ok, id da primeira linha adulterada)."""
    anterior = ''
    for l in s.scalars(select(Log).order_by(Log.id)):
        if l.hash_anterior != anterior or _hash(anterior, _campos(l)) != l.hash:
            return False, l.id
        anterior = l.hash
    return True, None


def como_json(l: Log, com_geom=False):
    d = {'id': l.id, 'dataHora': l.data_hora, 'usuario': l.usuario, 'acao': l.acao, 'loteId': l.lote_id,
         'versao': l.versao, 'fazenda': l.fazenda, 'talhao': l.talhao, 'descricao': l.descricao,
         'temGeometria': bool(l.geom_antes or l.geom_depois), 'hash': l.hash}
    if com_geom:
        d['geomAntes'], d['geomDepois'] = l.geom_antes, l.geom_depois
    return d


ACOES = {
    'login': 'Login', 'importacao': 'Importação', 'mesclagem': 'Mesclagem', 'auditoria': 'Auditoria',
    'decisao': 'Decisão', 'correcao': 'Correção', 'falha': 'Falha na correção', 'versao': 'Nova versão',
    'exportacao': 'Exportação', 'usuario': 'Usuários', 'regra': 'Regras', 'lote': 'Lote',
}


def gerar_csv(linhas):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(['id', 'data_hora_utc', 'usuario', 'acao', 'lote', 'versao', 'fazenda', 'talhao', 'descricao',
                'geometria_antes_wkt', 'geometria_depois_wkt', 'hash'])
    for l in linhas:
        w.writerow([l.id, l.data_hora.strftime('%Y-%m-%d %H:%M:%S'), l.usuario, ACOES.get(l.acao, l.acao), l.lote_id or '',
                    l.versao or '', l.fazenda, l.talhao, l.descricao, l.geom_antes or '', l.geom_depois or '', l.hash])
    return ('﻿' + buf.getvalue()).encode('utf-8')  # BOM: o Excel abre com acento certo


def gerar_pdf(linhas, titulo, filtros=''):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    pdf = FPDF(orientation='L', format='A4')
    pdf.set_auto_page_break(True, 12)
    pdf.set_title(titulo)
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 8, _latin(titulo), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 9)
    pdf.cell(0, 5, _latin(f'Gerado em {agora().strftime("%d/%m/%Y %H:%M")} UTC  ·  {len(linhas)} registros  {filtros}'),
             new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)
    pdf.set_font('Helvetica', '', 7.5)
    cab = FontFace(emphasis='BOLD', fill_color=(230, 236, 230))
    with pdf.table(col_widths=(9, 26, 20, 24, 10, 9, 38, 20, 121), headings_style=cab, line_height=4.2,
                   text_align='LEFT') as t:
        t.row(['#', 'Data/hora (UTC)', 'Usuário', 'Ação', 'Lote', 'Ver.', 'Fazenda', 'Talhão', 'Descrição'])
        for l in linhas:
            t.row([str(l.id), l.data_hora.strftime('%d/%m/%Y %H:%M:%S'), _latin(l.usuario), _latin(ACOES.get(l.acao, l.acao)),
                   str(l.lote_id or ''), str(l.versao or ''), _latin(l.fazenda), _latin(l.talhao), _latin(l.descricao)])
    return bytes(pdf.output())


def _latin(t):
    # fontes padrão do PDF são latin-1; troca o que não couber
    return (t or '').replace('→', '->').replace('·', '-').replace('—', '-').encode('latin-1', 'replace').decode('latin-1')
