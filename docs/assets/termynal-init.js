/*
 * termynal-init.js
 *
 * Re-initialise termynal widgets on every page load, including client-side
 * navigations driven by MkDocs Material's navigation.instant feature.
 * Material exposes a document$ RxJS observable that fires whenever the page
 * content is replaced; we subscribe to it so every newly-rendered termynal
 * block gets animated even if DOMContentLoaded has already fired.
 */
(function () {
    function initTermynal() {
        document
            .querySelectorAll('[data-termynal]:not([data-ty-inited])')
            .forEach(function (node) {
                node.setAttribute('data-ty-inited', 'true');
                new Termynal(node);
            });
    }

    if (typeof document$ !== 'undefined') {
        // MkDocs Material navigation.instant: subscribe to page changes.
        document$.subscribe(initTermynal);
    } else {
        // Fallback for standard (non-instant) navigation.
        document.addEventListener('DOMContentLoaded', initTermynal);
    }
}());
