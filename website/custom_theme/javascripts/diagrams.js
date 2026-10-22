/* Wrap before Material replaces each pre with its closed-shadow Mermaid host. */
document$.subscribe(() => {
  const english = document.documentElement.lang.startsWith('en');
  document.querySelectorAll('pre.mermaid, article div.mermaid').forEach(diagram => {
    if (diagram.closest('.rg-diagram-scroll')) return;
    const scroll = document.createElement('div');
    scroll.className = 'rg-diagram-scroll';
    // Authors can preserve readable label sizes on dense diagrams using a Mermaid comment.
    // Keep this on the outer wrapper: Material replaces the original pre with a shadow host.
    const width = diagram.textContent.match(/^\s*%% roboguide:min-width: (\d+)px\s*$/m);
    if (width) scroll.style.setProperty('--rg-diagram-min-width', `${Math.min(3000, Math.max(600, Number(width[1])))}px`);
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
