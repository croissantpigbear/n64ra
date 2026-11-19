# n64ra test game: bare metal, linked at 80000400h like a retail game.
N64_INST ?= /opt/libdragon
CC = $(N64_INST)/bin/mips64-elf-gcc
OBJCOPY = $(N64_INST)/bin/mips64-elf-objcopy
CFLAGS = -march=vr4300 -mabi=32 -EB -G0 -mno-abicalls -fno-pic -msoft-float -O2 -ffreestanding -nostdlib -static \
         -Wl,--build-id=none
all: game.bin
game.elf: game.S game.c game.ld ../../agent/isv.h
	$(CC) $(CFLAGS) -T game.ld -o $@ game.S game.c
game.bin: game.elf
	$(OBJCOPY) -O binary $< $@
clean:
	rm -f game.elf game.bin
