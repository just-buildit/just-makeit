/* Click-to-zoom for mermaid diagrams.
 *
 * Mermaid runs asynchronously, so we use event delegation on the document
 * rather than binding to .mermaid > svg at load time. Clicking the diagram
 * opens a fullscreen overlay containing a clone of the SVG; click anywhere
 * (or press Esc) to close. */

(function () {
  function close() {
    const overlay = document.getElementById("jm-mermaid-overlay");
    if (overlay) overlay.remove();
  }

  document.addEventListener("click", function (e) {
    const svg = e.target.closest(".mermaid > svg");
    if (!svg) return;
    e.preventDefault();
    close();
    const overlay = document.createElement("div");
    overlay.id = "jm-mermaid-overlay";
    const clone = svg.cloneNode(true);
    clone.removeAttribute("style");
    overlay.appendChild(clone);
    overlay.addEventListener("click", close);
    document.body.appendChild(overlay);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });
})();
