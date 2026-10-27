"""Configuração lida do ambiente (com valores padrão para rodar local)."""
import os
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DADOS = Path(os.getenv('VT_DADOS', BASE / 'dados'))
DADOS.mkdir(parents=True, exist_ok=True)

DB_URL = os.getenv('VT_DB', f"sqlite:///{(DADOS / 'verificador.db').as_posix()}")

# frontend compilado (npm run build) servido pelo próprio backend
FRONTEND = Path(os.getenv('VT_FRONTEND', BASE.parent / 'frontend' / 'dist'))


def _segredo():
    if os.getenv('VT_SEGREDO'):
        return os.environ['VT_SEGREDO']
    arq = DADOS / '.segredo'
    if not arq.exists():
        arq.write_text(secrets.token_urlsafe(48))
    return arq.read_text().strip()


SEGREDO = _segredo()
TOKEN_HORAS = int(os.getenv('VT_TOKEN_HORAS', 12))

# primeiro admin, criado quando o banco está vazio; a senha precisa ser trocada no primeiro login
ADMIN_USUARIO = os.getenv('VT_ADMIN_USUARIO', 'admin')
ADMIN_SENHA = os.getenv('VT_ADMIN_SENHA', 'admin')

LIMITE_UPLOAD = 500 * 1024 * 1024        # 500 MB por envio
LIMITE_DESCOMPACTADO = 2 * 1024 ** 3     # 2 GB depois de extrair
