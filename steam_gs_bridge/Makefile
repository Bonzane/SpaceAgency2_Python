STEAM_SDK ?= ../steamworks_sdk_163/sdk
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_M),aarch64)
  STEAM_SDK_ARCH ?= linuxarm64
else
  STEAM_SDK_ARCH ?= linux64
endif

CXX ?= g++
CXXFLAGS := -O2 -std=c++17 -I$(STEAM_SDK)/public
LDFLAGS := -L$(STEAM_SDK)/redistributable_bin/$(STEAM_SDK_ARCH) -lsteam_api -Wl,-rpath,'$$ORIGIN/../steamworks_sdk_163/sdk/redistributable_bin/$(STEAM_SDK_ARCH)'

all: steam_gs_unlock

steam_gs_unlock: steam_gs_unlock.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f steam_gs_unlock
