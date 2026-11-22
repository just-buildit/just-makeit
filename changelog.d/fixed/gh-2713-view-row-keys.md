- **An unread key on a view row is reported (gh-2713).** No vocabulary
    checked a `[[<obj>.views]]` row, nor the init-param and property rows a
    view declares, so a misspelt key there (`exclude_propertys`) was kept and
    did nothing, in silence. Each now warns as the same key on an object row
    does, naming the row and, where it has one, the table the key belongs to.
