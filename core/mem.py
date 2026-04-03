"""
macOS Mach API Memory Reader/Writer
League of Legends process memory erişimi için düşük seviye araç.
Kuki projesinden (kuki_dumper.py) adapt edilmiştir.

Kullanım:
    sudo python3 -c "
    from core.mem import MemReader
    m = MemReader.from_process_name('LeagueofLegends')
    base = m.find_base()
    print(f'Base: 0x{base:X}')
    "
"""

import ctypes
import struct
import subprocess
from ctypes import (
    c_uint32, c_uint64, c_int, c_int32, c_void_p,
    POINTER, byref,
)
from typing import Optional, List

# ============================================================================
# Mach API Tanımları
# ============================================================================

libc = ctypes.CDLL(None)

mach_port_t = c_uint32
mach_vm_address_t = c_uint64
mach_vm_size_t = c_uint64
kern_return_t = c_int32
mach_msg_type_number_t = c_uint32

KERN_SUCCESS = 0
VM_PROT_READ = 0x01
VM_PROT_WRITE = 0x02
VM_PROT_EXECUTE = 0x04
VM_REGION_BASIC_INFO_64 = 9
VM_REGION_BASIC_INFO_COUNT_64 = 9
MH_MAGIC_64 = 0xFEEDFACF


class vm_region_basic_info_64(ctypes.Structure):
    _fields_ = [
        ('protection', c_int),
        ('max_protection', c_int),
        ('inheritance', c_uint32),
        ('shared', c_uint32),
        ('reserved', c_uint32),
        ('offset', c_uint64),
        ('behavior', c_int),
        ('user_wired_count', c_uint32),
    ]


def _mach_task_self():
    return c_uint32.in_dll(libc, 'mach_task_self_').value


# Mach fonksiyon imzaları
libc.task_for_pid.restype = kern_return_t
libc.task_for_pid.argtypes = [mach_port_t, c_int, POINTER(mach_port_t)]

libc.mach_vm_read.restype = kern_return_t
libc.mach_vm_read.argtypes = [
    mach_port_t, mach_vm_address_t, mach_vm_size_t,
    POINTER(c_void_p), POINTER(mach_vm_size_t)
]

libc.vm_deallocate.restype = kern_return_t
libc.vm_deallocate.argtypes = [mach_port_t, mach_vm_address_t, mach_vm_size_t]

libc.mach_vm_region.restype = kern_return_t
libc.mach_vm_region.argtypes = [
    mach_port_t, POINTER(mach_vm_address_t),
    POINTER(mach_vm_size_t), c_int, c_void_p,
    POINTER(mach_msg_type_number_t), POINTER(mach_port_t)
]

# mach_vm_write için
libc.mach_vm_write.restype = kern_return_t
libc.mach_vm_write.argtypes = [
    mach_port_t, mach_vm_address_t, c_void_p, mach_msg_type_number_t
]


# ============================================================================
# MemReader Sınıfı
# ============================================================================

