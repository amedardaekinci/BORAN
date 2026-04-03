#!/usr/bin/env python3
"""
BORAN Inject — macOS ARM64 dylib injection via pthread_create_from_mach_thread.

Kuki inject.py v2.0'dan adapt edilmiştir.

Yöntem (vocaeq):
  bare mach thread → pthread_create_from_mach_thread(stage2, path)
  stage2 (real pthread) → dlopen(path, RTLD_NOW) → constructor çalışır

Kullanım:
  sudo python3 loader/inject.py metal/boran_range_draw.dylib
"""

import os
import struct
import ctypes
import time
import subprocess
import sys
from ctypes import c_uint32, c_uint64, c_int32, c_void_p, POINTER, byref

# ============================================================================
# Mach API
# ============================================================================

libc = ctypes.CDLL(None)

libc.mach_vm_allocate.restype = c_int32
libc.mach_vm_allocate.argtypes = [c_uint32, POINTER(c_uint64), c_uint64, c_int32]
libc.mach_vm_write.restype = c_int32
libc.mach_vm_write.argtypes = [c_uint32, c_uint64, c_void_p, c_uint32]
libc.mach_vm_protect.restype = c_int32
libc.mach_vm_protect.argtypes = [c_uint32, c_uint64, c_uint64, c_int32, c_int32]
libc.mach_vm_deallocate.restype = c_int32
libc.mach_vm_deallocate.argtypes = [c_uint32, c_uint64, c_uint64]
libc.mach_vm_read.restype = c_int32
libc.mach_vm_read.argtypes = [c_uint32, c_uint64, c_uint64,
                               POINTER(c_void_p), POINTER(c_uint32)]
libc.thread_create_running.restype = c_int32
libc.thread_create_running.argtypes = [c_uint32, c_int32, c_void_p, c_uint32, POINTER(c_uint32)]
libc.thread_terminate.restype = c_int32
libc.thread_terminate.argtypes = [c_uint32]
libc.thread_get_state.restype = c_int32
libc.thread_get_state.argtypes = [c_uint32, c_int32, c_void_p, POINTER(c_uint32)]
libc.task_for_pid.restype = c_int32
libc.task_for_pid.argtypes = [c_uint32, c_int32, POINTER(c_uint32)]
libc.mach_task_self.restype = c_uint32

libc.dlsym.restype = c_void_p
libc.dlsym.argtypes = [c_void_p, ctypes.c_char_p]

RTLD_DEFAULT = c_void_p(-2)
ARM_THREAD_STATE64 = 6
ARM_THREAD_STATE64_COUNT = 68

PTHREAD_CREATE_FROM_MACH = libc.dlsym(RTLD_DEFAULT, b"pthread_create_from_mach_thread")
DLOPEN_ADDR = ctypes.cast(libc.dlopen, c_void_p).value
PTHREAD_EXIT_ADDR = ctypes.cast(libc.pthread_exit, c_void_p).value

# Data page layout
STAGE1_RESULT_OFF = 0x00
STAGE2_DONE_OFF   = 0x08
DLOPEN_HANDLE_OFF = 0x10
PATH_OFF          = 0x18
PTHREAD_T_OFF     = 0x100


# ============================================================================
# ARM64 Shellcode Helpers
# ============================================================================

def _movx(reg, val):
    """MOVZ + 3x MOVK → 64-bit immediate yükle."""
    r = reg & 0x1F
    return [
        0xD2800000 | r | (((val >> 0) & 0xFFFF) << 5),
        0xF2A00000 | r | (((val >> 16) & 0xFFFF) << 5),
        0xF2C00000 | r | (((val >> 32) & 0xFFFF) << 5),
        0xF2E00000 | r | (((val >> 48) & 0xFFFF) << 5),
    ]

def _pack(insns):
    return b''.join(struct.pack('<I', i) for i in insns)


# ============================================================================
# Helpers
# ============================================================================

def _read_remote(task, addr, size):
    data_ptr = c_void_p(0)
    data_cnt = c_uint32(0)
    kr = libc.mach_vm_read(task, addr, size, byref(data_ptr), byref(data_cnt))
    if kr != 0 or data_cnt.value < size:
        return None
    return ctypes.string_at(data_ptr.value, size)

