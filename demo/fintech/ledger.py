class LedgerStore:
    def __init__(self):
        self.entries = []


def settle_batch(fetch_page, page_size=2):
    # BUG: a full page is treated as the last page, so later pages are skipped.
    page = 0
    settled = []
    while True:
        rows = fetch_page(page, page_size)
        if not rows:
            break
        settled.extend(rows)
        if len(rows) <= page_size:
            break
        page += 1
    return settled


def append_entries(store, entries):
    # BUG: a mid-batch failure leaves the ledger partially written.
    for entry in entries:
        if entry.get("fail"):
            raise ValueError("ledger write failed")
        store.entries.append(entry)
