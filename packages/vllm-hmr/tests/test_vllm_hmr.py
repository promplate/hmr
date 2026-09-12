"""Focused tests for the `vllm-hmr` CLI boundary. No real vLLM process is started."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import vllm_hmr
import vllm_hmr.shim as vllm_hmr_shim
import vllm_hmr.source as vllm_hmr_source
from vllm_hmr.runtime import scope

FAKE_VLLM = "/fake/bin/vllm"
OVERRIDE_RUNTIME = "vllm_hmr.shim:install_from_env"  # importable and callable, and not the packaged default: the wrapper preflights the spec, so a fictional one is a usage error


def which_stub(name: str) -> str | None:
    return FAKE_VLLM if name == "vllm" else None


def make_source_tree(root: Path) -> Path:
    """A tree with exactly the files the runtime's verified scope needs."""
    for relative in scope.REACTIVE_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")
    return root


class SourceTreeTestCase(unittest.TestCase):
    """Most CLI paths need a valid source root, since HMR is on by default."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = make_source_tree(Path(self.tmp.name) / "src")

    def base_env(self, **extra: str) -> dict[str, str]:
        return {"HMR_VLLM_SOURCE_ROOT": str(self.source), **extra}


class ArgvForwardingTests(unittest.TestCase):
    def test_plain_vllm_argv_is_forwarded_verbatim(self):
        argv = ["serve", "facebook/opt-125m", "--port", "18080", "--enforce-eager"]
        options, print_env, forwarded = vllm_hmr.split_argv(argv)
        self.assertEqual(forwarded, argv)
        self.assertEqual(options, {})
        self.assertFalse(print_env)

    def test_hmr_options_are_stripped_and_order_is_preserved(self):
        options, _, forwarded = vllm_hmr.split_argv(["--hmr-source-root", "/src", "serve", "m", "--port", "1", "--", "--hmr-manifest"])
        self.assertEqual(forwarded, ["serve", "m", "--port", "1", "--", "--hmr-manifest"])
        self.assertEqual(options, {"HMR_VLLM_SOURCE_ROOT": "/src"})

    def test_inline_equals_form(self):
        options, _, forwarded = vllm_hmr.split_argv(["--hmr-runtime=pkg.mod:entry", "serve", "m"])
        self.assertEqual(options, {"HMR_VLLM_RUNTIME": "pkg.mod:entry"})
        self.assertEqual(forwarded, ["serve", "m"])

    def test_disabled_flag_is_parsed_and_not_forwarded(self):
        options, _, forwarded = vllm_hmr.split_argv(["--hmr-disabled", "serve", "m"])
        self.assertEqual(options, {"HMR_VLLM_DISABLED": "1"})
        self.assertEqual(forwarded, ["serve", "m"])

    def test_print_env_flag_is_not_forwarded(self):
        _, print_env, forwarded = vllm_hmr.split_argv(["--hmr-print-env", "serve", "m"])
        self.assertTrue(print_env)
        self.assertEqual(forwarded, ["serve", "m"])

    def test_missing_value_and_unknown_option_are_usage_errors(self):
        with self.assertRaises(vllm_hmr.UsageError):
            vllm_hmr.split_argv(["serve", "m", "--hmr-source-root"])
        with self.assertRaises(vllm_hmr.UsageError):
            vllm_hmr.split_argv(["--hmr-source-root=", "serve", "m"])
        with self.assertRaises(vllm_hmr.UsageError):
            vllm_hmr.split_argv(["--hmr-nope", "serve", "m"])


class SubcommandDetectionTests(unittest.TestCase):
    def test_serve_is_detected_after_leading_flags(self):
        self.assertTrue(vllm_hmr.is_serve(["serve", "m"]))
        self.assertTrue(vllm_hmr.is_serve(["--quiet", "serve", "m"]))

    def test_other_subcommands_are_not_serve(self):
        for argv in (["chat"], ["complete"], ["bench", "latency"], ["--help"], []):
            self.assertFalse(vllm_hmr.is_serve(argv), argv)

    def test_serve_after_a_double_dash_is_not_our_subcommand(self):
        self.assertFalse(vllm_hmr.is_serve(["--", "serve", "m"]))


class FlagInjectionTests(unittest.TestCase):
    def test_both_flags_are_appended_for_serve(self):
        argv = vllm_hmr.inject_vllm_flags(["serve", "m", "--port", "1"])
        self.assertEqual(argv, ["serve", "m", "--port", "1", "--middleware", vllm_hmr.MIDDLEWARE, "--worker-extension-cls", vllm_hmr.WORKER_EXTENSION])

    def test_user_middleware_is_not_duplicated(self):
        argv = vllm_hmr.inject_vllm_flags(["serve", "m", "--middleware", "mine.Mw"])
        self.assertEqual(argv.count("--middleware"), 1)
        self.assertIn("mine.Mw", argv)
        self.assertNotIn(vllm_hmr.MIDDLEWARE, argv)
        self.assertIn(vllm_hmr.WORKER_EXTENSION, argv)

    def test_user_worker_extension_in_equals_form_is_not_duplicated(self):
        argv = vllm_hmr.inject_vllm_flags(["serve", "m", "--worker-extension-cls=mine.Ext"])
        self.assertEqual([arg for arg in argv if arg.startswith("--worker-extension-cls")], ["--worker-extension-cls=mine.Ext"])
        self.assertNotIn(vllm_hmr.WORKER_EXTENSION, argv)
        self.assertIn(vllm_hmr.MIDDLEWARE, argv)

    def test_both_user_flags_leave_argv_unchanged(self):
        argv = ["serve", "m", "--middleware", "mine.Mw", "--worker-extension-cls", "mine.Ext"]
        self.assertEqual(vllm_hmr.inject_vllm_flags(argv), argv)

    def test_injection_lands_before_a_double_dash(self):
        argv = vllm_hmr.inject_vllm_flags(["serve", "m", "--", "raw"])
        self.assertEqual(argv[-2:], ["--", "raw"])
        self.assertIn(vllm_hmr.MIDDLEWARE, argv[: argv.index("--")])


class EnvironmentTests(SourceTreeTestCase):
    def test_serve_enables_injection_with_the_packaged_runtime_by_default(self):
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source)}, {}, serve=True)
        self.assertEqual(env["HMR_VLLM_ENABLE"], "1")
        self.assertEqual(env["HMR_VLLM_RUNTIME"], vllm_hmr.DEFAULT_RUNTIME)
        self.assertEqual(env["HMR_VLLM_SOURCE_ROOT"], str(self.source))
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(vllm_hmr.SHIM_DIR))

    def test_explicit_runtime_overrides_the_default(self):
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source), "HMR_VLLM_RUNTIME": OVERRIDE_RUNTIME}, {}, serve=True)
        self.assertEqual(env["HMR_VLLM_RUNTIME"], OVERRIDE_RUNTIME)

    def test_non_serve_subcommand_is_left_alone(self):
        env = vllm_hmr.build_env({}, {"PATH": "/usr/bin"}, serve=False)
        self.assertEqual(env, {"PATH": "/usr/bin"})

    def test_disabled_skips_injection_entirely(self):
        env = vllm_hmr.build_env({"HMR_VLLM_DISABLED": "1"}, {}, serve=True)
        self.assertNotIn("HMR_VLLM_ENABLE", env)
        self.assertNotIn("HMR_VLLM_RUNTIME", env)
        self.assertNotIn("PYTHONPATH", env)

    def test_disabled_removes_an_activation_the_environment_already_carried(self):
        """`--hmr-disabled` has to undo an inherited activation, not just decline to add one.

        A base environment that already went through this wrapper (`run.sh`, a nested launch, an
        exported profile) arrives with `HMR_VLLM_ENABLE=1` and the shim on `PYTHONPATH`. Leaving
        those in place meant the exec'd interpreter installed HMR despite the opt-out.
        """
        activated = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source)}, {}, serve=True)
        env = vllm_hmr.build_env({"HMR_VLLM_DISABLED": "1"}, activated, serve=True)
        self.assertNotIn("HMR_VLLM_ENABLE", env)
        self.assertNotIn("PYTHONPATH", env)  # the shim was the only entry, and it was ours
        self.assertEqual(env["HMR_VLLM_RUNTIME"], vllm_hmr.DEFAULT_RUNTIME)  # inert without the gate, and documented: not ours to delete

    def test_deactivation_keeps_the_user_s_own_pythonpath_entries(self):
        base = {"HMR_VLLM_ENABLE": "1", "PYTHONPATH": os.pathsep.join(["/mine/first", str(vllm_hmr.SHIM_DIR), "/mine/second"])}
        env = vllm_hmr.build_env({"HMR_VLLM_DISABLED": "1"}, base, serve=True)
        self.assertEqual(env["PYTHONPATH"], os.pathsep.join(["/mine/first", "/mine/second"]))
        self.assertNotIn("HMR_VLLM_ENABLE", env)

    def test_a_non_serve_subcommand_also_drops_an_inherited_activation(self):
        """Every other subcommand is documented as untouched, which an inherited activation broke too."""
        activated = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source)}, {}, serve=True)
        env = vllm_hmr.build_env({}, activated, serve=False)
        self.assertNotIn("HMR_VLLM_ENABLE", env)
        self.assertNotIn("PYTHONPATH", env)

    def test_any_non_empty_disabled_value_disables_both_halves_of_the_wrapper(self):
        """`HMR_VLLM_DISABLED=0` must not mean "no flags" for argv and "install anyway" for the environment."""
        for value in ("0", "false", "no"):
            _, argv, env, _ = vllm_hmr.build_exec(["serve", "m"], self.base_env(HMR_VLLM_DISABLED=value), which=which_stub)
            self.assertEqual(argv, ["vllm", "serve", "m"], value)
            self.assertNotIn("HMR_VLLM_ENABLE", env, value)
            self.assertNotIn("HMR_VLLM_DISABLED", env, value)  # our marker never reaches vLLM

    def test_an_empty_disabled_value_is_not_an_opt_out(self):
        _, argv, env, _ = vllm_hmr.build_exec(["serve", "m"], self.base_env(HMR_VLLM_DISABLED=""), which=which_stub)
        self.assertIn(vllm_hmr.MIDDLEWARE, argv)
        self.assertEqual(env["HMR_VLLM_ENABLE"], "1")

    def test_a_malformed_runtime_spec_is_a_usage_error(self):
        """`site` only prints a line when `sitecustomize` fails, so a bad spec has to fail in the wrapper."""
        with self.assertRaises(vllm_hmr.UsageError) as caught:
            vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source), "HMR_VLLM_RUNTIME": "pkg.mod"}, {}, serve=True)
        self.assertIn("module:callable", str(caught.exception))

    def test_a_well_formed_but_unloadable_runtime_spec_is_a_usage_error(self):
        """Parsing `module:callable` proves nothing about the spec resolving.

        A `sitecustomize` that raises costs one stderr line inside vLLM's startup output and then
        HMR is simply absent, so the wrapper resolves the spec itself: unimportable module, missing
        attribute, and non-callable target are all rejected before `execve`.
        """
        for spec, expected in (
            ("vllm_hmr_definitely_not_a_module:entry", "ModuleNotFoundError"),
            ("vllm_hmr.shim:no_such_attribute", "AttributeError"),
            ("vllm_hmr.shim:SKIP_MARKER", "not callable"),
        ):
            with self.subTest(spec=spec):
                with self.assertRaises(vllm_hmr.UsageError) as caught:
                    vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source), "HMR_VLLM_RUNTIME": spec}, {}, serve=True)
                self.assertIn(expected, str(caught.exception))
                self.assertIn(spec, str(caught.exception))

    def test_the_packaged_default_runtime_passes_its_own_preflight(self):
        """The preflight must not reject the default: it also proves the runtime's own imports resolve here."""
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source)}, {}, serve=True)
        self.assertEqual(env["HMR_VLLM_RUNTIME"], vllm_hmr.DEFAULT_RUNTIME)

    def test_an_overridden_runtime_owns_its_own_scope(self):
        """The packaged two-file scope belongs to the packaged runtime, not to every runtime."""
        other = Path(self.tmp.name) / "own-scope"
        (other / "src").mkdir(parents=True)
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(other), "HMR_VLLM_RUNTIME": OVERRIDE_RUNTIME}, {}, serve=True)
        self.assertEqual(env["HMR_VLLM_SOURCE_ROOT"], str(other.resolve()))
        self.assertEqual(env["HMR_VLLM_ENABLE"], "1")

    def test_an_overridden_runtime_still_needs_an_existing_source_root(self):
        with self.assertRaises(vllm_hmr.UsageError) as caught:
            vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(Path(self.tmp.name) / "absent"), "HMR_VLLM_RUNTIME": OVERRIDE_RUNTIME}, {}, serve=True)
        self.assertIn("does not exist", str(caught.exception))

    def test_site_packages_vllm_without_source_root_is_a_usage_error(self):
        """The wrapper never copies source or guesses a root: it says what to pass."""
        with self.assertRaises(vllm_hmr.UsageError) as caught:
            vllm_hmr.build_env({}, {}, serve=True)
        self.assertIn("--hmr-source-root", str(caught.exception))

    def test_source_root_without_the_verified_target_is_a_usage_error(self):
        wrong = Path(self.tmp.name) / "wrong"
        (wrong / "vllm").mkdir(parents=True)
        with self.assertRaises(vllm_hmr.UsageError) as caught:
            vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(wrong)}, {}, serve=True)
        self.assertIn(scope.TARGET, str(caught.exception))

    def test_source_root_missing_the_dependent_is_a_usage_error(self):
        partial = Path(self.tmp.name) / "partial"
        target = partial / scope.TARGET
        target.parent.mkdir(parents=True)
        target.write_text("# target\n", encoding="utf-8")
        with self.assertRaises(vllm_hmr.UsageError) as caught:
            vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(partial)}, {}, serve=True)
        self.assertIn(scope.DEPENDENT_PATH, str(caught.exception))

    def test_a_broken_manifest_is_a_usage_error_not_a_silently_disabled_launch(self):
        """`site` reports a failing `sitecustomize` as one stderr line and starts vLLM anyway.

        A manifest first read there therefore turns "bad manifest" into "server runs without HMR",
        buried in vLLM's startup output. Same reason `check_runtime` preflights the runtime spec.
        """
        for name, text in (("malformed", "{not json"), ("shape", json.dumps(scope.build_manifest(self.source).as_dict() | {"files": [None]}))):
            path = Path(self.tmp.name) / f"cli-{name}.json"
            path.write_text(text, encoding="utf-8")
            with self.subTest(manifest=name), self.assertRaises(vllm_hmr.UsageError):
                vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source), "HMR_VLLM_MANIFEST": str(path)}, {}, serve=True)

    def test_a_valid_manifest_passes_the_cli_preflight_and_is_forwarded(self):
        path = scope.write_manifest(scope.build_manifest(self.source), Path(self.tmp.name) / "cli-good.json")
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source), "HMR_VLLM_MANIFEST": str(path)}, {}, serve=True)
        self.assertEqual(env["HMR_VLLM_MANIFEST"], str(path))

    def test_an_overridden_runtime_owns_its_own_manifest(self):
        """Only the packaged runtime reads `HMR_VLLM_MANIFEST` through this scope, so only it is preflighted."""
        path = Path(self.tmp.name) / "foreign.json"
        path.write_text("{not our schema", encoding="utf-8")
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source), "HMR_VLLM_MANIFEST": str(path), "HMR_VLLM_RUNTIME": OVERRIDE_RUNTIME}, {}, serve=True)
        self.assertEqual(env["HMR_VLLM_MANIFEST"], str(path))

    def test_relative_source_root_is_absolutised(self):
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": os.path.relpath(self.source)}, {}, serve=True)
        self.assertEqual(env["HMR_VLLM_SOURCE_ROOT"], str(self.source.resolve()))

    def test_shim_is_not_duplicated_on_pythonpath(self):
        first = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source)}, {}, serve=True)
        second = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(self.source)}, first, serve=True)
        self.assertEqual(second["PYTHONPATH"], first["PYTHONPATH"])

    def test_cli_options_override_inherited_variables(self):
        other = make_source_tree(Path(self.tmp.name) / "other")
        env = vllm_hmr.build_env({"HMR_VLLM_SOURCE_ROOT": str(other)}, {"HMR_VLLM_SOURCE_ROOT": str(self.source)}, serve=True)
        self.assertEqual(env["HMR_VLLM_SOURCE_ROOT"], str(other))

    def test_inherited_source_root_alone_enables_injection(self):
        env = vllm_hmr.build_env({}, self.base_env(), serve=True)
        self.assertEqual(env["HMR_VLLM_ENABLE"], "1")


