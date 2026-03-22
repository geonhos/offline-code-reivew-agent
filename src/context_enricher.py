"""컨텍스트 수집기 - LLM 없이 코드 분석으로 리뷰 컨텍스트를 강화한다.

전체 파일 소스 fetch, AST 분석(import/함수 시그니처/클래스), 호출 관계 탐지를 수행한다.
"""

import ast
import logging
from dataclasses import dataclass, field

from src.diff_parser import FileDiff
from src.gitlab_client import GitLabClient

logger = logging.getLogger(__name__)

# 파일 확장자 → 언어 매핑
_LANG_MAP = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".rs": "rust",
}


@dataclass
class EnrichedContext:
    full_source: str = ""
    imports: list[str] = field(default_factory=list)
    function_signatures: list[str] = field(default_factory=list)
    class_names: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    language: str = "unknown"


@dataclass
class FileContext:
    file_path: str
    enriched: EnrichedContext


class ContextEnricher:
    """코드 기반 컨텍스트 수집기. LLM 호출 없이 동작한다."""

    MAX_SOURCE_LINES = 3000

    def __init__(self, gitlab_client: GitLabClient):
        self._gitlab_client = gitlab_client

    def enrich(
        self,
        project_id: int,
        mr_iid: int,
        file_diffs: list[FileDiff],
        ref: str = "HEAD",
    ) -> list[FileContext]:
        """파일 목록에 대해 컨텍스트를 수집한다."""
        results: list[FileContext] = []
        for file_diff in file_diffs:
            language = self._detect_language(file_diff.filename)
            full_source = self._fetch_full_source(project_id, file_diff.filename, ref)

            if language == "python" and full_source:
                ctx = self._analyze_python_ast(full_source)
                changed_funcs = self._extract_changed_function_names(file_diff, full_source)
                if changed_funcs:
                    ctx.callers = self._find_callers(full_source, changed_funcs)
            else:
                ctx = EnrichedContext(language=language)

            ctx.full_source = self._truncate_source(full_source)
            results.append(FileContext(file_path=file_diff.filename, enriched=ctx))

        return results

    def _detect_language(self, file_path: str) -> str:
        """파일 확장자로 언어를 감지한다."""
        for ext, lang in _LANG_MAP.items():
            if file_path.endswith(ext):
                return lang
        return "unknown"

    def _fetch_full_source(self, project_id: int, file_path: str, ref: str) -> str:
        """GitLab API로 전체 파일 내용을 가져온다."""
        try:
            return self._gitlab_client.get_file_content(project_id, file_path, ref)
        except Exception:
            logger.warning("파일 fetch 실패: %s", file_path, exc_info=True)
            return ""

    def _truncate_source(self, source: str) -> str:
        """소스 코드를 최대 라인 수로 자른다."""
        lines = source.split("\n")
        if len(lines) > self.MAX_SOURCE_LINES:
            return "\n".join(lines[: self.MAX_SOURCE_LINES]) + "\n# ... (truncated)"
        return source

    def _analyze_python_ast(self, source: str) -> EnrichedContext:
        """Python AST를 분석하여 import, 함수, 클래스 정보를 추출한다."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            logger.warning("Python AST 파싱 실패")
            return EnrichedContext(language="python")

        imports: list[str] = []
        functions: list[str] = []
        classes: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = self._build_function_signature(node)
                functions.append(sig)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)

        return EnrichedContext(
            imports=imports,
            function_signatures=functions,
            class_names=classes,
            language="python",
        )

    @staticmethod
    def _build_function_signature(node: ast.FunctionDef) -> str:
        """AST FunctionDef 노드에서 함수 시그니처 문자열을 생성한다."""
        args_parts: list[str] = []
        for arg in node.args.args:
            name = arg.arg
            if arg.annotation:
                try:
                    name += f": {ast.unparse(arg.annotation)}"
                except Exception:
                    pass
            args_parts.append(name)

        sig = f"def {node.name}({', '.join(args_parts)})"
        if node.returns:
            try:
                sig += f" -> {ast.unparse(node.returns)}"
            except Exception:
                pass
        return sig

    def _find_callers(self, source: str, changed_functions: list[str]) -> list[str]:
        """변경된 함수를 호출하는 함수 목록을 찾는다."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        callers: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func_name = self._extract_call_name(child)
                    if func_name in changed_functions and func_name != node.name:
                        callers.add(node.name)

        return sorted(callers)

    @staticmethod
    def _extract_call_name(call_node: ast.Call) -> str:
        """Call 노드에서 함수명을 추출한다."""
        func = call_node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return ""

    def _extract_changed_function_names(
        self, file_diff: FileDiff, source: str
    ) -> list[str]:
        """diff에서 변경된 라인이 속하는 함수명 목록을 추출한다."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        # 함수별 라인 범위 매핑
        func_ranges: list[tuple[str, int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_ranges.append((node.name, node.lineno, node.end_lineno or node.lineno))

        changed_lines = {line.number for line in file_diff.added_lines}
        changed_funcs: set[str] = set()

        for name, start, end in func_ranges:
            if any(start <= ln <= end for ln in changed_lines):
                changed_funcs.add(name)

        return sorted(changed_funcs)
