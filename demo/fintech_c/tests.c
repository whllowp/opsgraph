#include "fintech.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

#define CHECK(condition, name)                                                   \
    do {                                                                         \
        if (!(condition)) {                                                      \
            printf("FAIL %s\\n", name);                                         \
            failures++;                                                          \
        }                                                                        \
    } while (0)

static void test_convert(void) {
    CHECK(convert_currency(101, 1, 2) == 51, "convert_currency");
}

static void test_fee(void) {
    CHECK(calculate_fee(10000) == 100, "calculate_fee_10000");
    CHECK(calculate_fee(100000) == 500, "calculate_fee_100000");
}

static void test_allocate(void) {
    long out[3] = {0, 0, 0};
    allocate_amount(5, 3, out);
    CHECK(out[0] + out[1] + out[2] == 5, "allocate_amount_sum");
}

static void test_retry(void) {
    long charges = 0;
    retry_charge(&charges);
    retry_charge(&charges);
    CHECK(charges == 1, "retry_charge");
}

static void test_refund(void) {
    long refunds = 0;
    refund(&refunds);
    refund(&refunds);
    CHECK(refunds == 1, "refund");
}

static void test_settle(void) {
    long pages[3] = {1, 2, 3};
    CHECK(settle_batch(pages) == 6, "settle_batch");
}

static void test_append(void) {
    long entries[3] = {0, 0, 0};
    CHECK(append_entries(entries, 1) == 0, "append_entries");
}

static void test_route(void) {
    CHECK(strcmp(route_payout("EUR"), "eu-bank") == 0, "route_eur");
    CHECK(strcmp(route_payout("GBP"), "uk-bank") == 0, "route_gbp");
}

static void test_fraud(void) {
    CHECK(fraud_score(1) == 50, "fraud_score_timeout");
}

static void test_settlement_date(void) {
    CHECK(next_settlement_date(4) == 7, "next_settlement_date");
}

int main(int argc, char **argv) {
    if (argc > 1) {
        int index = atoi(argv[1]);
        switch (index) {
            case 1: test_convert(); break;
            case 2: test_fee(); break;
            case 3: test_allocate(); break;
            case 4: test_retry(); break;
            case 5: test_refund(); break;
            case 6: test_settle(); break;
            case 7: test_append(); break;
            case 8: test_route(); break;
            case 9: test_fraud(); break;
            case 10: test_settlement_date(); break;
            default: return 2;
        }
    } else {
        test_convert();
        test_fee();
        test_allocate();
        test_retry();
        test_refund();
        test_settle();
        test_append();
        test_route();
        test_fraud();
        test_settlement_date();
    }
    if (failures) {
        printf("%d failures\\n", failures);
        return 1;
    }
    printf("OK\\n");
    return 0;
}
