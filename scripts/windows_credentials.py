"""Store API keys in Windows Credential Manager, never in settings files."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


def credential_target(provider_id: str) -> str:
    return f"Rimcat-JA/CK3JapaneseModMaker/{provider_id}"


def _advapi32() -> ctypes.WinDLL:
    if os.name != "nt":
        raise OSError("Windows Credential Manager is available only on Windows")
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
    library.CredReadW.restype = wintypes.BOOL
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    library.CredFree.restype = None
    return library


def save_api_key(provider_id: str, api_key: str) -> None:
    if not api_key:
        raise ValueError("API key is empty")
    encoded = api_key.encode("utf-8")
    if len(encoded) > 2560:
        raise ValueError("API key is too long for Windows Credential Manager")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = credential_target(provider_id)
    credential.Comment = "CK3 Japanese Mod Maker API key"
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = provider_id
    library = _advapi32()
    if not library.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def load_api_key(provider_id: str) -> str | None:
    if os.name != "nt":
        return None
    library = _advapi32()
    pointer = PCREDENTIALW()
    if not library.CredReadW(credential_target(provider_id), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return None
        raise ctypes.WinError(error)
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-8")
    finally:
        library.CredFree(pointer)


def delete_api_key(provider_id: str) -> bool:
    if os.name != "nt":
        return False
    library = _advapi32()
    if library.CredDeleteW(credential_target(provider_id), CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == 1168:
        return False
    raise ctypes.WinError(error)
