# Verificador de Talhões v2

Plataforma web para **auditar, revisar e corrigir** bases geográficas de talhões (shapefiles de polígonos e,
opcionalmente, camadas de estradas em linhas), seguindo a Documentação Funcional 2.0. Nada é alterado sem a
aprovação do usuário, toda correção é validada depois de executada e a base original (versão 1) é sempre
preservada. A v1 (pasta de cima) continua funcionando à parte.

Esta fase cobre o **administrador** com login local. Active Directory e o plugin do QGIS são as próximas fases.
A documentação detalhada do funcionamento está no documento "Verificador de Talhões v2 — Documentação do
funcionamento" (Claude Docs).

## Como rodar

**Windows:** dois cliques em `iniciar.bat` (precisa de Python 3.11+ e Node.js 20+) → `http://localhost:8000`.

**Docker (servidor):** `docker compose up -d --build` nesta pasta → `http://<servidor>:8080`
(outra porta: `PORTA=81 docker compose up -d`). Dados no volume `dados`. O build ainda não foi testado.

**Primeiro acesso:** `admin` / `admin`; o sistema obriga a trocar a senha.

**Testes:** `python -m pytest tests -q` em `backend` (5 testes de ponta a ponta sobre a base controlada).
**Base de exemplo:** `python scripts/gerar_exemplo.py` em `backend` (um problema conhecido por regra).

## O fluxo

1. **Auditoria** — envio de .zip, .rar ou arquivos soltos; validação da entrada (.shp/.shx/.dbf obrigatórios,
   .prj, leitura, tipo de geometria); escolha das verificações; registro das ocorrências com classificação
   (confirmado / suspeito / inconclusivo), motivo, características e se há correção automática. Só lê a base.
2. **Revisão** — filtros por fazenda, talhão, categoria, estado e correção automática; detalhes da feição;
   prévia original × corrigida lado a lado (calculada numa cópia); decisão **Aprovar / Rejeitar / Manter
   pendente** (atalhos A / R / P), individual ou em lote (só as elegíveis).
3. **Correção automática** — relatório de preparação, confirmação, execução em segundo plano com progresso,
   **validação de cada correção** (legível, válida, problema resolvido, sem novas sobreposições, atributos
   preservados, limites da regra), nova versão, reauditoria das fazendas, relatório da execução e downloads.

Estados das ocorrências: Identificada, Em revisão, Aprovada, Rejeitada, Pendente, Corrigida, Falha na correção,
Não corrigível automaticamente. Cada execução (auditoria ou correção) tem número próprio e relatório PDF/CSV;
o log de auditoria é imutável e encadeado por hash.

## Regras

| Grupo | Regra | Correção automática |
|---|---|---|
| A | Geometria vazia | remover o registro |
| A | Geometria inválida | reparar (make_valid) |
| A | Vértices duplicados | remover repetições |
| B | Múltiplas partes (suspeito) | remover a parte só se ≤ 2 ha e ≤ 5% do talhão |
| C | Buraco interno | preencher só se ≤ 1 ha |
| D | Possível corte por estrada (indicativo) | recortar a faixa só com camada de estradas |
| E | Sobreposição | recortar do talhão maior |
| E | Lacuna cercada por vizinhos | juntar ao vizinho de maior divisa |
| F | Estrada desconectada | ligar a ponta à linha vizinha |
| F | Segmento isolado | manual |
| — | Atributo ausente, Talhão duplicado | manual |
| — | Excesso de vértices | simplificar só se a área mudar ≤ 0,5% |

Todos os limites são editáveis em **Regras de correção** (admin). As colunas de fazenda, aglomerado e talhão são
sugeridas na importação e confirmadas por lote em **Campos da hierarquia** (nada é presumido).

## Estrutura

| Caminho | O quê |
|---|---|
| `backend/app/topologia.py` | detecção (só lê) |
| `backend/app/correcao.py` | operações de correção e validação |
| `backend/app/servico.py` | importar, auditar, decidir, executar, versionar, exportar |
| `backend/app/estados.py`, `regras.py` | estados e catálogo de regras |
| `backend/app/relatorios.py`, `log_auditoria.py` | relatórios de execução e log imutável |
| `backend/app/importacao.py` | leitura e validação dos arquivos |
| `backend/app/main.py` | API (FastAPI); documentação interativa em `/docs` |
| `frontend/src/pages/` | Lotes, Auditoria, Revisao, Correcao, Execucoes, LogAuditoria, Usuarios, Regras |

Variáveis: `VT_DADOS`, `VT_DB`, `VT_SEGREDO`, `VT_TOKEN_HORAS`, `VT_ADMIN_USUARIO`, `VT_ADMIN_SENHA`.
