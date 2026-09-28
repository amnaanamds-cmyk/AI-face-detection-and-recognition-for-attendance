// Live attendance: send frames to the server, draw the recognition results.
function startLive(sessionId) {
  const $ = id => document.getElementById(id);
  const video = $('video'), overlay = $('overlay'), ctx = overlay.getContext('2d');
  const cam = new Camera(video);
  let timer = null, busy = false;
  const COLORS = {marked: '#198754', duplicate: '#0d6efd', accepted: '#198754', checking: '#ffc107',
                  unknown: '#dc3545', spoof: '#d63384', rejected: '#6c757d'};
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  function draw(faces, scale) {
    overlay.width = video.videoWidth; overlay.height = video.videoHeight;
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    ctx.lineWidth = 3; ctx.font = '600 18px system-ui';
    for (const f of faces) {
      let [x, y, w, h] = f.bbox.map(v => v / scale);
      x = overlay.width - x - w;                     // mirror to match the preview
      const color = COLORS[f.state] || '#fff';
      ctx.strokeStyle = color; ctx.strokeRect(x, y, w, h);
      const text = f.label + (f.liveness === 'checking' ? ' · checking liveness' : '');
      const tw = ctx.measureText(text).width + 10;
      ctx.fillStyle = color; ctx.fillRect(x, Math.max(0, y - 24), tw, 24);
      ctx.fillStyle = f.state === 'checking' ? '#000' : '#fff'; ctx.fillText(text, x + 5, Math.max(18, y - 6));
    }
  }

  function render(data) {
    const s = data.summary;
    if (s) {
      $('sExpected').textContent = s.expected; $('sAttended').textContent = s.attended;
      $('sNotYet').textContent = s.not_yet; $('sRate').textContent = s.rate + '%';
      $('recent').innerHTML = s.recent.length ? s.recent.map(r =>
        `<li class="list-group-item d-flex justify-content-between small"><span>${esc(r.name)} <span class="text-muted">${esc(r.code)}</span></span>
         <span><span class="badge text-bg-${{present: 'success', late: 'warning', absent: 'danger'}[r.status] || 'secondary'}">${esc(r.status)}</span> ${esc(r.time)}</span></li>`).join('')
        : '<li class="list-group-item text-muted small">—</li>';
    }
    $('faces').innerHTML = data.faces.length ? data.faces.map(f =>
      `<li class="list-group-item small"><span class="badge me-1" style="background:${COLORS[f.state] || '#999'}">${esc(f.state)}</span>
       <strong>${esc(f.label)}</strong> <span class="text-muted">sim ${f.similarity.toFixed(2)} · liveness ${esc(f.liveness)}</span>
       ${f.message ? `<div class="text-muted">${esc(f.message)}</div>` : ''}</li>`).join('')
      : '<li class="list-group-item text-muted small">No face detected.</li>';
  }

  async function tick() {
    if (busy || !cam.running) return;
    busy = true;
    const t0 = performance.now();
    try {
      const image = cam.capture(0.85, cam.video.videoWidth);  // full resolution: small faces at the back need it
      const scale = cam.scale;
      const res = await fetch(`/api/sessions/${sessionId}/frame`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({image})});
      const data = await res.json();
      if (!res.ok || data.error) { $('status').textContent = 'Error: ' + (data.detail || data.error); if (res.status >= 400) stop(); return; }
      draw(data.faces, scale); render(data);
      const mh = $('motionHelp'); if (mh) mh.classList.toggle('d-none', !(data.liveness_mode || '').includes('motion'));
      $('status').textContent = `${data.faces.length} face(s) in view`;
      $('latency').textContent = `server round-trip ${Math.round(performance.now() - t0)} ms`;
    } catch (e) { $('status').textContent = 'Network error: ' + e.message; }
    finally { busy = false; }
  }

  function stop() { clearInterval(timer); timer = null; cam.stop(); $('btnStart').disabled = false; $('btnStop').disabled = true; $('status').textContent = 'Camera stopped.'; }

  $('btnStart').onclick = async () => {
    const [w, h] = $('res').value.split('x').map(Number);
    try { await cam.start(w, h); } catch (e) { $('status').textContent = 'Camera error: ' + e.message; return; }
    $('btnStart').disabled = true; $('btnStop').disabled = false;
    timer = setInterval(tick, +$('fps').value);
  };
  $('btnStop').onclick = stop;
  $('fps').onchange = () => { if (timer) { clearInterval(timer); timer = setInterval(tick, +$('fps').value); } };
  fetch(`/api/sessions/${sessionId}/summary`).then(r => r.json()).then(s => render({faces: [], summary: s}));
}
