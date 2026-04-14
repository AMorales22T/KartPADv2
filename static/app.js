// ═══════════════════════════════════════════════════════════════════════
//  KardPad — app.js  v2.0
//  Cambios clave:
//   • HapticEngine: navigator.vibrate + Web Audio API fallback para iOS
//     – AudioContext desbloqueado en el primer gesto del usuario
//     – Ruido de impacto corto: click audible como feedback en iOS Safari
//     – navigator.vibrate funciona en Android siempre; en iOS 16.4+ (PWA)
//   • Pointer events en todos los botones (táctil + ratón unificados)
//   • Recalibración automática del volante al girar la orientación
//   • Háptica diferenciada: A/B/drift/item/shake tienen duraciones distintas
// ═══════════════════════════════════════════════════════════════════════

/* ─── Colores por jugador ─────────────────────────────────────────── */
const PLAYER_COLORS = { 1: '#e74c3c', 2: '#3498db', 3: '#f1c40f', 4: '#2ecc71' };

/* ─── Sensibilidad del volante ───────────────────────────────────── */
const TILT_SENSE_MAP = {
  1: { deadzone: 0.12, threshold: 0.28 },
  2: { deadzone: 0.10, threshold: 0.26 },
  3: { deadzone: 0.07, threshold: 0.22 },
  4: { deadzone: 0.04, threshold: 0.18 },
  5: { deadzone: 0.02, threshold: 0.14 },
};
const DPAD_BUTTONS = new Set(['UP', 'DOWN', 'LEFT', 'RIGHT']);

// EMA: alpha = peso del valor nuevo (mayor → más rápido pero menos suave)
const TILT_SMOOTH_ALPHA = 0.3;

/* ─── Detección de shake ──────────────────────────────────────────── */
const SHAKE_THRESHOLD   = 14;   // m/s² — bajado para detectar mejor el gesto
const SHAKE_DEBOUNCE_MS = 300;  // ms mínimo entre shakes

// Magnitud del spike de aceleración enviado al Wiimote virtual (en g)
// Dolphin Wiimote shake umbral ≈ 2.5 g — mandamos 3.5 g para margen holgado
const SHAKE_SPIKE_G = 3.5;

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: HAPTIC ENGINE
   ──────────────────────────────────────────────────────────────────────
   Jerarquía de feedback:
     1. navigator.vibrate()  — Android siempre; iOS 16.4+ solo en PWA
     2. Web Audio API click  — fallback iOS Safari (audio sutil)
     3. Flash visual CSS     — siempre activo (manejado por CSS .pressed)

   IMPORTANTE — iOS Safari:
   El AudioContext DEBE crearse dentro de un handler de evento de usuario
   (touchstart / pointerdown). Lo desbloqueamos con unlock() en el primer
   toque antes de cualquier llamada a trigger().
   ═══════════════════════════════════════════════════════════════════════ */
const HapticEngine = {
  _ctx:   null,   // AudioContext
  _ready: false,  // true tras unlock()

  /**
   * Llama una sola vez en el primer gesto del usuario.
   * Crea el AudioContext y reproduce un buffer vacío para cumplir
   * con la política de autoplay de WebKit.
   */
  unlock() {
    if (this._ready) return;
    try {
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      // Buffer de 1 muestra → "desbloquea" el contexto en iOS
      const buf = this._ctx.createBuffer(1, 1, 22050);
      const src = this._ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this._ctx.destination);
      src.start(0);
      this._ready = true;
    } catch (_) { /* AudioContext no disponible en este entorno */ }
  },

  /**
   * Dispara feedback háptico.
   * @param {number}   ms      – duración deseada en milisegundos
   * @param {number[]} pattern – patrón navigator.vibrate opcional
   */
  trigger(ms = 22, pattern = null) {
    if (!state.vibrationEnabled) return;
    if (this._nativeImpact(ms)) return;

    // ── Capa 1: vibración nativa ───────────────────────────────────
    if (typeof navigator.vibrate === 'function') {
      try {
        navigator.vibrate(pattern || ms);
        return; // éxito → no reproducir audio
      } catch (_) { /* continúa al fallback */ }
    }

    // ── Capa 2: Web Audio click (iOS Safari sin vibración) ─────────
    // Genera ~5ms de ruido blanco con envolvente exponencial decreciente.
    // Es audible pero muy sutil; da feedback perceptible al usuario.
    this._audioClick(ms);
  },

  /** Patrón doble para confirmaciones (calibrar, conectar…) */
  double(ms = 28) {
    if (!state.vibrationEnabled) return;
    if (this._nativeImpact(ms, true)) return;
    if (typeof navigator.vibrate === 'function') {
      try { navigator.vibrate([ms, 60, ms]); return; } catch (_) {}
    }
    this._audioClick(ms);
    setTimeout(() => this._audioClick(ms), 90);
  },

  _nativeImpact(ms, doubleTap = false) {
    const haptics = window.Capacitor?.Plugins?.Haptics;
    if (!haptics?.impact) return false;

    const style = ms >= 36 ? 'MEDIUM' : 'LIGHT';
    try {
      haptics.impact({ style });
      if (doubleTap) setTimeout(() => haptics.impact({ style }), 90);
      return true;
    } catch (_) {
      return false;
    }
  },

  _audioClick(ms) {
    if (!this._ctx) return;
    try {
      const ctx = this._ctx;
      if (ctx.state === 'suspended') ctx.resume().catch(() => {});

      // Clamp: mínimo 3ms, máximo 50ms
      const dur     = Math.max(0.003, Math.min(ms / 1000, 0.05));
      const frames  = Math.floor(ctx.sampleRate * dur);
      const buffer  = ctx.createBuffer(1, frames, ctx.sampleRate);
      const data    = buffer.getChannelData(0);

      // Ruido blanco × envolvente decaying rápida → click/tap
      const decay = frames * 0.25;
      for (let i = 0; i < frames; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.exp(-i / decay);
      }

      const src  = ctx.createBufferSource();
      const gain = ctx.createGain();
      src.buffer = buffer;
      src.connect(gain);
      gain.connect(ctx.destination);
      gain.gain.setValueAtTime(0.10, ctx.currentTime); // sutil
      src.start(ctx.currentTime);
    } catch (_) { /* fallo silencioso */ }
  },
};

/* ─── Estado global ───────────────────────────────────────────────── */
const state = {
  socket:          null,
  selectedPlayer:  1,
  connectedPlayer: null,
  wsUrl:           null,

  activeButtons: new Set(),

  tiltEnabled:       false,
  tiltPermission:    false,
  tiltNeutral:       null,
  tiltSmoothed:      0,
  tiltSensLevel:     Number(lsGet('kardpad_tilt_sens') || '3'),
  invertSteering:    lsGet('kardpad_invert_steering') === 'true',
  lastTiltRaw:       null,
  touchSteeringPointerId: null,
  touchSteeringValue:     0,

  tiltLastHapticSide: null,
  tiltHapticTs:       0,
  motionSendTs:       0,

  lastShakeTs:        0,
  accelLast:          { x: 0, y: 0, z: 0 },
  trickPulseTimers:   new Map(),
  vibrationEnabled:  lsGet('kardpad_vibration') !== 'false',

  qrStream:    null,
  qrAnimFrame: null,
};

