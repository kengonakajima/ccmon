APP_NAME := ccMonMac
SRC_DIR := src
BUILD_DIR := build
APP_DIR := $(BUILD_DIR)/$(APP_NAME).app
MACOS_DIR := $(APP_DIR)/Contents/MacOS
CONTENTS_DIR := $(APP_DIR)/Contents

SRCS := $(SRC_DIR)/main.m \
        $(SRC_DIR)/AppDelegate.m \
        $(SRC_DIR)/FileWatcher.m \
        $(SRC_DIR)/PollingWatcher.m \
        $(SRC_DIR)/SoundPlayer.m \
        $(SRC_DIR)/Logger.m

CFLAGS := -fobjc-arc -Wall -Wextra -O2
LDFLAGS := -framework Cocoa -framework AVFoundation -framework AudioToolbox -framework CoreAudio -framework CoreServices

.PHONY: all build run clean

all: build

$(MACOS_DIR)/$(APP_NAME): $(SRCS)
	@mkdir -p $(MACOS_DIR)
	clang $(CFLAGS) -o $@ $^ $(LDFLAGS)

$(CONTENTS_DIR)/Info.plist: Info.plist
	@mkdir -p $(CONTENTS_DIR)
	cp $< $@

build: $(MACOS_DIR)/$(APP_NAME) $(CONTENTS_DIR)/Info.plist
	@echo "Built $(APP_DIR)"

run: build
	open $(APP_DIR)

clean:
	rm -rf $(BUILD_DIR)