def _dealloc_all(task, stack_addr, stack_size, data_addr, code_addr):
    if stack_addr:
        libc.mach_vm_deallocate(task, stack_addr, stack_size)
    if data_addr:
        libc.mach_vm_deallocate(task, data_addr, 0x1000)
    if code_addr:
        libc.mach_vm_deallocate(task, code_addr, 0x1000)


# ============================================================================
# Public API
# ============================================================================

def find_league():
    """League process'i bul. Döndürür: (pid, task_port)"""
    result = subprocess.run(['pgrep', '-x', 'LeagueofLegends'],
                           capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("LeagueofLegends bulunamadı!")
    pid = int(result.stdout.strip().split('\n')[0])

    task = c_uint32(0)
    kr = libc.task_for_pid(libc.mach_task_self(), pid, byref(task))
    if kr != 0:
        raise RuntimeError(f"task_for_pid failed: kr={kr} (sudo ile çalıştır)")
    return pid, task.value


def inject_dylib(task, dylib_path, timeout=10.0, verbose=True):
    """
    Dylib'i hedef process'e inject et.

    Args:
        task: Mach task port
        dylib_path: .dylib dosyasının absolute path'i
        timeout: Max bekleme süresi (sn)
        verbose: Progress yazdır

    Returns:
        (success: bool, info: str)
    """
    if not PTHREAD_CREATE_FROM_MACH:
        return False, "pthread_create_from_mach_thread bulunamadı"

    dylib_path = os.path.abspath(dylib_path)
    if not os.path.exists(dylib_path):
        return False, f"dylib bulunamadı: {dylib_path}"

    def log(msg):
        if verbose:
            print(f"  [inject] {msg}")

    log(f"Dylib: {dylib_path}")

    STACK_SIZE = 0x20000
    stack_addr = data_addr = code_addr = 0

    try:
        # Allocate: stack, data page, code page
        stack_a = c_uint64(0)
        kr = libc.mach_vm_allocate(task, byref(stack_a), STACK_SIZE, 1)
        if kr != 0:
            return False, f"stack alloc failed (kr={kr})"
        stack_addr = stack_a.value

        data_a = c_uint64(0)
        kr = libc.mach_vm_allocate(task, byref(data_a), 0x1000, 1)
        if kr != 0:
            return False, f"data alloc failed (kr={kr})"
        data_addr = data_a.value
        DATA = data_addr

        code_a = c_uint64(0)
        kr = libc.mach_vm_allocate(task, byref(code_a), 0x1000, 1)
        if kr != 0:
            return False, f"code alloc failed (kr={kr})"
        code_addr = code_a.value
        CODE = code_addr
        STAGE1 = CODE
        STAGE2 = CODE + 0x100

        # Data page yaz
        PATH_ADDR = DATA + PATH_OFF
        PTHREAD_T = DATA + PTHREAD_T_OFF
        path_bytes = dylib_path.encode('utf-8') + b'\x00'
        page_data = bytearray(0x1000)
        page_data[PATH_OFF:PATH_OFF + len(path_bytes)] = path_bytes

        kr = libc.mach_vm_write(task, DATA, bytes(page_data), len(page_data))
        if kr != 0:
            return False, f"data write failed (kr={kr})"

        # Stage1 shellcode: pthread_create_from_mach_thread
        s1 = [0xA9BF7BFD, 0x910003FD]          # STP X29,X30,[SP,-16]! ; MOV X29,SP
        s1 += _movx(0, PTHREAD_T)               # X0 = &pthread_t
        s1.append(0xD2800001)                    # X1 = NULL (attr)
        s1 += _movx(2, STAGE2)                  # X2 = stage2 entry
        s1 += _movx(3, PATH_ADDR)              # X3 = dylib path addr
        s1 += _movx(9, PTHREAD_CREATE_FROM_MACH) # X9 = function addr
        s1.append(0xD63F0120)                    # BLR X9
        s1 += _movx(10, DATA + STAGE1_RESULT_OFF)
        s1.append(0xF9000140)                    # STR X0, [X10]
        s1.append(0x14000000)                    # B . (spin)

        # Stage2 shellcode: dlopen
        s2 = []
        s2.append(0xAA0003E8)                    # MOV X8, X0 (save path)
        s2.append(0xAA0803E0)                    # MOV X0, X8 (path → X0)
        s2.append(0xD2800041)                    # MOV X1, #2 (RTLD_NOW)
        s2 += _movx(9, DLOPEN_ADDR)
        s2.append(0xD63F0120)                    # BLR X9 (dlopen)
        s2 += _movx(10, DATA)
        s2.append(0xF9000940)                    # STR X0, [X10, #0x10]
        s2.append(0xD280002B)                    # MOV X11, #1
        s2.append(0xF900054B)                    # STR X11, [X10, #0x08]
        s2.append(0xD2800000)                    # MOV X0, #0
        s2 += _movx(9, PTHREAD_EXIT_ADDR)
        s2.append(0xD63F0120)                    # BLR X9 (pthread_exit)
        s2.append(0x14000000)                    # B . (safety)

        # Code page yaz
        code_page = bytearray(0x1000)
        s1_bytes, s2_bytes = _pack(s1), _pack(s2)
        code_page[0:len(s1_bytes)] = s1_bytes
        code_page[0x100:0x100 + len(s2_bytes)] = s2_bytes

        kr = libc.mach_vm_write(task, CODE, bytes(code_page), len(code_page))
        if kr != 0:
            return False, f"code write failed (kr={kr})"

        kr = libc.mach_vm_protect(task, CODE, 0x1000, 0, 1 | 4)  # R+X
        if kr != 0:
            return False, f"code protect failed (kr={kr})"

        # SP alignment + thread state
        SP = (stack_addr + STACK_SIZE - 0x20) & ~0xF
        state = (c_uint64 * 34)()
        state[29] = SP        # FP
        state[30] = STAGE1    # LR
        state[31] = SP        # SP
        state[32] = STAGE1    # PC

        th = c_uint32(0)
        kr = libc.thread_create_running(task, ARM_THREAD_STATE64,
                                        state, ARM_THREAD_STATE64_COUNT, byref(th))
        if kr != 0:
            return False, f"thread_create_running failed (kr={kr})"

        log(f"Thread oluşturuldu (th={th.value})")
        log(f"  CODE=0x{CODE:X}, STAGE1=0x{STAGE1:X}, STAGE2=0x{STAGE2:X}")
        log(f"  DATA=0x{DATA:X}, STACK SP=0x{SP:X}")
        log(f"  s1={len(s1_bytes)} bytes, s2={len(s2_bytes)} bytes")

        # İlk kısa bekleme — thread'e başlama şansı ver
        time.sleep(0.1)

        # Thread state kontrol et (crash mi?)
        libc.thread_get_state.restype = c_int32
        libc.thread_get_state.argtypes = [c_uint32, c_int32, c_void_p, POINTER(c_uint32)]
        rs = (c_uint64 * 34)()
        sc = c_uint32(ARM_THREAD_STATE64_COUNT)
        kr_ts = libc.thread_get_state(th, ARM_THREAD_STATE64, rs, byref(sc))
        if kr_ts == 0:
            pc = rs[32]
            sp_now = rs[31]
            log(f"  Thread state: PC=0x{pc:X}, SP=0x{sp_now:X}")
            spin_addr = STAGE1 + len(s1_bytes) - 4
            if pc == spin_addr:
                log(f"  Stage1 spin'de bekliyor ✓")
            elif STAGE1 <= pc < STAGE1 + len(s1_bytes):
                log(f"  Stage1 çalışıyor (offset +{pc - STAGE1})")
            else:
                log(f"  PC beklenmeyen adreste! (STAGE1=0x{STAGE1:X})")
        else:
            log(f"  thread_get_state failed: kr={kr_ts} (thread öldü mü?)")

        # Polling
        POLL_INTERVAL = 0.2
        max_polls = int(timeout / POLL_INTERVAL)
        dlopen_handle = 0

        for i in range(max_polls):
            time.sleep(POLL_INTERVAL)

            # Data page oku
            data_ptr = c_void_p(0)
            data_cnt = c_uint32(0)
            kr_rd = libc.mach_vm_read(task, c_uint64(DATA), c_uint64(0x18),
                                       byref(data_ptr), byref(data_cnt))
            if kr_rd != 0:
                log(f"  Poll {i+1}: mach_vm_read failed kr={kr_rd}")
                # Process hala yaşıyor mu kontrol et
                alive = subprocess.run(['kill', '-0', str(find_pid())],
                                      capture_output=True).returncode == 0 \
                        if 'find_pid' in dir() else True
                # Thread state kontrol
                sc2 = c_uint32(ARM_THREAD_STATE64_COUNT)
                kr2 = libc.thread_get_state(th, ARM_THREAD_STATE64, rs, byref(sc2))
                if kr2 != 0:
                    log(f"  Thread da öldü (kr={kr2})")
                    return False, f"process crash — mach_vm_read kr={kr_rd}, thread_get_state kr={kr2}"
                log(f"  Thread hala yaşıyor, PC=0x{rs[32]:X}")
                continue

            page = ctypes.string_at(data_ptr.value, min(data_cnt.value, 0x18))

            stage1_res = struct.unpack('<Q', page[0:8])[0]
            done_flag = struct.unpack('<Q', page[8:16])[0]
            dlopen_handle = struct.unpack('<Q', page[16:24])[0]

            if i == 0:
                log(f"  Poll 1: stage1_result={stage1_res}, done={done_flag}, handle=0x{dlopen_handle:X}")

            if done_flag != 0:
                break
        else:
            # Timeout — thread state kontrol et
            sc3 = c_uint32(ARM_THREAD_STATE64_COUNT)
            libc.thread_get_state(th, ARM_THREAD_STATE64, rs, byref(sc3))
            log(f"  Timeout! Final PC=0x{rs[32]:X}, stage1_result={stage1_res}")
            libc.thread_terminate(th)
            return False, f"timeout ({timeout}s), PC=0x{rs[32]:X}"

        libc.thread_terminate(th)

        if dlopen_handle == 0:
            return False, f"dlopen NULL döndürdü (stage1_result={stage1_res})"

        elapsed = (i + 1) * POLL_INTERVAL
        log(f"Injection OK! ({elapsed:.1f}s, handle=0x{dlopen_handle:X})")
        return True, f"handle=0x{dlopen_handle:X}"

    finally:
        _dealloc_all(task, stack_addr, STACK_SIZE, data_addr, code_addr)


def check_syslog(tag="[BORAN-MTL]", seconds=10):
    """Syslog'da inject edilen dylib mesajlarını kontrol et."""
    try:
        result = subprocess.run(
            ['log', 'show', '--last', f'{seconds}s',
             '--predicate', f'eventMessage contains "{tag}"',
             '--style', 'compact'],
            capture_output=True, text=True, timeout=8
        )
        lines = [l for l in result.stdout.strip().split('\n') if tag in l]
        return lines
    except Exception:
        return []


# ============================================================================
# CLI
# ============================================================================

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f"Kullanım: sudo python3 {sys.argv[0]} <dylib_path>")
        print(f"Örnek:    sudo python3 {sys.argv[0]} metal/boran_range_draw.dylib")
        sys.exit(1)

    dylib = sys.argv[1]

    print("[*] League of Legends aranıyor...")
    pid, task = find_league()
    print(f"[+] PID: {pid}")

    print(f"[*] Dylib inject ediliyor: {dylib}")
    success, info = inject_dylib(task, dylib)

    if success:
        print(f"[+] BAŞARILI: {info}")
        time.sleep(2)
        logs = check_syslog()
        if logs:
            print(f"[*] Syslog ({len(logs)} mesaj):")
            for l in logs[-10:]:
                print(f"    {l}")
        else:
            print("[*] Syslog mesajı henüz yok (birkaç saniye bekle)")
    else:
        print(f"[-] BAŞARISIZ: {info}")
        sys.exit(1)