/* ═══════════════════════════════════════════════════════════════════════
   INICIALIZACIÓN
   ═══════════════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
  bindSetup();
  bindController();
  applyPlayerTheme(1);
  initSettingsPanel();

  // Si la URL incluye ?wsHost= (enlace directo), conectar directamente
  const urlWsHost = new URLSearchParams(window.location.search).get('wsHost');
  if (urlWsHost) {
    state.wsUrl = buildWsUrl();
    updateServerAddress();
    const p = getInitialPlayer();
    if (p) connectAs(p);
    else setSetupMessage('Toca tu jugador para conectarte.');
  } else {
    // APK o web sin wsHost: pantalla de IP unificada
    injectIpScreen();
  }
});

// Desbloquear AudioContext en el primer toque (requerido por iOS WebKit)
document.addEventListener('touchstart',  () => HapticEngine.unlock(), { once: true, passive: true });
document.addEventListener('pointerdown', () => HapticEngine.unlock(), { once: true });

/* ─── Helpers de entorno ─────────────────────────────────────────── */
function isCapacitor() {
  return (
    window.Capacitor !== undefined ||
    window.location.protocol === 'capacitor:' ||
    (window.location.protocol === 'http:' && window.location.hostname === 'localhost')
  );
}

function buildWsUrl(hostOverride) {
  const params   = new URLSearchParams(window.location.search);
  const host     = hostOverride || params.get('wsHost');
  // Si la página se cargó por HTTPS usamos WSS (puerto 8001) para mantener
  // el contexto seguro y habilitar el giroscopio en Android Chrome.
  const isHttps  = window.location.protocol === 'https:';
  const defaultPort = isHttps ? '8001' : '8000';
  const port     = params.get('wsPort') || defaultPort;
  const scheme   = isHttps ? 'wss' : 'ws';
  if (host) return `${scheme}://${host}:${port}`;
  return `${scheme}://${window.location.hostname || '127.0.0.1'}:${port}`;
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: RECONEXIÓN CON COUNTDOWN CANCELABLE
   ─────────────────────────────────────────────────────────────────────
   Cuando se pierde la conexión, en lugar de mostrar "Conexión perdida"
   y quedar bloqueado, ofrecemos 8 segundos con countdown visible.
   El usuario puede:
     • Esperar → la app reconecta sola con la misma IP
     • Tocar el mensaje → cancelar y luego ir a ⚙️ → Cambiar servidor
   ═══════════════════════════════════════════════════════════════════════ */
let _reconnectTimer   = null;
let _reconnectSeconds = 0;
let _reconnectPlayer  = null;

function scheduleReconnect(player, delaySec = 8) {
  cancelReconnect();
  _reconnectPlayer  = player;
  _reconnectSeconds = delaySec;

  const tick = () => {
    if (_reconnectSeconds <= 0) {
      _reconnectTimer = null;
      if (_reconnectPlayer !== null) connectAs(_reconnectPlayer);
      return;
    }
    const el = document.getElementById('setupCopy');
    if (el) {
      el.textContent = '¿Cambió la IP? Reconectando en ' + _reconnectSeconds + 's… (toca para cancelar)';
      if (!el.dataset.cancelBound) {
        el.dataset.cancelBound = '1';
        el.style.cursor = 'pointer';
        el.style.textDecoration = 'underline dashed';
        el.addEventListener('click', () => {
          cancelReconnect();
          delete el.dataset.cancelBound;
          el.style.cursor = '';
          el.style.textDecoration = '';
          el.textContent = 'Ve a ⚙️ → Cambiar servidor para actualizar la IP.';
        }, { once: true });
      }
    }
    _reconnectSeconds--;
    _reconnectTimer = setTimeout(tick, 1000);
  };
  tick();
}

function cancelReconnect() {
  if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
  _reconnectPlayer  = null;
  _reconnectSeconds = 0;
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: PANTALLA IP (Capacitor / WebView)
   ═══════════════════════════════════════════════════════════════════════ */

function injectIpScreen(prefillIp = null) {
  document.getElementById('ipScreen')?.remove();
  const saved = prefillIp || lsGet('kardpad_ip') || '';
  const scr = document.createElement('div');
  scr.id = 'ipScreen';
  scr.style.cssText = "position:fixed;inset:0;z-index:9999;display:flex;align-items:center;" +
    "justify-content:center;background:rgba(6,6,14,.97);font-family:'Share Tech Mono',monospace";
  scr.innerHTML = `
    <div style="width:min(90%,380px);padding:28px 24px;border:1px solid rgba(255,255,255,.1);
                border-radius:22px;background:rgba(10,12,22,.9);display:grid;gap:16px;text-align:center;">
      <div style="font-family:'Orbitron',sans-serif;font-size:22px;color:#fff;letter-spacing:.06em;">
        KARD<span style="color:#e74c3c;">PAD</span>
      </div>
      <div style="font-size:11px;color:#7c8ba1;letter-spacing:.1em;">IP DEL PC CON DOLPHIN</div>
      <input id="ipInput" type="text" inputmode="decimal" placeholder="192.168.1.X" value="${saved}"
        style="padding:14px 16px;border-radius:12px;border:1px solid rgba(6,182,212,.35);
               background:rgba(6,182,212,.07);color:#d7fbff;font-size:18px;
               font-family:'Share Tech Mono',monospace;text-align:center;
               outline:none;width:100%;-webkit-appearance:none;touch-action:manipulation;"/>
      <p style="font-size:11px;color:#7c8ba1;line-height:1.6;margin:0;">
        Ejecuta <code style="color:#06b6d4;">python server.py</code> en el PC.<br>
        <span style="color:#4ade80;">Si la IP cambió, corrígela antes de conectar.</span>
      </p>
      <button id="ipConnectBtn" type="button"
        style="padding:15px;border-radius:999px;border:none;cursor:pointer;touch-action:manipulation;
               background:linear-gradient(180deg,#e74c3c,#c0392b);color:#fff;
               font-family:'Orbitron',sans-serif;font-size:14px;letter-spacing:.08em;
               -webkit-appearance:none;">CONECTAR</button>
      <button id="ipQrBtn" type="button"
        style="padding:12px;border-radius:999px;border:1px solid rgba(6,182,212,.4);
               background:rgba(6,182,212,.1);color:#06b6d4;font-size:13px;touch-action:manipulation;
               letter-spacing:.06em;cursor:pointer;-webkit-appearance:none;">📷 Escanear QR</button>
      <div id="ipError" style="font-size:12px;color:#e74c3c;min-height:18px;line-height:1.4;"></div>
    </div>`;
  document.body.appendChild(scr);

  const inp = document.getElementById('ipInput');
  const btn = document.getElementById('ipConnectBtn');
  const qrb = document.getElementById('ipQrBtn');
  const err = document.getElementById('ipError');

  const attempt = () => {
    const raw = inp.value.trim();
    if (!raw) { err.textContent = 'Escribe la IP del PC.'; return; }
    let ip = raw.replace(/^wss?:\/\//i,'').replace(/^https?:\/\//i,'').split('/')[0].split(':')[0].trim();
    const ipv4Re = /^(\d{1,3}\.){3}\d{1,3}$/;
    const hostRe = /^[a-zA-Z0-9][a-zA-Z0-9\-\.]{0,253}$/;
    if (!ip || (!ipv4Re.test(ip) && !hostRe.test(ip))) {
      err.textContent = 'IP no válida. Ej: 192.168.1.37'; return;
    }
    lsSet('kardpad_ip', ip);
    // Usar WSS si la página se cargó por HTTPS (activa giroscopio en Android)
    const _isHttps = window.location.protocol === 'https:';
    state.wsUrl = _isHttps ? `wss://${ip}:8001` : `ws://${ip}:8000`;
    err.textContent = 'Conectando…';
    btn.disabled = true; btn.style.opacity = '0.6';
    const advance = () => {
      btn.disabled = false; btn.style.opacity = '1';
      scr.remove(); updateServerAddress();
      const p = getInitialPlayer();
      if (p) connectAs(p);
      else showSetup();
    };
    let probe;
    try { probe = new WebSocket(state.wsUrl); }
    catch (ex) { advance(); return; }
    let done = false;
    const finish = (ok) => {
      if (done) return; done = true; clearTimeout(timer);
      btn.disabled = false; btn.style.opacity = '1';
      if (ok) {
        scr.remove(); updateServerAddress();
        const p = getInitialPlayer();
        if (p) connectAs(p);
        else showSetup();
      }
      else err.textContent = 'No se pudo conectar. ¿server.py corriendo? ¿Misma Wi-Fi?';
    };
    const timer = setTimeout(() => {
      try { probe.close(); } catch {}
      if (!done) { done = true; btn.disabled=false; btn.style.opacity='1';
        err.textContent = 'Sin respuesta (timeout). Verifica la IP y que server.py esté activo.'; }
    }, 5000);
    probe.addEventListener('open',  () => { try { probe.close(); } catch {} finish(true); });
    probe.addEventListener('error', () => finish(false));
  };

  qrb.addEventListener('click', () => {
    scr.style.display = 'none'; openQrScanner();
    window._qrCloseOverride = () => {
      closeQrScanner();
      if (!state.wsUrl || state.wsUrl.includes('localhost')) scr.style.display = 'flex';
    };
  });
  btn.addEventListener('click', attempt);
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') attempt(); });
  // NO auto-conectar al iniciar: si hay IP guardada, se pre-rellena pero
  // el usuario debe pulsar CONECTAR para confirmar. Evita reconexiones
  // automaticas cuando la IP del PC cambia entre sesiones.
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: SETUP
   ═══════════════════════════════════════════════════════════════════════ */

function getInitialPlayer() {
  const p = Number.parseInt(new URLSearchParams(location.search).get('player') || '', 10);
  return Number.isInteger(p) && p >= 1 && p <= 4 ? p : null;
}

function bindSetup() {
  document.querySelectorAll('.player-card').forEach((card) => {
    card.addEventListener('click', () => {
      const p = Number.parseInt(card.dataset.player || '', 10);
      if (p) connectAs(p);
    });
  });
  document.getElementById('openQrScannerBtn')?.addEventListener('click', openQrScanner);
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: WEBSOCKET
   ═══════════════════════════════════════════════════════════════════════ */

function connectAs(player) {
  state.selectedPlayer = player;
  applyPlayerTheme(player);
  setStatus(`Conectando jugador ${player}...`);
  setSetupMessage(`Conectando P${player}…`);
  if (state.socket) disconnect('switch');

  let socket;
  try { socket = new WebSocket(state.wsUrl); }
  catch { showSetup(); setSetupMessage('No se pudo abrir el WebSocket.'); return; }
  state.socket = socket;

  const timer = setTimeout(() => {
    if (state.socket !== socket) return;
    if (socket.readyState === WebSocket.CONNECTING) {
      socket.close(); state.socket = null;
      showSetup(); setSetupMessage('Sin respuesta. ¿Está corriendo server.py?');
    }
  }, 6000);

  socket.addEventListener('open', () => { clearTimeout(timer); safeSend({ player }); });

  socket.addEventListener('message', (event) => {
    let msg; try { msg = JSON.parse(event.data); } catch { return; }
    if (msg.status === 'connected') {
      state.connectedPlayer = msg.player;
      applyPlayerTheme(msg.player);
      syncSettingsPlayerBtns(msg.player);
      showController();
      setStatus(`P${msg.player} conectado 🏎️`);
      HapticEngine.double(30);
    }
    if (msg.type === 'haptic' && state.vibrationEnabled) {
      HapticEngine.trigger(msg.duration_ms || 80);
    }
  });

  socket.addEventListener('error', () => {
    clearTimeout(timer);
    if (state.socket !== socket) return;
    showSetup(); setSetupMessage('No se pudo conectar. Revisa la IP y la Wi-Fi.');
  });

  socket.addEventListener('close', () => {
    clearTimeout(timer);
    if (state.socket !== socket) return;
    state.socket = null; releaseAllButtons();
    const wasConnected = state.connectedPlayer !== null;
    state.connectedPlayer = null;
    if (wasConnected) {
      showSetup();
      // Countdown cancelable antes de reconectar
      scheduleReconnect(player, 8);
    }
  });
}

function disconnect(reason) {
  releaseAllButtons();
  if (!state.socket) { state.connectedPlayer = null; return; }
  const s = state.socket; state.socket = null; state.connectedPlayer = null;
  try { s.close(1000, reason); } catch {}
}

function safeSend(payload) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
  state.socket.send(JSON.stringify(payload));
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: BOTONES DIGITALES
   ═══════════════════════════════════════════════════════════════════════ */

function bindController() {
  document.getElementById('tiltBtn')?.addEventListener('click', toggleTiltMode);
  document.getElementById('tiltCenterBtn')?.addEventListener('click', calibrateTilt);
  document.getElementById('fullscreenBtn')?.addEventListener('click', toggleFullscreen);
  document.getElementById('settingsGearBtn')?.addEventListener('click', openSettings);
  document.getElementById('controller')?.addEventListener('pointerdown', () => {
    if (isControllerVisible()) lockLandscape();
  }, { passive: true });

  bindButtonPad();
  bindTouchSteering();

  window.addEventListener('beforeunload', () => disconnect('pagehide'));
  window.addEventListener('pagehide',     () => disconnect('pagehide'));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) releaseAllButtons();
    else if (isControllerVisible()) lockLandscape();
  });
  window.addEventListener('blur', releaseAllButtons);
  window.addEventListener('focus', () => {
    if (isControllerVisible()) lockLandscape();
  });
  window.addEventListener('devicemotion', handleDeviceMotion);

  const onOrientationChange = () => {
    if (isControllerVisible()) setTimeout(() => lockLandscape(), 60);
    setTimeout(() => {
      if (state.tiltEnabled) {
        state.tiltNeutral  = null;
        state.tiltSmoothed = 0;
        setTiltCopy('Orientación cambiada. Pulsa "Centrar".');
        HapticEngine.trigger(18);
      }
    }, 350);
  };
  if (screen.orientation) screen.orientation.addEventListener('change', onOrientationChange);
  else window.addEventListener('orientationchange', onOrientationChange);

  updateTiltUi();
}

function bindButtonPad() {
  document.querySelectorAll('[data-btn]').forEach((btn) => {
    const name = btn.dataset.btn; if (!name) return;
    if (DPAD_BUTTONS.has(name)) return;
    const mode = btn.dataset.btnMode || 'hold';
    const pulseMs = Number(btn.dataset.btnPulse || '90');

    const press = (e) => {
      e.preventDefault();
      if (e.pointerId !== undefined) { try { btn.setPointerCapture(e.pointerId); } catch {} }
      if (btn.dataset.pressed === '1') return;
      btn.dataset.pressed = '1';
      btn.classList.add('pressed');
      triggerButtonHaptic(name);
      if (mode === 'pulse') {
        pulseButton(name, pulseMs, btn);
        return;
      }
      state.activeButtons.add(name);
      safeSend({ type: 'button', name, action: 'press' });
      // Háptica diferenciada por tipo de acción
    };

    const release = (e) => {
      if (e) e.preventDefault();
      if (btn.dataset.pressed !== '1') return;
      if (e?.pointerId !== undefined) { try { btn.releasePointerCapture(e.pointerId); } catch {} }
      btn.dataset.pressed = '0';
      btn.classList.remove('pressed');
      if (mode === 'pulse') return;
      state.activeButtons.delete(name);
      safeSend({ type: 'button', name, action: 'release' });
    };

    btn.addEventListener('pointerdown',        press,   { passive: false });
    btn.addEventListener('pointerup',          release, { passive: false });
    btn.addEventListener('pointercancel',      release, { passive: false });
    // NOTA: NO usamos pointerleave para release — si el dedo se sale del botón
    // mientras hacemos setPointerCapture, el evento sigue llegando al botón.
    // Quitarlo evita que un deslizamiento accidental suelte el botón,
    // y también permite navegar el menú sin disparar botones de juego.
    btn.addEventListener('lostpointercapture', release, { passive: false });
  });

  bindDpad();
}

/* ═══════════════════════════════════════════════════════════════════════
   D-PAD: lógica de deslizamiento entre direcciones
   ─────────────────────────────────────────────────────────────────────
   El problema con el d-pad estándar es que setPointerCapture hace que
   el primer botón que tocas "capture" todos los eventos, así que si
   deslizas el dedo a otra dirección, el botón original sigue recibiendo
   los eventos y el nuevo nunca se activa.
   Solución: escuchar pointermove en el wrap, calcular qué botón está
   bajo el dedo y activarlo/desactivarlo manualmente.
   ═══════════════════════════════════════════════════════════════════════ */
function bindDpad() {
  const wrap = document.querySelector('#dpad-cluster .dpad-wrap');
  if (!wrap) return;
  const buttons = Array.from(wrap.querySelectorAll('.dpad-btn'));

  let activeDpad = null;
  let activePointerId = null;

  const dpadPress = (name) => {
    if (activeDpad === name) return;
    if (activeDpad) dpadRelease(activeDpad);
    activeDpad = name;
    const el = wrap.querySelector(`[data-btn="${name}"]`);
    if (el) { el.classList.add('pressed'); el.dataset.pressed = '1'; }
    state.activeButtons.add(name);
    safeSend({ type: 'button', name, action: 'press' });
    HapticEngine.trigger(12);
  };

  const dpadRelease = (name) => {
    if (!name) return;
    const el = wrap.querySelector(`[data-btn="${name}"]`);
    if (el) { el.classList.remove('pressed'); el.dataset.pressed = '0'; }
    state.activeButtons.delete(name);
    safeSend({ type: 'button', name, action: 'release' });
    if (activeDpad === name) activeDpad = null;
  };

  const getBtnUnder = (x, y) => {
    const activeEl = document.elementFromPoint(x, y);
    const directBtn = activeEl?.closest?.('.dpad-btn');
    const directName = directBtn?.dataset?.btn;
    if (directName && DPAD_BUTTONS.has(directName)) return directName;

    // Usamos elementsFromPoint para encontrar qué botón del dpad está bajo el dedo
    const els = document.elementsFromPoint(x, y);
    for (const el of els) {
      const n = el.dataset?.btn;
      if (n && DPAD_BUTTONS.has(n)) return n;
    }
    return null;
  };

  const startPointer = (pointerId) => {
    if (activePointerId !== null && activePointerId !== pointerId) return false;
    activePointerId = pointerId;
    try { wrap.setPointerCapture(pointerId); } catch {}
    return true;
  };

  const finishPointer = (e) => {
    if (e.pointerId !== activePointerId) return;
    e.preventDefault();
    if (wrap.hasPointerCapture(e.pointerId)) {
      try { wrap.releasePointerCapture(e.pointerId); } catch {}
    }
    dpadRelease(activeDpad);
    activePointerId = null;
  };

  wrap.addEventListener('pointerdown', (e) => {
    if (!startPointer(e.pointerId)) return;
    e.preventDefault();
    const name = getBtnUnder(e.clientX, e.clientY);
    if (name) dpadPress(name);
  }, { passive: false });

  wrap.addEventListener('pointermove', (e) => {
    if (e.pointerId !== activePointerId || !wrap.hasPointerCapture(e.pointerId)) return;
    e.preventDefault();
    const name = getBtnUnder(e.clientX, e.clientY);
    if (name) dpadPress(name);
    else if (activeDpad) dpadRelease(activeDpad);
  }, { passive: false });

  wrap.addEventListener('pointerup', finishPointer, { passive: false });
  wrap.addEventListener('pointercancel', finishPointer, { passive: false });
  wrap.addEventListener('lostpointercapture', finishPointer, { passive: false });

  buttons.forEach((btn) => {
    const name = btn.dataset.btn;
    if (!name) return;

    btn.addEventListener('pointerdown', (e) => {
      if (!startPointer(e.pointerId)) return;
      e.preventDefault();
      e.stopPropagation();
      dpadPress(name);
    }, { passive: false });

    btn.addEventListener('pointerup', (e) => {
      if (e.pointerId !== activePointerId) return;
      e.preventDefault();
      e.stopPropagation();
      finishPointer(e);
    }, { passive: false });

    btn.addEventListener('pointercancel', (e) => {
      if (e.pointerId !== activePointerId) return;
      e.preventDefault();
      e.stopPropagation();
      finishPointer(e);
    }, { passive: false });
  });
}

function releaseAllButtons() {
  state.activeButtons.forEach(name => safeSend({ type: 'button', name, action: 'release' }));
  state.activeButtons.clear();
  state.trickPulseTimers.forEach((timer) => clearTimeout(timer));
  state.trickPulseTimers.clear();
  document.querySelectorAll('[data-btn]').forEach(b => {
    b.classList.remove('pressed'); b.dataset.pressed = '0';
  });
  sendNeutralMotion();
}

function triggerButtonHaptic(name) {
  if      (name === 'ACCELERATE') HapticEngine.trigger(18);
  else if (name === 'BRAKE')      HapticEngine.trigger(28);
  else if (name === 'DRIFT')      HapticEngine.trigger(22);
  else if (name === 'ITEM')       HapticEngine.trigger(30);  // ítem: fuerte y claro
  else if (name === 'LOOKBACK')   HapticEngine.trigger(14);
  else if (name === 'TRICK')      HapticEngine.trigger(50);  // truco: el más fuerte
  else                            HapticEngine.trigger(12);
}

function pulseButton(name, durationMs = 90, element = null) {
  const activeTimer = state.trickPulseTimers.get(name);
  if (activeTimer) clearTimeout(activeTimer);

  state.activeButtons.delete(name);
  safeSend({ type: 'button', name, action: 'press' });

  // Si es TRICK, lanzar también el spike de aceleración para el IMU Wiimote
  if (name === 'TRICK') sendShakeSpike();

  if (element) {
    element.dataset.pressed = '1';
    element.classList.add('pressed');
  }

  const timer = setTimeout(() => {
    safeSend({ type: 'button', name, action: 'release' });
    state.trickPulseTimers.delete(name);
    if (element) {
      element.dataset.pressed = '0';
      element.classList.remove('pressed');
    }
  }, durationMs);

  state.trickPulseTimers.set(name, timer);
}

/* ═══════════════════════════════════════════════════════════════════════
   SHAKE SPIKE — simula la sacudida del Wiimote en el IMU virtual
   ───────────────────────────────────────────────────────────────────────
   Dolphin detecta el shake del Wiimote midiendo un cambio brusco en la
   aceleración. Enviamos 3 frames:
     Frame 0 (t=0ms):   spike positivo en Y  (+SHAKE_SPIKE_G, 0, 1)
     Frame 1 (t=35ms):  spike negativo en Y  (-SHAKE_SPIKE_G, 0, 1)
     Frame 2 (t=70ms):  vuelta al neutro       (0, 0, 1)
   El eje Y del DSU corresponde al eje de sacudida del Wiimote horizontal.
   ═══════════════════════════════════════════════════════════════════════ */
function sendShakeSpike() {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;

  const G = SHAKE_SPIKE_G;
  const t = Date.now();

  // Frame 0 — spike positivo
  safeSend({ type: 'motion', accel: { x: 0, y: G, z: 1.0 }, gyro: { pitch: 0, yaw: 0, roll: 0 }, timestamp: t });

  // Frame 1 — spike negativo (cambio brusco necesario para que Dolphin lo detecte)
  setTimeout(() =>
    safeSend({ type: 'motion', accel: { x: 0, y: -G, z: 1.0 }, gyro: { pitch: 0, yaw: 0, roll: 0 }, timestamp: Date.now() }),
  35);

  // Frame 2 — retorno al neutro (o al valor real del volante si está activo)
  setTimeout(() => {
    if (state.tiltEnabled) {
      // El volante ya se encargará del siguiente paquete de motion real
      sendNeutralMotion();
    } else {
      safeSend({ type: 'motion', accel: { x: 0, y: 0, z: 1.0 }, gyro: { pitch: 0, yaw: 0, roll: 0 }, timestamp: Date.now() });
    }
  }, 75);
}

function sendNeutralMotion() {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
  safeSend({
    type: 'motion',
    accel: { x: 0, y: 0, z: 1 },
    gyro:  { pitch: 0, yaw: 0, roll: 0 },
    timestamp: Date.now(),
  });
}

function transformMotionToDsu(acc, rot, angle, steering) {
  // ── Synthetic accelerometer ───────────────────────────────────
  // The phone is held vertically (steering wheel) but DSU expects
  // data as if from a flat DualShock controller:
  //   At rest:       (0, 0, +1)   — gravity through Z (face up)
  //   Tilt right:    (+sin, 0, +cos)
  //   Tilt left:     (-sin, 0, +cos)
  //
  // We use the already-calculated `steering` value (-1..+1) to
  // generate a clean gravity vector.  This avoids all coordinate-
  // system mismatches between phone orientation and DSU convention.
  const tiltRad = steering * 0.7854;          // ±45° max (π/4)
  const accelX  = Math.sin(tiltRad);          // lateral tilt
  const accelZ  = Math.cos(tiltRad);          // vertical component

  return {
    accel: { x: accelX, y: 0.0, z: accelZ },
    gyro:  { pitch: 0.0, yaw: 0.0, roll: steering * 50.0 },
  };
}

function sendMotionPacket(acc, rot, steering, angle) {
  const now = Date.now();
  if (now - state.motionSendTs < 14) return;
  state.motionSendTs = now;

  const motion = transformMotionToDsu(acc, rot, angle, steering);
  safeSend({
    type: 'motion',
    accel: motion.accel,
    gyro: motion.gyro,
    timestamp: now,
  });
}

function bindTouchSteering() {
  const wrap = document.getElementById('tiltBarWrap');
  const track = document.querySelector('#tiltBarWrap .tilt-track');
  if (!wrap || !track) return;

  const start = (e) => {
    if (state.tiltEnabled || !isControllerVisible()) return;
    if (state.touchSteeringPointerId !== null && state.touchSteeringPointerId !== e.pointerId) return;
    e.preventDefault();
    state.touchSteeringPointerId = e.pointerId;
    wrap.classList.add('touch-steering');
    try { wrap.setPointerCapture(e.pointerId); } catch {}
    updateTouchSteeringFromClientX(e.clientX, track);
    setTiltCopy('Arrastra la barra para girar.');
  };

  const move = (e) => {
    if (e.pointerId !== state.touchSteeringPointerId) return;
    e.preventDefault();
    updateTouchSteeringFromClientX(e.clientX, track);
  };

  const finish = (e) => {
    if (e.pointerId !== state.touchSteeringPointerId) return;
    e.preventDefault();
    if (wrap.hasPointerCapture?.(e.pointerId)) {
      try { wrap.releasePointerCapture(e.pointerId); } catch {}
    }
    state.touchSteeringPointerId = null;
    wrap.classList.remove('touch-steering');
    state.touchSteeringValue = 0;
    updateTiltIndicator(0);
    sendNeutralMotion();
    refreshTiltIdleCopy();
  };

  wrap.addEventListener('pointerdown', start, { passive: false });
  wrap.addEventListener('pointermove', move, { passive: false });
  wrap.addEventListener('pointerup', finish, { passive: false });
  wrap.addEventListener('pointercancel', finish, { passive: false });
  wrap.addEventListener('lostpointercapture', finish, { passive: false });
}

function updateTouchSteeringFromClientX(clientX, track) {
  const rect = track.getBoundingClientRect();
  if (!rect.width) return;
  const ratio = clamp((clientX - rect.left) / rect.width, 0, 1);
  const steering = clamp(ratio * 2 - 1, -1, 1);
  state.touchSteeringValue = steering;
  updateTiltIndicator(steering);
  sendMotionPacket(null, null, steering, getEffectiveAngle());
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: INCLINACIÓN (VOLANTE)
   ═══════════════════════════════════════════════════════════════════════ */

async function toggleTiltMode() {
  if (state.tiltEnabled) { disableTilt(); return; }
  const ok = await requestMotionPermission();
  if (!ok) {
    const msg = getMotionBlockedCopy();
    setTiltCopy(msg);
    setStatus(msg);
    return;
  }
  state.tiltEnabled  = true;
  state.tiltSmoothed = 0;
  state.tiltLastHapticSide = null;
  calibrateTilt();
  setStatus(`P${state.connectedPlayer ?? state.selectedPlayer} — Volante activo 🏎️`);
  setTiltCopy('Inclina como un volante. Pulsa "Centrar" si se desvía.');
  updateTiltUi();
  HapticEngine.trigger(25);
}

function disableTilt() {
  state.tiltEnabled = false; state.tiltNeutral = null; state.tiltSmoothed = 0;
  state.tiltLastHapticSide = null;
  updateTiltIndicator(0);
  updateTiltUi();
  refreshTiltIdleCopy();
  sendNeutralMotion();
}

async function requestMotionPermission() {
  if (state.tiltPermission) return true;
  if (getMotionBlockedReason()) return false;
  // iOS 13+ requiere solicitud explícita desde gesto de usuario
  if (typeof DeviceMotionEvent.requestPermission === 'function') {
    try {
      const r = await DeviceMotionEvent.requestPermission();
      state.tiltPermission = r === 'granted'; return state.tiltPermission;
    } catch { return false; }
  }
  state.tiltPermission = true; return true;
}

function calibrateTilt() {
  if (state.lastTiltRaw == null) { setTiltCopy('Sujeta el móvil horizontal y pulsa "Centrar".'); return; }
  state.tiltNeutral  = state.lastTiltRaw;
  state.tiltSmoothed = 0;
  state.tiltLastHapticSide = null;
  updateTiltIndicator(0);
  HapticEngine.double(30);
  if (state.tiltEnabled) setTiltCopy('Centro guardado. Inclina para girar.');
}

function getScreenAngle() {
  if (typeof screen !== 'undefined' && screen.orientation) return screen.orientation.angle ?? 0;
  if (typeof window.orientation === 'number') return window.orientation;
  return 0;
}

/**
 * Devuelve el ángulo efectivo de la pantalla, corrigiendo el caso en que
 * iOS reporta angle=0 aunque el teléfono esté en landscape.
 * Detectamos landscape real comparando innerWidth vs innerHeight.
 */
function getEffectiveAngle() {
  const reported = getScreenAngle();
  const isLandscape = window.innerWidth > window.innerHeight * 1.15;
  if (isLandscape && reported === 0) {
    // iOS con pantalla bloqueada/no reportada → asumimos landscape derecha
    return 90;
  }
  return reported;
}

function getMotionBlockedReason() {
  // Capacitor WebView siempre tiene acceso a sensores
  if (isCapacitor()) return null;

  // Chrome en Android requiere HTTPS estricto para el giroscopio.
  // iOS (WebKit) permite DeviceMotionEvent en IPs locales HTTP.
  const isAndroid = /Android/i.test(navigator.userAgent || '');
  if (isAndroid && !window.isSecureContext) return 'insecure-context';

  if (typeof DeviceMotionEvent === 'undefined') return 'unsupported';
  return null;
}

/** Devuelve la IP del servidor actual como texto legible (para mensajes de error). */
function _getServerIp() {
  if (state.wsUrl) {
    return state.wsUrl.replace(/^wss?:\/\//i, '').split(':')[0];
  }
  return lsGet('kardpad_ip') || '192.168.1.X';
}

function getMotionBlockedCopy() {
  const reason = getMotionBlockedReason();
  if (reason === 'insecure-context') {
    const ip = _getServerIp();
    return (
      `Android Chrome requiere HTTPS para el giroscopio. ` +
      `Abre https://${ip}:3443 en Chrome, acepta la advertencia del certificado ` +
      `(toca "Avanzado" → "Continuar") y vuelve a activar el Volante.`
    );
  }
  if (reason === 'unsupported') {
    return 'Este navegador no expone sensores. Usa la barra para girar o abre la URL HTTPS.';
  }
  return 'Permiso denegado. Pulsa "Volante" de nuevo.';
}

function refreshTiltIdleCopy() {
  if (state.tiltEnabled || state.touchSteeringPointerId !== null) return;
  const reason = getMotionBlockedReason();
  if (reason) {
    setTiltCopy(getMotionBlockedCopy());
    return;
  }
  setTiltCopy('Activa el Volante para girar o arrastra la barra.');
}

function handleDeviceMotion(ev) {
  const acc = ev.accelerationIncludingGravity;
  if (!acc) return;
  const rot = ev.rotationRate || {};

  // ── Inclinación del volante ────────────────────────────────────
  // Usamos getEffectiveAngle() que corrige el bug de iOS reportando 0 en landscape
  const angle = getEffectiveAngle();
  let sx = 0, sy = 0;
  if (angle === 90 || angle === -270) {
    // Landscape «derecha»: el teléfono girado 90° en sentido horario
    sx = (acc.y ?? 0);  sy = (acc.x ?? 0);
  } else if (angle === 270 || angle === -90) {
    // Landscape «izquierda»: girado 90° en antihorario
    sx = -(acc.y ?? 0); sy = -(acc.x ?? 0);
  } else if (angle === 180 || angle === -180) {
    // Portrait invertido
    sx = -(acc.x ?? 0); sy = -(acc.y ?? 0);
  } else {
    // Portrait normal
    sx = (acc.x ?? 0);  sy = (acc.y ?? 0);
  }
  let rawAngle = Math.atan2(sx, Math.abs(sy));
  let rawRoll = clamp(rawAngle / (Math.PI / 2), -1, 1);
  if (sx < 0) rawRoll = -Math.abs(rawRoll);
  state.lastTiltRaw = rawRoll;

  if (state.tiltEnabled) {
    const raw = state.tiltNeutral != null ? rawRoll - state.tiltNeutral : rawRoll;
    // EMA correcta: alpha = peso del nuevo valor
    state.tiltSmoothed = TILT_SMOOTH_ALPHA * raw + (1 - TILT_SMOOTH_ALPHA) * state.tiltSmoothed;

    const sens     = TILT_SENSE_MAP[state.tiltSensLevel] || TILT_SENSE_MAP[3];
    const smoothed = Math.abs(state.tiltSmoothed) > sens.deadzone ? state.tiltSmoothed : 0;
    const steering = state.invertSteering ? -smoothed : smoothed;

    updateTiltIndicator(steering);
    triggerTiltHaptic(steering, sens.threshold);
    sendMotionPacket(acc, rot, steering, angle);
  } else {
    updateTiltIndicator(0);
  }

  // ── Detección de shake ────────────────────────────────────────
  const ax = acc.x ?? 0, ay = acc.y ?? 0, az = acc.z ?? 0;
  const dx = ax - state.accelLast.x;
  const dy = ay - state.accelLast.y;
  const dz = az - state.accelLast.z;
  const jerk = Math.sqrt(dx*dx + dy*dy + dz*dz);
  state.accelLast = { x: ax, y: ay, z: az };

  if (jerk > SHAKE_THRESHOLD) {
    const now = Date.now();
    if (now - state.lastShakeTs > SHAKE_DEBOUNCE_MS) {
      state.lastShakeTs = now;
      // 1. Pulsa botón TRICK (Shake/Y en WiimoteNew.ini)
      pulseButton('TRICK', 90, document.getElementById('shakeBtn'));
      // 2. Flash visual + háptico extra
      flashShakeButton();
      // Nota: sendShakeSpike() se llama DENTRO de pulseButton cuando name === 'TRICK'
    }
  }
}

/* ─── Haptic proporcional al ángulo de giro ──────────────────────── */
function triggerTiltHaptic(value, threshold) {
  const now = Date.now();
  const absVal = Math.abs(value);

  if (absVal <= threshold) {
    if (state.tiltLastHapticSide !== 'center') {
      state.tiltLastHapticSide = 'center';
      HapticEngine.trigger(10);
    }
    return;
  }

  const side      = value > 0 ? 'right' : 'left';
  const intensity = clamp((absVal - threshold) / (1 - threshold), 0, 1);
  const interval  = Math.round(180 - intensity * 120); // 60ms – 180ms
  const duration  = Math.round(12 + intensity * 18);   // 12ms – 30ms

  if (now - state.tiltHapticTs < interval) return;
  state.tiltHapticTs       = now;
  state.tiltLastHapticSide = side;
  HapticEngine.trigger(duration);
}

function updateTiltIndicator(value) {
  const ind = document.getElementById('tiltIndicator'); if (!ind) return;
  const pct = clamp((value + 1) / 2 * 100, 0, 100);
  ind.style.left = `${pct}%`;
  const sens = TILT_SENSE_MAP[state.tiltSensLevel] || TILT_SENSE_MAP[3];
  if (value > sens.threshold)       ind.style.background = '#3498db';
  else if (value < -sens.threshold) ind.style.background = '#e74c3c';
  else                              ind.style.background = '#f1c40f';
}

function flashShakeButton() {
  const btn = document.getElementById('shakeBtn'); if (!btn) return;
  btn.classList.add('shake-flash');
  setTimeout(() => btn.classList.remove('shake-flash'), 180);
  HapticEngine.trigger(40);
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: PUNTERO TÁCTIL
   ═══════════════════════════════════════════════════════════════════════ */


/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: AJUSTES
   ═══════════════════════════════════════════════════════════════════════ */

function initSettingsPanel() {
  document.getElementById('settingsCloseBtn')?.addEventListener('click', closeSettings);
  document.getElementById('settingsBackdrop')?.addEventListener('click', closeSettings);

  document.querySelectorAll('[data-settings-player]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const p = Number.parseInt(btn.dataset.settingsPlayer || '', 10); if (!p) return;
      if (state.connectedPlayer) { disconnect('switch'); connectAs(p); }
      else { state.selectedPlayer = p; applyPlayerTheme(p); syncSettingsPlayerBtns(p); }
      closeSettings();
    });
  });

  const vibToggle = document.getElementById('vibrationToggle');
  if (vibToggle) {
    vibToggle.setAttribute('aria-checked', state.vibrationEnabled ? 'true' : 'false');
    vibToggle.addEventListener('click', () => {
      state.vibrationEnabled = !state.vibrationEnabled;
      lsSet('kardpad_vibration', String(state.vibrationEnabled));
      vibToggle.setAttribute('aria-checked', state.vibrationEnabled ? 'true' : 'false');
      if (state.vibrationEnabled) HapticEngine.trigger(30);
    });
  }

  document.getElementById('pointer-cluster')?.remove();
  document.getElementById('pointerToggle')?.closest('.settings-section')?.remove();

  const slider = document.getElementById('tiltSensSlider');
  if (slider) {
    slider.value = String(state.tiltSensLevel); updateTiltSensLabel();
    slider.addEventListener('input', () => {
      state.tiltSensLevel = Number(slider.value);
      lsSet('kardpad_tilt_sens', String(state.tiltSensLevel));
      updateTiltSensLabel();
    });
  }

  const invertToggle = document.getElementById('invertSteeringToggle');
  if (invertToggle) {
    invertToggle.setAttribute('aria-checked', state.invertSteering ? 'true' : 'false');
    invertToggle.addEventListener('click', () => {
      state.invertSteering = !state.invertSteering;
      lsSet('kardpad_invert_steering', String(state.invertSteering));
      invertToggle.setAttribute('aria-checked', state.invertSteering ? 'true' : 'false');
      updateTiltIndicator(state.tiltEnabled ? (state.invertSteering ? -state.tiltSmoothed : state.tiltSmoothed) : 0);
      HapticEngine.trigger(18);
    });
  }

  document.getElementById('rescanQrBtn')?.addEventListener('click',  () => { closeSettings(); setTimeout(openQrScanner, 300); });
  document.getElementById('reconnectBtn')?.addEventListener('click', () => {
    closeSettings();
    if (state.wsUrl) { const p = state.connectedPlayer||state.selectedPlayer||1; disconnect('manual'); setTimeout(() => connectAs(p), 300); }
    else showSetup();
  });
  document.getElementById('changePlayerBtn')?.addEventListener('click', () => {
    closeSettings(); disconnect('manual'); disableTilt(); showSetup(); setStatus('Elige jugador.');
  });

  document.getElementById('changeServerBtn')?.addEventListener('click', () => {
    closeSettings();
    cancelReconnect();
    disconnect('manual');
    disableTilt();
    // Extraer IP actual para pre-rellenarla (el usuario solo edita el ultimo octeto si cambia)
    const currentIp = state.wsUrl
      ? state.wsUrl.replace(/^wss?:\/\//i,'').split(':')[0]
      : (lsGet('kardpad_ip') || '');
    state.wsUrl = null;
    injectIpScreen(currentIp);
  });

  syncSettingsPlayerBtns(state.selectedPlayer);
}

function openSettings()  {
  const o = document.getElementById('settingsOverlay');
  if (o) { o.classList.add('open'); o.setAttribute('aria-hidden','false'); }
}
function closeSettings() {
  const o = document.getElementById('settingsOverlay');
  if (o) { o.classList.remove('open'); o.setAttribute('aria-hidden','true'); }
}

function syncSettingsPlayerBtns(player) {
  document.querySelectorAll('[data-settings-player]').forEach((btn) => {
    btn.classList.toggle('active', Number.parseInt(btn.dataset.settingsPlayer||'',10) === player);
  });
}

function updateTiltSensLabel() {
  const labels = {1:'Zona muerta: muy amplia',2:'Zona muerta: amplia',3:'Zona muerta: media',4:'Zona muerta: pequeña',5:'Zona muerta: mínima'};
  const el = document.getElementById('tiltSensLabel');
  if (el) el.textContent = labels[state.tiltSensLevel] || labels[3];
}

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: QR SCANNER
   ═══════════════════════════════════════════════════════════════════════ */

function openQrScanner() {
  const modal = document.getElementById('qrScannerModal'); if (!modal) return;
  setQrResult('','');
  document.getElementById('qrScannerHint').textContent = 'Apunta al QR del servidor';
  modal.classList.add('open'); modal.setAttribute('aria-hidden','false');
  startQrCamera();
}

function closeQrScanner() {
  stopQrCamera();
  const modal = document.getElementById('qrScannerModal');
  if (modal) { modal.classList.remove('open'); modal.setAttribute('aria-hidden','true'); }
  if (typeof window._qrCloseOverride === 'function') {
    window._qrCloseOverride(); window._qrCloseOverride = null;
  }
}

function startQrCamera() {
  const video = document.getElementById('qrVideo'), canvas = document.getElementById('qrCanvas');
  if (!video || !canvas) return;
  stopQrCamera();
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .then((stream) => {
        state.qrStream = stream; video.srcObject = stream; video.play().catch(() => {});
        video.addEventListener('loadedmetadata', () => {
          canvas.width = video.videoWidth || 640; canvas.height = video.videoHeight || 640;
          scheduleQrScan();
        }, { once: true });
      })
      .catch((err) => {
        if (['NotAllowedError','NotFoundError','OverconstrainedError'].includes(err.name)) startQrFileFallback();
        else setQrResult(`No se pudo acceder a la cámara: ${err.name}`, 'error');
      });
  } else {
    startQrFileFallback();
  }
}

function startQrFileFallback() {
  const hint = document.getElementById('qrScannerHint');
  const result = document.getElementById('qrScannerResult');
  const videoWrap = document.querySelector('.qr-video-wrap');
  if (hint) hint.textContent = 'Toca el botón para abrir la cámara y fotografiar el QR';
  if (videoWrap) {
    videoWrap.innerHTML = `
      <div style="width:100%;height:100%;display:flex;flex-direction:column;align-items:center;
                  justify-content:center;gap:16px;background:#0a0c18;border-radius:20px;">
        <div style="font-size:48px;">📷</div>
        <label id="qrFileLabel"
          style="padding:14px 28px;border-radius:999px;border:1px solid rgba(6,182,212,.5);
                 background:rgba(6,182,212,.15);color:#06b6d4;font-size:14px;touch-action:manipulation;
                 letter-spacing:.06em;cursor:pointer;font-family:'Orbitron',sans-serif;">
          FOTOGRAFIAR QR
          <input type="file" id="qrFileInput" accept="image/*" capture="environment"
                 style="position:absolute;opacity:0;width:0;height:0;pointer-events:none;">
        </label>
        <div style="font-size:11px;color:#7c8ba1;text-align:center;padding:0 16px;line-height:1.5;">
          Se abrirá la cámara.<br>Fotografía el QR del servidor.
        </div>
      </div>`;
    const fileInput = document.getElementById('qrFileInput');
    if (fileInput) {
      fileInput.addEventListener('change', async (e) => {
        const file = e.target.files?.[0]; if (!file) return;
        if (result) { result.textContent = 'Procesando imagen…'; result.className = 'qr-scanner-result'; }
        try {
          const bitmap = await createImageBitmap(file);
          const cvs = document.createElement('canvas');
          cvs.width = bitmap.width; cvs.height = bitmap.height;
          const ctx = cvs.getContext('2d'); ctx.drawImage(bitmap, 0, 0);
          const imageData = ctx.getImageData(0, 0, cvs.width, cvs.height);
          const code = (typeof jsQR !== 'undefined')
            ? jsQR(imageData.data, imageData.width, imageData.height, { inversionAttempts: 'dontInvert' })
            : null;
          if (code?.data) handleQrDetected(code.data);
          else if (result) { result.textContent = 'No se detectó QR. Inténtalo de nuevo.'; result.className = 'qr-scanner-result error'; }
        } catch {
          if (result) { result.textContent = 'Error al leer la imagen.'; result.className = 'qr-scanner-result error'; }
        }
      });
    }
  }
}

function stopQrCamera() {
  if (state.qrAnimFrame) { cancelAnimationFrame(state.qrAnimFrame); state.qrAnimFrame=null; }
  if (state.qrStream)    { state.qrStream.getTracks().forEach(t=>t.stop()); state.qrStream=null; }
  const video=document.getElementById('qrVideo'); if(video) video.srcObject=null;
}

function scheduleQrScan() { state.qrAnimFrame = requestAnimationFrame(scanQrFrame); }

function scanQrFrame() {
  const video=document.getElementById('qrVideo'), canvas=document.getElementById('qrCanvas');
  if (!video||!canvas||!state.qrStream) return;
  if (video.readyState !== video.HAVE_ENOUGH_DATA) { scheduleQrScan(); return; }
  const ctx=canvas.getContext('2d',{willReadFrequently:true});
  canvas.width=video.videoWidth; canvas.height=video.videoHeight;
  ctx.drawImage(video,0,0,canvas.width,canvas.height);
  let imageData; try { imageData=ctx.getImageData(0,0,canvas.width,canvas.height); } catch { scheduleQrScan(); return; }
  const code=(typeof jsQR!=='undefined') ? jsQR(imageData.data,imageData.width,imageData.height,{inversionAttempts:'dontInvert'}) : null;
  if (code?.data) handleQrDetected(code.data); else scheduleQrScan();
}

function handleQrDetected(rawData) {
  let ip=null;
  try { ip=new URL(rawData.trim()).hostname; }
  catch { const m=rawData.trim().match(/(\d{1,3}(?:\.\d{1,3}){3})/); if(m) ip=m[1]; }
  if (!ip) { setQrResult('QR sin IP válida.','error'); scheduleQrScan(); return; }
  HapticEngine.double(40);
  setQrResult(`✓ Servidor: ${ip}`,'success');
  lsSet('kardpad_ip', ip);
  setTimeout(() => {
    const _isHttps = window.location.protocol === 'https:';
    state.wsUrl = _isHttps ? `wss://${ip}:8001` : `ws://${ip}:8000`;
    updateServerAddress();
    closeQrScanner(); closeSettings();
    connectAs(getInitialPlayer()||state.selectedPlayer||1);
  }, 900);
}

function setQrResult(text,type) {
  const el=document.getElementById('qrScannerResult'); if(!el) return;
  el.textContent=text; el.className='qr-scanner-result'+(type?` ${type}`:'');
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('qrScannerClose')?.addEventListener('click', closeQrScanner);
});

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: UI HELPERS
   ═══════════════════════════════════════════════════════════════════════ */

function applyPlayerTheme(player) {
  const color = PLAYER_COLORS[player] || PLAYER_COLORS[1];
  document.documentElement.style.setProperty('--player-color', color);
  document.documentElement.style.setProperty('--player-glow', `${color}66`);
  document.querySelectorAll('.player-card').forEach(c => {
    const sel = Number.parseInt(c.dataset.player||'',10) === player;
    c.classList.toggle('selected', sel); c.setAttribute('aria-checked', String(sel));
  });
}

function updateTiltUi() {
  const btn = document.getElementById('tiltBtn');
  const ctr = document.getElementById('tiltCenterBtn');
  if (btn) { btn.textContent = state.tiltEnabled ? 'Volante ON' : 'Volante OFF'; btn.classList.toggle('mini-btn-active', state.tiltEnabled); }
  if (ctr) { ctr.disabled = !state.tiltEnabled; ctr.classList.toggle('mini-btn-disabled', !state.tiltEnabled); }
}

function setTiltCopy(t) { const el=document.getElementById('tiltCopy'); if(el) el.textContent=t; }
function updateServerAddress() { const el=document.getElementById('serverAddress'); if(el) el.textContent=state.wsUrl||'--'; }
function showController() {
  document.getElementById('setup').style.display='none';
  document.getElementById('controller').style.display='block';
  refreshTiltIdleCopy();
  acquireWakeLock();
  lockLandscape();
}
function showSetup() {
  document.getElementById('controller').style.display='none';
  document.getElementById('setup').style.display='flex';
  releaseWakeLock();
  unlockOrientation();
}
function setStatus(t)     { const el=document.getElementById('statusText'); if(el) el.textContent=t; }
function setSetupMessage(t) { const el=document.getElementById('setupCopy'); if(el) el.textContent=t; }

async function toggleFullscreen() {
  const root=document.documentElement;
  if (!document.fullscreenElement && root.requestFullscreen) { try { await root.requestFullscreen(); } catch {} }
  else if (document.fullscreenElement && document.exitFullscreen) { try { await document.exitFullscreen(); } catch {} }
}

function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function isControllerVisible() {
  return document.getElementById('controller')?.style.display !== 'none';
}
function lsGet(k)    { try { return localStorage.getItem(k); }    catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); }        catch {} }

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: WAKE LOCK (evita que la pantalla se apague)
   ═══════════════════════════════════════════════════════════════════════ */