class MemReader:
    """macOS Mach API ile hedef process'in belleğini oku/yaz."""

    def __init__(self, pid: int):
        self.pid = pid
        self.task = mach_port_t(0)
        kr = libc.task_for_pid(_mach_task_self(), c_int(pid), byref(self.task))
        if kr != KERN_SUCCESS:
            raise RuntimeError(
                f"task_for_pid failed (kr={kr}). sudo ile çalıştır."
            )
        self._base_cache: Optional[int] = None

    @classmethod
    def from_process_name(cls, name: str) -> 'MemReader':
        """Process adından PID bulup MemReader oluştur."""
        try:
            out = subprocess.check_output(['pgrep', '-x', name], text=True).strip()
        except subprocess.CalledProcessError:
            raise RuntimeError(f"Process bulunamadı: {name}")
        pids = out.split('\n')
        if not pids or not pids[0]:
            raise RuntimeError(f"Process bulunamadı: {name}")
        return cls(int(pids[0]))

    # ========================================================================
    # Temel Okuma/Yazma
    # ========================================================================

    def read(self, addr: int, size: int) -> Optional[bytes]:
        """Hedef process'ten ham bytes oku."""
        data_ptr = c_void_p(0)
        data_cnt = mach_vm_size_t(0)
        kr = libc.mach_vm_read(
            self.task, mach_vm_address_t(addr),
            mach_vm_size_t(size), byref(data_ptr), byref(data_cnt)
        )
        if kr != KERN_SUCCESS:
            return None
        try:
            result = ctypes.string_at(data_ptr.value, data_cnt.value)
        finally:
            libc.vm_deallocate(
                _mach_task_self(),
                mach_vm_address_t(data_ptr.value),
                mach_vm_size_t(data_cnt.value)
            )
        return result

    def write(self, addr: int, data: bytes) -> bool:
        """Hedef process'e ham bytes yaz."""
        buf = ctypes.create_string_buffer(data)
        kr = libc.mach_vm_write(
            self.task, mach_vm_address_t(addr),
            buf, mach_msg_type_number_t(len(data))
        )
        return kr == KERN_SUCCESS

    # ========================================================================
    # Tipli Okuma
    # ========================================================================

    def read_u32(self, addr: int) -> int:
        d = self.read(addr, 4)
        return struct.unpack('<I', d)[0] if d else 0

    def read_u64(self, addr: int) -> int:
        d = self.read(addr, 8)
        return struct.unpack('<Q', d)[0] if d else 0

    def read_float(self, addr: int) -> float:
        d = self.read(addr, 4)
        return struct.unpack('<f', d)[0] if d else 0.0

    def read_i32(self, addr: int) -> int:
        d = self.read(addr, 4)
        return struct.unpack('<i', d)[0] if d else 0

    def read_u8(self, addr: int) -> int:
        d = self.read(addr, 1)
        return d[0] if d else 0

    def read_string(self, addr: int, max_len: int = 64) -> str:
        d = self.read(addr, max_len)
        if not d:
            return ""
        null = d.find(b'\x00')
        if null >= 0:
            d = d[:null]
        return d.decode('utf-8', errors='replace')

    # ========================================================================
    # Tipli Yazma
    # ========================================================================

    def write_float(self, addr: int, val: float) -> bool:
        return self.write(addr, struct.pack('<f', val))

    def write_u32(self, addr: int, val: int) -> bool:
        return self.write(addr, struct.pack('<I', val))

    # ========================================================================
    # Memory Snapshot
    # ========================================================================

    def snapshot(self, addr: int, size: int) -> Optional[bytes]:
        """Bellek bölgesinin snapshot'ını al (diff için)."""
        return self.read(addr, size)

    # ========================================================================
    # Memory Region Tarama
    # ========================================================================

    def get_regions(self) -> List[dict]:
        """Tüm memory region'ları listele."""
        regions = []
        addr = mach_vm_address_t(0)
        size = mach_vm_size_t(0)
        info = vm_region_basic_info_64()
        info_count = mach_msg_type_number_t(VM_REGION_BASIC_INFO_COUNT_64)
        obj = mach_port_t(0)

        while True:
            info_count.value = VM_REGION_BASIC_INFO_COUNT_64
            kr = libc.mach_vm_region(
                self.task, byref(addr), byref(size),
                c_int(VM_REGION_BASIC_INFO_64),
                byref(info), byref(info_count), byref(obj)
            )
            if kr != KERN_SUCCESS:
                break
            regions.append({
                'start': addr.value,
                'end': addr.value + size.value,
                'size': size.value,
                'prot': info.protection,
            })
            addr.value += size.value
        return regions

    # ========================================================================
    # Mach-O Base Bulma
    # ========================================================================

    def find_base(self) -> int:
        """Ana Mach-O modülünün base adresini bul (FEEDFACF magic)."""
        if self._base_cache:
            return self._base_cache
        for r in self.get_regions():
            if not (r['prot'] & VM_PROT_READ):
                continue
            if r['size'] < 32:
                continue
            d = self.read(r['start'], 4)
            if d and struct.unpack('<I', d)[0] == MH_MAGIC_64:
                self._base_cache = r['start']
                return r['start']
        return 0


# ============================================================================
# CLI Test
# ============================================================================

if __name__ == '__main__':
    import sys
    proc = sys.argv[1] if len(sys.argv) > 1 else 'LeagueofLegends'
    print(f"[*] Process: {proc}")
    m = MemReader.from_process_name(proc)
    print(f"[+] PID: {m.pid}, Task: {m.task.value}")
    base = m.find_base()
    if base:
        print(f"[+] Mach-O Base: 0x{base:X}")
    else:
        print("[-] Base bulunamadı!")
