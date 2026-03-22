"""의존성 파일 파서 - diff에서 추가/변경된 라이브러리를 추출한다."""

import json
import re
from dataclasses import dataclass

from src.diff_parser import DiffResult, FileDiff

# 의존성 파일 패턴
DEPENDENCY_FILE_PATTERNS: list[str] = [
    r"requirements.*\.txt$",
    r"pyproject\.toml$",
    r"setup\.cfg$",
    r"package\.json$",
    r"pom\.xml$",
    r"build\.gradle$",
]


@dataclass
class Dependency:
    name: str
    version: str
    source_file: str
    line_number: int


def is_dependency_file(filename: str) -> bool:
    """의존성 파일인지 확인한다."""
    return any(re.search(p, filename) for p in DEPENDENCY_FILE_PATTERNS)


def parse_dependencies_from_diff(diff_result: DiffResult) -> list[Dependency]:
    """DiffResult에서 추가된 의존성을 추출한다."""
    deps: list[Dependency] = []
    for file_diff in diff_result.files:
        if not is_dependency_file(file_diff.filename):
            continue
        deps.extend(_parse_file(file_diff))
    return deps


def _parse_file(file_diff: FileDiff) -> list[Dependency]:
    """파일 형식에 따라 적절한 파서를 호출한다."""
    filename = file_diff.filename
    if re.search(r"requirements.*\.txt$", filename):
        return _parse_requirements_txt(file_diff)
    if filename.endswith("pyproject.toml"):
        return _parse_pyproject_toml(file_diff)
    if filename.endswith("package.json"):
        return _parse_package_json(file_diff)
    if filename.endswith("pom.xml"):
        return _parse_pom_xml(file_diff)
    if filename.endswith("build.gradle"):
        return _parse_build_gradle(file_diff)
    return []


def _parse_requirements_txt(file_diff: FileDiff) -> list[Dependency]:
    """requirements.txt에서 추가된 패키지를 추출한다."""
    deps: list[Dependency] = []
    for line in file_diff.added_lines:
        content = line.content.strip()
        if not content or content.startswith("#") or content.startswith("-"):
            continue
        match = re.match(r"^([a-zA-Z0-9_.-]+)\[?[^\]]*\]?([=<>~!]+)(.+)", content)
        if match:
            deps.append(Dependency(
                name=match.group(1).lower(),
                version=match.group(3).strip().split(",")[0],
                source_file=file_diff.filename,
                line_number=line.number,
            ))
        else:
            # 버전 없는 패키지
            pkg_match = re.match(r"^([a-zA-Z0-9_.-]+)", content)
            if pkg_match:
                deps.append(Dependency(
                    name=pkg_match.group(1).lower(),
                    version="",
                    source_file=file_diff.filename,
                    line_number=line.number,
                ))
    return deps


def _parse_pyproject_toml(file_diff: FileDiff) -> list[Dependency]:
    """pyproject.toml에서 추가된 패키지를 추출한다."""
    deps: list[Dependency] = []
    for line in file_diff.added_lines:
        content = line.content.strip()
        # "패키지명>=버전" 또는 "패키지명==버전" 형식
        match = re.match(r'"([a-zA-Z0-9_.-]+)\[?[^\]]*\]?([=<>~!]+)([^"]+)"', content)
        if match:
            deps.append(Dependency(
                name=match.group(1).lower(),
                version=match.group(3).strip().rstrip('"').split(",")[0],
                source_file=file_diff.filename,
                line_number=line.number,
            ))
    return deps


def _parse_package_json(file_diff: FileDiff) -> list[Dependency]:
    """package.json에서 추가된 패키지를 추출한다."""
    deps: list[Dependency] = []
    for line in file_diff.added_lines:
        content = line.content.strip().rstrip(",")
        # "패키지명": "^버전" 또는 "패키지명": "~버전"
        match = re.match(r'"([^"]+)"\s*:\s*"[^~]?([0-9][^"]*)"', content)
        if match:
            deps.append(Dependency(
                name=match.group(1).lower(),
                version=match.group(2).strip(),
                source_file=file_diff.filename,
                line_number=line.number,
            ))
    return deps


def _parse_pom_xml(file_diff: FileDiff) -> list[Dependency]:
    """pom.xml에서 추가된 패키지를 추출한다 (단순 라인 기반 파싱)."""
    deps: list[Dependency] = []
    lines = file_diff.added_lines
    for i, line in enumerate(lines):
        content = line.content.strip()
        artifact_match = re.search(r"<artifactId>([^<]+)</artifactId>", content)
        if artifact_match:
            # 다음 라인에서 version 태그를 찾는다
            version = ""
            for j in range(i + 1, min(i + 3, len(lines))):
                ver_match = re.search(r"<version>([^<]+)</version>", lines[j].content)
                if ver_match:
                    version = ver_match.group(1)
                    break
            deps.append(Dependency(
                name=artifact_match.group(1).lower(),
                version=version,
                source_file=file_diff.filename,
                line_number=line.number,
            ))
    return deps


def _parse_build_gradle(file_diff: FileDiff) -> list[Dependency]:
    """build.gradle에서 추가된 패키지를 추출한다."""
    deps: list[Dependency] = []
    for line in file_diff.added_lines:
        content = line.content.strip()
        # implementation 'group:artifact:version'
        match = re.search(r"['\"]([^:]+):([^:]+):([^'\"]+)['\"]", content)
        if match:
            deps.append(Dependency(
                name=f"{match.group(1)}:{match.group(2)}".lower(),
                version=match.group(3),
                source_file=file_diff.filename,
                line_number=line.number,
            ))
    return deps
