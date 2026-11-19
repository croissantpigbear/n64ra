# n64ra agent: agent.bin is the blob the menu stages, agent_blob.h the
# same bytes as a C array for the menu build.
N64_INST ?= /opt/libdragon
CC = $(N64_INST)/bin/mips64-elf-gcc
OBJCOPY = $(N64_INST)/bin/mips64-elf-objcopy
CFLAGS = -march=vr4300 -mtune=vr4300 -mabi=32 -EB -G0 -mno-abicalls -fno-pic -msoft-float -mno-gpopt \
         -Os -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns -fno-stack-protector \
         -fno-asynchronous-unwind-tables -Wall -Wextra
DEBUG ?=
ifneq ($(DEBUG),)
CFLAGS += -DN64RA_DEBUG
endif
all: agent.bin agent_blob.h
agent.elf: entry.S agent.c n64ra.h agent.ld
	$(CC) $(CFLAGS) -nostdlib -static -Wl,--build-id=none -T agent.ld -o $@ entry.S agent.c
agent.bin: agent.elf
	$(OBJCOPY) -O binary $< $@
	@echo "agent: $$(stat -c %s $@) bytes"
agent_blob.h: agent.bin
	python3 -c "import sys;d=open('agent.bin','rb').read();print('/* generated from agent.bin */\nstatic const unsigned char n64ra_blob[%d] __attribute__((aligned(16))) = {\n'%len(d)+',\n'.join(','.join('0x%02x'%b for b in d[i:i+16]) for i in range(0,len(d),16))+'\n};')" > $@
clean:
	rm -f agent.elf agent.bin agent_blob.h
