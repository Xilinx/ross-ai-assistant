// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/*
 * wf.js -- the Workflows panel's page script, for a browser.
 *
 * The documents are drawn by workflow_view.js, the Vivado AI extension's own
 * workflowViewModel.ts (bundled; global WorkflowView), exactly as the editor
 * draws them. This file does what the editor's panel script and its extension
 * host do around that renderer: fetch the workflow's documents, keep the chosen
 * tab, open rows, scroll and followed logs across refreshes (localStorage here,
 * webview state there), follow logs, open files, and turn "Ask the agent"
 * (which in the editor puts a prompt into the chat) into "Copy prompt".
 * Nothing here runs a workflow action other than the read-only view actions
 * the server runs for it.
 */
(function () {
  'use strict';
  var W = window.WorkflowView;
  var body = document.body;
  var app = document.getElementById('app');
  var statusEl = document.getElementById('wf-status');
  // The page's own values, all data-wf-*: wf.js finds click targets with
  // closest('[data-refresh]') and the like, and when the refresh period sat on
  // <body> as data-refresh every click on the page was a refresh, so a run's
  // details never opened.
  var cfg = {};
  Object.keys(body.dataset).forEach(function (k) {
    if (/^wf[A-Z]/.test(k)) { cfg[k.charAt(2).toLowerCase() + k.slice(3)] = body.dataset[k]; }
  });
  var P = cfg.prefix || '';
  // A link carries its secret once (?k=...): the server has set its cookie by
  // now, so take the secret out of the address bar, the history and any copy.
  (function () {
    var u = new URL(location.href);
    if (u.searchParams.has('k')) {
      u.searchParams.delete('k');
      history.replaceState(history.state, '', u.pathname + (u.search || '') + u.hash);
    }
  })();
  var FOLLOW_MS = 3000;                  // the extension's FOLLOW_MS

  function q(params) {
    return Object.keys(params).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
    }).join('&');
  }
  function getJSON(url) {
    return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) {
      if (r.status === 401) { location.href = P + '/signin?next=' + encodeURIComponent(location.pathname + location.search); }
      if (r.status === 403) { throw new Error('access refused: open the full link you were given (it carries the access key)'); }
      return r.json().then(function (d) {
        if (!r.ok || !d.ok) { throw new Error(d.error || ('HTTP ' + r.status)); }
        return d;
      });
    });
  }
  function stamp(text) { if (statusEl) { statusEl.textContent = text; } }
  function now() { return new Date().toLocaleTimeString(); }
  var toastTimer;
  function toast(text) {
    var t = document.getElementById('wf-toast');
    if (!t) { return; }
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 3500);
  }
  function copy(text) {
    var done = function () { toast('Prompt copied: paste it into your agent’s chat.'); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  }
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) { /* nothing more to try */ }
    document.body.removeChild(ta);
  }

  // ------------------------------------------------------------- the run list
  var STATE_LABEL = { running: 'running', attention: 'needs you', done: 'done', failed: 'failed', stopped: 'stopped', idle: 'idle' };

  function card(wf, ws, item) {
    var e = W.esc;
    var href = wf.detail ? P + '/wf/run?' + (function () {
      var p = { workflow: wf.id, workspace: ws }; p[wf.detail_arg] = item.id; return q(p);
    })() : null;
    var metrics = item.metrics.map(function (m) {
      return '<span class="' + (m.tone ? 't-' + m.tone : '') + '">' + e(m.label) + ' ' + e(m.value) + e(m.unit || '') + '</span>';
    }).join('');
    var badges = item.badges.map(function (b) {
      return '<span class="badge' + (b.tone ? ' t-' + b.tone : '') + '">' + e(b.text) + '</span>';
    }).join('');
    var spin = item.state === 'running' ? '<span class="wf-spin">↻</span> ' : '';
    var inner = '<div class="wf-head"><span class="wf-name">' + e(item.title) + '</span>' +
      '<span class="wf-state st-' + e(item.state) + '">' + spin + e(STATE_LABEL[item.state] || item.state) + '</span></div>' +
      (item.subtitle ? '<div class="muted small">' + e(item.subtitle) + '</div>' : '') +
      (metrics ? '<div class="wf-line metrics">' + metrics + '</div>' : '') +
      (badges ? '<div class="wf-line">' + badges + '</div>' : '') +
      (item.updated ? '<div class="muted small">updated ' + e(item.updated) + '</div>' : '');
    var tip = e(W.urgentLine(item));
    return href ? '<a class="wf-card st-' + e(item.state) + '" href="' + e(href) + '" title="' + tip + '">' + inner + '</a>'
      : '<div class="wf-card st-' + e(item.state) + '" title="' + tip + '">' + inner + '</div>';
  }

  function renderList(d) {
    var e = W.esc;
    var all = [];
    var html = [];
    (d.errors || []).forEach(function (x) { html.push('<p class="t-bad">' + e(x.dir) + ': ' + e(x.error) + '</p>'); });
    if (!(d.workspaces || []).length) {
      html.push('<p class="muted">No workspace is configured: set TCF_WORKSPACES (folders whose runs to show).</p>');
    }
    (d.workflows || []).forEach(function (wf) {
      var parts = [];
      (wf.workspaces || []).forEach(function (s) {
        if (!s.ok) {
          parts.push('<div class="wf-ws">' + e(s.workspace) + '</div><p class="t-bad small">' + e(s.error) + '</p>');
          return;
        }
        var doc = W.parseListDocument(s.doc);
        if (!doc.items.length) { return; }
        all = all.concat(doc.items);
        var ordered = W.byUrgency(doc.items.map(function (item) { return { item: item }; }));
        parts.push('<div class="wf-ws">' + e(s.workspace) + '</div><div class="wf-cards">' +
          ordered.map(function (x) { return card(wf, s.workspace, x.item); }).join('') + '</div>');
      });
      html.push('<section class="wf-group"><h2>' + e(wf.title) + ' <span class="small muted">' + e(wf.id) + '</span></h2>' +
        (parts.length ? parts.join('') : '<p class="muted">No runs in the configured workspaces.</p>') + '</section>');
    });
    var sum = W.statusSummary(all);
    var head = sum ? '<div class="banner' + (sum.warn ? '' : ' t-info') + ' wf-summary" title="' + e(sum.tooltip) + '">' +
      e(sum.text) + '</div>' : '';
    var y = window.scrollY;
    app.innerHTML = head + html.join('');
    window.scrollTo(0, y);
  }

  function listPage() {
    var only = cfg.workflow || '';
    var period = 30;
    function load() {
      getJSON(P + '/api/wf/list/?' + q(only ? { workflow: only } : {})).then(function (d) {
        renderList(d);
        period = Math.max(5, Math.min.apply(null, (d.workflows || []).map(function (w) { return w.refresh_seconds; }).concat([30])));
        stamp('updated ' + now());
      }, function (err) {
        stamp('could not refresh: ' + err.message);
      }).then(function () { setTimeout(load, period * 1000); });
    }
    load();
  }

  // ------------------------------------------------------------- one run
  function runPage() {
    var ds = cfg;
    var target = { workflow: ds.workflow, workspace: ds.workspace };
    target[ds.arg] = ds.value;
    var key = 'wfview:' + ds.workflow + ':' + ds.workspace + ':' + ds.value;
    var state = {};
    try { state = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (e) { state = {}; }
    var open = new Set(state.open || []);
    var last = null;
    var refreshMs = Math.max(5, parseInt(ds.refresh, 10) || 30) * 1000;
    var timer = null;

    function save(patch) {
      Object.keys(patch).forEach(function (k) { state[k] = patch[k]; });
      try { localStorage.setItem(key, JSON.stringify(state)); } catch (e) { /* storage full or off */ }
    }
    function show(tab) {
      var panes = Array.prototype.slice.call(document.querySelectorAll('[data-pane]'));
      if (!panes.length) { return; }
      if (!panes.some(function (p) { return p.dataset.pane === tab; })) { tab = panes[0].dataset.pane; }
      panes.forEach(function (p) { p.classList.toggle('on', p.dataset.pane === tab); });
      document.querySelectorAll('.tabs a').forEach(function (a) { a.classList.toggle('on', a.dataset.tab === tab); });
      save({ tab: tab });
    }
    function setLog(pre, text) {
      if (!pre || pre.textContent === text) { return; }
      var atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
      pre.textContent = text;
      if (atEnd) { pre.scrollTop = pre.scrollHeight; }
    }
    function showTail() {
      var box = document.getElementById('tail');
      if (!box) { return; }
      box.hidden = !state.tail;
      if (state.tail) {
        document.getElementById('tail-name').textContent = state.tail.split('/').slice(-2).join('/');
        var pre = document.getElementById('tail-text');
        pre.textContent = state.tailText || '';
        pre.scrollTop = pre.scrollHeight;
      }
    }
    // The editor-only actions, made browser ones: open = view the file, ask = copy.
    function adapt() {
      document.querySelectorAll('button[data-ask]').forEach(function (b) {
        b.textContent = 'Copy prompt';
        b.title = 'Copy this prompt, to paste into your agent’s chat';
      });
      document.querySelectorAll('[data-open]').forEach(function (a) {
        a.setAttribute('href', fileHref(a.dataset.open));
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener');
      });
    }
    function fileHref(path) {
      var p = {}; Object.keys(target).forEach(function (k) { p[k] = target[k]; });
      p.path = path;
      return P + '/wf/file?' + q(p);
    }
    function apply() {
      var doc = document.getElementById('doc');
      if (!doc) { return; }
      if (state.id !== doc.dataset.id) {
        open.clear();
        save({ id: doc.dataset.id, tab: null, open: [], tail: null, tailText: '', logs: {}, scroll: 0 });
      }
      body.classList.toggle('notabs', doc.dataset.tabs !== '1');
      show(state.tab);
      document.querySelectorAll('details[data-key]').forEach(function (d) { d.open = open.has(d.dataset.key); });
      document.querySelectorAll('pre[data-follow]').forEach(function (p) {
        var kept = (state.logs || {})[p.dataset.follow];
        if (kept !== undefined) { p.textContent = kept; }
        p.scrollTop = p.scrollHeight;
      });
      showTail();
      adapt();
    }
    function draw(error) {
      if (!last) {
        app.innerHTML = '<p class="t-bad">' + W.esc(error || 'no document') + '</p>';
        return;
      }
      var y = window.scrollY;
      app.innerHTML = W.renderDetailBody(last, { workflowTitle: ds.workflowTitle || ds.workflow, error: error });
      apply();
      window.scrollTo(0, y);
    }
    function load() {
      clearTimeout(timer);
      return getJSON(P + '/api/wf/view/?' + q(target)).then(function (d) {
        var doc = W.parseDetailDocument(d.doc);
        if (!doc) { throw new Error('the workflow returned a document without an id or title'); }
        var first = !last;
        last = doc;
        draw();
        if (first && state.scroll) { window.scrollTo(0, state.scroll); }
        document.title = doc.title + ' — ' + (ds.workflowTitle || ds.workflow);
        stamp('updated ' + now() + ' · every ' + Math.round(refreshMs / 1000) + ' s');
        follow();
      }, function (err) {
        draw(err.message);
        stamp('could not refresh: ' + err.message);
      }).then(function () { timer = setTimeout(load, refreshMs); });
    }
    function tailOf(path) {
      var p = {}; Object.keys(target).forEach(function (k) { p[k] = target[k]; });
      p.path = path;
      return getJSON(P + '/api/wf/tail/?' + q(p)).then(function (d) { return d.text; });
    }
    var following = false;
    function follow() {
      if (following || !last) { return; }
      following = true;
      var jobs = W.followedLogs(last).map(function (f) {
        return tailOf(f.path).then(function (text) {
          var logs = {}; Object.keys(state.logs || {}).forEach(function (k) { logs[k] = state.logs[k]; });
          logs[f.key] = text;
          save({ logs: logs });
          setLog(document.querySelector('pre[data-follow="' + f.key + '"]'), text);
        }, function () { /* the file may not exist yet */ });
      });
      if (state.tail) {
        var path = state.tail;
        jobs.push(tailOf(path).then(function (text) {
          if (state.tail !== path) { return; }
          save({ tailText: text });
          var box = document.getElementById('tail');
          if (box) { box.hidden = false; }
          setLog(document.getElementById('tail-text'), text);
        }, function () {
          if (state.tail !== path) { return; }
          save({ tailText: '(cannot follow ' + path + ': not a readable text file inside the workspace or the run)' });
          showTail();
        }));
      }
      Promise.all(jobs).then(function () { following = false; }, function () { following = false; });
    }

    document.addEventListener('toggle', function (ev) {
      var d = ev.target;
      if (!d.dataset || !d.dataset.key) { return; }
      if (d.open) { open.add(d.dataset.key); } else { open.delete(d.dataset.key); }
      save({ open: Array.from(open) });
    }, true);
    var st;
    window.addEventListener('scroll', function () {
      clearTimeout(st); st = setTimeout(function () { save({ scroll: window.scrollY }); }, 150);
    });
    document.addEventListener('click', function (ev) {
      var t = ev.target.closest('[data-ask],[data-refresh],[data-tab],[data-tail],[data-untail]');
      // Only the document's own controls: nothing outside #app is one, and a
      // click anywhere else (a row's details, a link) keeps its default.
      if (!t || !app.contains(t)) { return; }  // [data-open] links are real links now
      ev.preventDefault();
      if (t.dataset.tail) {
        save({ tail: t.dataset.tail, tailText: '(reading…)' });
        showTail();
        document.getElementById('tail').scrollIntoView({ block: 'nearest' });
        following = false;
        follow();
      } else if (t.dataset.untail) {
        save({ tail: null, tailText: '' });
        showTail();
      } else if (t.dataset.ask) {
        copy(t.dataset.ask);
      } else if (t.dataset.refresh) {
        load();
      } else if (t.dataset.tab) {
        show(t.dataset.tab); window.scrollTo(0, 0); save({ scroll: 0 });
      }
    });
    setInterval(function () { if (!document.hidden) { follow(); } }, FOLLOW_MS);
    load();
  }

  if (!W) {
    app.innerHTML = '<p class="t-bad">workflow_view.js did not load.</p>';
  } else if (cfg.page === 'run') {
    runPage();
  } else {
    listPage();
  }
})();
