#ifndef FINTECH_H
#define FINTECH_H

long convert_currency(long amount_cents, long rate_num, long rate_den);
long calculate_fee(long amount_cents);
void allocate_amount(long total_cents, int parts, long *out);
long retry_charge(long *charges);
long refund(long *refunds);
long settle_batch(long *pages);
long append_entries(long *entries, int fail_at);
const char *route_payout(const char *currency);
int fraud_score(int timeout);
int next_settlement_date(int day);

#endif
