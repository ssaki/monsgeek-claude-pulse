"""Windows backend for monsgeek-claude-pulse: ctypes over setupapi.dll, hid.dll and kernel32.

Does for Windows what /sys/class/hidraw, ioctl, /proc and flock do for pulse.py on
Linux: finds the keyboard's vendor-config HID collection (vendor 0x3151, usage page
0xFFFF), exchanges 65-byte feature reports with it, and lists processes. The bytes
on the wire are identical to the Linux path. No driver and no admin rights are
needed: Windows only protects the keyboard and mouse collections, not vendor ones.

Standard library only. Written on Linux without a Windows machine; see
docs/windows.md for the verification checklist.

Run directly to probe:   python pulse_windows.py
"""
import ctypes
import os
import sys
import tempfile
from ctypes import wintypes

VENDOR_ID = 0x3151
VENDOR_USAGE_PAGE = 0xFFFF
REPORT_LEN = 65  # report id 0 + 64 data bytes, as on Linux

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
OPEN_EXISTING = 3
HIDP_STATUS_SUCCESS = 0x00110000
TH32CS_SNAPPROCESS = 0x2


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", wintypes.WCHAR * 1)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):  # 64 bytes
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17),
                ("NumberLinkCollectionNodes", wintypes.USHORT),
                ("NumberInputButtonCaps", wintypes.USHORT), ("NumberInputValueCaps", wintypes.USHORT),
                ("NumberInputDataIndices", wintypes.USHORT),
                ("NumberOutputButtonCaps", wintypes.USHORT), ("NumberOutputValueCaps", wintypes.USHORT),
                ("NumberOutputDataIndices", wintypes.USHORT),
                ("NumberFeatureButtonCaps", wintypes.USHORT), ("NumberFeatureValueCaps", wintypes.USHORT),
                ("NumberFeatureDataIndices", wintypes.USHORT)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def _proto(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


P = ctypes.POINTER
SetupDiGetClassDevsW = _proto(setupapi.SetupDiGetClassDevsW, wintypes.HANDLE,
                              P(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD)
SetupDiEnumDeviceInterfaces = _proto(setupapi.SetupDiEnumDeviceInterfaces, wintypes.BOOL,
                                     wintypes.HANDLE, ctypes.c_void_p, P(GUID), wintypes.DWORD,
                                     P(SP_DEVICE_INTERFACE_DATA))
SetupDiGetDeviceInterfaceDetailW = _proto(setupapi.SetupDiGetDeviceInterfaceDetailW, wintypes.BOOL,
                                          wintypes.HANDLE, P(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
                                          wintypes.DWORD, P(wintypes.DWORD), ctypes.c_void_p)
SetupDiDestroyDeviceInfoList = _proto(setupapi.SetupDiDestroyDeviceInfoList, wintypes.BOOL, wintypes.HANDLE)
HidD_GetHidGuid = _proto(hid.HidD_GetHidGuid, None, P(GUID))
HidD_GetAttributes = _proto(hid.HidD_GetAttributes, wintypes.BOOLEAN, wintypes.HANDLE, P(HIDD_ATTRIBUTES))
HidD_GetPreparsedData = _proto(hid.HidD_GetPreparsedData, wintypes.BOOLEAN, wintypes.HANDLE, P(ctypes.c_void_p))
HidD_FreePreparsedData = _proto(hid.HidD_FreePreparsedData, wintypes.BOOLEAN, ctypes.c_void_p)
HidP_GetCaps = _proto(hid.HidP_GetCaps, ctypes.c_long, ctypes.c_void_p, P(HIDP_CAPS))
HidD_SetFeature = _proto(hid.HidD_SetFeature, wintypes.BOOLEAN, wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG)
HidD_GetFeature = _proto(hid.HidD_GetFeature, wintypes.BOOLEAN, wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG)
CreateFileW = _proto(kernel32.CreateFileW, wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                     ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
CloseHandle = _proto(kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
CreateToolhelp32Snapshot = _proto(kernel32.CreateToolhelp32Snapshot, wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
Process32FirstW = _proto(kernel32.Process32FirstW, wintypes.BOOL, wintypes.HANDLE, P(PROCESSENTRY32W))
Process32NextW = _proto(kernel32.Process32NextW, wintypes.BOOL, wintypes.HANDLE, P(PROCESSENTRY32W))


def _err(what):
    return ctypes.WinError(ctypes.get_last_error(), what)


# ---------------------------------------------------------------- HID

def device_paths():
    """Every present HID interface path, e.g. \\\\?\\hid#vid_3151&pid_5030&mi_01#..."""
    guid = GUID()
    HidD_GetHidGuid(ctypes.byref(guid))
    hdev = SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE_VALUE or hdev is None:
        raise _err("SetupDiGetClassDevsW")
    try:
        index = 0
        while True:
            ifdata = SP_DEVICE_INTERFACE_DATA()
            ifdata.cbSize = ctypes.sizeof(ifdata)
            if not SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), index, ctypes.byref(ifdata)):
                return
            index += 1
            needed = wintypes.DWORD(0)
            SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifdata), None, 0, ctypes.byref(needed), None)
            buf = ctypes.create_string_buffer(max(needed.value, 8))
            detail = ctypes.cast(buf, P(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
            # cbSize is the fixed header size: 8 on 64-bit Python, 6 on 32-bit
            detail.contents.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifdata), detail, needed, None, None):
                yield ctypes.wstring_at(ctypes.addressof(buf) + SP_DEVICE_INTERFACE_DETAIL_DATA_W.DevicePath.offset)
    finally:
        SetupDiDestroyDeviceInfoList(hdev)


def _open(path, access):
    h = CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h is None:
        raise _err(f"CreateFileW({path})")
    return h


def describe(path):
    """{'path','vid','pid','usage_page','usage','feature_len'} or None.
    Opens with no access rights, which works even for the protected keyboard collections."""
    try:
        h = _open(path, 0)
    except OSError:
        return None
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(attrs)
        if not HidD_GetAttributes(h, ctypes.byref(attrs)):
            return None
        pp = ctypes.c_void_p()
        if not HidD_GetPreparsedData(h, ctypes.byref(pp)):
            return None
        try:
            caps = HIDP_CAPS()
            if HidP_GetCaps(pp, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
                return None
        finally:
            HidD_FreePreparsedData(pp)
        return {"path": path, "vid": attrs.VendorID, "pid": attrs.ProductID,
                "usage_page": caps.UsagePage, "usage": caps.Usage,
                "feature_len": caps.FeatureReportByteLength}
    finally:
        CloseHandle(h)


def candidates():
    """All collections of our vendor, cheap prefilter on the path then a real check."""
    for path in device_paths():
        if f"vid_{VENDOR_ID:04x}" not in path.lower():
            continue
        info = describe(path)
        if info and info["vid"] == VENDOR_ID:
            yield info


_FEATURE_LEN = {}


def find_device():
    """Path of the vendor-config collection, or None."""
    for info in candidates():
        if info["usage_page"] == VENDOR_USAGE_PAGE and info["feature_len"] >= 2:
            _FEATURE_LEN[info["path"]] = info["feature_len"]
            return info["path"]
    return None


def _feature_len(path):
    if path not in _FEATURE_LEN:
        info = describe(path)
        _FEATURE_LEN[path] = info["feature_len"] if info else REPORT_LEN
    return _FEATURE_LEN[path]


def set_feature(path, report):
    """Send one feature report; report[0] is the report id (0 for this keyboard)."""
    n = _feature_len(path)
    buf = ctypes.create_string_buffer(bytes(report)[:n].ljust(n, b"\0"), n)
    h = _open(path, GENERIC_READ | GENERIC_WRITE)
    try:
        if not HidD_SetFeature(h, buf, n):
            raise _err("HidD_SetFeature")
    finally:
        CloseHandle(h)


def get_feature(path, length=REPORT_LEN, report_id=0):
    """Read one feature report; returns report id + data, like Linux HIDIOCGFEATURE."""
    n = _feature_len(path)
    buf = ctypes.create_string_buffer(n)
    buf[0] = bytes([report_id])
    h = _open(path, GENERIC_READ | GENERIC_WRITE)
    try:
        if not HidD_GetFeature(h, buf, n):
            raise _err("HidD_GetFeature")
    finally:
        CloseHandle(h)
    return buf.raw[:length].ljust(length, b"\0")


# ---------------------------------------------------------------- processes, paths

def process_table():
    """{pid: (ppid, exe_name)} for every running process."""
    table = {}
    snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE or snap is None:
        return table
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = Process32NextW(snap, ctypes.byref(entry))
    finally:
        CloseHandle(snap)
    return table


def state_base():
    return os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()


def config_dir():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "monsgeek-claude-pulse")


# ---------------------------------------------------------------- probe

def main():
    found = list(candidates())
    if not found:
        print(f"no HID collection with vendor {VENDOR_ID:04x} found; is the keyboard plugged in (wired)?")
        print("all HID paths seen:")
        for p in device_paths():
            print("  ", p)
        return 1
    chosen = find_device()
    for info in found:
        mark = "->" if info["path"] == chosen else "  "
        print(f"{mark} pid={info['pid']:04x} usage_page={info['usage_page']:04x} usage={info['usage']:02x} "
              f"feature_len={info['feature_len']}  {info['path']}")
    if not chosen:
        print(f"none has usage page {VENDOR_USAGE_PAGE:04x}: the vendor collection was not matched")
        return 1
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pulse
    print("keyboard answers:", pulse.read_led(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
