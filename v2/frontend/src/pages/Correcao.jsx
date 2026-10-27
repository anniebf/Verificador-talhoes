// Etapa 3: aplica as decisões da revisão, grava a nova versão e mostra o log, os downloads e o próximo passo.
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, data, num } from '../api.js'
import Mapa, { ESTILOS } from '../components/Mapa.jsx'
import { Aviso, Etapas, STATUS } from '../components/Comuns.jsx'

export default function Correcao() {
  const { id } = useParams()
  const { state } = useLocation()
  const nav = useNavigate()
  const ids = state?.ids || []
  const fazenda = state?.fazenda ?? '*'
  const [incs, setIncs] = useState(null)
  const [res, setRes] = useState(null)
  const [log, setLog] = useState([])
  const [mapas, setMapas] = useState({})
  const [erro, setErro] = useState('')
  const [ocupado, setOcupado] = useState('')

  useEffect(() => {
    if (!ids.length) return
    api.get(`/api/lotes/${id}/inconsistencias`, { fazenda: fazenda === '*' ? undefined : fazenda })
      .then((l) => setIncs(l.filter((i) => ids.includes(i.id)))).catch((e) => setErro(e.message))
  }, [id])

  const aplicar = async () => {
    setOcupado('aplicando'); setErro('')
    try {
      const r = await api.post(`/api/lotes/${id}/aplicar`, { ids })
      setRes(r)
      const linhas = (await api.get('/api/auditoria', { lote_id: id, limite: 1000 })).linhas
      setLog(linhas.filter((l) => l.versao === r.versao && ['correcao', 'mantido', 'versao'].includes(l.acao)).reverse())
      if (r.novaVersao) {
        const p = r.fazendas.length === 1 ? { fazenda: r.fazendas[0] } : {}
        const [depois, antes] = await Promise.all([
          api.get(`/api/lotes/${id}/mapa`, { ...p, versao: r.versao }),
          api.get(`/api/lotes/${id}/mapa`, { ...p, versao: r.versao - 1 }),
        ])
        setMapas({ depois, antes })
      }
    } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }

  const baixar = async (url, params, qual) => {
    setOcupado(qual)
    try { await api.baixar(url, params) } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }

  const alteradas = new Set(res?.alteradas.map((a) => a.id) || [])
  const camadas = useMemo(() => !mapas.depois ? [] : [
    { dados: mapas.depois, estilo: (f) => alteradas.has(f.properties.id) ? ESTILOS.alterado : ESTILOS.talhao,
      dica: (f) => `Talhão ${f.properties.talhao || '?'} · ${num(f.properties.areaHa)} ha${alteradas.has(f.properties.id) ? ' · alterado' : ''}` },
    { dados: { ...mapas.antes, features: mapas.antes.features.filter((f) => alteradas.has(f.properties.id)) },
      estilo: { color: '#ff1f3d', weight: 2, dashArray: '5 5', fillOpacity: 0 } },
  ], [mapas])

  const voltar = `/lotes/${id}?fazenda=${encodeURIComponent(fazenda)}`

  if (!ids.length) {
    return (
      <div className="pagina"><div className="cartao">
        <p>Nenhuma revisão em andamento. Comece pela auditoria do lote.</p>
        <Link className="btn" to={`/lotes/${id}`}>Ir para a auditoria</Link>
      </div></div>
    )
  }

  const aceitas = incs?.filter((i) => i.status === 'aceita') || []
  const mantidas = incs?.filter((i) => i.status === 'rejeitada') || []

  return (
    <div className="tela-lote">
      <div className="barra-lote">
        <div>
          {!res && <Link to={`/lotes/${id}/revisao?fazenda=${encodeURIComponent(fazenda)}`} className="btn-link">← Voltar à revisão</Link>}
          <h1>Correção e envio {fazenda === '*' ? '' : `— fazenda ${fazenda || 'sem código'}`}</h1>
        </div>
        <Etapas atual={3} loteId={id} fazenda={fazenda} />
      </div>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>

      {!res ? (
        <div className="pagina">
          <div className="cartao">
            <h2>Confirmar e aplicar</h2>
            {!incs ? <p className="sub">Carregando…</p> : (
              <>
                <div className="numeros grande">
                  <div><b className="verde">{aceitas.length}</b><span>correções a aplicar</span></div>
                  <div><b>{mantidas.length}</b><span>mantidas como original</span></div>
                </div>
                <p className="sub">Será criada uma nova versão do lote com as correções aceitas. Cada decisão vai para o log de auditoria
                  com usuário, data, hora e a geometria antes e depois.</p>
                <table className="tabela compacta">
                  <thead><tr><th>#</th><th>Tipo</th><th>Descrição</th><th>Decisão</th></tr></thead>
                  <tbody>
                    {incs.map((i) => (
                      <tr key={i.id}><td>{i.id}</td><td>{i.nome}</td><td>{i.descricao}</td>
                        <td><span className={`etiqueta ${STATUS[i.status].cls}`}>{STATUS[i.status].texto}</span></td></tr>
                    ))}
                  </tbody>
                </table>
                <div className="acoes">
                  <button className="btn primario grande" disabled={!!ocupado} onClick={aplicar}>
                    {ocupado === 'aplicando' ? 'Aplicando…' : 'Aplicar e salvar nova versão'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      ) : (
        <div className="resultado">
          <div className="resultado-info">
            <div className="cartao">
              <h2>{res.novaVersao ? `✓ Versão ${res.versao} salva` : '✓ Decisões registradas'}</h2>
              <div className="numeros">
                <div><b className="verde">{res.aplicadas}</b><span>corrigidas</span></div>
                <div><b>{res.mantidas}</b><span>mantidas</span></div>
                <div><b>{res.alteradas.length}</b><span>talhões alterados</span></div>
                <div><b className={res.restantes ? 'laranja' : ''}>{res.restantes}</b><span>ainda em aberto</span></div>
              </div>
              {res.restantes > 0 && <p className="aviso">A nova auditoria da fazenda achou {res.restantes} inconsistência(s) em aberto (outros erros ou efeitos das correções).</p>}
              {res.alteradas.length > 0 && (
                <table className="tabela compacta">
                  <thead><tr><th>Talhão</th><th>Ajustes</th><th>Área antes</th><th>Área depois</th><th>Vértices</th></tr></thead>
                  <tbody>
                    {res.alteradas.map((a) => (
                      <tr key={a.id}><td>{a.talhao || '?'} <small>({a.fazenda})</small></td><td>{a.ajustes.join(', ')}</td>
                        <td>{num(a.areaAntes, 4)} ha</td><td>{a.removida ? 'removido' : `${num(a.areaDepois, 4)} ha`}</td>
                        <td>{a.verticesAntes} → {a.verticesDepois}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="cartao">
              <div className="titulo-secao">
                <h3>Log de auditoria desta operação</h3>
                <div className="botoes">
                  <button className="btn pequeno" disabled={!!ocupado} onClick={() => baixar('/api/auditoria/exportar/csv', { lote_id: id }, 'csv')}>Relatório CSV</button>
                  <button className="btn pequeno" disabled={!!ocupado} onClick={() => baixar('/api/auditoria/exportar/pdf', { lote_id: id }, 'pdf')}>Relatório PDF</button>
                </div>
              </div>
              <table className="tabela compacta">
                <thead><tr><th>Data/hora</th><th>Usuário</th><th>Fazenda</th><th>Talhão</th><th>Descrição</th></tr></thead>
                <tbody>
                  {log.map((l) => (
                    <tr key={l.id}><td>{data(l.dataHora)}</td><td>{l.usuario}</td><td>{l.fazenda}</td><td>{l.talhao}</td><td>{l.descricao}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="cartao acoes-finais">
              <h3>E agora?</h3>
              <div className="botoes">
                <button className="btn" disabled={!!ocupado} onClick={() => baixar(`/api/lotes/${id}/exportar`, { formato: 'shp', versao: res.versao }, 'shp')}>
                  Baixar shapefile v{res.versao}
                </button>
                <button className="btn" disabled={!!ocupado} onClick={() => baixar(`/api/lotes/${id}/exportar`, { formato: 'geojson', versao: res.versao }, 'geojson')}>
                  GeoJSON v{res.versao}
                </button>
              </div>
              <div className="opcoes-finais">
                <button className="btn primario grande" onClick={() => nav(voltar)}>A · Voltar ao mapa principal</button>
                <button className="btn grande" disabled title="O plugin do QGIS entra na próxima etapa do projeto">
                  B · Enviar ao QGIS <small>(plugin em breve)</small>
                </button>
              </div>
            </div>
          </div>
          <div className="centro">
            {res.novaVersao ? (
              <Mapa camadas={camadas} enquadrar={mapas.antes ? { ...mapas.antes, features: mapas.antes.features.filter((f) => alteradas.has(f.properties.id)) } : null}
                chaveEnquadrar={!!mapas.antes} rotulo={`Versão ${res.versao}`}
                legenda={[{ texto: 'Alterado (novo)', cor: ESTILOS.alterado.color, fundo: ESTILOS.alterado.fillColor }, { texto: 'Como era', cor: '#ff1f3d', tipo: 'tracejado' },
                  { texto: 'Sem alteração', cor: ESTILOS.talhao.color }]} />
            ) : <div className="cartao"><p>Nenhuma geometria mudou: todas as inconsistências foram mantidas como original.</p></div>}
          </div>
        </div>
      )}
    </div>
  )
}
