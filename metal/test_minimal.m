/*
 * test_minimal.m — Minimal test dylib, sadece log yazar.
 * Injection mekanizmasının çalışıp çalışmadığını test eder.
 *
 * Build:
 *   clang -dynamiclib -O2 -arch arm64 -fobjc-arc \
 *         -framework Foundation -o test_minimal.dylib test_minimal.m
 */

#import <Foundation/Foundation.h>
#include <syslog.h>
#include <mach-o/dyld.h>

__attribute__((constructor))
static void test_init(void) {
    syslog(LOG_NOTICE, "[BORAN-TEST] === MINIMAL DYLIB LOADED ===");
    syslog(LOG_NOTICE, "[BORAN-TEST] dyld image count: %u", _dyld_image_count());
    syslog(LOG_NOTICE, "[BORAN-TEST] Constructor ran successfully!");
}
