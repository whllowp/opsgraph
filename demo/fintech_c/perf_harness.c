#include "fintech.h"

#include <stdlib.h>
#include <string.h>

volatile long sink = 0;

int main(int argc, char **argv) {
    if (argc < 2) {
        return 2;
    }
    int index = atoi(argv[1]);
    long slots[3] = {1, 2, 3};
    long counter = 0;
    for (;;) {
        switch (index) {
            case 1: sink += convert_currency(101, 1, 2); break;
            case 2: sink += calculate_fee(10000); break;
            case 3: allocate_amount(5, 3, slots); sink += slots[0]; break;
            case 4: sink += retry_charge(&counter); break;
            case 5: sink += refund(&counter); break;
            case 6: sink += settle_batch(slots); break;
            case 7: sink += append_entries(slots, 1); break;
            case 8: sink += route_payout("EUR")[0]; break;
            case 9: sink += fraud_score(1); break;
            case 10:
                for (int j = 0; j < 1000; j++) {
                    sink += next_settlement_date(4);
                }
                break;
            default: return 2;
        }
    }
}
