/* TBHStargaze web UI - vanilla JS, no deps */
'use strict';

const POLL_MS = 800;
const $ = (id) => document.getElementById(id);

const state = {
  itemNames: {},          // id(str) -> name
  itemGrades: {},         // id(str) -> 'COMMON' / 'RARE' / ...
  watched: new Set(),     // Set<int>
  lastSig: '',
  muted: false,
  lastHits: new Set(),
  lastUpdateTs: 0,
  refreshReadyAlerted: false,
};

// Maps the GRADE enum value to the CSS class shared with the TBH guide site.
const GRADE_CLS = {
  COMMON: 'c-common',
  UNCOMMON: 'c-uncommon',
  RARE: 'c-rare',
  LEGENDARY: 'c-legendary',
  IMMORTAL: 'c-immortal',
  ARCANA: 'c-arcana',
  BEYOND: 'c-beyond',
  CELESTIAL: 'c-celestial',
  DIVINE: 'c-divine',
  COSMIC: 'c-cosmic',
};

// Chinese label for each grade - matches rarity.html on the TBH guide site.
const GRADE_LABEL = {
  COMMON: '普通',
  UNCOMMON: '罕见',
  RARE: '稀有',
  LEGENDARY: '传奇',
  IMMORTAL: '不朽',
  ARCANA: '至宝',
  BEYOND: '超凡',
  CELESTIAL: '天界',
  DIVINE: '神圣',
  COSMIC: '宇宙',
};

function gradeOf(id, gradeMaybe) {
  return gradeMaybe || state.itemGrades[String(id)] || '';
}

function gradeClass(id, gradeMaybe) {
  return GRADE_CLS[gradeOf(id, gradeMaybe)] || '';
}

function gradeLabel(id, gradeMaybe) {
  return GRADE_LABEL[gradeOf(id, gradeMaybe)] || '';
}

// Steam Market badge for an item (rendered inside each row).
// Item is the enriched object from /queue: { price?, market_link?, price_pending?, price_failed?, price_unavailable? }
function priceBadge(it) {
  if (it.price_unavailable) {
    return '<span class="price na" title="此稀有度暂不可在 Steam 市场交易">—</span>';
  }
  if (it.price_pending) {
    return '<span class="price loading" title="价格查询中（受 Steam 限速）">…</span>';
  }
  if (it.price) {
    const link = it.market_link || '#';
    return `<a class="price ok" href="${link}" target="_blank" rel="noopener" title="点击在 Steam Market 查看">${escapeHtml(it.price)}</a>`;
  }
  if (it.price_failed) {
    return '<span class="price fail" title="查询失败 (网络/Steam 屏蔽)，稍后自动重试">!</span>';
  }
  // Cached "no listing": Steam responded but item is not currently listed.
  if (it.market_link) {
    return `<a class="price empty" href="${it.market_link}" target="_blank" rel="noopener" title="当前无人挂单，点击查看 Market">·</a>`;
  }
  return '';
}

// ---- HTTP ----
async function api(path, opts) {
  try {
    const r = await fetch(path, opts);
    return await r.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---- toast ----
let toastTimer = null;
let audioCtx = null;

function toast(title, body, isAlert) {
  const t = $('toast');
  t.innerHTML = `<div class="t">${title}</div>${body || ''}`;
  t.className = isAlert ? 'show alert' : 'show';
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ''; }, 4500);
}

function getAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return audioCtx;
}

function playBeep(ctx) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.connect(gain); gain.connect(ctx.destination);
  osc.frequency.value = 880; osc.type = 'sine';
  gain.gain.setValueAtTime(0.18, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
  osc.start(); osc.stop(ctx.currentTime + 0.25);
  setTimeout(() => {
    const osc2 = ctx.createOscillator();
    const gain2 = ctx.createGain();
    osc2.connect(gain2); gain2.connect(ctx.destination);
    osc2.frequency.value = 1100; osc2.type = 'sine';
    gain2.gain.setValueAtTime(0.18, ctx.currentTime);
    gain2.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
    osc2.start(); osc2.stop(ctx.currentTime + 0.25);
  }, 180);
}

function playRefreshCue(ctx) {
  const notes = [523.25, 659.25, 783.99];
  notes.forEach((freq, idx) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = freq;
    osc.type = 'triangle';
    const start = ctx.currentTime + idx * 0.11;
    gain.gain.setValueAtTime(0.001, start);
    gain.gain.exponentialRampToValueAtTime(0.14, start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.16);
    osc.start(start);
    osc.stop(start + 0.17);
  });
}

function unlockAudio() {
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') ctx.resume();
  } catch (e) { /* ignore */ }
}

function beep() {
  if (state.muted) return;
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') {
      ctx.resume().then(() => playBeep(ctx)).catch(() => {});
    } else {
      playBeep(ctx);
    }
  } catch (e) { /* ignore */ }
}

