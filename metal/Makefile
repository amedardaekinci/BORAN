CC = clang
CFLAGS = -dynamiclib -O2 -arch arm64 -fobjc-arc -fobjc-link-runtime
FRAMEWORKS = -framework Metal -framework QuartzCore -framework Foundation
LIBS = -lpthread

TARGET = boran_range_draw.dylib
SOURCE = boran_range_draw.m

all: $(TARGET)

$(TARGET): $(SOURCE)
	$(CC) $(CFLAGS) $(FRAMEWORKS) $(LIBS) -o $@ $<
	@echo "[+] Build OK: $(TARGET)"

clean:
	rm -f $(TARGET)

.PHONY: all clean
