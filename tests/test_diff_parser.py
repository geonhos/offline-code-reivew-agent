"""Git diff 파서 테스트 - 다양한 diff 케이스 검증."""

from pathlib import Path

import pytest

from src.diff_parser import DiffResult, parse_diff

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def sample_diff() -> DiffResult:
    text = (FIXTURES_DIR / "sample.diff").read_text()
    return parse_diff(text)


class TestParseDiffStructure:
    def test_parse_total_files(self, sample_diff):
        # Python: main.py, utils.py, config.json, image.png, requirements.txt, package-lock.json
        # Java: UserService.java, SecurityConfig.java
        assert len(sample_diff.files) == 8

    def test_summary(self, sample_diff):
        s = sample_diff.summary
        assert s["total_files"] == 8
        assert s["total_added"] > 0
        assert s["total_deleted"] > 0


class TestPythonFiles:
    def test_modified_file(self, sample_diff):
        main_py = next(f for f in sample_diff.files if f.filename == "src/main.py")
        assert main_py.status == "modified"
        assert len(main_py.added_lines) > 0
        assert len(main_py.deleted_lines) > 0

    def test_new_file(self, sample_diff):
        utils_py = next(f for f in sample_diff.files if f.filename == "src/utils.py")
        assert utils_py.status == "added"
        assert len(utils_py.added_lines) == 12
        assert len(utils_py.deleted_lines) == 0

    def test_deleted_file(self, sample_diff):
        config = next(f for f in sample_diff.files if f.filename == "config.json")
        assert config.status == "deleted"
        assert len(config.deleted_lines) == 5

    def test_added_line_content(self, sample_diff):
        main_py = next(f for f in sample_diff.files if f.filename == "src/main.py")
        added_contents = [l.content for l in main_py.added_lines]
        assert any("process_user_data" in c for c in added_contents)
        assert any("API_KEY" in c for c in added_contents)


class TestJavaFiles:
    def test_modified_java_file(self, sample_diff):
        user_svc = next(
            f for f in sample_diff.files if "UserService.java" in f.filename
        )
        assert user_svc.status == "modified"
        assert len(user_svc.added_lines) > 0

    def test_java_optional_usage(self, sample_diff):
        user_svc = next(
            f for f in sample_diff.files if "UserService.java" in f.filename
        )
        added = [l.content for l in user_svc.added_lines]
        assert any("Optional<User>" in c for c in added)
        assert any("orElseThrow" in c for c in added)

    def test_new_java_file(self, sample_diff):
        security = next(
            f for f in sample_diff.files if "SecurityConfig.java" in f.filename
        )
        assert security.status == "added"
        added = [l.content for l in security.added_lines]
        # 보안 이슈가 있는 코드
        assert any("hardcoded-secret-key" in c for c in added)
        assert any("SELECT * FROM users" in c for c in added)


class TestBinaryAndSkipFiles:
    def test_binary_file(self, sample_diff):
        image = next(f for f in sample_diff.files if "image.png" in f.filename)
        assert image.is_binary is True
        assert image.status == "binary"

    def test_lock_file_skipped(self, sample_diff):
        reviewable = sample_diff.reviewable_files
        filenames = [f.filename for f in reviewable]
        assert "package-lock.json" not in filenames

    def test_binary_file_skipped(self, sample_diff):
        reviewable = sample_diff.reviewable_files
        assert all(not f.is_binary for f in reviewable)

    def test_reviewable_files_count(self, sample_diff):
        reviewable = sample_diff.reviewable_files
        # 제외: image.png (binary), package-lock.json (lock file)
        # 포함: main.py, utils.py, config.json, requirements.txt, UserService.java, SecurityConfig.java
        assert len(reviewable) == 6


class TestHunkParsing:
    def test_hunk_line_numbers(self, sample_diff):
        main_py = next(f for f in sample_diff.files if f.filename == "src/main.py")
        assert len(main_py.hunks) == 1
        hunk = main_py.hunks[0]
        assert hunk.new_start == 1

    def test_line_numbers_are_correct(self, sample_diff):
        utils_py = next(f for f in sample_diff.files if f.filename == "src/utils.py")
        first_line = utils_py.added_lines[0]
        assert first_line.number == 1
        assert '"""유틸리티 함수 모듈."""' in first_line.content