function refreshCue() {
  if (state.muted) return;
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') {
      ctx.resume().then(() => playRefreshCue(ctx)).catch(() => {});
    } else {
      playRefreshCue(ctx);
    }
  } catch (e) { /* ignore */ }
}

// ---- status dot ----
function setStatus(connected, msg) {
  const dot = $('dot');
  const txt = $('statusText');
  dot.className = 'dot ' + (connected ? 'ok' : (msg && msg.includes('错') ? 'err' : 'warn'));
  txt.textContent = msg || (connected ? '已连接' : '未连接');
}

function renderRefreshAge() {
  const el = $('lastUpdate');
  const banner = $('bannerUpdate');
  if (!state.lastUpdateTs) {
    if (el) el.textContent = '--';
    if (banner) banner.textContent = '等待队列刷新…';
    return;
  }
  const age = Math.max(0, Math.floor(Date.now() / 1000 - state.lastUpdateTs));
  const d = new Date(state.lastUpdateTs * 1000);
  if (el) el.textContent = `更新于 ${d.toLocaleTimeString()} · ${age} 秒前`;
  if (banner) banner.innerHTML = `上次队列刷新 <span class="sec">${age}</span> 秒前 <span class="time">${d.toLocaleTimeString()}</span>`;
  if (age >= 15 && !state.refreshReadyAlerted) {
    state.refreshReadyAlerted = true;
    toast('可以切图刷新了', '上次队列刷新已超过 15 秒', true);
    refreshCue();
  }
}

