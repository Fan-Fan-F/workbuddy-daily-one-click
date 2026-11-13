"""Offline AST behavior tests; never import or run the deployment application.

All tokens, state files, Git/index behavior, and pushes are synthetic. The test
suite does not inspect local login state or use any existing deployment repo.
"""
import ast
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "deploy.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
FUNCTION_NAMES = {"jwt_exp", "merge_account_into_store", "commit_store"}
FUNCTIONS = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTION_NAMES]
assert {node.name for node in FUNCTIONS} == FUNCTION_NAMES
assert all(not node.decorator_list for node in FUNCTIONS)
STORE_NAME = "wb_refresh_tokens.json"
GIT = shutil.which("git")
if os.environ.get("REQUIRE_GIT") == "1" and not GIT:
    raise RuntimeError("REQUIRE_GIT=1 but Git was not found")


class DeploymentStopped(RuntimeError):
    pass


def fake_token(expiry, identity):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return "synthetic." + payload + "." + identity


def namespace(json_module=json):
    warnings = []
    def stop(message, code=1):
        raise DeploymentStopped(message)
    result = {
        "json": json_module, "base64": base64, "tempfile": tempfile,
        "os": SimpleNamespace(path=os.path, fsync=os.fsync, replace=os.replace, unlink=os.unlink),
        "time": SimpleNamespace(strftime=lambda fmt: "2026-11-13 09:10"),
        "die": stop, "warn": warnings.append,
        "_test_warnings": warnings,
    }
    exec(compile(ast.Module(body=FUNCTIONS, type_ignores=[]), str(SOURCE), "exec"), result)
    return result


class FakeGit:
    def __init__(self, staged=(), add_code=0, diff_code=None, index_code=0, commit_code=0,
                 stage_during_add=None, push_error=None):
        self.staged = set(staged)
        self.add_code, self.diff_code, self.index_code = add_code, diff_code, index_code
        self.commit_code, self.stage_during_add, self.push_error = commit_code, stage_during_add, push_error
        self.calls, self.commits, self.pushes = [], [], []

    def __call__(self, arguments, directory, check=True):
        arguments = list(arguments)
        self.calls.append((arguments, check))
        if arguments[:3] == ["diff", "--cached", "--name-only"]:
            code = self.index_code
            output = "\0".join(sorted(self.staged)) + ("\0" if self.staged else "")
        elif arguments[0] == "add":
            code, output = self.add_code, ""
            if code == 0:
                self.staged.add(STORE_NAME)
                if self.stage_during_add:
                    self.staged.add(self.stage_during_add)
        elif arguments[:3] == ["diff", "--cached", "--quiet"]:
            if self.diff_code is not None:
                code = self.diff_code
            else:
                considered = self.staged & {STORE_NAME} if "--" in arguments else self.staged
                code = int(bool(considered))
            output = ""
        elif "commit" in arguments:
            code, output = self.commit_code, ""
            if code == 0:
                selected = self.staged & {STORE_NAME} if "--only" in arguments else self.staged.copy()
                self.commits.append(sorted(selected))
                self.staged.difference_update(selected)
        else:
            raise AssertionError("Unexpected fake Git command: " + repr(arguments))
        if check and code:
            raise DeploymentStopped("synthetic Git command failed")
        return SimpleNamespace(returncode=code, stdout=output, stderr="synthetic Git error" if code else "")

    def push(self, directory):
        self.pushes.append("synthetic push only")
        if self.push_error:
            raise self.push_error

    def mutations(self):
        return [arguments for arguments, _ in self.calls if arguments[0] == "add" or "commit" in arguments]


