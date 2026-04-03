#!/usr/bin/env python3
"""
Injection diagnostik — adres doğrulama ve basit test.
"""
import ctypes
import subprocess
import struct
from ctypes import c_uint32, c_uint64, c_void_p, c_int32, POINTER, byref

libc = ctypes.CDLL(None)

libc.task_for_pid.restype = c_int32
libc.task_for_pid.argtypes = [c_uint32, c_int32, POINTER(c_uint32)]
libc.mach_task_self.restype = c_uint32

libc.dlsym.restype = c_void_p
libc.dlsym.argtypes = [c_void_p, ctypes.c_char_p]

RTLD_DEFAULT = c_void_p(-2)

print("=" * 60)
print("  Injection Diagnostik")
print("=" * 60)

# 1. Process kontrol
result = subprocess.run(['pgrep', '-x', 'LeagueofLegends'],
                       capture_output=True, text=True)
if result.returncode != 0:
    print("[-] LeagueofLegends ÇALIŞMIYOR!")
    print("    Oyunu tekrar aç ve Practice Tool'a gir.")
    exit(1)

pid = int(result.stdout.strip().split('\n')[0])
print(f"[+] PID: {pid}")

# 2. task_for_pid
task = c_uint32(0)
kr = libc.task_for_pid(libc.mach_task_self(), pid, byref(task))
print(f"[{'+'if kr==0 else '-'}] task_for_pid: kr={kr}")
if kr != 0:
    print("    sudo ile çalıştır!")
    exit(1)

# 3. Resolved adresler
pthread_create_from_mach = libc.dlsym(RTLD_DEFAULT, b"pthread_create_from_mach_thread")
dlopen_addr = ctypes.cast(libc.dlopen, c_void_p).value
pthread_exit_addr = ctypes.cast(libc.pthread_exit, c_void_p).value

print(f"\n[*] Resolved Addresses:")
print(f"    pthread_create_from_mach_thread: {pthread_create_from_mach}")
if pthread_create_from_mach:
    print(f"    → 0x{pthread_create_from_mach:X}")
else:
    print("    → NULL! Bu fonksiyon bu macOS versiyonunda yok olabilir!")

print(f"    dlopen:       0x{dlopen_addr:X}")
print(f"    pthread_exit: 0x{pthread_exit_addr:X}")

# 4. Basit memory allocation testi
libc.mach_vm_allocate.restype = c_int32
libc.mach_vm_allocate.argtypes = [c_uint32, POINTER(c_uint64), c_uint64, c_int32]
libc.mach_vm_deallocate.restype = c_int32
libc.mach_vm_deallocate.argtypes = [c_uint32, c_uint64, c_uint64]
libc.mach_vm_write.restype = c_int32
libc.mach_vm_write.argtypes = [c_uint32, c_uint64, c_void_p, c_uint32]
libc.mach_vm_read.restype = c_int32
libc.mach_vm_read.argtypes = [c_uint32, c_uint64, c_uint64,
                               POINTER(c_void_p), POINTER(c_uint32)]

addr = c_uint64(0)
kr = libc.mach_vm_allocate(task.value, byref(addr), 0x1000, 1)
print(f"\n[*] Remote Memory Test:")
print(f"    mach_vm_allocate: kr={kr}, addr=0x{addr.value:X}")

if kr == 0:
    # Write test
    test_data = b"BORAN_TEST_1234"
    kr2 = libc.mach_vm_write(task.value, addr.value, test_data, len(test_data))
    print(f"    mach_vm_write: kr={kr2}")

    # Read test
    data_ptr = c_void_p(0)
    data_cnt = c_uint32(0)
    kr3 = libc.mach_vm_read(task.value, addr.value, 16,
                             byref(data_ptr), byref(data_cnt))
    print(f"    mach_vm_read: kr={kr3}, cnt={data_cnt.value}")

    if kr3 == 0 and data_cnt.value >= 15:
        read_back = ctypes.string_at(data_ptr.value, data_cnt.value)
        match = read_back[:15] == test_data
        print(f"    Read-back match: {match}")

    libc.mach_vm_deallocate(task.value, addr.value, 0x1000)
    print(f"    Cleanup OK")

# 5. macOS version
import platform
print(f"\n[*] macOS: {platform.mac_ver()[0]}")
print(f"    Python: {platform.python_version()}")
print(f"    Arch: {platform.machine()}")

# 6. Dylib kontrol
import os
dylib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'metal', 'boran_range_draw.dylib')
if os.path.exists(dylib):
    size = os.path.getsize(dylib)
    print(f"\n[+] Dylib: {dylib}")
    print(f"    Size: {size} bytes")
    # Verify it's ARM64
    with open(dylib, 'rb') as f:
        magic = struct.unpack('<I', f.read(4))[0]
        if magic == 0xFEEDFACF:
            print(f"    Format: Mach-O 64-bit ✓")
        elif magic == 0xCAFEBABE or magic == 0xBEBAFECA:
            print(f"    Format: Universal binary")
        else:
            print(f"    Format: UNKNOWN (magic=0x{magic:X})")
else:
    print(f"\n[-] Dylib bulunamadı: {dylib}")

print(f"\n{'='*60}")
if pthread_create_from_mach:
    print("  Tüm kontroller OK — injection yapılabilir")
else:
    print("  PROBLEM: pthread_create_from_mach_thread NULL!")
print(f"{'='*60}")
