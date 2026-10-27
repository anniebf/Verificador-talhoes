import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

// Fundo de satélite (Esri) com opção de mapa de ruas, como na v1.
function fundos() {
  const satelite = L.layerGroup([
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 20, maxNativeZoom: 19, attribution: 'Imagens © Esri' }),
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 20, maxNativeZoom: 19, opacity: 0.8 }),
  ])
  const ruas = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 20, maxNativeZoom: 19, attribution: '© OpenStreetMap' })
  return { 'Satélite': satelite, 'Ruas': ruas }
}

/**
 * camadas: [{ id, dados (GeoJSON), estilo (obj ou função), aoClicar(feature, layer), dica(feature) -> texto,
 *             ponto (estilo de circleMarker para pontos) }]
 * enquadrar: GeoJSON/array de GeoJSON ou bounds Leaflet; muda a vista quando a `chaveEnquadrar` muda.
 * aoCriar(map): para sincronizar dois mapas; pode devolver uma função chamada quando o mapa sai da tela.
 */
export default function Mapa({ camadas = [], enquadrar, chaveEnquadrar, aoCriar, legenda, rotulo, className = '' }) {
  const div = useRef(null)
  const mapa = useRef(null)
  const grupo = useRef(null)
  const ultimo = useRef(null)   // último enquadramento pedido
  const mexeu = useRef(false)   // o usuário já moveu/zoom no mapa depois dele

  useEffect(() => {
    const m = L.map(div.current, { zoomControl: true, preferCanvas: true, attributionControl: true })
    const f = fundos()
    f['Satélite'].addTo(m)
    L.control.layers(f, null, { position: 'topright' }).addTo(m)
    L.control.scale({ imperial: false }).addTo(m)
    m.setView([-15.8, -47.9], 4)
    grupo.current = L.layerGroup().addTo(m)
    mapa.current = m
    const desfazer = aoCriar?.(m)
    // se o painel muda de tamanho antes de o usuário mexer no mapa, reenquadra no que foi pedido
    const obs = new ResizeObserver(() => {
      m.invalidateSize()
      if (ultimo.current && !mexeu.current) m.fitBounds(ultimo.current, { padding: [30, 30], maxZoom: 18, animate: false })
    })
    obs.observe(div.current)
    const marcar = () => { mexeu.current = true }
    for (const ev of ['mousedown', 'wheel', 'touchstart']) div.current.addEventListener(ev, marcar, { passive: true })
    return () => { obs.disconnect(); desfazer?.(); m.remove(); mapa.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const g = grupo.current
    if (!g) return
    g.clearLayers()
    for (const c of camadas) {
      if (!c?.dados) continue
      const camada = L.geoJSON(c.dados, {
        style: c.estilo,
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 7, weight: 2, color: '#fff', fillColor: '#e5383b', fillOpacity: 1, ...(c.ponto || {}) }),
        onEachFeature: (f, layer) => {
          if (c.dica) {
            const t = c.dica(f)
            if (t) layer.bindTooltip(t, { sticky: true, className: 'dica-mapa' })
          }
          if (c.aoClicar) layer.on('click', (e) => { L.DomEvent.stopPropagation(e); c.aoClicar(f, layer) })
        },
        interactive: !!(c.dica || c.aoClicar),
      })
      camada.addTo(g)
    }
  }, [camadas])

  useEffect(() => {
    const m = mapa.current
    if (!m || !enquadrar) return
    let b
    if (enquadrar instanceof L.LatLngBounds) b = enquadrar
    else {
      const lista = (Array.isArray(enquadrar) ? enquadrar : [enquadrar]).filter(Boolean)
      if (!lista.length) return
      b = L.featureGroup(lista.map((x) => L.geoJSON(x))).getBounds()
    }
    if (b.isValid()) {
      ultimo.current = b
      mexeu.current = false
      m.fitBounds(b, { padding: [30, 30], maxZoom: 18, animate: false })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chaveEnquadrar])

  return (
    <div className={`mapa ${className}`}>
      <div ref={div} className="mapa-leaflet" />
      {rotulo && <div className="mapa-rotulo">{rotulo}</div>}
      {legenda && (
        <div className="mapa-legenda">
          {legenda.map((l) => (
            <div key={l.texto}><span className={`leg ${l.tipo || ''}`} style={{ background: l.fundo, borderColor: l.cor }} />{l.texto}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// Liga dois mapas: mexer num move o outro.
export function sincronizar(a, b) {
  let travado = false
  const seguir = (de, para) => () => {
    if (travado) return
    travado = true
    para.setView(de.getCenter(), de.getZoom(), { animate: false })
    travado = false
  }
  a.on('move', seguir(a, b))
  b.on('move', seguir(b, a))
}

export const ESTILOS = {
  talhao: { color: '#f5d90a', weight: 1.5, fillColor: '#f5d90a', fillOpacity: 0.08 },
  comErro: { color: '#ff8c1a', weight: 2, fillColor: '#ff8c1a', fillOpacity: 0.18 },
  selecionado: { color: '#22d3ee', weight: 3, fillColor: '#22d3ee', fillOpacity: 0.2 },
  vizinho: { color: '#94a3b8', weight: 1.5, fillColor: '#94a3b8', fillOpacity: 0.12, dashArray: '4 3' },
  erro: { color: '#ff1f3d', weight: 2.5, fillColor: '#ff1f3d', fillOpacity: 0.55 },
  original: { color: '#ff8c1a', weight: 2.5, fillColor: '#ff8c1a', fillOpacity: 0.15 },
  proposta: { color: '#3ddc84', weight: 2.5, fillColor: '#3ddc84', fillOpacity: 0.25 },
  alterado: { color: '#3ddc84', weight: 2.5, fillColor: '#3ddc84', fillOpacity: 0.3 },
  estrada: { color: '#60a5fa', weight: 3, opacity: 0.95 },
  estradaErro: { color: '#c084fc', weight: 3.5, opacity: 1 },
}
