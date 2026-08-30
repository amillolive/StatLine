from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Final

from statline.manifest.definitions.file_type import FileTypeManifest
from statline.manifest.definitions.windows import FileAssociationError

if TYPE_CHECKING:
    from statline.manifest.definitions.windows import WindowsFileAssociation

USER_CLASSES_ROOT: Final[str] = r"Software\Classes"


def _load_windows_registry() -> Any | None:
    try:
        return import_module("winreg")
    except ModuleNotFoundError:
        return None


winreg = _load_windows_registry()


def _require_windows() -> Any:
    if winreg is None:
        raise OSError("Windows file associations are only available on Windows.")

    return winreg


def _set_default_value(path: str, value: str) -> None:
    registry = _require_windows()

    try:
        with registry.CreateKey(registry.HKEY_CURRENT_USER, path) as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, value)
    except OSError as exc:
        raise FileAssociationError(f"Unable to update Windows registry key {path!r}.") from exc


def _set_named_value(path: str, name: str, value: str) -> None:
    registry = _require_windows()

    try:
        with registry.CreateKey(registry.HKEY_CURRENT_USER, path) as key:
            registry.SetValueEx(key, name, 0, registry.REG_SZ, value)
    except OSError as exc:
        raise FileAssociationError(f"Unable to update Windows registry key {path!r}.") from exc


def register_windows_file_type(
    association: WindowsFileAssociation,
) -> None:
    """
    Register a custom file type for the current Windows user.

    This only creates an operating-system association. It does not read,
    validate, decode, or otherwise interact with files of that type.
    """
    _require_windows()

    manifest = association.manifest
    extension_path = rf"{USER_CLASSES_ROOT}\{manifest.extension}"
    prog_id_path = rf"{USER_CLASSES_ROOT}\{manifest.prog_id}"

    _set_default_value(extension_path, manifest.prog_id)
    _set_named_value(extension_path, "Content Type", manifest.mime_type)
    _set_named_value(extension_path, "PerceivedType", "document")

    _set_default_value(prog_id_path, manifest.friendly_name)
    _set_named_value(
        prog_id_path,
        "FriendlyTypeName",
        manifest.friendly_name,
    )

    _set_default_value(
        rf"{prog_id_path}\DefaultIcon",
        association.icon,
    )

    _set_default_value(
        rf"{prog_id_path}\shell\open\command",
        association.command,
    )

    _notify_shell()


def unregister_windows_file_type(
    manifest: FileTypeManifest,
) -> None:
    """
    Remove the current-user Windows association for a custom file type.

    Files using the extension are not deleted or modified.
    """
    registry = _require_windows()

    _delete_registry_tree(
        registry.HKEY_CURRENT_USER,
        rf"{USER_CLASSES_ROOT}\{manifest.extension}",
    )

    _delete_registry_tree(
        registry.HKEY_CURRENT_USER,
        rf"{USER_CLASSES_ROOT}\{manifest.prog_id}",
    )

    _notify_shell()


def _delete_registry_tree(root: int, path: str) -> None:
    registry = _require_windows()

    try:
        with registry.OpenKey(
            root,
            path,
            0,
            registry.KEY_READ | registry.KEY_WRITE,
        ) as key:
            while True:
                try:
                    child_name = registry.EnumKey(key, 0)
                except OSError:
                    break

                _delete_registry_tree(root, rf"{path}\{child_name}")

        registry.DeleteKey(root, path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise FileAssociationError(f"Unable to remove Windows registry key {path!r}.") from exc


def _notify_shell() -> None:
    """
    Ask Windows Explorer to refresh file-association information.

    Failure to notify Explorer is non-fatal because the association itself
    has already been written to the registry.
    """
    if winreg is None:
        return

    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return

        shell_change_association = 0x08000000
        shell_notify_id_list = 0x0000

        windll.shell32.SHChangeNotify(
            shell_change_association,
            shell_notify_id_list,
            None,
            None,
        )
    except (AttributeError, OSError):
        return
