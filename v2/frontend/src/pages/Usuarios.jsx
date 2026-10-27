import { useEffect, useState } from 'react'
import { api, data } from '../api.js'
import { useSessao } from '../App.jsx'
import { Aviso } from '../components/Comuns.jsx'

const NOVO = { usuario: '', nome: '', email: '', perfil: 'analista', provedor: 'local', senha: '', ativo: true }

export default function Usuarios() {
  const { usuario: eu } = useSessao()
  const [lista, setLista] = useState([])
  const [form, setForm] = useState(null)   // null = fechado; {id?...}
  const [erro, setErro] = useState('')
  const [ok, setOk] = useState('')

  const carregar = () => api.get('/api/admin/usuarios').then(setLista).catch((e) => setErro(e.message))
  useEffect(() => { carregar() }, [])

  const salvar = async (e) => {
    e.preventDefault()
    setErro('')
    try {
      if (form.id) {
        const { nome, email, perfil, ativo, senha } = form
        await api.patch(`/api/admin/usuarios/${form.id}`, { nome, email, perfil, ativo, senha: senha || null })
        setOk(`Usuário ${form.usuario} atualizado.`)
      } else {
        await api.post('/api/admin/usuarios', form)
        setOk(`Usuário ${form.usuario} criado. Ele vai trocar a senha no primeiro acesso.`)
      }
      setForm(null)
      carregar()
    } catch (e) { setErro(e.message) }
  }

  const alternar = async (u) => {
    try { await api.patch(`/api/admin/usuarios/${u.id}`, { ativo: !u.ativo }); carregar() } catch (e) { setErro(e.message) }
  }
  const campo = (k) => ({ value: form[k], onChange: (e) => setForm({ ...form, [k]: e.target.value }) })

  return (
    <div className="pagina">
      <div className="titulo-secao">
        <h1>Usuários e perfis</h1>
        <button className="btn primario" onClick={() => setForm({ ...NOVO })}>+ Novo usuário</button>
      </div>
      <p className="sub"><b>Admin</b>: gerencia usuários, perfis e regras de correção. <b>Analista</b>: importa, revisa, corrige e exporta.</p>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>
      <Aviso tipo="ok" aoFechar={() => setOk('')}>{ok}</Aviso>

      <div className="cartao">
        <table className="tabela">
          <thead><tr><th>Login</th><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Origem</th><th>Situação</th><th>Último acesso</th><th /></tr></thead>
          <tbody>
            {lista.map((u) => (
              <tr key={u.id} className={u.ativo ? '' : 'inativo'}>
                <td><b>{u.usuario}</b>{u.trocarSenha && <small className="sub"> · troca de senha pendente</small>}</td>
                <td>{u.nome}</td><td>{u.email}</td>
                <td><span className={`etiqueta perfil-${u.perfil}`}>{u.perfil}</span></td>
                <td>{u.provedor === 'ad' ? 'Active Directory' : 'Local'}</td>
                <td>{u.ativo ? 'Ativo' : 'Desativado'}</td>
                <td>{data(u.ultimoLogin)}</td>
                <td className="dir">
                  <button className="btn pequeno" onClick={() => setForm({ ...u, senha: '' })}>Editar</button>
                  {u.id !== eu.id && <button className="btn pequeno" onClick={() => alternar(u)}>{u.ativo ? 'Desativar' : 'Reativar'}</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {form && (
        <div className="modal-fundo">
          <form className="modal" onSubmit={salvar}>
            <h2>{form.id ? `Editar ${form.usuario}` : 'Novo usuário'}</h2>
            {!form.id && <label>Login<input {...campo('usuario')} required autoFocus /></label>}
            <label>Nome<input {...campo('nome')} /></label>
            <label>E-mail<input type="email" {...campo('email')} /></label>
            <label>Perfil
              <select {...campo('perfil')} disabled={form.id === eu.id}>
                <option value="analista">Analista / Operador</option>
                <option value="admin">Administrador</option>
              </select>
            </label>
            {!form.id && (
              <label>Origem do login
                <select {...campo('provedor')}>
                  <option value="local">Local (senha no sistema)</option>
                  <option value="ad" disabled>Active Directory / LDAP (em breve)</option>
                </select>
              </label>
            )}
            {form.provedor === 'local' && (
              <label>{form.id ? 'Redefinir senha (deixe vazio para manter)' : 'Senha inicial'}
                <input type="password" {...campo('senha')} required={!form.id} minLength={6} />
              </label>
            )}
            <div className="acoes">
              <button type="button" className="btn" onClick={() => setForm(null)}>Cancelar</button>
              <button className="btn primario">Salvar</button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
