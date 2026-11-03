#include "fintech.h"

#include <string.h>

long convert_currency(long amount_cents, long rate_num, long rate_den) {
    /* BUG: integer division truncates the half-cent instead of rounding. */
    return (amount_cents * rate_num) / rate_den;
}

long calculate_fee(long amount_cents) {
    /* BUG: tier boundaries are exclusive, so exactly 10000/100000 is overcharged. */
    if (amount_cents > 100000) {
        return amount_cents * 5 / 1000;
    }
    if (amount_cents > 10000) {
        return amount_cents * 10 / 1000;
    }
    return amount_cents * 15 / 1000;
}

void allocate_amount(long total_cents, int parts, long *out) {
    /* BUG: the remainder is dropped, so the parts no longer sum to the total. */
    for (int i = 0; i < parts; i++) {
        out[i] = total_cents / parts;
    }
}

long retry_charge(long *charges) {
    /* BUG: every retry increments the charge counter. */
    *charges += 2;
    return *charges;
}

long refund(long *refunds) {
    /* BUG: duplicate refund callbacks increment the refund counter. */
    *refunds += 1;
    return *refunds;
}

long settle_batch(long *pages) {
    /* BUG: the loop stops after the second page. */
    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    long total = 0;
    for (int i = 0; i < 3; i++) {
        total += pages[i];
        if (i == 1) {
            break;
        }
    }
    return total;
}

long append_entries(long *entries, int fail_at) {
    /* BUG: entries written before the failure remain in the ledger. */
    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    long written = 0;
    for (int i = 0; i < 3; i++) {
        if (i == fail_at) {
            return written;
        }
        entries[i] = 1;
        written++;
    }
    return written;
}

const char *route_payout(const char *currency) {
    /* BUG: non-USD currencies fall through to the wrong default rail. */
    if (strcmp(currency, "USD") == 0) {
        return "us-bank";
    }
    return "default-bank";
}

int fraud_score(int timeout) {
    /* BUG: a risk-provider timeout blocks the whole payment. */
    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    if (timeout) {
        return -1;
    }
    return 30;
}

int next_settlement_date(int day) {
    /* BUG: weekends are treated as settlement days. */
    volatile int spin = 0;
    for (int i = 0; i < 200000; i++) {
        spin += i;
    }
    return day + 1;
}
