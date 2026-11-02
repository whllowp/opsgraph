def fraud_score(provider, txn):
    # BUG: provider timeout propagates and blocks every payment in the request.
    return provider.score(txn)
