"""Git diff 파서 - unified diff를 구조화된 데이터로 변환."""

import re
from dataclasses import dataclass, field

# 리뷰 불필요 파일 패턴
SKIP_PATTERNS: list[str] = [
    r"package-lock\.json$",
    r"yarn\.lock$",
    r"pnpm-lock\.yaml$",
    r"poetry\.lock$",
    r"Pipfile\.lock$",
    r"go\.sum$",
    r"\.min\.js$",
    r"\.min\.css$",
    r"\.map$",
    r"\.svg$",
    r"\.ico$",
]

BINARY_MARKER = "Binary files"


@dataclass
class Line:
    number: int
    content: str
    type: str  # "add", "delete", "context"


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[Line] = field(default_factory=list)


@dataclass
class FileDiff:
    filename: str
    old_filename: str | None = None
    status: str = "modified"  # "added", "deleted", "modified", "renamed", "binary"
    hunks: list[Hunk] = field(default_factory=list)
    is_binary: bool = False

    @property
    def added_lines(self) -> list[Line]:
        return [l for h in self.hunks for l in h.lines if l.type == "add"]

    @property
    def deleted_lines(self) -> list[Line]:
        return [l for h in self.hunks for l in h.lines if l.type == "delete"]


@dataclass
class DiffResult:
    files: list[FileDiff] = field(default_factory=list)

    @property
    def reviewable_files(self) -> list[FileDiff]:
        """리뷰 대상 파일만 반환한다 (바이너리, 락 파일 등 제외)."""
        return [f for f in self.files if not f.is_binary and not _should_skip(f.filename)]

    @property
    def summary(self) -> dict:
        total_added = sum(len(f.added_lines) for f in self.files)
        total_deleted = sum(len(f.deleted_lines) for f in self.files)
        return {
            "total_files": len(self.files),
            "reviewable_files": len(self.reviewable_files),
            "total_added": total_added,
            "total_deleted": total_deleted,
        }


def _should_skip(filename: str) -> bool:
    """리뷰 불필요 파일인지 확인한다."""
    return any(re.search(p, filename) for p in SKIP_PATTERNS)


def parse_diff(diff_text: str) -> DiffResult:
    """unified diff 텍스트를 구조화된 DiffResult로 파싱한다."""
    result = DiffResult()
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None
    new_line_num = 0

    # diff --git 블록 사이에서 상태를 추적
    pending_status: str | None = None

    for line in diff_text.split("\n"):
        # 새 파일 블록 시작
        if line.startswith("diff --git"):
            current_file = None
            current_hunk = None
            pending_status = None
            continue

        # 파일 상태 마커 (아직 FileDiff 생성 전)
        if line.startswith("new file"):
            pending_status = "added"
            continue
        if line.startswith("deleted file"):
            pending_status = "deleted"
            continue

        # 바이너리 파일
        if line.startswith(BINARY_MARKER):
            match = re.search(r"and b/(.+?) differ", line)
            if match:
                current_file = FileDiff(
                    filename=match.group(1),
                    status="binary",
                    is_binary=True,
                )
                result.files.append(current_file)
            continue

        # --- 라인 (원본 파일 경로)
        if line.startswith("--- "):
            path = line[4:]
            # 삭제 파일의 경우: --- a/config.json, +++ /dev/null
            # 여기서 파일명을 기억해두고 +++ /dev/null일 때 사용
            if pending_status == "deleted" and path.startswith("a/"):
                current_file = FileDiff(
                    filename=path[2:],
                    status="deleted",
                )
                result.files.append(current_file)
            continue

        # +++ 라인 (변경 파일 경로)
        if line.startswith("+++ "):
            path = line[4:]
            if path == "/dev/null":
                # 삭제 파일 — 이미 --- 라인에서 처리됨
                continue
            filename = path[2:] if path.startswith("b/") else path
            current_file = FileDiff(
                filename=filename,
                status=pending_status or "modified",
            )
            result.files.append(current_file)
            continue

        # 인덱스 라인 무시
        if line.startswith("index "):
            continue

        # 헌크 헤더
        hunk_match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match and current_file:
            current_hunk = Hunk(
                old_start=int(hunk_match.group(1)),
                old_count=int(hunk_match.group(2) or 1),
                new_start=int(hunk_match.group(3)),
                new_count=int(hunk_match.group(4) or 1),
            )
            current_file.hunks.append(current_hunk)
            new_line_num = current_hunk.new_start
            continue

        # diff 내용 라인
        if current_hunk is not None:
            if line.startswith("+"):
                current_hunk.lines.append(Line(
                    number=new_line_num,
                    content=line[1:],
                    type="add",
                ))
                new_line_num += 1
            elif line.startswith("-"):
                current_hunk.lines.append(Line(
                    number=new_line_num,
                    content=line[1:],
                    type="delete",
                ))
            elif line.startswith(" "):
                current_hunk.lines.append(Line(
                    number=new_line_num,
                    content=line[1:],
                    type="context",
                ))
                new_line_num += 1

    return result
