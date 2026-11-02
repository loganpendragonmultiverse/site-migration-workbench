"""Verify candidate distribution identities and source/package bytes."""

import hashlib
import json
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from smw import __version__

root = Path(__file__).resolve().parents[1]
wheel = next((root / "dist").glob("*.whl"))
source = next((root / "dist").glob("*.tar.gz"))
with zipfile.ZipFile(wheel) as archive:
    metadata = BytesParser().parsebytes(
        archive.read(next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA")))
    )
    assert metadata["Name"] == "site-migration-workbench"
    assert metadata["Version"] == __version__
    assert metadata.get_all("Requires-Dist") == ["defusedxml==0.7.1"]
    checked = []
    for p in (root / "smw").iterdir():
        if p.suffix in (".py", ".html", ".css", ".js"):
            name = "smw/" + p.name
            assert archive.read(name) == p.read_bytes(), name
            checked.append(name)
with tarfile.open(source) as archive:
    prefix = archive.getnames()[0].split("/")[0]
    names = set(archive.getnames())
    for name in [
        "README.md",
        "CHANGELOG.md",
        "DEVELOPMENT.md",
        "TESTING.md",
        "LICENSE",
        "SECURITY.md",
        "docs/SCHEMAS.md",
        "docs/ACCEPTANCE.md",
        "examples/completed-migration-backup.json",
        ".github/CODEOWNERS",
        ".github/workflows/ci.yml",
    ]:
        assert prefix + "/" + name in names, name
    assert not any(".test-tools" in name or name.endswith(".sqlite") for name in names)
result = {
    "version": __version__,
    "runtime_dependencies": ["defusedxml==0.7.1"],
    "verified_package_files": checked,
    "artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (wheel, source)},
    "scope": "local candidate artifacts, not tag-built public release",
}
(root / "evidence" / "artifacts.json").write_text(json.dumps(result, indent=2))
(root / "dist" / "SHA256SUMS").write_text(
    "".join(digest + "  " + name + "\n" for name, digest in sorted(result["artifacts"].items()))
)
print(json.dumps(result, indent=2))
