/* IS-Viewer debug output (emulators print it; so does the SC64 over
   USB). Debug builds only. */
#ifndef N64RA_ISV_H
#define N64RA_ISV_H
#include <stdint.h>
static void isv_wait(void)
{
    while (*(volatile uint32_t *)0xA4600010 & 3) {}
}
static void isv_puts(const char *s)
{
    uint32_t n = 0, w = 0;
    while (s[n]) {
        w = (w << 8) | (uint8_t)s[n];
        n++;
        if (!(n & 3)) {
            isv_wait();
            *(volatile uint32_t *)(0xB3FF0020 + n - 4) = w;
            w = 0;
        }
    }
    if (n & 3) {
        isv_wait();
        *(volatile uint32_t *)(0xB3FF0020 + (n & ~3u)) = w << (8 * (4 - (n & 3)));
    }
    isv_wait();
    *(volatile uint32_t *)0xB3FF0014 = n;
}
static void isv_hex(const char *label, uint32_t v)
{
    char b[64];
    int i = 0, k;
    while (*label && i < 40) b[i++] = *label++;
    for (k = 7; k >= 0; k--) b[i++] = "0123456789abcdef"[(v >> (4 * k)) & 15];
    b[i++] = '\n';
    b[i] = 0;
    isv_puts(b);
}
#endif
