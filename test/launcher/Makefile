N64_INST ?= /opt/libdragon
export N64_INST
export PATH := $(N64_INST)/bin:$(PATH)
BUILD_DIR = build
include $(N64_INST)/include/n64.mk
all: n64ra_test.z64
game_bin.h: ../game/game.bin
	python3 -c "d=open('../game/game.bin','rb').read();print('static const unsigned char game_bin[%d] __attribute__((aligned(8))) = {'%len(d)+','.join('0x%02x'%b for b in d)+'};')" > $@
$(BUILD_DIR)/launcher.o: game_bin.h ../../agent/agent_blob.h
n64ra_test.z64: N64_ROM_TITLE = "N64RA TEST"
$(BUILD_DIR)/n64ra_test.elf: $(BUILD_DIR)/launcher.o $(BUILD_DIR)/tramp.o
clean:
	rm -rf $(BUILD_DIR) *.z64 game_bin.h
