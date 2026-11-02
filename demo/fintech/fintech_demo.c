#include <stdio.h>
volatile double total = 0;
__attribute__((noinline)) void retry_charge(double amount) {
    for (int i = 0; i < 100000; i++) {
        total += amount * 0.01;
    }
}
int main(void) {
    while (1) { retry_charge(1000.0); }
    return 0;
}
