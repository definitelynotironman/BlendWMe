"""Where your API key lives.

The key is never saved in Blender preferences or .blend files. Choose one:

- KEYCHAIN (recommended): your operating system's password store.
  Windows Credential Manager, macOS Keychain, or Linux Secret Service.
  It is encrypted for your user account and survives restarts.
- SESSION: kept in memory only. You paste it again after restarting Blender.
- ENV: read from the NEBIUS_API_KEY environment variable.

The AI's own code cannot import this module or read the environment
(see executor.py), so the model can never print your key into a chat.
"""

import os
import shutil
import subprocess
import sys

SERVICE = "BlendWMe-Nebius"
_memory = {"key": "", "loaded": False}


# ----------------------------------------------------------------------
# Operating-system password stores
# ----------------------------------------------------------------------

def _win():
    import ctypes
    from ctypes import wintypes as wt

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                    ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
                    ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wt.LPWSTR), ("UserName", wt.LPWSTR)]

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wt.DWORD]
    api.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    api.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
    api.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, CREDENTIAL, api


def _win_write(secret):
    ctypes, CREDENTIAL, api = _win()
    blob = secret.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL()
    cred.Type = 1                      # CRED_TYPE_GENERIC
    cred.TargetName = SERVICE
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = 2                   # CRED_PERSIST_LOCAL_MACHINE (this user, this PC)
    cred.UserName = "BlendWMe"
    if not api.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(f"Windows Credential Manager error {ctypes.get_last_error()}")


def _win_read():
    ctypes, CREDENTIAL, api = _win()
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not api.CredReadW(SERVICE, 1, 0, ctypes.byref(ptr)):
        return ""
    try:
        c = ptr.contents
        return bytes(c.CredentialBlob[:c.CredentialBlobSize]).decode("utf-16-le")
    finally:
        api.CredFree(ptr)


def _win_delete():
    _, _, api = _win()
    api.CredDeleteW(SERVICE, 1, 0)


def _run(args, stdin=None):
    return subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=10)


def keychain_name():
    if sys.platform == "win32":
        return "Windows Credential Manager"
    if sys.platform == "darwin":
        return "macOS Keychain"
    if shutil.which("secret-tool"):
        return "Secret Service"
    return ""


def keychain_write(secret):
    if sys.platform == "win32":
        _win_write(secret)
    elif sys.platform == "darwin":
        r = _run(["security", "add-generic-password", "-U", "-a", "BlendWMe", "-s", SERVICE, "-w", secret])
        if r.returncode:
            raise OSError(r.stderr.strip() or "Keychain refused the key")
    elif shutil.which("secret-tool"):
        r = _run(["secret-tool", "store", "--label", SERVICE, "service", SERVICE], stdin=secret)
        if r.returncode:
            raise OSError(r.stderr.strip() or "Secret Service refused the key")
    else:
        raise OSError("No password store found on this system. Use 'This session only'.")


def keychain_read():
    try:
        if sys.platform == "win32":
            return _win_read()
        if sys.platform == "darwin":
            r = _run(["security", "find-generic-password", "-a", "BlendWMe", "-s", SERVICE, "-w"])
            return r.stdout.strip() if r.returncode == 0 else ""
        if shutil.which("secret-tool"):
            r = _run(["secret-tool", "lookup", "service", SERVICE])
            return r.stdout.strip() if r.returncode == 0 else ""
    except Exception as e:
        print("BlendWMe: could not read the password store:", e)
    return ""


def keychain_delete():
    try:
        if sys.platform == "win32":
            _win_delete()
        elif sys.platform == "darwin":
            _run(["security", "delete-generic-password", "-a", "BlendWMe", "-s", SERVICE])
        elif shutil.which("secret-tool"):
            _run(["secret-tool", "clear", "service", SERVICE])
    except Exception as e:
        print("BlendWMe: could not delete from the password store:", e)


# ----------------------------------------------------------------------
# What the rest of BlendWMe uses
# ----------------------------------------------------------------------

def looks_like_key(text):
    text = (text or "").strip()
    return 20 <= len(text) <= 4096 and not any(c.isspace() for c in text)


def set_key(secret, storage):
    """Remember a key. Returns a short message for the user."""
    secret = secret.strip()
    _memory["key"] = secret
    _memory["loaded"] = True
    if storage == "KEYCHAIN":
        keychain_write(secret)
        return f"Key saved in {keychain_name()}."
    return "Key loaded for this session only."


def get_key(storage):
    if storage == "ENV":
        return os.environ.get("NEBIUS_API_KEY", "").strip()
    if not _memory["key"] and storage == "KEYCHAIN" and not _memory["loaded"]:
        _memory["loaded"] = True          # read the store once per session
        _memory["key"] = keychain_read()
    return _memory["key"]


def forget(storage):
    _memory["key"] = ""
    _memory["loaded"] = True
    if storage == "KEYCHAIN":
        keychain_delete()


def masked(key):
    return ("•" * 6 + key[-4:]) if key else "none"


def clear_memory():
    _memory["key"] = ""
    _memory["loaded"] = False
