# Verificador de Talhões v2

Plataforma web para **auditar, revisar visualmente e corrigir** shapefiles de fazendas, aglomerados
e talhões antes de mandar as camadas para produção no QGIS. A v1 (pasta de cima) continua
funcionando à parte.

Esta é a primeira fase: o sistema completo com **login local e perfil de administrador**.
Usuários do Active Directory e o plugin do QGIS entram nas próximas fases (o banco e a API já
têm os ganchos para isso).

## Como rodar

**Windows:** dê dois cliques em `iniciar.bat` (na primeira vez ele instala tudo; precisa do
Python 3.11+ e do Node.js). Abra `http://localhost:8000`.

**Primeiro acesso:** usuário `admin`, senha `admin`. O sistema obriga a trocar a senha na entrada.
Para outra senha inicial, defina `VT_ADMIN_SENHA` antes de iniciar pela primeira vez.

Manualmente:

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

```bash
cd frontend
npm install && npm run build      # o backend serve a pasta dist/
npm run dev                       # ou: tela em modo desenvolvimento em :5173 (usa a API em :8000)
```

**Dados de exemplo** (um erro de cada tipo, 3 fazendas): `python scripts/gerar_exemplo.py`
dentro de `backend` gera `dados/exemplo/fazendas_exemplo.zip` e `fazenda_extra.zip` (para testar
a mesclagem). **Testes** do fluxo inteiro pela API: `python -m pytest tests -q`.

## O fluxo em 3 etapas

1. **Auditoria** (tela do lote) – importa `.zip`, `.rar` ou os arquivos soltos (`.shp`, `.dbf`,
   `.shx`, `.prj`, `.cpg`); vários shapefiles viram um lote só e dá para **mesclar** mais depois.
   A hierarquia fazenda → aglomerado → talhão é reconhecida pelos nomes das colunas (configurável).
   Painel esquerdo com fazendas, aglomerados, talhões e polígonos; mapa de satélite no centro;
   relatório de inconsistências por categoria à direita.
2. **Revisão visual** – como um *merge* do git: dois mapas sincronizados, **original com o erro**
   à esquerda e **proposta corrigida** à direita, com área, vértices e partes antes/depois. Dá
   para revisar um erro só ou o pacote completo da fazenda. Cada item precisa de uma decisão
   explícita (**Manter original** ou **Aceitar correção**; atalhos `M` e `A`) antes de seguir.
3. **Correção e envio** – aplica só o que foi aceito, grava uma **nova versão** do lote, registra
   cada decisão no log e reaudita a fazenda. Daí: baixar shapefile/GeoJSON da versão, relatório
   CSV/PDF, voltar ao mapa principal ou (próxima fase) enviar ao QGIS.

## O que é detectado e como é corrigido

| Erro | Correção proposta |
|---|---|
| Geometria vazia | remover o registro |
| Autointerseção / geometria inválida | refazer válida (`make_valid`) |
| Vértices duplicados | remover as repetições |
| Buraco interno | preencher (ignora faixas com formato de estrada e buracos acima do limite) |
| Parte solta | remover o pedaço |
| Excesso de vértices | simplificar (só se a área mudar menos que o limite) |
| Sobreposição entre talhões | recortar a área sobreposta do maior (ou do menor) |
| Lacuna fechada entre vizinhos | juntar ao vizinho com a maior divisa |
| Atributo ausente (fazenda/talhão) | só manual |
| Talhão duplicado na fazenda | só manual |

Os limites ficam em **Regras de correção** (só admin), e cada mudança vai para o log. As contas de
área e distância são feitas em metros (UTM estimado do lote) mesmo quando o shapefile está em
graus. Recortes e uniões usam precisão de 1 mm para não deixar lascas.

Ao exportar, o campo de área (`HA`/`AREA`…) é recalculado só nos talhões que mudaram; os demais
atributos saem como vieram.

## Versões e log de auditoria

- Cada correção aplicada cria uma versão nova do lote; qualquer versão pode ser baixada.
  Uma decisão "manter original" não volta a aparecer nas próximas auditorias.
- O **log de auditoria** guarda data/hora (UTC), usuário, lote, versão, fazenda, talhão,
  descrição e a **geometria antes e depois** (WKT). É imutável: o banco recusa `UPDATE`/`DELETE`
  e cada linha leva o hash da anterior; a tela mostra se a corrente está íntegra.

## Estrutura

| Caminho | O quê |
|---|---|
| `backend/app/main.py` | API (FastAPI) e entrega da tela |
| `backend/app/topologia.py` | detecção dos erros e as operações de correção (Shapely/PyProj) |
| `backend/app/servico.py` | importar, auditar, versionar, aplicar, exportar |
| `backend/app/importacao.py` | leitura de .zip/.rar/arquivos soltos (pyogrio/GDAL) |
| `backend/app/regras.py` | regras padrão e mapeamento de colunas |
| `backend/app/log_auditoria.py` | log encadeado por hash, CSV e PDF |
| `backend/app/auth.py`, `db.py` | login/perfis (admin, analista) e banco (SQLite) |
| `frontend/src/pages/` | telas: Lotes, Auditoria (etapa 1), Revisao (2), Correcao (3), Log, Usuários, Regras |

Configuração por variáveis de ambiente: `VT_DADOS` (pasta do banco), `VT_DB` (URL do banco;
padrão SQLite), `VT_SEGREDO` (chave dos tokens), `VT_ADMIN_USUARIO`/`VT_ADMIN_SENHA`.

## Próximas fases

- **Login pelo Active Directory / LDAP**: `auth.autenticar` é o ponto de entrada; o cadastro já
  aceita a origem "AD".
- **Plugin QGIS (PyQGIS)**: vai consumir `GET /api/lotes/{id}/exportar?formato=geojson` com o
  token do usuário e carregar a camada no projeto aberto.
- Banco PostgreSQL/PostGIS para muitos usuários ao mesmo tempo (hoje SQLite).