let _wakeLock = null;
let _landscapeRelockTimer = null;

async function acquireWakeLock() {
  if (!('wakeLock' in navigator)) return;
  try {
    _wakeLock = await navigator.wakeLock.request('screen');
    _wakeLock.addEventListener('release', () => { _wakeLock = null; });
  } catch (_) {}
}

function releaseWakeLock() {
  if (_wakeLock) { try { _wakeLock.release(); } catch {} _wakeLock = null; }
}

// Re-adquirir cuando la app vuelve al frente (iOS libera el lock en background)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && _wakeLock === null) acquireWakeLock();
});

/* ═══════════════════════════════════════════════════════════════════════
   MÓDULO: ORIENTATION LOCK (forzar landscape)
   ═══════════════════════════════════════════════════════════════════════ */
async function lockLandscape(retry = false) {
  if (_landscapeRelockTimer) {
    clearTimeout(_landscapeRelockTimer);
    _landscapeRelockTimer = null;
  }
  const plugin = window.Capacitor?.Plugins?.ScreenOrientation;
  if (plugin?.lock) {
    try {
      await plugin.lock({ orientation: 'landscape' });
      return;
    } catch (_) {}
  }
  try {
    if (screen.orientation && screen.orientation.lock) {
      await screen.orientation.lock('landscape');
      return;
    }
  } catch (_) {
    // iOS Safari no soporta lock() fuera de fullscreen — silenciar el error
  }
  if (!retry) {
    _landscapeRelockTimer = setTimeout(() => {
      _landscapeRelockTimer = null;
      if (isControllerVisible()) lockLandscape(true);
    }, 800);
  }
}

async function unlockOrientation() {
  if (_landscapeRelockTimer) {
    clearTimeout(_landscapeRelockTimer);
    _landscapeRelockTimer = null;
  }
  const plugin = window.Capacitor?.Plugins?.ScreenOrientation;
  if (plugin?.unlock) {
    try {
      await plugin.unlock();
      return;
    } catch (_) {}
  }
  try {
    if (screen.orientation?.unlock) screen.orientation.unlock();
  } catch (_) {}
}

/* ─── Alias de compatibilidad ────────────────────────────────────── */
function triggerHaptic(ms = 22) { HapticEngine.trigger(ms); }