class MergeAccountTests(unittest.TestCase):
    def setUp(self):
        self.ns = namespace()
        self.merge = self.ns["merge_account_into_store"]
        self.old_at = fake_token(1900000100, "saved-at")
        self.old_rt = fake_token(2000000000, "saved-rotated-rt")
        self.original = {
            "synthetic-a": {"access_token": self.old_at, "refresh_token": self.old_rt,
                            "updated": "2026-11-12 09:10", "note": "keep synthetic metadata"},
            "synthetic-b": {"access_token": "synthetic-other-at", "refresh_token": "synthetic-other-rt",
                            "updated": "2026-11-10 09:10"},
        }
        self.original_snapshot = json.loads(json.dumps(self.original))

    def check_preserved(self, access, refresh):
        merged, status = self.merge(self.original, "synthetic-a", access, refresh)
        self.assertEqual(status, "kept")
        self.assertEqual(merged, self.original_snapshot)
        self.assertEqual(self.original, self.original_snapshot)

    def test_different_rt_equal_expiry_keeps_saved_rotation(self):
        self.check_preserved(fake_token(1900000100, "local-old-at"), fake_token(2000000000, "local-old-rt"))

    def test_different_rt_later_expiry_still_requires_explicit_replacement(self):
        self.check_preserved(fake_token(2100000000, "local-at"), fake_token(2200000000, "local-rt"))

    def test_different_rt_unknown_expiry_keeps_saved_rotation(self):
        original = {"synthetic-a": {"access_token": "synthetic-saved-at", "refresh_token": "synthetic-saved-rt"}}
        merged, status = self.merge(original, "synthetic-a", "synthetic-local-at", "synthetic-local-rt")
        self.assertEqual((merged, status), (original, "kept"))

    def test_identical_credentials_preserve_timestamp_and_all_entries(self):
        merged, status = self.merge(self.original, "synthetic-a", self.old_at, self.old_rt)
        self.assertEqual(status, "same")
        self.assertEqual(merged, self.original_snapshot)

    def test_same_rt_accepts_strictly_later_at_and_preserves_metadata(self):
        access = fake_token(1900000200, "newer-at")
        merged, status = self.merge(self.original, "synthetic-a", access, self.old_rt)
        expected = dict(self.original_snapshot["synthetic-a"], access_token=access, updated="2026-11-13 09:10")
        self.assertEqual(status, "updated")
        self.assertEqual(merged["synthetic-a"], expected)
        self.assertEqual(merged["synthetic-b"], self.original_snapshot["synthetic-b"])
        self.assertEqual(self.original, self.original_snapshot)

    def test_same_long_lived_rt_cannot_mask_an_older_equal_or_unknown_at(self):
        for access in (fake_token(1900000000, "older-at"), fake_token(1900000100, "equal-exp-at"), "synthetic-opaque-at", ""):
            with self.subTest(access_case=access.rsplit(".", 1)[-1]):
                self.check_preserved(access, self.old_rt)

    def test_same_rt_and_unknown_existing_at_does_not_claim_newer_at(self):
        original = {"synthetic-a": {"access_token": "synthetic-unknown-at", "refresh_token": self.old_rt}}
        merged, status = self.merge(original, "synthetic-a", fake_token(2100000000, "new-at"), self.old_rt)
        self.assertEqual((merged, status), (original, "kept"))

    def test_same_rt_can_fill_empty_at_with_parseable_at(self):
        original = {"synthetic-a": {"access_token": "", "refresh_token": self.old_rt, "note": "synthetic"}}
        merged, status = self.merge(original, "synthetic-a", self.old_at, self.old_rt)
        self.assertEqual(status, "updated")
        self.assertEqual(merged["synthetic-a"]["access_token"], self.old_at)
        self.assertEqual(merged["synthetic-a"]["refresh_token"], self.old_rt)
        self.assertEqual(merged["synthetic-a"]["note"], "synthetic")
        self.assertEqual(original["synthetic-a"]["access_token"], "")

    def test_new_account_does_not_change_existing_users(self):
        merged, status = self.merge(self.original, "synthetic-c", "synthetic-new-at", "synthetic-new-rt")
        self.assertEqual(status, "new")
        self.assertEqual({key: merged[key] for key in self.original}, self.original_snapshot)
        self.assertEqual(merged["synthetic-c"]["refresh_token"], "synthetic-new-rt")
        self.assertEqual(self.original, self.original_snapshot)

    def test_missing_rt_can_be_seeded_without_replacing_existing_at_or_metadata(self):
        original = {"synthetic-a": {"access_token": self.old_at, "refresh_token": "", "note": "synthetic"}}
        merged, status = self.merge(original, "synthetic-a", "synthetic-local-at", "synthetic-seed-rt")
        self.assertEqual(status, "updated")
        self.assertEqual(merged["synthetic-a"]["refresh_token"], "synthetic-seed-rt")
        self.assertEqual(merged["synthetic-a"]["access_token"], self.old_at)
        self.assertEqual(merged["synthetic-a"]["note"], "synthetic")
        self.assertEqual(original["synthetic-a"]["refresh_token"], "")


class CommitStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="deployer-state-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / STORE_NAME
        self.original = {"synthetic-用户": {"refresh_token": "synthetic-saved-rt", "access_token": "synthetic-saved-at"}}
        self.replacement = {"synthetic-用户": {"refresh_token": "synthetic-new-rt", "access_token": "synthetic-new-at"}}
        self.path.write_text(json.dumps(self.original, ensure_ascii=False), encoding="utf-8")
        self.original_bytes = self.path.read_bytes()
        self.ns = namespace()
        self.fake = self.attach_git(FakeGit())
        redirect = contextlib.redirect_stdout(io.StringIO())
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def attach_git(self, fake):
        self.ns["git"], self.ns["push"] = fake, fake.push
        return fake

    def commit(self, store=None):
        return self.ns["commit_store"](str(self.folder), self.replacement if store is None else store, "synthetic state update")

    def assert_clean_candidates(self):
        self.assertEqual(set(self.folder.iterdir()), {self.path})

    def test_serialization_failure_leaves_old_store_and_does_not_call_git(self):
        with self.assertRaises(TypeError):
            self.commit({"synthetic-invalid": object()})
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_mid_write_failure_leaves_old_store_and_does_not_call_git(self):
        def fail_write(store, handle, **options):
            handle.write('{"synthetic-partial":')
            raise OSError("synthetic disk full")
        self.ns["json"] = SimpleNamespace(dump=fail_write)
        with self.assertRaisesRegex(OSError, "synthetic disk full"):
            self.commit()
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_fsync_failure_leaves_old_store_and_does_not_call_git(self):
        self.ns["os"].fsync = Mock(side_effect=OSError("synthetic fsync failure"))
        with self.assertRaisesRegex(OSError, "synthetic fsync failure"):
            self.commit()
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_replace_failure_leaves_old_store_and_does_not_mutate_git(self):
        self.ns["os"].replace = Mock(side_effect=OSError("synthetic replace failure"))
        with self.assertRaisesRegex(OSError, "synthetic replace failure"):
            self.commit()
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(self.fake.mutations(), [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_unrelated_staged_files_are_refused_before_replacing_store(self):
        self.fake = self.attach_git(FakeGit(staged={"synthetic-unrelated.txt", "nested/synthetic\nname.txt"}))
        staged = self.fake.staged.copy()
        with self.assertRaises(DeploymentStopped):
            self.commit()
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(self.fake.staged, staged)
        self.assertEqual(self.fake.mutations(), [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_failed_index_inspection_preserves_original_and_index(self):
        self.fake = self.attach_git(FakeGit(index_code=128))
        with self.assertRaises(DeploymentStopped):
            self.commit()
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(self.fake.mutations(), [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_add_failure_stops_before_commit_or_push_and_reports_local_save(self):
        self.fake = self.attach_git(FakeGit(add_code=128))
        with self.assertRaises(DeploymentStopped) as caught:
            self.commit()
        self.assertIn("本地", str(caught.exception))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.replacement)
        self.assertEqual(self.fake.commits, [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_diff_error_is_not_treated_as_pending_change(self):
        self.fake = self.attach_git(FakeGit(diff_code=128))
        with self.assertRaises(DeploymentStopped) as caught:
            self.commit()
        self.assertIn("本地", str(caught.exception))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.replacement)
        self.assertEqual(self.fake.commits, [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()

    def test_commit_failure_does_not_push_or_claim_rollback(self):
        self.fake = self.attach_git(FakeGit(commit_code=1))
        with self.assertRaises(DeploymentStopped) as caught:
            self.commit()
        self.assertIn("本地", str(caught.exception))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.replacement)
        self.assertEqual(self.fake.pushes, [])

    def test_push_failure_preserves_error_and_reports_local_commit(self):
        self.fake = self.attach_git(FakeGit(push_error=RuntimeError("synthetic push failure")))
        with self.assertRaisesRegex(RuntimeError, "synthetic push failure"):
            self.commit()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.replacement)
        self.assertEqual(self.fake.commits, [[STORE_NAME]])
        self.assertTrue(any("本地" in warning and "推送" in warning for warning in self.ns["_test_warnings"]))

    def test_success_commits_only_state_even_if_other_file_is_staged_later(self):
        self.fake = self.attach_git(FakeGit(stage_during_add="synthetic-concurrent.txt"))
        self.assertIs(self.commit(), True)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.replacement)
        self.assertEqual(self.fake.commits, [[STORE_NAME]])
        self.assertEqual(self.fake.staged, {"synthetic-concurrent.txt"})
        self.assertEqual(len(self.fake.pushes), 1)
        self.assert_clean_candidates()

    def test_only_target_pre_staged_is_allowed(self):
        self.fake = self.attach_git(FakeGit(staged={STORE_NAME}))
        self.assertIs(self.commit(), True)
        self.assertEqual(self.fake.commits, [[STORE_NAME]])
        self.assert_clean_candidates()

    def test_no_target_diff_returns_false_without_commit_or_push(self):
        self.fake = self.attach_git(FakeGit(diff_code=0))
        self.assertIs(self.commit(self.original), False)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.original)
        self.assertEqual(self.fake.commits, [])
        self.assertEqual(self.fake.pushes, [])
        self.assert_clean_candidates()


@unittest.skipUnless(GIT, "Real Git boundary checks require an existing Git executable")
class RealGitFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="deployer-git-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.repository = self.folder / "fixture"
        self.repository.mkdir()
        self.hooks = self.folder / "empty-hooks"
        self.hooks.mkdir()
        # A minimal environment and empty HOME prevent hooks, signing, or a
        # user's Git configuration from affecting this disposable repository.
        self.environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
        self.environment.update({
            "HOME": str(self.folder), "USERPROFILE": str(self.folder),
            "XDG_CONFIG_HOME": str(self.folder), "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "0",
            "TEMP": str(self.folder), "TMP": str(self.folder),
        })
        self.raw_git(["init", "--quiet"])
        self.path = self.repository / STORE_NAME
        self.other = self.repository / "synthetic-other.txt"
        self.original = {"synthetic-user": {"refresh_token": "synthetic-original-rt"}}
        self.replacement = {"synthetic-user": {"refresh_token": "synthetic-new-rt"}}
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        self.other.write_text("synthetic original\n", encoding="utf-8")
        self.raw_git(["add", "--", STORE_NAME, self.other.name])
        self.raw_git(["commit", "--quiet", "-m", "synthetic fixture baseline"])
        self.ns = namespace()
        self.pushes = []
        self.ns["push"] = lambda directory: self.pushes.append("synthetic push only")
        self.ns["git"] = self.fixture_git

    def raw_git(self, arguments):
        prefix = [GIT, "-c", "user.name=Synthetic Fixture", "-c", "user.email=synthetic@example.invalid",
                  "-c", "commit.gpgSign=false", "-c", "core.autocrlf=false",
                  "-c", "core.hooksPath=" + str(self.hooks), "-c", "init.defaultBranch=synthetic-fixture"]
        if any(argument in {"push", "fetch", "pull", "clone", "remote", "ls-remote"} for argument in arguments):
            raise AssertionError("Network-capable Git commands are prohibited in this fixture")
        return subprocess.run(prefix + list(arguments), cwd=self.repository, env=self.environment,
                              check=True, capture_output=True, text=True, encoding="utf-8")

    def fixture_git(self, arguments, directory, check=True):
        self.assertEqual(Path(directory).resolve(), self.repository.resolve())
        try:
            return self.raw_git(arguments)
        except subprocess.CalledProcessError as error:
            if check:
                raise DeploymentStopped("synthetic fixture Git failure") from error
            return SimpleNamespace(returncode=error.returncode, stdout=error.stdout, stderr=error.stderr)

    def commit(self):
        return self.ns["commit_store"](str(self.repository), self.replacement, "synthetic state update")

    def test_real_git_refuses_unrelated_index_without_changing_store(self):
        self.other.write_text("synthetic staged change\n", encoding="utf-8")
        self.raw_git(["add", "--", self.other.name])
        original_bytes = self.path.read_bytes()
        original_index = self.raw_git(["ls-files", "--stage", "-z"]).stdout
        original_head = self.raw_git(["rev-parse", "HEAD"]).stdout
        with self.assertRaises(DeploymentStopped):
            self.commit()
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(self.raw_git(["ls-files", "--stage", "-z"]).stdout, original_index)
        self.assertEqual(self.raw_git(["rev-parse", "HEAD"]).stdout, original_head)
        self.assertEqual(self.pushes, [])
        self.assertFalse(list(self.repository.glob(".wb-refresh-*")))

    def test_real_git_commit_only_leaves_later_unrelated_staging_untouched(self):
        injected = []
        def git_with_concurrent_stage(arguments, directory, check=True):
            result = self.fixture_git(arguments, directory, check)
            if arguments[0] == "add" and STORE_NAME in arguments and not injected:
                self.other.write_text("synthetic later staging\n", encoding="utf-8")
                self.raw_git(["add", "--", self.other.name])
                injected.append(True)
            return result
        self.ns["git"] = git_with_concurrent_stage
        self.assertIs(self.commit(), True)
        self.assertEqual(self.raw_git(["show", "--format=", "--name-only", "HEAD"]).stdout.splitlines(), [STORE_NAME])
        self.assertEqual(self.raw_git(["diff", "--cached", "--name-only"]).stdout.splitlines(), [self.other.name])
        self.assertEqual(self.raw_git(["show", "HEAD:" + self.other.name]).stdout, "synthetic original\n")
        self.assertEqual(json.loads(self.raw_git(["show", "HEAD:" + STORE_NAME]).stdout), self.replacement)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.replacement)
        self.assertEqual(self.pushes, ["synthetic push only"])
        self.assertFalse(list(self.repository.glob(".wb-refresh-*")))


if __name__ == "__main__":
    unittest.main()
