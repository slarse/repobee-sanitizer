import collections
import pathlib
import sys

import pytest
import git
import repobee

from repobee_sanitizer import sanitizer
import repobee_plug as plug
import testhelpers


_FileInfo = collections.namedtuple(
    "_FileInfo", "original_text expected_text abspath relpath".split()
)
_FakeRepoInfo = collections.namedtuple(
    "_FakeRepoInfo", "repo path file_list_path file_infos default_branch"
)


def test_sanitize_valid_file(sanitizer_config, tmpdir):
    test_case_dir = (
        testhelpers.VALID_CASES_BASEDIR
        / "replace"
        / "prefixed"
        / "junit_test_case"
    )
    inp_text, out_text = testhelpers.read_valid_test_case_files(test_case_dir)

    infile = pathlib.Path(tmpdir) / "input.in"
    infile.write_text(inp_text)
    outfile = pathlib.Path(tmpdir) / "output.out"

    repobee.main(
        f"repobee --config-file {sanitizer_config} sanitize-file "
        f"{infile} {outfile}".split()
    )

    assert outfile.read_text(encoding="utf8") == out_text


def test_sanitize_repo_dictate_mode_no_commit(sanitizer_config, fake_repo):
    """Test sanitize repo with a list of files to sanitize in current working
    tree of the repository.
    """
    repobee.main(
        f"repobee --config-file {sanitizer_config} sanitize-repo "
        "--no-commit "
        f"--file-list {fake_repo.file_list_path} "
        f"--repo-root {fake_repo.path}".split()
    )

    assert_expected_text_in_files(fake_repo.file_infos)


def test_sanitize_repo_with_target_branch_does_not_mutate_worktree(
    sanitizer_config, fake_repo
):
    """Test that sanitize-repo does not mutate the current worktree when given
    a target branch.
    """
    target_branch = "student-version"
    repobee.main(
        f"repobee --config-file {sanitizer_config} sanitize-repo "
        f"--file-list {fake_repo.file_list_path} "
        f"--target-branch {target_branch} "
        f"--repo-root {fake_repo.path}".split()
    )

    assert fake_repo.repo.git.diff(fake_repo.repo.head.commit) == ""


def test_sanitize_repo_commits_to_non_existing_target_branch(
    sanitizer_config, fake_repo
):
    """Test that sanitize-repo commits the sanitized files to the target
    branch when the target branch does not exist prior to the commit.
    """
    target_branch = "student-version"
    repobee.main(
        f"repobee --config-file {sanitizer_config} sanitize-repo "
        f"--file-list {fake_repo.file_list_path} "
        f"--target-branch {target_branch} "
        f"--repo-root {fake_repo.path}".split()
    )

    fake_repo.repo.git.checkout(target_branch)
    assert_expected_text_in_files(fake_repo.file_infos)


def test_sanitize_repo_commits_to_existing_target_branch(
    sanitizer_config, fake_repo
):
    """Test that sanitize-repo commits the sanitized files to the target branch
    when the target branch all ready exists.
    """
    target_branch = "student-version"
    fake_repo.repo.git.branch(target_branch)

    repobee.main(
        f"repobee --config-file {sanitizer_config} sanitize-repo "
        f"--file-list {fake_repo.file_list_path} "
        f"--target-branch {target_branch} "
        f"--repo-root {fake_repo.path}".split()
    )

    fake_repo.repo.git.checkout(target_branch)
    assert_expected_text_in_files(fake_repo.file_infos)


def test_sanitize_repo_commits_non_processed_files_to_target_branch(
    sanitizer_config, fake_repo
):
    """sanitize-repo should not only commit the sanitized files, but also any
    other files that happend to be in the repo.
    """
    target_branch = "student-version"
    other_file_contents = "Some boring\ncontents in a non-interesting\nfile"
    other_file = fake_repo.path / "some-other-file.txt"
    other_file.write_text(other_file_contents)
    fake_repo.repo.git.add(str(other_file))
    fake_repo.repo.git.commit("-m", "'Add other file'")

    repobee.main(
        f"repobee --config-file {sanitizer_config} sanitize-repo "
        f"--file-list {fake_repo.file_list_path} "
        f"--target-branch {target_branch} "
        f"--repo-root {fake_repo.path}".split()
    )

    # note that "other_file" is tracked on the current branch, and so if it's
    # not on the target branch it will disappear from the worktree
    fake_repo.repo.git.checkout(target_branch)
    assert (
        other_file.read_text(sys.getdefaultencoding()) == other_file_contents
    )


def test_sanitize_repo_returns_fail_when_repo_has_staged_changes(
    sanitizer_config, fake_repo
):
    tracked_file = fake_repo.file_infos[0].abspath
    tracked_file.write_text("this is the new text!")
    fake_repo.repo.git.add(tracked_file)

    result = execute_sanitize_repo(
        f"--file-list {fake_repo.file_list_path} "
        f"--repo-root {fake_repo.path} "
        "--no-commit"
    )

    assert result.status == plug.Status.ERROR
    assert "uncommitted staged file" in result.msg


