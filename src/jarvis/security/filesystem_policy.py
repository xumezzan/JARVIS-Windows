"""Trusted startup roots and pure lexical policy. No model-controlled expansion of access."""

from pathlib import Path

from pydantic import Field

from jarvis.files.protected import macos_path_denied
from jarvis.tools.base import ToolError, ToolModel

TEXT_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".py"}
READ_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TEXT = 16000
EXCLUDED = {
    "node_modules",
    "__pycache__",
    "appdata",
    "library",
    "windows",
    "program files",
    "programdata",
    "venv",
    "site-packages",
    "credentials",
    "secrets",
    "private",
    "credentials.json",
    "secrets.json",
    "tokens.json",
    "token.json",
    "application_default_credentials.json",
}


class Root(ToolModel):
    name: str = Field(min_length=1, max_length=30)
    path: str = Field(min_length=1, max_length=2000)


class FilePolicy(ToolModel):
    roots: tuple[Root, ...] = Field(min_length=1, max_length=12)
    workspace: str = Field(min_length=1, max_length=2000)
    protected: str = Field(min_length=1, max_length=2000)

    def path(self, value: str, *, write: bool = False) -> Path:
        if not value or "\x00" in value or value.startswith(("\\\\", "//", "~")):
            raise ToolError("file_denied")
        path = Path(value)
        if not path.is_absolute():
            first, *rest = path.parts
            alias = next(
                (root for root in self.roots if root.name.casefold() == first.casefold()), None
            )
            if alias is None:
                raise ToolError("file_denied")
            path = Path(alias.path).joinpath(*rest)
        if ".." in path.parts or not path.is_absolute():
            raise ToolError("file_denied")
        if macos_path_denied(path):
            raise ToolError("file_denied")
        candidates = [Path(root.path) for root in self.roots if path.is_relative_to(root.path)]
        root = max(candidates, key=lambda value: len(value.parts)) if candidates else None
        if root is None:
            raise ToolError("file_denied")
        # Check relative components: trusted roots may legitimately live under a hidden system dir.
        for part in path.relative_to(root).parts:
            stem = part.split(".")[0].casefold().translate(str.maketrans("¹²³", "123"))
            if (
                part.startswith(".")
                or part.casefold() in EXCLUDED
                or part.casefold().startswith(("client_secret", "service-account"))
                or ":" in part
                or part.endswith((" ", "."))
                or any(ord(char) < 32 for char in part)
                or stem
                in {
                    "con",
                    "prn",
                    "aux",
                    "nul",
                    "conin$",
                    "conout$",
                    "clock$",
                    *(f"com{i}" for i in range(10)),
                    *(f"lpt{i}" for i in range(10)),
                }
            ):
                raise ToolError("file_denied")
        if path.is_relative_to(self.protected) and not path.is_relative_to(self.workspace):
            raise ToolError("file_denied")
        if write and path.suffix.casefold() not in TEXT_EXTENSIONS:
            raise ToolError("file_unsupported")
        return path

    def creation_path(self, value: str) -> Path:
        path = self.path(value, write=True)
        if not path.is_relative_to(self.workspace):
            raise ToolError("file_denied")
        return path

    def wire(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def filesystem_policy(data_dir: Path, projects: tuple[Path, ...] = ()) -> FilePolicy:
    from jarvis.platforms.folders import working_folders

    protected = data_dir.expanduser().resolve()
    workspace = protected / "workspace"
    roots = [Root(name="workspace", path=str(workspace))]
    roots.extend(
        Root(name=name, path=str(path.resolve()))
        for name, path in working_folders()
        if not str(path).startswith(("\\\\", "//"))
    )
    roots.extend(
        Root(name=f"project{index + 1}", path=str(path.resolve()))
        for index, path in enumerate(projects)
    )
    return FilePolicy(roots=tuple(roots), workspace=str(workspace), protected=str(protected))
