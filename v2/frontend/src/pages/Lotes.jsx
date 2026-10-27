import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, data } from '../api.js'
import { useSessao } from '../App.jsx'
import { Aviso, SoltarArquivos } from '../components/Comuns.jsx'

export default function Lotes() {
  const { admin } = useSessao()
  const [lotes, setLotes] = useState(null)
  const [arquivos, setArquivos] = useState([])
  const [nome, setNome] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState('')
  const nav = useNavigate()

  const carregar = () => api.get('/api/lotes').then(setLotes).catch((e) => setErro(e.message))
  useEffect(() => { carregar() }, [])

  const importar = async () => {
    setErro('')
    setEnviando(true)
    const fd = new FormData()
    arquivos.forEach((f) => fd.append('arquivos', f))
    fd.append('nome', nome)
    try {
      const lote = await api.post('/api/lotes', fd)
      nav(`/lotes/${lote.id}`)
    } catch (e) {
      setErro(e.message)
    } finally {
      setEnviando(false)
    }
  }

  const apagar = async (l) => {
    if (!confirm(`Apagar o lote "${l.nome}" e todas as suas versões? O log de auditoria continua guardado.`)) return
    try { await api.del(`/api/lotes/${l.id}`); carregar() } catch (e) { setErro(e.message) }
  }

  return (
    <div className="pagina">
      <section className="cartao importar">
        <h1>Nova importação</h1>
        <p className="sub">Envie os shapefiles de fazendas, aglomerados e talhões. A auditoria roda logo depois da leitura.</p>
        <SoltarArquivos arquivos={arquivos} setArquivos={setArquivos} />
        <div className="linha-form">
          <label className="cresce">Nome do lote (opcional)
            <input value={nome} onChange={(e) => setNome(e.target.value)} placeholder="ex.: Safra 2026/27 – Regional Norte" />
          </label>
          <button className="btn primario" disabled={!arquivos.length || enviando} onClick={importar}>
            {enviando ? 'Importando e auditando…' : 'Importar e auditar'}
          </button>
        </div>
        <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>
      </section>

      <section className="cartao">
        <h2>Lotes importados</h2>
        {lotes === null ? <p className="sub">Carregando…</p> : !lotes.length ? <p className="sub">Nenhum lote ainda.</p> : (
          <table className="tabela">
            <thead>
              <tr><th>#</th><th>Nome</th><th>Sistema</th><th>Versão</th><th>Inconsistências abertas</th><th>Resolvidas</th><th>Importado por</th><th>Em</th><th /></tr>
            </thead>
            <tbody>
              {lotes.map((l) => (
                <tr key={l.id}>
                  <td>{l.id}</td>
                  <td><Link to={`/lotes/${l.id}`}>{l.nome}</Link></td>
                  <td><small>{l.crs}</small></td>
                  <td>v{l.versaoAtual}</td>
                  <td>{l.abertas ? <span className="badge erro">{l.abertas}</span> : <span className="badge ok">0</span>}</td>
                  <td>{l.resolvidas}</td>
                  <td>{l.criadoPor}</td>
                  <td>{data(l.criadoEm)}</td>
                  <td className="dir">
                    <Link className="btn pequeno" to={`/lotes/${l.id}`}>Abrir</Link>
                    {admin && <button className="btn pequeno perigo" onClick={() => apagar(l)}>Apagar</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