def test_sanitize_repo_return_fail_when_has_unstaged_changes(
    sanitizer_config, fake_repo
):
    unstaged_file = fake_repo.file_infos[0].abspath
    unstaged_file.write_text("This is some new text!")

    result = execute_sanitize_repo(
        f"--file-list {fake_repo.file_list_path} "
        f"--repo-root {fake_repo.path} "
        "--no-commit"
    )

    assert result.status == plug.Status.ERROR
    assert "uncommitted unstaged file" in result.msg


def test_sanitize_repo_return_fail_when_has_untracked_files(
    sanitizer_config, fake_repo
):
    """Places an untracked file in the repo"""
    untracked_file = fake_repo.path / "untracked.txt"
    untracked_file.write_text("This is some untracked text")

    result = execute_sanitize_repo(
        f"--file-list {fake_repo.file_list_path} "
        f"--repo-root {fake_repo.path} "
        "--no-commit"
    )

    assert result.status == plug.Status.ERROR
    assert "untracked file" in result.msg


def test_sanitize_repo_passes_with_force_flag(sanitizer_config, fake_repo):
    """Test adds an utracked, unstaged, and staged file to make sure
    that repobee does not complain when we add --force to the command.
    Expects these files to NOT exist on target branch, as only
    committed changes should be sanitized.
    """
    untracked_file_content = "This is some untracked text"
    untracked_file = fake_repo.path / "untracked.txt"
    untracked_file.write_text(untracked_file_content)

    unstaged_file_content = "This is some unstaged text!"
    unstaged_file = fake_repo.file_infos[0].abspath
    unstaged_file.write_text(unstaged_file_content)

    staged_file_content = "This file is staged!"
    staged_file = fake_repo.file_infos[1].abspath
    staged_file.write_text(staged_file_content)
    fake_repo.repo.git.add(staged_file)

    target_branch = "student-version"

    execute_sanitize_repo(
        f"--file-list {fake_repo.file_list_path} "
        f"--repo-root {fake_repo.path} "
        f"--target-branch {target_branch} "
        "--force"
    )

    # Assert that working tree has not been modified
    assert untracked_file.is_file()
    assert unstaged_file.read_text() == unstaged_file_content
    assert staged_file.read_text() == staged_file_content

    fake_repo.repo.git.reset("--hard")
    fake_repo.repo.git.clean("-dfx")

    fake_repo.repo.git.checkout(target_branch)
    assert not untracked_file.is_file()
    assert_expected_text_in_files(fake_repo.file_infos)


def test_sanitize_repo_raises_plug_error_if_file_list_doesnt_exist(
    sanitizer_config, fake_repo
):
    non_existing_file_list = fake_repo.path / "fake_file_list.txt"

    with pytest.raises(plug.PlugError):
        execute_sanitize_repo(
            f"--file-list {non_existing_file_list} "
            f"--repo-root {fake_repo.path} "
            "--no-commit"
        )


def execute_sanitize_repo(arguments: str):
    """Run the sanitize-repo function with the given arguments"""
    sanitize_repo = sanitizer.SanitizeRepo()
    cmd = sanitize_repo.create_extension_command()
    args = cmd.parser.parse_args(arguments.split())
    result = cmd.callback(args, None)
    return result


@pytest.fixture
def fake_repo(tmpdir) -> _FakeRepoInfo:
    """Setup a fake repository to sanitize."""
    fake_repo_path = pathlib.Path(tmpdir)
    file_infos = []
    for raw_case, id_ in zip(*testhelpers.generate_valid_test_cases()):
        input_text, expected_output_text = raw_case
        output_path = fake_repo_path / id_
        output_path.parent.mkdir(parents=True)
        output_path.write_text(input_text)
        file_infos.append(
            _FileInfo(
                original_text=input_text,
                expected_text=expected_output_text,
                abspath=fake_repo_path / id_,
                relpath=pathlib.Path(id_),
            )
        )

    file_list = fake_repo_path / "file_list.txt"
    file_list_content = "\n".join([str(c.relpath) for c in file_infos])
    file_list.write_text(file_list_content)

    default_branch = "develop"
    fake_repo = git.Repo.init(tmpdir)
    fake_repo.git.symbolic_ref("HEAD", f"refs/heads/{default_branch}")
    fake_repo.git.add(".")
    fake_repo.git.commit("-m", "'Initial commit'")

    return _FakeRepoInfo(
        repo=fake_repo,
        path=fake_repo_path,
        file_list_path=file_list,
        file_infos=file_infos,
        default_branch=default_branch,
    )


def assert_expected_text_in_files(file_infos):
    asserted = False
    for file_info in file_infos:
        asserted = True
        assert file_info.abspath.read_text("utf8") == file_info.expected_text
    assert asserted, "Loop not run, test error"