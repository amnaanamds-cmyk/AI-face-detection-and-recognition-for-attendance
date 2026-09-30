// Entrance kiosk: continuous recognition, big welcome / goodbye messages, local log of today's events.
(function () {
  const $ = id => document.getElementById(id);
  const video = $('video'), overlay = $('overlay'), ctx = overlay.getContext('2d');
  const cam = new Camera(video);
  let busy = false, timer = null;
  const COLORS = {marked: '#198754', checked_out: '#0d6efd', duplicate: '#0d6efd', checking: '#ffc107',
                  unknown: '#dc3545', spoof: '#d63384', rejected: '#6c757d'};
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const log = [];

  function draw(faces) {
    overlay.width = video.videoWidth; overlay.height = video.videoHeight;
    ctx.clearRect(0, 0, overlay.width, overlay.height); ctx.lineWidth = 4; ctx.font = '600 22px system-ui';
    for (const f of faces) {
      let [x, y, w, h] = f.bbox.map(v => v / cam.scale);
      if (cam.mirrored) x = overlay.width - x - w;
      const c = COLORS[f.state] || '#fff';
      ctx.strokeStyle = c; ctx.strokeRect(x, y, w, h);
      const tw = ctx.measureText(f.label).width + 12;
      ctx.fillStyle = c; ctx.fillRect(x, Math.max(0, y - 30), tw, 30);
      ctx.fillStyle = f.state === 'checking' ? '#000' : '#fff'; ctx.fillText(f.label, x + 6, Math.max(22, y - 8));
    }
  }

  function show(ev) {
    const out = ev.state === 'checked_out';
    const title = ev.state === 'marked' ? `Welcome, ${esc(ev.name)}!` : out ? `Goodbye, ${esc(ev.name)}!` :
                  ev.state === 'duplicate' ? `Hello again, ${esc(ev.name)}` : esc(ev.name);
    $('msg').innerHTML = `<div class="welcome text-${out ? 'primary' : 'success'}">${title}</div><div class="text-muted">${esc(ev.message)}</div>`;
    log.unshift({time: new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}), ...ev});
    $('log').innerHTML = log.slice(0, 15).map(e => `<li class="list-group-item d-flex justify-content-between">
      <span><span class="badge me-1" style="background:${COLORS[e.state] || '#999'}">${e.state === 'checked_out' ? 'out' : e.state === 'marked' ? 'in' : esc(e.state)}</span>${esc(e.name)}</span>
      <span class="text-muted">${e.time}</span></li>`).join('');
  }

  async function tick() {
    if (busy || !cam.running) return;
    busy = true;
    try {
      const image = cam.capture(0.85, 1280);
      const res = await fetch('/api/kiosk/frame', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({image})});
      const data = await res.json();
      if (!res.ok) { $('msg').innerHTML = `<div class="text-danger">${esc(data.detail || 'Error')}</div>`; return; }
      draw(data.faces);
      data.events.forEach(show);
      const spoof = data.faces.find(f => f.state === 'spoof'), unknown = data.faces.find(f => f.state === 'unknown');
      if (!data.events.length && spoof) $('msg').innerHTML = '<div class="welcome text-danger">Not accepted</div><div class="text-muted">Please look at the camera yourself - photos and screens are not allowed.</div>';
      else if (!data.events.length && unknown) $('msg').innerHTML = '<div class="welcome text-danger">Not registered</div><div class="text-muted">Please contact the front desk.</div>';
    } catch (e) { $('msg').innerHTML = `<div class="text-danger">Connection problem: ${esc(e.message)}</div>`; }
    finally { busy = false; }
  }

  $('btnStart').onclick = async () => {
    try { await cam.start(1280, 720, $('facing').value); } catch (e) { $('msg').textContent = 'Camera error: ' + e.message; return; }
    $('btnStart').disabled = true;
    timer = setInterval(tick, 500);
  };
  $('facing').onchange = async () => { if (cam.running) await cam.start(1280, 720, $('facing').value); };
  $('btnFull').onclick = () => {
    document.body.classList.toggle('kiosk-fullscreen');
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.(); else document.exitFullscreen?.();
  };
  // keep a kiosk tablet awake
  if ('wakeLock' in navigator) navigator.wakeLock.request('screen').catch(() => {});
})();
