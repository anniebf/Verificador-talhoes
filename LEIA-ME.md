# Verificador de Talhões

Página web onde qualquer pessoa da rede envia o .zip do shapefile e vê quais talhões têm
**buracos**, **partes soltas** ou **geometria inválida**, separados por fazenda, com o desenho
do talhão e a coordenada de cada erro.

## Arquivos

| Arquivo | Para que serve |
|---|---|
| `app.py` | Servidor web (Flask + Waitress) |
| `analise.py` | Lê o .zip e encontra os erros (contas com numpy e Shapely) |
| `correcao.py` | Simplifica, corrige e grava o shapefile novo (usa Shapely) |
| `static/index.html` | A tela |
| `static/correcao.js` | A aba "Corrigir e baixar" |
| `iniciar.bat` | Liga o servidor no Windows |
| `requirements.txt` | Dependências (Flask, Waitress e Shapely) |
| `fazendas.csv` | Nomes das fazendas que não vêm no shapefile (código;nome) |
| `Dockerfile`, `compose.yaml`, `.dockerignore` | Para rodar em Docker |

## De onde vem o nome da fazenda

O código vem da coluna `FAZ`. O nome, nesta ordem:

1. **`fazendas.csv`**: se o código estiver lá com um nome, vale esse nome.
2. **Coluna `FAZENDA`** do shapefile, como nome principal.
3. **Coluna `DE_FAZ`** junto, quando diz outra coisa. Às vezes `FAZENDA` é o grupo e `DE_FAZ` a
   fazenda em si, então aparece `MALU · EMA (cód. 5004)`. Se as duas dizem o mesmo (ignorando
   acento e maiúscula), aparece uma vez só. Sem `FAZENDA`, vale só o `DE_FAZ`.

Em cada código vale o nome que mais aparece nos talhões dele; vazio, `NULL` e texto com acento
corrompido são ignorados.

## Aba "Corrigir e baixar"

Depois da verificação, a página avisa quando há talhões com **muitos vértices** (mais de 1.000,
ou um ponto a cada menos de 5 m). A aba guia a correção em 3 etapas separadas, e **nada muda
sem o usuário marcar**:

1. **Simplificar** – escolhe a tolerância (1 m recomendado) e quais talhões. Mostra vértices
   antes → depois e a variação de área de cada um; no mapa, os pontos de antes (vermelho) e de
   depois (verde). Se a área mudar mais de 0,5%, o talhão não é recomendado.
2. **Corrigir erros** – marca quais buracos preencher, quais partes soltas apagar, quais
   contornos invertidos regravar e quais registros sem área apagar. Estradas não entram.
3. **Revisar e baixar** – resumo de tudo, opção de recalcular os campos de área
   (`AREA`, `ha`) e o botão que baixa `<nome do arquivo>_simplificado.zip`, com o shapefile
   novo e o `relatorio_alteracoes.csv`.

Ao lado de cada etapa fica um **mapa de satélite** com todos os itens daquela etapa em cores
(legenda no canto do mapa): aprovados e não aprovados na etapa 1; buracos a preencher e partes
a apagar na etapa 2; e, na etapa 3, como cada talhão alterado fica (verde) sobre como era
(tracejado vermelho). Clicar no mapa marca o item na lista e clicar na lista mostra o item de
perto no mapa; **← Ver todos** volta para a visão geral e **Ampliar mapa** deixa o mapa maior.

**Desempenho:** com um arquivo de 2.166 talhões e 726 mil vértices, a verificação leva ~1,3 s
e a resposta tem ~0,5 MB. Os contornos dos talhões sem problema (só usados nos mapas) chegam
logo depois, à parte. Cada etapa da correção leva menos de 1 s, porque as simplificações ficam
guardadas enquanto a correção está aberta.

Talhões não alterados são copiados byte a byte, inclusive a tabela de atributos. O arquivo
enviado fica guardado na memória do servidor por 1 hora para a correção; depois disso é preciso
enviar de novo. Os limites (1.000 vértices, 5 m, 0,5%, 1 m²) ficam no começo do `correcao.py`.

A simplificação mexe em cada talhão separado, então pode criar pequenas sobreposições entre
vizinhos (geralmente faixas de menos de 1 m). O site não trata sobreposições; se precisar,
confira no QGIS depois de baixar o arquivo.

## Subir com Docker (qualquer servidor Linux ou Windows com Docker)

Copie a pasta `verificador_talhoes` para o servidor e, dentro dela, rode:

```bash
docker compose up -d --build
```

Pronto: `http://<ip-ou-dns-do-servidor>:8080`. O container reinicia sozinho se o servidor
reiniciar. Comandos úteis:

| Para | Comando |
|---|---|
| Usar outra porta (ex.: 80) | `PORTA=80 docker compose up -d` (no PowerShell: `$env:PORTA=80; docker compose up -d`) |
| Ver o log | `docker compose logs -f` |
| Atualizar depois de mudar o código | `docker compose up -d --build` |
| Parar | `docker compose down` |

