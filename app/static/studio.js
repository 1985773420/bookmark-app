/* Workspace preferences and unified local search; no build dependencies. */
(() => {
  'use strict';
  const read = (key, fallback) => { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } };
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  document.addEventListener('DOMContentLoaded', () => {
    const grid = document.querySelector('#grid');
    const cards = grid ? [...grid.querySelectorAll('.card')] : [];
    const input = document.querySelector('.page-head .search input[name=q]');
    const form = input?.form;
    const clear = form?.querySelector('.search-clear');
    const filter = document.querySelector('#bookmark-filter');
    const sort = document.querySelector('#bookmark-sort');
    const count = document.querySelector('#collection-count');
    const serverQuery = new URLSearchParams(location.search).get('q') || '';
    let empty;
    function view(value) {
      value = value === 'list' ? 'list' : 'grid';
      if (grid) grid.dataset.view = value;
      document.querySelectorAll('[data-view]').forEach(button => {
        if (button.tagName === 'BUTTON') button.setAttribute('aria-pressed', String(button.dataset.view === value));
      });
      save('bm-view', value);
    }
    view(read('bm-view', 'grid'));
    document.querySelectorAll('button[data-view]').forEach(button => button.addEventListener('click', () => view(button.dataset.view)));
    function update() {
      if (clear) clear.hidden = !input.value;
      if (!grid) return;
      const query = input?.value.trim().toLocaleLowerCase() || '';
      const type = filter?.value || 'all';
      let shown = 0;
      cards.forEach(card => {
        const haystack = [card.dataset.title, card.dataset.host, card.dataset.description, card.dataset.tags, card.dataset.urlWan, card.dataset.urlLan].join(' ').toLocaleLowerCase();
        const matchesType = type === 'all' || (type === 'lan' && card.classList.contains('has-lan')) || (type === 'private' && card.classList.contains('is-private')) || (type === 'warning' && !!card.querySelector('.warn'));
        const matches = haystack.includes(query) && matchesType;
        card.classList.toggle('is-filtered-out', !matches);
        if (matches) shown++;
      });
      if (count) count.textContent = shown === cards.length ? `${shown} 个书签` : `${shown} / ${cards.length} 个书签`;
      form?.classList.toggle('is-filtering', !!query);
      empty?.remove();
      if (!shown) {
        empty = document.createElement('div');
        empty.className = 'empty is-instant';
        const heading = document.createElement('p'); heading.className = 'empty-title'; heading.textContent = query ? `没有匹配「${input.value.trim()}」的书签` : '当前筛选下没有书签';
        const hint = document.createElement('p'); hint.className = 'muted'; hint.textContent = '试试其他关键词，或重置当前筛选。按回车可重新搜索当前分类。';
        const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'btn'; reset.textContent = '重置筛选'; reset.addEventListener('click', resetFilters);
        empty.append(heading, hint, reset); grid.after(empty);
      }
    }
    function resetFilters() {
      if (serverQuery) {
        const url = new URL(location.href); url.searchParams.delete('q'); location.assign(url.pathname + url.search); return;
      }
      if (input) input.value = '';
      if (filter) filter.value = 'all';
      update(); input?.focus();
    }
    clear?.addEventListener('click', resetFilters);
    input?.addEventListener('input', update);
    input?.addEventListener('keydown', event => {
      if (event.key === 'Escape' && input.value) { event.stopPropagation(); resetFilters(); }
    });
    filter?.addEventListener('change', update);
    function reorder() {
      if (!grid || !sort) return;
      const sorted = [...cards];
      if (sort.value === 'title' || sort.value === 'domain') {
        const key = sort.value === 'title' ? 'title' : 'host';
        sorted.sort((a,b) => (a.dataset[key] || '').localeCompare(b.dataset[key] || '', 'zh-CN', {numeric:true}));
      } else if (sort.value === 'newest') sorted.sort((a,b) => Number(b.dataset.id) - Number(a.dataset.id));
      sorted.forEach(card => grid.append(card));
      // Reordering is a display preference; keep drag-to-category but do not persist an accidental sorted order.
      cards.forEach(card => { if (card.hasAttribute('draggable')) card.draggable = sort.value === 'default'; });
      save('bm-sort', sort.value);
    }
    if (sort) {
      const saved = read('bm-sort', 'default');
      if ([...sort.options].some(option => option.value === saved)) sort.value = saved;
      sort.addEventListener('change', reorder); reorder();
    }
    if (grid) update();
    else if (clear) clear.hidden = !input.value;
    // Nested category toggles are keyboard-operable without changing category links.
    document.querySelectorAll('[data-twist]').forEach(toggle => {
      const node = toggle.closest('.tree-node');
      toggle.setAttribute('role','button'); toggle.tabIndex = 0;
      const sync = () => { toggle.setAttribute('aria-expanded', String(node.classList.contains('open'))); toggle.setAttribute('aria-label', `${node.classList.contains('open') ? '收起' : '展开'}子分类`); };
      sync(); toggle.addEventListener('click', sync);
      toggle.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); toggle.click(); }
      });
    });
    document.querySelectorAll('.tree-row.is-active,.nav a.is-active').forEach(link => link.setAttribute('aria-current','page'));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { document.querySelector('.sidebar.is-open')?.classList.remove('is-open'); document.querySelector('#side-toggle')?.setAttribute('aria-expanded','false'); }
    });
    const modal = document.querySelector('#modal-box');
    if (modal) new MutationObserver(() => {
      const title = modal.querySelector('h2');
      if (title) { title.id = 'workspace-modal-title'; modal.setAttribute('aria-labelledby', title.id); }
    }).observe(modal, {childList:true});
  });
})();
