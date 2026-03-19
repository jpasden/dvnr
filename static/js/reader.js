/**
 * DVNR Reader — reader.js
 *
 * Handles:
 *  - Rendering tokenised text into the text column
 *  - Chunk hover highlighting
 *  - Token / chunk click → vocabulary note creation
 *  - Note management (add, delete, clear, renumber)
 *  - Text size controls
 *  - Language toggle (with confirmation + re-parse)
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  const tokens = window.DVNR_TOKENS || [];
  const language = window.DVNR_LANGUAGE || 'es';
  const showLangToggle = window.DVNR_SHOW_LANG_TOGGLE || false;
  const isPrepared = window.DVNR_IS_PREPARED || false;

  let noteCounter = 0;
  // Map from noteKey → { noteEl, tokenEls }
  const notesMap = new Map();

  // ---------------------------------------------------------------------------
  // DOM refs
  // ---------------------------------------------------------------------------
  const textBody = document.getElementById('text-body');
  const notesList = document.getElementById('notes-list');
  const notesEmpty = document.getElementById('notes-empty');
  const clearAllBtn = document.getElementById('clear-all-btn');

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function makeNoteKey(surfaceText) {
    return surfaceText.trim().toLowerCase();
  }

  function posClass(pos) {
    if (!pos) return 'pos-other';
    return 'pos-' + pos.toLowerCase();
  }

  function chunkRoleClass(role) {
    if (!role || role === 'solo') return 'chunk-solo';
    return 'chunk-' + role;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ---------------------------------------------------------------------------
  // Text rendering
  // ---------------------------------------------------------------------------

  function renderTokens() {
    if (!textBody || !tokens.length) return;

    // Group tokens into chunk spans and solo tokens
    // We need to wrap consecutive tokens sharing the same chunk_id in a
    // <span class="chunk"> wrapper.

    // First pass: build an ordered list of "segments":
    // each segment is either { type: 'chunk', chunkId, tokens: [...] }
    // or { type: 'token', token }
    const segments = [];
    let i = 0;

    while (i < tokens.length) {
      const tok = tokens[i];
      if (tok.chunk_id !== null && tok.chunk_id !== undefined) {
        // Collect all tokens with this chunk_id contiguously
        const chunkId = tok.chunk_id;
        const chunkTokens = [];
        while (i < tokens.length && tokens[i].chunk_id === chunkId) {
          chunkTokens.push(tokens[i]);
          i++;
        }
        segments.push({ type: 'chunk', chunkId, tokens: chunkTokens });
      } else {
        segments.push({ type: 'token', token: tok });
        i++;
      }
    }

    const fragment = document.createDocumentFragment();

    // Collect title token indices for heading wrapper
    const titleIndices = new Set(
      tokens.filter(function (t) { return t.is_title; }).map(function (t) { return t.idx; })
    );

    // We'll wrap title segments in a heading div
    let inTitleBlock = false;
    let titleDiv = null;

    segments.forEach(function (seg, segIdx) {
      // Determine if this segment belongs to the title
      const segIsTitle = seg.type === 'token'
        ? (seg.token.is_title && !seg.token.is_newline && !seg.token.is_space)
        : seg.tokens.some(function (t) { return t.is_title; });

      if (segIsTitle && !inTitleBlock) {
        titleDiv = document.createElement('div');
        titleDiv.className = 'text-title-line';
        fragment.appendChild(titleDiv);
        inTitleBlock = true;
      } else if (!segIsTitle && inTitleBlock) {
        inTitleBlock = false;
        titleDiv = null;
      }

      const target = inTitleBlock ? titleDiv : fragment;

      if (seg.type === 'token') {
        const tok = seg.token;
        if (tok.is_newline) {
          if (!inTitleBlock) fragment.appendChild(document.createElement('br'));
          return;
        }
        if (tok.is_space) {
          return; // spaces come from between-segment logic below
        }
        const span = makeTokenSpan(tok);
        target.appendChild(span);
      } else {
        // chunk — render all non-space tokens inside a chunk wrapper
        const chunkSpan = document.createElement('span');
        chunkSpan.className = 'chunk';
        chunkSpan.dataset.chunkId = seg.chunkId;

        const visibleToks = seg.tokens.filter(function (t) { return !t.is_space && !t.is_newline; });
        visibleToks.forEach(function (tok, idx) {
          const span = makeTokenSpan(tok);
          chunkSpan.appendChild(span);
          // Space between tokens inside the chunk (not after last)
          if (idx < visibleToks.length - 1 && !visibleToks[idx + 1].is_punct) {
            chunkSpan.appendChild(document.createTextNode(' '));
          }
        });

        target.appendChild(chunkSpan);
      }

      // Find the next non-space, non-newline segment to decide whether to add a space
      let nextRealTok = null;
      for (let ni = segIdx + 1; ni < segments.length; ni++) {
        const ns = segments[ni];
        if (ns.type === 'token') {
          if (!ns.token.is_space && !ns.token.is_newline) { nextRealTok = ns.token; break; }
        } else {
          const nf = ns.tokens.find(t => !t.is_space && !t.is_newline);
          if (nf) { nextRealTok = nf; break; }
        }
      }
      if (nextRealTok && !nextRealTok.is_punct) {
        target.appendChild(document.createTextNode(' '));
      }
    });

    textBody.appendChild(fragment);
    attachInteractions();
  }

  function makeTokenSpan(tok) {
    const span = document.createElement('span');
    const classes = ['token', posClass(tok.pos), chunkRoleClass(tok.chunk_role)];
    if (tok.is_punct) classes.push('is-punct');
    if (tok.is_space) classes.push('is-space');
    span.className = classes.join(' ');

    span.dataset.idx = tok.idx;
    span.dataset.text = tok.text;
    span.dataset.lemma = tok.lemma || '';
    span.dataset.pos = tok.pos || '';
    if (tok.definition_surface) span.dataset.defSurface = tok.definition_surface;
    if (tok.definition_lemma) span.dataset.defLemma = tok.definition_lemma;
    if (tok.chunk_id != null) span.dataset.chunkId = tok.chunk_id;
    if (tok.fixed_expr_def) span.dataset.fixedExprDef = tok.fixed_expr_def;
    if (tok.fixed_expr_canonical) span.dataset.fixedExprCanonical = tok.fixed_expr_canonical;

    span.textContent = tok.text;
    return span;
  }

  // ---------------------------------------------------------------------------
  // Interactions: chunk hover, token click
  // ---------------------------------------------------------------------------

  function attachInteractions() {
    // Chunk hover: highlight entire chunk
    document.querySelectorAll('.chunk').forEach(function (chunkEl) {
      chunkEl.addEventListener('mouseenter', function () {
        chunkEl.classList.add('chunk-hover');
      });
      chunkEl.addEventListener('mouseleave', function () {
        chunkEl.classList.remove('chunk-hover');
      });
    });

    // Token clicks
    document.querySelectorAll('.token').forEach(function (tokenEl) {
      if (tokenEl.classList.contains('is-punct') || tokenEl.classList.contains('is-space')) return;

      tokenEl.addEventListener('click', function (e) {
        e.stopPropagation();
        handleTokenClick(tokenEl);
      });
    });
  }

  function handleTokenClick(tokenEl) {
    const chunkId = tokenEl.dataset.chunkId;

    if (chunkId) {
      // Treat the entire chunk as the click target
      handleChunkClick(chunkId, tokenEl);
    } else {
      handleSoloTokenClick(tokenEl);
    }
  }

  function handleSoloTokenClick(tokenEl) {
    const surface = tokenEl.dataset.text;
    const noteKey = makeNoteKey(surface);

    if (notesMap.has(noteKey)) {
      // Already in notes: highlight + scroll
      highlightToken(tokenEl);
      scrollToNote(noteKey);
      return;
    }

    highlightToken(tokenEl);
    addSoloTokenNote(tokenEl);
  }

  function handleChunkClick(chunkId, clickedTokenEl) {
    // Find all tokens in this chunk
    const chunkEl = clickedTokenEl.closest('.chunk');
    if (!chunkEl) {
      handleSoloTokenClick(clickedTokenEl);
      return;
    }

    // Build surface text of entire chunk (skip spaces/punct for key)
    const chunkTokenEls = Array.from(chunkEl.querySelectorAll('.token'));
    const chunkText = chunkTokenEls.map(function (t) { return t.dataset.text; }).join(' ');
    const noteKey = makeNoteKey(chunkText);

    if (notesMap.has(noteKey)) {
      highlightChunk(chunkEl);
      scrollToNote(noteKey);
      return;
    }

    highlightChunk(chunkEl);
    addChunkNote(chunkEl, chunkTokenEls);
  }

  // ---------------------------------------------------------------------------
  // Highlight helpers
  // ---------------------------------------------------------------------------

  function highlightToken(tokenEl) {
    tokenEl.classList.add('highlighted');
  }

  function highlightChunk(chunkEl) {
    chunkEl.classList.add('chunk-selected');
    chunkEl.querySelectorAll('.token').forEach(function (t) {
      t.classList.add('highlighted');
    });
  }

  // ---------------------------------------------------------------------------
  // Note creation
  // ---------------------------------------------------------------------------

  function addSoloTokenNote(tokenEl) {
    const surface = tokenEl.dataset.text;
    const lemma = tokenEl.dataset.lemma;
    const pos = tokenEl.dataset.pos;
    const defSurface = tokenEl.dataset.defSurface || '';
    const defLemma = tokenEl.dataset.defLemma || '';
    const isVerb = pos === 'VERB' || pos === 'AUX';
    const noteKey = makeNoteKey(surface);

    noteCounter++;
    const noteEl = document.createElement('li');
    noteEl.className = 'note-entry';
    noteEl.dataset.noteKey = noteKey;

    let html = '<div class="note-number">' + noteCounter + '.</div>';
    html += '<div class="note-body">';
    html += '<span class="note-surface">' + escapeHtml(surface) + '</span>';

    if (isVerb && lemma && lemma !== surface.toLowerCase()) {
      html += '<span class="note-line note-infinitive"><span class="note-label">[infinitive: ' + escapeHtml(lemma) + ']</span></span>';
    }

    if (isVerb) {
      if (defSurface) {
        html += '<span class="note-line note-def-surface">' + escapeHtml(defSurface) + '</span>';
      }
      if (defLemma) {
        html += '<span class="note-line note-def-lemma">' + escapeHtml(defLemma) + '</span>';
      }
    } else {
      if (defLemma) {
        html += '<span class="note-line note-def-lemma">' + escapeHtml(defLemma) + '</span>';
      }
    }

    html += '</div>';
    html += '<button class="note-dismiss" aria-label="Remove note" data-note-key="' + escapeHtml(noteKey) + '">✕</button>';

    noteEl.innerHTML = html;
    appendNote(noteEl, noteKey, [tokenEl]);
  }

  function addChunkNote(chunkEl, chunkTokenEls) {
    const chunkText = chunkTokenEls.map(function (t) { return t.dataset.text; }).join(' ');
    const noteKey = makeNoteKey(chunkText);

    // Get fixed expression data from first token if available
    const firstToken = chunkTokenEls[0];
    const fixedExprDef = firstToken ? (firstToken.dataset.fixedExprDef || '') : '';
    const fixedExprCanonical = firstToken ? (firstToken.dataset.fixedExprCanonical || '') : '';

    // Build canonical form from lemmas
    const lemmaForm = chunkTokenEls
      .filter(function (t) { return !t.classList.contains('is-punct'); })
      .map(function (t) { return t.dataset.lemma || t.dataset.text; })
      .join(' ');

    noteCounter++;
    const noteEl = document.createElement('li');
    noteEl.className = 'note-entry note-chunk';
    noteEl.dataset.noteKey = noteKey;

    let html = '<div class="note-number">' + noteCounter + '.</div>';
    html += '<div class="note-body">';
    html += '<span class="note-surface">' + escapeHtml(chunkText) + '</span>';

    const canonical = fixedExprCanonical || (lemmaForm !== chunkText.toLowerCase() ? lemmaForm : '');
    if (canonical) {
      html += '<span class="note-line"><span class="note-label">[' + escapeHtml(canonical) + ']</span></span>';
    }

    const definition = fixedExprDef;
    if (definition) {
      html += '<span class="note-line note-def-lemma">' + escapeHtml(definition) + '</span>';
    } else {
      const firstVerb = chunkTokenEls.find(function (t) { return t.dataset.pos === 'VERB' || t.dataset.pos === 'AUX'; });
      if (firstVerb && firstVerb.dataset.defLemma) {
        html += '<span class="note-line note-def-lemma">' + escapeHtml(firstVerb.dataset.defLemma) + '</span>';
      }
    }

    html += '</div>';
    html += '<button class="note-dismiss" aria-label="Remove note" data-note-key="' + escapeHtml(noteKey) + '">✕</button>';

    noteEl.innerHTML = html;
    appendNote(noteEl, noteKey, chunkTokenEls);
  }

  function appendNote(noteEl, noteKey, tokenEls) {
    notesList.appendChild(noteEl);
    notesMap.set(noteKey, { noteEl, tokenEls });

    // Wire dismiss button
    noteEl.querySelector('.note-dismiss').addEventListener('click', function () {
      removeNote(noteKey);
    });

    updateNotesEmpty();
    noteEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function removeNote(noteKey) {
    const entry = notesMap.get(noteKey);
    if (!entry) return;
    entry.noteEl.remove();
    notesMap.delete(noteKey);
    // Remove highlights from tokens belonging to this note
    if (entry.tokenEls) {
      entry.tokenEls.forEach(function (t) { t.classList.remove('highlighted'); });
    }
    renumberNotes();
    updateNotesEmpty();
  }

  function renumberNotes() {
    const items = notesList.querySelectorAll('.note-entry');
    items.forEach(function (item, idx) {
      const numEl = item.querySelector('.note-number');
      if (numEl) numEl.textContent = (idx + 1) + '.';
    });
    noteCounter = items.length;
  }

  function updateNotesEmpty() {
    if (notesEmpty) {
      notesEmpty.style.display = notesMap.size === 0 ? 'block' : 'none';
    }
  }

  function scrollToNote(noteKey) {
    const entry = notesMap.get(noteKey);
    if (!entry) return;
    entry.noteEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    entry.noteEl.classList.add('note-pulse');
    setTimeout(function () { entry.noteEl.classList.remove('note-pulse'); }, 800);
  }

  // ---------------------------------------------------------------------------
  // Clear all
  // ---------------------------------------------------------------------------

  if (clearAllBtn) {
    clearAllBtn.addEventListener('click', function () {
      if (notesMap.size === 0) return;
      notesList.innerHTML = '';
      notesMap.clear();
      noteCounter = 0;
      document.querySelectorAll('.token.highlighted').forEach(function (el) { el.classList.remove('highlighted'); });
      document.querySelectorAll('.chunk.chunk-selected').forEach(function (el) { el.classList.remove('chunk-selected'); });
      updateNotesEmpty();
    });
  }

  // ---------------------------------------------------------------------------
  // Text size controls
  // ---------------------------------------------------------------------------

  const sizeClasses = ['size-s', 'size-m', 'size-l', 'size-xl'];

  document.querySelectorAll('.size-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const size = btn.dataset.size;
      if (!textBody) return;
      sizeClasses.forEach(function (cls) { textBody.classList.remove(cls); });
      textBody.classList.add('size-' + size);
      document.querySelectorAll('.size-btn').forEach(function (b) {
        b.classList.toggle('active', b.dataset.size === size);
      });
    });
  });

  // ---------------------------------------------------------------------------
  // Language toggle (paste flow only)
  // ---------------------------------------------------------------------------

  const langToggleEl = document.getElementById('lang-toggle-reader');
  if (langToggleEl && showLangToggle) {
    langToggleEl.addEventListener('click', function (e) {
      if (!e.target.classList.contains('lang-btn')) return;
      const newLang = e.target.dataset.lang;
      if (newLang === language) return;

      const confirmed = window.confirm(
        'This will re-parse the text with ' + newLang.toUpperCase() + ' and clear your notes. Continue?'
      );
      if (!confirmed) return;

      const reparseForm = document.getElementById('reparse-form');
      const reparseLang = document.getElementById('reparse-lang');
      if (reparseForm && reparseLang) {
        reparseLang.value = newLang;
        const overlay = document.getElementById('loading-overlay');
        if (overlay) overlay.classList.remove('hidden');
        reparseForm.submit();
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------

  renderTokens();
  updateNotesEmpty();

})();
