CC = clang
CFLAGS = -dynamiclib -O2 -arch arm64 -fobjc-arc -fobjc-link-runtime
FRAMEWORKS = -framework Metal -framework QuartzCore -framework Foundation
LIBS = -lpthread

TARGET = boran_range_draw.dylib
SOURCE = boran_range_draw.m

TEST_TARGET = test_minimal.dylib
TEST_SOURCE = test_minimal.m

all: $(TARGET) $(TEST_TARGET)

$(TARGET): $(SOURCE)
	$(CC) $(CFLAGS) $(FRAMEWORKS) $(LIBS) -o $@ $<
	@echo "[+] Build OK: $(TARGET)"

$(TEST_TARGET): $(TEST_SOURCE)
	$(CC) -dynamiclib -O2 -arch arm64 -fobjc-arc -framework Foundation -o $@ $<
	@echo "[+] Build OK: $(TEST_TARGET)"

clean:
	rm -f $(TARGET) $(TEST_TARGET)

.PHONY: all clean
