"""Servidor do Verificador de Talhões.

Uso:
    python app.py                 -> abre em http://0.0.0.0:8080
    python app.py --porta 80      -> outra porta
"""
import argparse
import functools
import gzip
import io
import json
import os

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

import correcao
from analise import ErroArquivo, abrir_zip, analisar, ler_tabela_fazendas

PASTA = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(PASTA, 'static'), static_url_path='/static')
app.config['MAX_CONTENT_LENGTH'] = 300 * 1024 * 1024  # 300 MB por envio


def resposta_json(obj):
    corpo = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    resp = Response(corpo, mimetype='application/json')
    if 'gzip' in request.headers.get('Accept-Encoding', ''):
        # com os contornos de todos os talhões a resposta passa de 5 MB; comprimida fica ~1/4
        resp.set_data(gzip.compress(corpo, 5))
        resp.headers['Content-Encoding'] = 'gzip'
    return resp


def tratar_erros(f):
    @functools.wraps(f)
    def envolto(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ErroArquivo as e:
            return jsonify(erro=str(e)), 400
        except Exception:
            app.logger.exception('Falha em %s', request.path)
            return jsonify(erro='Algo deu errado no servidor. Tente de novo; se continuar, envie o .zip outra vez.'), 500
    return envolto


@app.get('/')
def inicio():
    return send_from_directory(app.static_folder, 'index.html')


@app.post('/api/verificar')
@tratar_erros
def verificar():
    arquivo = request.files.get('arquivo')
    if not arquivo or not arquivo.filename:
        return jsonify(erro='Nenhum arquivo foi enviado.'), 400
    if not arquivo.filename.lower().endswith('.zip'):
        return jsonify(erro='Esse arquivo não é .zip. Compacte os arquivos do shapefile (.shp, .dbf, .shx, .prj) em um .zip e envie de novo.'), 400
    # lido a cada envio: quem editar fazendas.csv não precisa reiniciar o servidor
    nomes = ler_tabela_fazendas(os.path.join(PASTA, 'fazendas.csv'))
    ctx = abrir_zip(arquivo.read(), arquivo.filename)
    resultado = analisar(ctx['campos'], ctx['linhas'], ctx['geoms'], ctx['geografico'], ctx['nome'], nomes)
    try:
        sessao, info = correcao.criar_sessao(ctx, resultado)
        resultado['correcao'] = {'sessao': sessao, **info}
        # os contornos dos talhões sem problema (a maior parte do arquivo) só servem para os
        # mapas: a tela busca em /contornos depois de mostrar o resultado, e a resposta fica leve
        resultado['outros'] = []
        resultado['contornosDepois'] = True
    except Exception:  # a verificação continua valendo mesmo se a correção não puder ser preparada
        app.logger.exception('Não preparou a correção de %s', arquivo.filename)
        resultado['correcao'] = {'podeCorrigir': False, 'motivo': 'Não consegui preparar a correção deste arquivo.'}
    app.logger.info('%s: %d talhões, %d com erro', arquivo.filename, resultado['total'], len(resultado['items']))
    return resposta_json(resultado)


@app.get('/api/correcao/<sid>/contornos')
@tratar_erros
def contornos(sid):
    return resposta_json({'outros': correcao.pegar(sid).outros})


# ---------- correção em etapas ----------
@app.post('/api/correcao/<sid>/simplificar')
@tratar_erros
def simplificar(sid):
    d = request.get_json(force=True) or {}
    return resposta_json(correcao.previa_simplificacao(correcao.pegar(sid), d.get('tolerancia', 1), d.get('escopo', 'muitos')))


@app.get('/api/correcao/<sid>/talhao/<int:idx>')
@tratar_erros
def talhao(sid, idx):
    return resposta_json(correcao.previa_talhao(correcao.pegar(sid), idx, request.args.get('tol', 1)))


@app.post('/api/correcao/<sid>/resumo')
@tratar_erros
def resumo(sid):
    plano = correcao.ler_plano((request.get_json(force=True) or {}).get('plano'))
    return resposta_json(correcao.resumo(correcao.pegar(sid), plano))


@app.post('/api/correcao/<sid>/baixar')
@tratar_erros
def baixar(sid):
    plano = correcao.ler_plano((request.get_json(force=True) or {}).get('plano'))
    nome, dados = correcao.gerar_zip(correcao.pegar(sid), plano)
    app.logger.info('Arquivo corrigido gerado: %s', nome)
    return send_file(io.BytesIO(dados), mimetype='application/zip', as_attachment=True, download_name=nome)


@app.errorhandler(413)
def grande_demais(_):
    return jsonify(erro='O arquivo passa de 300 MB. Envie um .zip menor.'), 413


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Verificador de Talhões')
    p.add_argument('--host', default='0.0.0.0', help='0.0.0.0 aceita acesso de outros computadores da rede')
    p.add_argument('--porta', type=int, default=8080)
    a = p.parse_args()
    from waitress import serve
    print(f'Verificador de Talhões rodando em http://{a.host}:{a.porta}  (Ctrl+C para parar)')
    serve(app, host=a.host, port=a.porta, threads=8, max_request_body_size=app.config['MAX_CONTENT_LENGTH'])