// ---- render ----
function renderQueue(panelId, countId, items, watchedSet) {
  const container = $(panelId);
  const cnt = $(countId);
  cnt.textContent = items.length;
  if (!items.length) {
    container.innerHTML = '';
    container.classList.add('empty');
    return;
  }
  container.classList.remove('empty');
  const html = items.map((it, idx) => {
    const isFirst = idx === 0;
    const isWatched = watchedSet.has(it.id);
    const cls = ['item', isFirst ? 'first' : '', isWatched ? 'watched' : ''].filter(Boolean).join(' ');
    const grade = gradeClass(it.id, it.grade);
    const tier = gradeLabel(it.id, it.grade);
    return `
      <div class="${cls}" data-id="${it.id}">
        <span class="idx">${idx + 1}</span>
        <span class="name ${grade}">${escapeHtml(it.name)}${tier ? `<span class="tier ${grade}">·${tier}</span>` : ''}</span>
        ${isFirst ? '<span class="label">即将掉落</span>' : ''}
        ${priceBadge(it)}
        <span class="id">#${it.id}</span>
        <span class="star" data-id="${it.id}" title="${isWatched ? '从关注移除' : '加入关注'}">${isWatched ? '★' : '☆'}</span>
      </div>
    `;
  }).join('');
  container.innerHTML = html;

  container.querySelectorAll('.star').forEach(el => {
    el.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = parseInt(el.dataset.id, 10);
      if (watchedSet.has(id)) {
        await api('/watched/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [id] }) });
        toast('已移除关注', escapeHtml(state.itemNames[id] || ('#' + id)));
      } else {
        await api('/watched/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [id] }) });
        toast('已加入关注', escapeHtml(state.itemNames[id] || ('#' + id)));
      }
      refreshWatched();
      fetchQueue();
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---- watched chips ----
function renderChips() {
  const wrap = $('chips');
  const ids = [...state.watched].sort();
  $('watchedCount').textContent = ids.length;
  if (!ids.length) {
    wrap.innerHTML = '<span class="empty">尚未添加 · 在下方队列点 ☆ 或在搜索框添加</span>';
    return;
  }
  wrap.innerHTML = ids.map(id => {
    const name = state.itemNames[id] || ('#' + id);
    const grade = gradeClass(id);
    const tier = gradeLabel(id);
    return `<span class="chip"><span class="${grade}">${escapeHtml(name)}${tier ? `<span class="tier ${grade}">·${tier}</span>` : ''}</span>
      <span style="font-size:.7em;opacity:.5;font-family:monospace">#${id}</span>
      <span class="x" data-id="${id}" title="移除">×</span></span>`;
  }).join('');
  wrap.querySelectorAll('.x').forEach(x => {
    x.addEventListener('click', async () => {
      const id = parseInt(x.dataset.id, 10);
      await api('/watched/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [id] }) });
      refreshWatched();
      fetchQueue();
    });
  });
}

async function refreshWatched() {
  const r = await api('/watched');
  if (r && r.watched_ids) {
    state.watched = new Set(r.watched_ids);
    renderChips();
  }
}

// ---- queue fetch loop ----
async function fetchQueue() {
  const r = await api('/queue');
  if (!r) return;
  setStatus(r.connected, r.status);
  if (r.last_update) {
    if (r.last_update !== state.lastUpdateTs) {
      state.refreshReadyAlerted = false;
    }
    state.lastUpdateTs = r.last_update;
    renderRefreshAge();
  }

  if (r.watched_ids) {
    state.watched = new Set(r.watched_ids);
  }

  const normal = r.normal_named || [];
  const boss = r.boss_named || [];

  const allItems = [...normal, ...boss];
  const currentHits = new Set();
  for (const it of allItems) {
    if (state.watched.has(it.id)) currentHits.add(it.id);
  }
  const newHits = [...currentHits].filter(id => !state.lastHits.has(id));
  state.lastHits = currentHits;
  if (newHits.length) {
    const names = newHits.map(id => state.itemNames[id] || ('#' + id));
    toast('🎯 关注命中!', escapeHtml(names.join(' · ')), true);
    beep();
  }

  renderQueue('normalList', 'normalCount', normal, state.watched);
  renderQueue('bossList', 'bossCount', boss, state.watched);
  renderChips();
}

// ---- search box ----
function buildIndex(items) {
  state.itemNames = items;
}

function searchItems(q) {
  q = q.trim().toLowerCase();
  if (!q) return [];
  if (/^\d+$/.test(q) && state.itemNames[q]) {
    return [{ id: parseInt(q, 10), name: state.itemNames[q] }];
  }
  const hits = [];
  for (const [id, name] of Object.entries(state.itemNames)) {
    if (name.toLowerCase().includes(q)) {
      hits.push({ id: parseInt(id, 10), name });
      if (hits.length >= 30) break;
    }
  }
  return hits;
}

function setupSearch() {
  const input = $('search');
  const sugs = $('sugs');
  let activeIdx = -1;
  let current = [];

  function render() {
    if (!current.length) {
      sugs.classList.remove('show');
      sugs.innerHTML = '';
      return;
    }
    sugs.innerHTML = current.map((it, i) => {
      const isWatched = state.watched.has(it.id);
      const grade = gradeClass(it.id);
      const tier = gradeLabel(it.id);
      return `<div class="suggestion ${i === activeIdx ? 'active' : ''}" data-id="${it.id}">
        <span class="${grade}">${escapeHtml(it.name)}${tier ? `<span class="tier ${grade}">·${tier}</span>` : ''} ${isWatched ? '<span style="color:#e94560">★</span>' : ''}</span>
        <span class="sid">#${it.id}</span>
      </div>`;
    }).join('');
    sugs.classList.add('show');
    sugs.querySelectorAll('.suggestion').forEach(el => {
      el.addEventListener('click', async () => {
        const id = parseInt(el.dataset.id, 10);
        await api('/watched/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [id] }) });
        toast('已加入关注', escapeHtml(state.itemNames[id]));
        refreshWatched();
        fetchQueue();
        input.value = '';
        current = [];
        render();
      });
    });
  }

  input.addEventListener('input', () => {
    current = searchItems(input.value);
    activeIdx = -1;
    render();
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      activeIdx = Math.min(activeIdx + 1, current.length - 1);
      render();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      activeIdx = Math.max(activeIdx - 1, -1);
      render();
    } else if (e.key === 'Enter' && activeIdx >= 0) {
      e.preventDefault();
      const el = sugs.querySelectorAll('.suggestion')[activeIdx];
      if (el) el.click();
    } else if (e.key === 'Escape') {
      input.value = '';
      current = [];
      render();
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-wrap')) {
      sugs.classList.remove('show');
    }
  });
}

// ---- buttons ----
function setupButtons() {
  $('saveBtn').addEventListener('click', async () => {
    const r = await api('/watched/save', { method: 'POST' });
    if (r && r.ok) {
      toast('已保存关注配置', `共 ${r.watched_ids.length} 个关注物品`);
    } else {
      toast('保存失败', JSON.stringify(r));
    }
  });

  $('reloadBtn').addEventListener('click', async () => {
    const r = await api('/watched/reload', { method: 'POST' });
    if (r && r.ok) {
      toast('已重读配置', `共 ${r.watched_ids.length} 个关注物品`);
      refreshWatched();
      fetchQueue();
    } else {
      toast('重读失败', JSON.stringify(r));
    }
  });

  $('muteBtn').addEventListener('click', () => {
    state.muted = !state.muted;
    $('muteBtn').textContent = state.muted ? '🔕 提醒关' : '🔔 提醒开';
    toast(state.muted ? '提醒已静音' : '提醒已开启', '');
  });
}

function setupAudioUnlock() {
  document.addEventListener('pointerdown', unlockAudio, { passive: true });
  document.addEventListener('keydown', unlockAudio);
}

// ---- bootstrap ----
async function main() {
  setupAudioUnlock();
  setupSearch();
  setupButtons();

  // Load item names + grades in parallel
  const [items, grades] = await Promise.all([api('/items'), api('/grades')]);
  buildIndex(items.item || {});
  state.itemGrades = grades || {};

  await refreshWatched();
  await fetchQueue();

  setInterval(fetchQueue, POLL_MS);
  setInterval(renderRefreshAge, 1000);
}

main().catch(e => {
  console.error(e);
  toast('启动失败', String(e));
});
