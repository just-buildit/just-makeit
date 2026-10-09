- **`jm remove` refuses an object, module or function an app is built
    from, naming each app's `jm remove app <name>`, and writes nothing**
    (gh-2075). It removed the component and left `[app]` naming it, so
    `jm status --check` and `jm apply` both failed ("function 'f' not found
    in module 'mod'") on a tree no command could reconcile. An app is
    author-facing code, so removing its source does not silently delete it:
    remove the app first, then the component.