O `fazendas.csv` da pasta fica ligado ao container: edite no servidor e o nome aparece no
próximo envio, sem reconstruir.

**Servidor sem internet para baixar a imagem base:** gere a imagem numa máquina com internet
e leve o arquivo:

```bash
docker build -t verificador-talhoes .
docker save verificador-talhoes -o verificador-talhoes.tar
# no servidor:
docker load -i verificador-talhoes.tar
docker run -d --restart unless-stopped -p 8080:8080 --name verificador-talhoes verificador-talhoes
```

No servidor, libere a porta no firewall só para a rede interna (veja o passo 2 abaixo).

## 1. Ligar o servidor sem Docker (Windows)

Precisa do Python 3.10 ou mais novo instalado no computador que vai ser o servidor.

1. Copie a pasta `verificador_talhoes` para esse computador (ex.: `C:\verificador_talhoes`).
2. Dê dois cliques em `iniciar.bat`. Na primeira vez ele instala o que precisa (leva um minuto).
3. Teste no próprio servidor: abra `http://localhost:8080`.

Para usar outra porta, abra o terminal na pasta e rode `iniciar.bat 80`.

## 2. Liberar o acesso pela rede

No servidor, abra o PowerShell **como administrador** (menu Iniciar → digite PowerShell →
botão direito → **Executar como administrador**) e libere a porta só para a rede interna:

```powershell
New-NetFirewallRule -DisplayName "Verificador de Talhoes" -Direction Inbound -Protocol TCP -LocalPort 8080 -Profile Domain,Private -Action Allow
```

Se aparecer **"Acesso negado"**, o PowerShell não foi aberto como administrador ou o seu usuário
não tem permissão de administrador. Nesse caso peça para a TI criar essa regra.

De outro computador, teste com o IP do servidor: `http://192.168.x.x:8080`
(veja o IP com `ipconfig` no servidor).

## 3. Apontar o seu DNS

Peça ao responsável pelo DNS (ou faça no seu painel) um registro apontando para o servidor:

- **Registro A**: `talhoes.suaempresa.com.br` → IP do servidor
- ou **CNAME**: `talhoes.suaempresa.com.br` → nome do servidor (ex.: `srv-gis01.suaempresa.local`)

Depois o acesso fica `http://talhoes.suaempresa.com.br:8080`.

**Sem o `:8080` no endereço:** rode na porta 80 (`iniciar.bat 80`) e libere a porta 80 no firewall.
Se já existe um IIS/Apache/Nginx usando a porta 80 ou você quer HTTPS, deixe o verificador na 8080
e configure um proxy reverso nesse servidor web apontando `talhoes.suaempresa.com.br` para
`http://127.0.0.1:8080`.

O verificador é feito para a **rede interna**: não crie DNS público nem libere a porta no
roteador para a internet, porque a página não tem login.

## 4. Deixar ligado sempre

Para o servidor subir sozinho quando o computador ligar, crie uma tarefa no
**Agendador de Tarefas** do Windows:
- Disparador: **Ao iniciar o sistema**
- Ação: iniciar `C:\verificador_talhoes\.venv\Scripts\python.exe` com argumentos `app.py --porta 8080`
  e **Iniciar em** `C:\verificador_talhoes`
- Marque **Executar estando o usuário conectado ou não**.

## O que é verificado

- **Buraco**: anel interno dentro do talhão (área recortada). Mostra a área em ha e o centro.
- **Parte solta**: o talhão tem mais de um pedaço. O maior é o talhão; os outros são marcados.
- **Geometria inválida**: registro sem desenho ou com contorno no sentido invertido.
- **Possível estrada (aviso, não conta como erro)**: faixa estreita e comprida que corta o talhão.
  É um buraco com até 20 m de largura, pelo menos 100 m de comprimento e 20 vezes mais comprido
  que largo, ou uma parte separada do talhão por um vão de 1 a 20 m ao longo de pelo menos 100 m.
  Os limites ficam no começo do `analise.py` (`ESTRADA_LARGURA_MAX`, `ESTRADA_COMPRIMENTO_MIN`,
  `ESTRADA_ALONGAMENTO_MIN`) e podem ser ajustados.

Os arquivos enviados são lidos na memória e não ficam salvos no servidor.

## Aba Mapa

Mostra todos os talhões sobre a imagem de satélite: **vermelho** = com erro, **azul** = só aviso
de possível estrada, **branco** = sem problema, **amarelo** = selecionado. Na lista, o botão
**Ver no mapa** (no detalhe do talhão) e **Ver fazenda no mapa** (em cada fazenda) levam direto
para o lugar. As imagens de satélite (Esri) e o mapa de ruas (OpenStreetMap) vêm da internet,
então o computador de quem abre a página precisa ter acesso à internet. O shapefile precisa
estar em graus (WGS 84 / SIRGAS 2000, ex.: EPSG:4326).
