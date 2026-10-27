import { useEffect, useMemo, useState } from 'react'
import { api, data } from '../api.js'
import Mapa, { ESTILOS } from '../components/Mapa.jsx'
import { Aviso } from '../components/Comuns.jsx'

const VAZIO = { lote_id: '', acao: '', usuario: '', fazenda: '', de: '', ate: '' }

export default function LogAuditoria() {
  const [filtro, setFiltro] = useState(VAZIO)
  const [dados, setDados] = useState(null)
  const [integ, setInteg] = useState(null)
  const [aberto, setAberto] = useState(null)
  const [erro, setErro] = useState('')

  // campos vazios não filtram
  const params = (f = filtro) => Object.fromEntries(Object.entries(f).filter(([, v]) => v !== '')
    .map(([k, v]) => [k, k === 'ate' ? `${v}T23:59:59` : k === 'de' ? `${v}T00:00:00` : v]))

  const buscar = (f) => api.get('/api/auditoria', params(f)).then(setDados).catch((e) => setErro(e.message))
  useEffect(() => {
    buscar()
    api.get('/api/auditoria/integridade').then(setInteg).catch(() => {})
  }, [])

  const abrir = (l) => api.get(`/api/auditoria/registro/${l.id}`).then(setAberto).catch((e) => setErro(e.message))
  const baixar = (f) => api.baixar(`/api/auditoria/exportar/${f}`, params()).catch((e) => setErro(e.message))
  const campo = (k) => ({ value: filtro[k], onChange: (e) => setFiltro({ ...filtro, [k]: e.target.value }) })

  const camadas = useMemo(() => !aberto ? [] : [
    aberto.geomAntes && { dados: aberto.geomAntes, estilo: { color: '#ff1f3d', weight: 2, dashArray: '5 5', fillOpacity: 0.1 } },
    aberto.geomDepois && { dados: aberto.geomDepois, estilo: ESTILOS.alterado },
  ].filter(Boolean), [aberto])

  return (
    <div className="pagina larga">
      <div className="titulo-secao">
        <h1>Log de auditoria</h1>
        {integ && (
          <span className={`badge ${integ.ok ? 'ok' : 'erro'}`} title="Cada registro guarda o hash do anterior; qualquer alteração por fora quebra a corrente">
            {integ.ok ? `✓ Íntegro · ${integ.registros} registros encadeados` : `⚠ Corrente quebrada no registro #${integ.quebraEm}`}
          </span>
        )}
      </div>
      <p className="sub">Registro imutável: o banco recusa alterar ou apagar linhas do log.</p>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>

      <div className="cartao filtros">
        <label>Lote<input type="number" min="1" {...campo('lote_id')} /></label>
        <label>Ação
          <select {...campo('acao')}>
            <option value="">Todas</option>
            {Object.entries(dados?.acoes || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>Usuário<input {...campo('usuario')} /></label>
        <label>Fazenda<input {...campo('fazenda')} /></label>
        <label>De<input type="date" {...campo('de')} /></label>
        <label>Até<input type="date" {...campo('ate')} /></label>
        <div className="botoes">
          <button className="btn primario" onClick={() => buscar()}>Filtrar</button>
          <button className="btn" onClick={() => { setFiltro(VAZIO); buscar(VAZIO) }}>Limpar</button>
          <button className="btn" onClick={() => baixar('csv')}>CSV</button>
          <button className="btn" onClick={() => baixar('pdf')}>PDF</button>
        </div>
      </div>

      <div className="cartao">
        {!dados ? <p className="sub">Carregando…</p> : (
          <table className="tabela">
            <thead><tr><th>#</th><th>Data/hora</th><th>Usuário</th><th>Ação</th><th>Lote</th><th>Ver.</th><th>Fazenda</th><th>Talhão</th><th>Descrição</th><th /></tr></thead>
            <tbody>
              {dados.linhas.map((l) => (
                <tr key={l.id}>
                  <td>{l.id}</td><td className="nowrap">{data(l.dataHora)}</td><td>{l.usuario}</td>
                  <td><span className={`etiqueta acao-${l.acao}`}>{dados.acoes[l.acao] || l.acao}</span></td>
                  <td>{l.loteId || ''}</td><td>{l.versao || ''}</td><td>{l.fazenda}</td><td>{l.talhao}</td><td>{l.descricao}</td>
                  <td>{l.temGeometria && <button className="btn pequeno" onClick={() => abrir(l)}>Ver geometria</button>}</td>
                </tr>
              ))}
              {!dados.linhas.length && <tr><td colSpan={10} className="vazio">Nenhum registro.</td></tr>}
            </tbody>
          </table>
        )}
      </div>

      {aberto && (
        <div className="modal-fundo" onClick={() => setAberto(null)}>
          <div className="modal largo" onClick={(e) => e.stopPropagation()}>
            <div className="titulo-secao"><h2>Registro #{aberto.id}</h2><button className="btn-link" onClick={() => setAberto(null)}>fechar</button></div>
            <p>{aberto.descricao}</p>
            <p className="sub">{data(aberto.dataHora)} · {aberto.usuario} · lote {aberto.loteId} v{aberto.versao} · hash <code>{aberto.hash.slice(0, 16)}…</code></p>
            <div className="mapa-modal">
              <Mapa camadas={camadas} enquadrar={[aberto.geomAntes, aberto.geomDepois].filter(Boolean)} chaveEnquadrar={aberto.id}
                legenda={[{ texto: 'Antes', cor: '#ff1f3d', tipo: 'tracejado' }, { texto: 'Depois', cor: ESTILOS.alterado.color, fundo: ESTILOS.alterado.fillColor }]} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
