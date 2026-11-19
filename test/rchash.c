/* rcheevos's own N64 hash of a ROM file, to compare with the bridge's. */
#include <stdio.h>
#include "rc_hash.h"
#include "rc_consoles.h"
int main(int argc, char **argv) {
    char hash[33];
    if (argc < 2 || !rc_hash_generate_from_file(hash, RC_CONSOLE_NINTENDO_64, argv[1])) return 1;
    puts(hash);
    return 0;
}
