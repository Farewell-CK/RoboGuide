/* Wrap before Material replaces each pre with its closed-shadow Mermaid host. */
document$.subscribe(() => {
  const english = document.documentElement.lang.startsWith('en');
  document.querySelectorAll('pre.mermaid, article div.mermaid').forEach(diagram => {
    if (diagram.closest('.rg-diagram-scroll')) return;
    const scroll = document.createElement('div');
    scroll.className = 'rg-diagram-scroll';
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', english ? 'Architecture diagram, scroll horizontally' : '架构图，可横向滚动');
    diagram.before(scroll);
    scroll.append(diagram);
    const hint = document.createElement('p');
    hint.className = 'rg-diagram-hint';
    hint.textContent = english ? 'Scroll the diagram horizontally.' : '图示可左右滚动查看。';
    scroll.after(hint);
  });
});
