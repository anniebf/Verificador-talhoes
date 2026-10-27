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
  const [regras, setRegras] = useState([])
  const [sel, setSel] = useState(new Set())
  const nav = useNavigate()

  const carregar = () => api.get('/api/lotes').then(setLotes).catch((e) => setErro(e.message))
  useEffect(() => {
    carregar()
    // verificações disponíveis: as que o admin deixou ativas vêm marcadas
    api.get('/api/admin/regras').then((d) => {
      setRegras(d.regras)
      setSel(new Set(d.regras.filter((r) => r.ativo).map((r) => r.chave)))
    }).catch(() => {})
  }, [])
  const alternar = (k) => { const n = new Set(sel); n.has(k) ? n.delete(k) : n.add(k); setSel(n) }

  const importar = async () => {
    setErro('')
    setEnviando(true)
    const fd = new FormData()
    arquivos.forEach((f) => fd.append('arquivos', f))
    fd.append('nome', nome)
    fd.append('verificacoes', [...sel].join(','))
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
        <ol className="passos">
          <li><b>Envie a base</b><span>o .rar ou .zip do shapefile de talhões</span></li>
          <li><b>Veja os problemas</b><span>por fazenda, no mapa de satélite</span></li>
          <li><b>Revise e corrija</b><span>nada muda sem a sua aprovação</span></li>
        </ol>
        <SoltarArquivos arquivos={arquivos} setArquivos={setArquivos} />
        <details className="verificacoes">
          <summary>Opções avançadas <small>· {sel.size} de {regras.length} verificações ligadas</small></summary>
          <p className="sub">A auditoria só lê os dados: nada é alterado nesta etapa. Regras desligadas pelo administrador aparecem desabilitadas.</p>
          <div className="grade-verificacoes">
            {regras.map((r) => (
              <label key={r.chave} className={`linha-check ${r.ativo ? '' : 'desligada'}`} title={r.descricao}>
                <input type="checkbox" checked={sel.has(r.chave)} disabled={!r.ativo} onChange={() => alternar(r.chave)} />
                <span><b>{r.grupo}</b> · {r.nome}</span>
              </label>
            ))}
          </div>
        </details>
        <div className="linha-form">
          <label className="cresce">Nome do lote (opcional)
            <input value={nome} onChange={(e) => setNome(e.target.value)} placeholder="ex.: Safra 2026/27 – Regional Norte" />
          </label>
          <button className="btn primario" disabled={!arquivos.length || enviando || !sel.size} onClick={importar}>
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
