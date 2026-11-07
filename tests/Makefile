CC ?= gcc
CFLAGS ?= -O2 -Wall
LDFLAGS ?= -lwayland-client

all: wlr_ptr

wlr_ptr: wlr_ptr.c wlr-virtual-pointer-protocol.c wlr-virtual-pointer-client-protocol.h
	$(CC) $(CFLAGS) wlr_ptr.c wlr-virtual-pointer-protocol.c $(LDFLAGS) -o $@

clean:
	rm -f wlr_ptr

.PHONY: all clean
