"""Login, tokens e perfis.

Por enquanto só usuários locais (senha no banco). O campo `provedor` já existe para o login
pelo Active Directory / LDAP, que entra numa próxima etapa: `autenticar` é o ponto onde ele liga.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from . import config
from .db import Sessao, Usuario, agora

PERFIS = ('admin', 'analista')
ITERACOES = 310_000


def hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac('sha256', senha.encode(), sal, ITERACOES)
    return f'pbkdf2_sha256${ITERACOES}${sal.hex()}${h.hex()}'


def conferir_senha(senha: str, guardado: str) -> bool:
    try:
        _, it, sal, h = guardado.split('$')
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac('sha256', senha.encode(), bytes.fromhex(sal), int(it))
    return hmac.compare_digest(calc.hex(), h)


def criar_admin_inicial():
    with Sessao() as s:
        if s.scalar(select(Usuario.id).limit(1)) is None:
            s.add(Usuario(usuario=config.ADMIN_USUARIO, nome='Administrador', perfil='admin',
                          senha_hash=hash_senha(config.ADMIN_SENHA), trocar_senha=True))
            s.commit()


def autenticar(s, usuario: str, senha: str) -> Usuario | None:
    u = s.scalar(select(Usuario).where(Usuario.usuario == usuario.strip().lower()))
    if not u or not u.ativo:
        return None
    if u.provedor == 'ad':
        return None  # login pelo AD ainda não implementado
    if not conferir_senha(senha, u.senha_hash):
        return None
    u.ultimo_login = agora()
    return u


def gerar_token(u: Usuario) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=config.TOKEN_HORAS)
    return jwt.encode({'sub': str(u.id), 'perfil': u.perfil, 'exp': exp}, config.SEGREDO, algorithm='HS256')


def usuario_atual(request: Request) -> Usuario:
    cab = request.headers.get('authorization', '')
    token = cab[7:] if cab.lower().startswith('bearer ') else ''
    if not token:
        raise HTTPException(401, 'Faça login para continuar.')
    try:
        dados = jwt.decode(token, config.SEGREDO, algorithms=['HS256'])
    except jwt.PyJWTError:
        raise HTTPException(401, 'Sessão expirada. Entre de novo.')
    with Sessao() as s:
        u = s.get(Usuario, int(dados['sub']))
    if not u or not u.ativo:
        raise HTTPException(401, 'Usuário desativado.')
    return u


def exigir_admin(u: Usuario = Depends(usuario_atual)) -> Usuario:
    if u.perfil != 'admin':
        raise HTTPException(403, 'Só o administrador pode fazer isso.')
    return u


def usuario_json(u: Usuario):
    return {'id': u.id, 'usuario': u.usuario, 'nome': u.nome, 'email': u.email, 'perfil': u.perfil,
            'provedor': u.provedor, 'ativo': u.ativo, 'trocarSenha': u.trocar_senha,
            'criadoEm': u.criado_em, 'ultimoLogin': u.ultimo_login}