class ExecTests(SourceTreeTestCase):
    def test_serve_execs_official_vllm_with_injected_flags(self):
        executable, argv, env, print_env = vllm_hmr.build_exec(["serve", "m", "--port", "1"], self.base_env(), which=which_stub)
        self.assertEqual(executable, FAKE_VLLM)
        # argv[0] stays "vllm" so vLLM's own usage strings and prog name are unchanged.
        self.assertEqual(argv[:5], ["vllm", "serve", "m", "--port", "1"])
        self.assertEqual(argv[5:], ["--middleware", vllm_hmr.MIDDLEWARE, "--worker-extension-cls", vllm_hmr.WORKER_EXTENSION])
        self.assertEqual(env["HMR_VLLM_ENABLE"], "1")
        self.assertFalse(print_env)

    def test_non_serve_subcommand_gets_neither_flags_nor_env(self):
        _, argv, env, _ = vllm_hmr.build_exec(["chat", "--url", "http://localhost:8000"], self.base_env(), which=which_stub)
        self.assertEqual(argv, ["vllm", "chat", "--url", "http://localhost:8000"])
        self.assertNotIn("HMR_VLLM_ENABLE", env)
        self.assertNotIn("PYTHONPATH", env)

    def test_disabled_serve_is_a_plain_vllm_launch(self):
        _, argv, env, _ = vllm_hmr.build_exec(["--hmr-disabled", "serve", "m"], self.base_env(), which=which_stub)
        self.assertEqual(argv, ["vllm", "serve", "m"])
        self.assertNotIn("HMR_VLLM_ENABLE", env)

    def test_disabled_via_environment_also_suppresses_injection(self):
        _, argv, env, _ = vllm_hmr.build_exec(["serve", "m"], self.base_env(HMR_VLLM_DISABLED="1"), which=which_stub)
        self.assertEqual(argv, ["vllm", "serve", "m"])
        self.assertNotIn("HMR_VLLM_ENABLE", env)

    def test_double_dash_passthrough_is_preserved(self):
        _, argv, _, _ = vllm_hmr.build_exec(["serve", "m", "--", "--middleware", "raw"], self.base_env(), which=which_stub)
        tail = argv[argv.index("--") :]
        self.assertEqual(tail, ["--", "--middleware", "raw"])
        # the user's post-`--` tokens must not be read as "middleware already given"
        self.assertIn(vllm_hmr.MIDDLEWARE, argv[: argv.index("--")])

    def test_missing_vllm_is_a_usage_error(self):
        with self.assertRaises(vllm_hmr.UsageError):
            vllm_hmr.build_exec(["serve", "m"], self.base_env(), which=lambda _: None)

    def test_main_execs_once_with_computed_argv_and_env(self):
        calls: list[tuple] = []

        def mock_execve(*args):
            calls.append(args)
            raise SystemExit(0)

        import shutil as shutil_module

        original_execve, os.execve = os.execve, mock_execve
        original_which, shutil_module.which = shutil_module.which, which_stub
        original_environ = os.environ.copy()
        os.environ["HMR_VLLM_SOURCE_ROOT"] = str(self.source)
        try:
            with self.assertRaises(SystemExit) as caught:
                vllm_hmr.main(["serve", "m"])
            self.assertEqual(caught.exception.code, 0)
        finally:
            os.execve = original_execve
            shutil_module.which = original_which
            os.environ.clear()
            os.environ.update(original_environ)
        self.assertEqual(len(calls), 1)
        executable, argv, env = calls[0]
        self.assertEqual(executable, FAKE_VLLM)
        self.assertEqual(argv[:3], ["vllm", "serve", "m"])
        self.assertEqual(env["HMR_VLLM_ENABLE"], "1")
        self.assertEqual(env["HMR_VLLM_RUNTIME"], vllm_hmr.DEFAULT_RUNTIME)

    def test_usage_error_exits_two_without_execing(self):
        err = io.StringIO()
        import shutil as shutil_module

        original_which, shutil_module.which = shutil_module.which, which_stub
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as caught:
                vllm_hmr.main(["serve", "m", "--hmr-source-root", str(Path(self.tmp.name) / "absent")])
        finally:
            shutil_module.which = original_which
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("vllm-hmr:", err.getvalue())

    def test_bare_invocation_prints_help_and_fails(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            vllm_hmr.main([])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("Usage: vllm-hmr", out.getvalue())

    def test_explicit_help_exits_zero(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            vllm_hmr.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("Not implemented", out.getvalue())  # the capability boundary must stay in --help

    def test_example_probe_argv_keeps_its_own_worker_extension(self):
        """`examples/vllm-cpu-hmr` brings only a worker extension; the packaged runtime and middleware are what it tests."""
        vllm_argv = ["serve", "facebook/opt-125m", "--enforce-eager", "--worker-extension-cls", "hmr_vllm_probe.worker.HMRProbeWorkerExtension"]
        _, argv, env, _ = vllm_hmr.build_exec(["--hmr-source-root", str(self.source), *vllm_argv], {}, which=which_stub)
        self.assertEqual(argv[1:], [*vllm_argv, "--middleware", vllm_hmr.MIDDLEWARE])  # the CLI must still supply the middleware it owns
        self.assertEqual(env["HMR_VLLM_RUNTIME"], vllm_hmr.DEFAULT_RUNTIME)

    def test_print_env_reports_exec_argv_without_execing(self):
        out = io.StringIO()
        import shutil as shutil_module

        original_which, shutil_module.which = shutil_module.which, which_stub
        try:
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
                vllm_hmr.main(["--hmr-print-env", "--hmr-source-root", str(self.source), "serve", "m"])
            self.assertEqual(caught.exception.code, 0)
        finally:
            shutil_module.which = original_which
        printed = out.getvalue()
        self.assertIn(f"HMR_VLLM_SOURCE_ROOT={self.source}", printed)
        self.assertIn(f"HMR_VLLM_RUNTIME={vllm_hmr.DEFAULT_RUNTIME}", printed)
        self.assertIn(f"{FAKE_VLLM} serve m --middleware", printed)


class SourceDetectionTests(SourceTreeTestCase):
    def test_explicit_root_wins_without_importing_vllm(self):
        self.assertEqual(vllm_hmr_source.resolve_source_root(str(self.source)), self.source.resolve())

    def test_installed_vllm_is_not_treated_as_a_source_checkout(self):
        """A site-packages layout must not be auto-detected: editing it is not a real workflow."""
        fake = self.source / "site-packages" / "vllm"
        fake.mkdir(parents=True)
        (fake / "__init__.py").write_text("", encoding="utf-8")
        module = types.ModuleType("vllm")
        module.__file__ = str(fake / "__init__.py")
        original = sys.modules.get("vllm")
        sys.modules["vllm"] = module
        try:
            self.assertIsNone(vllm_hmr_source.find_editable_vllm_root())
        finally:
            sys.modules.pop("vllm", None)
            if original is not None:
                sys.modules["vllm"] = original

    def test_editable_checkout_is_detected(self):
        module = types.ModuleType("vllm")
        module.__file__ = str(self.source / "vllm" / "__init__.py")
        (self.source / "vllm" / "__init__.py").write_text("", encoding="utf-8")
        original = sys.modules.get("vllm")
        sys.modules["vllm"] = module
        try:
            self.assertEqual(vllm_hmr_source.find_editable_vllm_root(), self.source)
        finally:
            sys.modules.pop("vllm", None)
            if original is not None:
                sys.modules["vllm"] = original


class ScopeTests(SourceTreeTestCase):
    def test_default_scope_is_the_two_verified_files(self):
        self.assertEqual(scope.REACTIVE_PATHS, (scope.TARGET, scope.DEPENDENT_PATH))
        self.assertEqual(scope.TARGET, "vllm/renderers/inputs/preprocess.py")
        self.assertEqual(scope.DEPENDENT_PATH, "vllm/v1/engine/async_llm.py")

    def test_generated_manifest_records_root_hashes_and_reactive_paths(self):
        manifest = scope.build_manifest(self.source)
        self.assertEqual(manifest.source_root, self.source.resolve())
        self.assertEqual(manifest.reactive_paths, scope.REACTIVE_PATHS)
        self.assertEqual(manifest.auto_paths, (scope.TARGET,))  # only the provider is auto-published
        self.assertEqual(manifest.forced_dependents, {scope.TARGET: (scope.DEPENDENT,)})
        self.assertEqual({item["path"] for item in manifest.files}, set(scope.REACTIVE_PATHS))
        for item in manifest.files:
            self.assertEqual(item["sha256"], scope.sha256(self.source / item["path"]))

    def test_manifest_round_trips_through_disk(self):
        path = scope.write_manifest(scope.build_manifest(self.source), Path(self.tmp.name) / "m.json")
        self.assertEqual(scope.load_manifest(path, self.source.resolve()).as_dict(), scope.build_manifest(self.source).as_dict())

    def test_stale_manifest_hash_fails_fast(self):
        path = scope.write_manifest(scope.build_manifest(self.source), Path(self.tmp.name) / "m.json")
        (self.source / scope.TARGET).write_text("# edited after the manifest was written\n", encoding="utf-8")
        with self.assertRaises(scope.ScopeError) as caught:
            scope.load_manifest(path, self.source.resolve())
        self.assertIn("hash mismatch", str(caught.exception))

    def test_manifest_for_another_source_root_is_rejected(self):
        path = scope.write_manifest(scope.build_manifest(self.source), Path(self.tmp.name) / "m.json")
        other = make_source_tree(Path(self.tmp.name) / "other")
        with self.assertRaises(scope.ScopeError) as caught:
            scope.load_manifest(path, other.resolve())
        self.assertIn("source_root", str(caught.exception))

    def test_manifest_cannot_widen_the_scope_through_auto_paths(self):
        raw = scope.build_manifest(self.source).as_dict()
        raw["auto_paths"].append("vllm/v1/core/sched/scheduler.py")
        path = Path(self.tmp.name) / "wide-auto.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(scope.ScopeError) as caught:
            scope.load_manifest(path, self.source.resolve())
        self.assertIn("verified scope", str(caught.exception))

    def test_manifest_cannot_force_re_execution_of_an_out_of_scope_module(self):
        """`forced_dependents` re-executes modules by name, so it is scope too."""
        raw = scope.build_manifest(self.source).as_dict()
        raw["forced_dependents"][scope.TARGET].append("vllm.v1.core.sched.scheduler")
        path = Path(self.tmp.name) / "wide-dependents.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(scope.ScopeError) as caught:
            scope.load_manifest(path, self.source.resolve())
        self.assertIn("vllm.v1.core.sched.scheduler", str(caught.exception))

    def test_manifest_cannot_widen_the_scope(self):
        raw = scope.build_manifest(self.source).as_dict()
        raw["reactive_paths"].append("vllm/v1/core/sched/scheduler.py")
        path = Path(self.tmp.name) / "wide.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(scope.ScopeError) as caught:
            scope.load_manifest(path, self.source.resolve())
        self.assertIn("verified scope", str(caught.exception))

    def test_manifest_cannot_narrow_the_scope_either(self):
        """The scope is fixed, so a manifest that omits part of it is as wrong as one that adds to it.

        Rejecting only additions accepted a manifest that watches nothing (`files: []`), one that
        never re-executes the dependent (`reactive_paths` without it), and one that publishes
        nothing (`auto_paths: []`). Each installs a runtime that looks healthy and serves stale
        code, which is worse than a startup error.
        """
        cases = {
            "files": lambda raw: raw.__setitem__("files", []),
            "reactive_paths": lambda raw: raw.__setitem__("reactive_paths", [scope.TARGET]),
            "auto_paths": lambda raw: raw.__setitem__("auto_paths", []),
            "forced_dependents": lambda raw: raw.__setitem__("forced_dependents", {}),
            "duplicate_files": lambda raw: raw["files"].append(raw["files"][0]),
        }
        for name, mutate in cases.items():
            raw = scope.build_manifest(self.source).as_dict()
            mutate(raw)
            path = Path(self.tmp.name) / f"narrow-{name}.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.subTest(field=name):
                with self.assertRaises(scope.ScopeError) as caught:
                    scope.load_manifest(path, self.source.resolve())
                self.assertIn("verified scope", str(caught.exception))

    def test_a_manifest_missing_required_fields_is_rejected(self):
        """An absent field is not a default: it is a manifest that never stated the scope."""
        for name, raw in (("empty", {}), ("schema-only", {"schema_version": scope.MANIFEST_SCHEMA_VERSION})):
            path = Path(self.tmp.name) / f"{name}.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.subTest(manifest=name), self.assertRaises(scope.ScopeError):
                scope.load_manifest(path, self.source.resolve())
        for field in ("source_root", "files", "reactive_paths", "auto_paths", "forced_dependents"):
            raw = scope.build_manifest(self.source).as_dict()
            del raw[field]
            path = Path(self.tmp.name) / f"without-{field}.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.subTest(missing=field):
                with self.assertRaises(scope.ScopeError) as caught:
                    scope.load_manifest(path, self.source.resolve())
                self.assertIn(field, str(caught.exception))

    def test_a_manifest_with_wrongly_shaped_fields_is_rejected(self):
        """Wrong shape must be a `ScopeError` like every other bad manifest, not an `AttributeError`.

        `files: [null]`, a non-list `reactive_paths`, or a `forced_dependents` value that is not a
        list used to raise out of the loader's own unpacking. That reaches `sitecustomize` as a
        crash rather than a rejected manifest, and reaches the CLI past its `ScopeError` handler.
        """
        cases = {
            "files_null_entry": lambda raw: raw["files"].__setitem__(0, None),
            "files_entry_is_a_string": lambda raw: raw["files"].__setitem__(0, scope.TARGET),
            "files_entry_path_null": lambda raw: raw["files"][0].__setitem__("path", None),
            "files_entry_sha_null": lambda raw: raw["files"][0].__setitem__("sha256", None),
            "files_is_an_object": lambda raw: raw.__setitem__("files", {"path": scope.TARGET}),
            "reactive_paths_is_a_string": lambda raw: raw.__setitem__("reactive_paths", scope.TARGET),
            "reactive_paths_is_an_int": lambda raw: raw.__setitem__("reactive_paths", 2),
            "auto_paths_null": lambda raw: raw.__setitem__("auto_paths", None),
            "auto_paths_entry_null": lambda raw: raw.__setitem__("auto_paths", [None]),
            "forced_dependents_is_a_list": lambda raw: raw.__setitem__("forced_dependents", []),
            "forced_dependents_value_is_a_string": lambda raw: raw["forced_dependents"].__setitem__(scope.TARGET, scope.DEPENDENT),
            "forced_dependents_value_null": lambda raw: raw["forced_dependents"].__setitem__(scope.TARGET, None),
            "source_root_is_an_int": lambda raw: raw.__setitem__("source_root", 2),
            "source_root_null": lambda raw: raw.__setitem__("source_root", None),
        }
        for name, mutate in cases.items():
            raw = scope.build_manifest(self.source).as_dict()
            mutate(raw)
            path = Path(self.tmp.name) / f"shape-{name}.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.subTest(case=name), self.assertRaises(scope.ScopeError):
                scope.load_manifest(path, self.source.resolve())

    def test_a_manifest_that_is_not_a_json_object_is_rejected(self):
        for name, text in (("list", "[]"), ("string", '"vllm"'), ("null", "null"), ("truncated", '{"schema_version": 1'), ("empty", "")):
            path = Path(self.tmp.name) / f"not-an-object-{name}.json"
            path.write_text(text, encoding="utf-8")
            with self.subTest(body=name), self.assertRaises(scope.ScopeError):
                scope.load_manifest(path, self.source.resolve())

    def test_an_unreadable_manifest_or_source_file_is_a_scope_error(self):
        with self.assertRaises(scope.ScopeError):
            scope.load_manifest(Path(self.tmp.name) / "absent.json", self.source.resolve())
        path = scope.write_manifest(scope.build_manifest(self.source), Path(self.tmp.name) / "m.json")
        target = self.source / scope.TARGET
        mode = target.stat().st_mode
        target.chmod(0)
        self.addCleanup(target.chmod, mode)
        if os.access(target, os.R_OK):
            self.skipTest("running as root, where chmod 0 does not make a file unreadable")
        with self.assertRaises(scope.ScopeError):
            scope.load_manifest(path, self.source.resolve())

    def test_unknown_schema_version_is_rejected(self):
        raw = scope.build_manifest(self.source).as_dict() | {"schema_version": 99}
        path = Path(self.tmp.name) / "v99.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(scope.ScopeError):
            scope.load_manifest(path, self.source.resolve())

    def test_syntax_preflight_rejects_a_half_written_file(self):
        broken = Path(self.tmp.name) / "broken.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        ok, error = scope.syntax_preflight(broken)
        self.assertFalse(ok)
        self.assertIn("SyntaxError", str(error))
        self.assertEqual(scope.syntax_preflight(self.source / scope.TARGET), (True, None))


class ShimTests(unittest.TestCase):
    def test_runtime_spec_parsing(self):
        self.assertEqual(vllm_hmr_shim.parse_runtime("pkg.mod:entry"), ("pkg.mod", "entry"))
        for bad in ("pkg.mod", ":entry", "pkg.mod:", ""):
            with self.assertRaises(ValueError):
                vllm_hmr_shim.parse_runtime(bad)

    def test_check_runtime_resolves_the_spec_without_calling_it(self):
        calls: list[int] = []
        module = types.ModuleType("vllm_hmr_preflight_probe")
        module.entry = lambda: calls.append(1)  # pyright: ignore[reportAttributeAccessIssue]
        module.not_callable = 42  # pyright: ignore[reportAttributeAccessIssue]
        sys.modules["vllm_hmr_preflight_probe"] = module
        self.addCleanup(lambda: sys.modules.pop("vllm_hmr_preflight_probe", None))
        vllm_hmr_shim.check_runtime("vllm_hmr_preflight_probe:entry")  # a resolvable spec is accepted by returning, so not raising is the assertion
        self.assertEqual(calls, [])  # a preflight that ran the runtime would install HMR in the wrapper, which is then replaced by `execve`
        for spec in ("vllm_hmr_preflight_probe:absent", "vllm_hmr_preflight_probe:not_callable", "vllm_hmr_no_such_module:entry", "not-a-spec"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                vllm_hmr_shim.check_runtime(spec)

    def test_load_runtime_rejects_a_non_callable_target(self):
        """`sitecustomize` would raise `TypeError` on the call, one line into vLLM's output; this is the same rejection, earlier."""
        with self.assertRaises(ValueError) as caught:
            vllm_hmr_shim.load_runtime("vllm_hmr.shim:SKIP_MARKER")
        self.assertIn("not callable", str(caught.exception))

    def test_should_install_requires_enable_and_runtime_and_no_skip(self):
        self.assertTrue(vllm_hmr_shim.should_install({"HMR_VLLM_ENABLE": "1", "HMR_VLLM_RUNTIME": "m:e"}))
        self.assertFalse(vllm_hmr_shim.should_install({"HMR_VLLM_RUNTIME": "m:e"}))
        self.assertFalse(vllm_hmr_shim.should_install({"HMR_VLLM_ENABLE": "1"}))
        self.assertFalse(vllm_hmr_shim.should_install({"HMR_VLLM_ENABLE": "1", "HMR_VLLM_RUNTIME": "m:e", "HMR_VLLM_SKIP": "1"}))

    def test_install_from_env_invokes_the_named_runtime(self):
        import types

        marker = object()
        module = types.ModuleType("vllm_hmr_fake_runtime")
        module.entry = lambda: marker  # pyright: ignore[reportAttributeAccessIssue]
        sys.modules["vllm_hmr_fake_runtime"] = module
        try:
            env = {"HMR_VLLM_ENABLE": "1", "HMR_VLLM_RUNTIME": "vllm_hmr_fake_runtime:entry"}
            self.assertIs(vllm_hmr_shim.install_from_env(env), marker)
            self.assertIsNone(vllm_hmr_shim.install_from_env({}))
        finally:
            del sys.modules["vllm_hmr_fake_runtime"]

    def test_shadowed_sitecustomize_is_found_and_chained(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shim_dir, other = root / "shim", root / "other"
            shim_dir.mkdir()
            other.mkdir()
            shim_file = shim_dir / "sitecustomize.py"
            shim_file.write_text("", encoding="utf-8")
            sentinel = root / "chained.txt"
            (other / "sitecustomize.py").write_text(f"with open({str(sentinel)!r}, 'w') as f: f.write('chained')\n", encoding="utf-8")
            path = [str(shim_dir), str(other)]
            self.assertEqual(vllm_hmr_shim.next_sitecustomize(str(shim_file), path), other / "sitecustomize.py")
            original = sys.path
            sys.path = path
            try:
                self.assertEqual(vllm_hmr_shim.chain_to_next_sitecustomize(str(shim_file)), other / "sitecustomize.py")
            finally:
                sys.path = original
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "chained")

    def test_no_shadowed_sitecustomize_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as raw:
            shim_dir = Path(raw) / "shim"
            shim_dir.mkdir()
            shim_file = shim_dir / "sitecustomize.py"
            shim_file.write_text("", encoding="utf-8")
            self.assertIsNone(vllm_hmr_shim.next_sitecustomize(str(shim_file), [str(shim_dir), ""]))


if __name__ == "__main__":
    unittest.main()
