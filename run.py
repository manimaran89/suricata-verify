#! /usr/bin/env python2
#
# Copyright 2017 Jason Ish
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import print_function

import sys
import os
import os.path
import subprocess
import threading
import shutil
import argparse
import yaml
import glob
import re
import json
import unittest
from collections import namedtuple

import yaml

class SelfTest(unittest.TestCase):

    def test_parse_suricata_version(self):
        version = parse_suricata_version("4.0.0")
        self.assertEqual(
            (4, 0, 0), (version.major, version.minor, version.patch))

        version = parse_suricata_version("444.444.444")
        self.assertEqual(
            (444, 444, 444), (version.major, version.minor, version.patch))

        version = parse_suricata_version("4.1.0-dev")
        self.assertEqual(
            (4, 1, 0), (version.major, version.minor, version.patch))

        version = parse_suricata_version("4")
        self.assertEqual(
            (4, None, None), (version.major, version.minor, version.patch))

        version = parse_suricata_version("4.0.3")
        self.assertEqual(
            (4, 0, 3), (version.major, version.minor, version.patch))

    def test_version_equal(self):
        self.assertTrue(version_equal("4", "4.0.3"))
        self.assertTrue(version_equal("4.0", "4.0.3"))
        self.assertTrue(version_equal("4.0.3", "4.0.3"))

        self.assertTrue(version_equal("4.0.3", "4"))
        self.assertTrue(version_equal("4.0.3", "4.0"))
        self.assertTrue(version_equal("4.0.3", "4.0.3"))

        self.assertFalse(version_equal("3", "4.0.3"))
        self.assertFalse(version_equal("4.0", "4.1.3"))
        self.assertFalse(version_equal("4.0.2", "4.0.3"))

class TestError(Exception):
    pass

class UnsatisfiedRequirementError(Exception):
    pass

SuricataVersion = namedtuple(
    "SuricataVersion", ["major", "minor", "patch"])

def parse_suricata_version(buf):
    m = re.search("(\d+)\.?(\d+)?\.?(\d+)?.*", str(buf).strip())
    if m:
        if m.group(1) is not None:
            major = int(m.group(1))
        else:
            major = None

        if m.group(2) is not None:
            minor = int(m.group(2))
        else:
            minor = None

        if m.group(3) is not None:
            patch = int(m.group(3))
        else:
            patch = None

        return SuricataVersion(
            major=major, minor=minor, patch=patch)

    return None

def get_suricata_version():
    output = subprocess.check_output(["./src/suricata", "-V"])
    return parse_suricata_version(output)

def version_equal(a, b):
    """Check if version a and version b are equal in a semantic way.

    For example:
      - 4 would match 4, 4.x and 4.x.y.
      - 4.0 would match 4.0.x.
      - 4.0.3 would match only 4.0.3.
    """
    if not a.major == b.major:
        return False

    if a.minor is not None and b.minor is not None:
        if a.minor != b.minor:
            return False

    if a.patch is not None and b.patch is not None:
        if a.patch != b.patch:
            return False

    return True

def version_gte(v1, v2):
    """Return True if v1 is great than or equal to v2."""
    if v1.major < v2.major:
        return False
    elif v1.major > v2.major:
        return True

    if v1.minor < v2.minor:
        return False
    elif v1.minor > v2.minor:
        return True

    if v1.patch < v2.patch:
        return False

    return True

def pipe_reader(fileobj, output=None, verbose=False):
    for line in fileobj:
        line = line.decode()
        if output:
            output.write(line)
        if verbose:
            print(line.strip())

class SuricataConfig:

    def __init__(self, version):
        self.version = version
        self.features = set()

        self.load_build_info()

    def load_build_info(self):
        output = subprocess.check_output(["./src/suricata", "--build-info"])
        for line in output.splitlines():
            if line.decode().startswith("Features:"):
                self.features = set(line.decode().split()[1:])

    def has_feature(self, feature):
        return feature in self.features

def find_value(name, obj):
    """Find the value in an object for a field specified by name.

    Example names:
      event_type
      alert.signature_id
      smtp.rcpt_to[0]
    """
    parts = name.split(".")
    for part in parts:
        name = None
        index = None
        m = re.match("^(.*)\[(\d+)\]$", part)
        if m:
            name = m.group(1)
            index = m.group(2)
        else:
            name = part

        if not name in obj:
            return None
        obj = obj[name]

        if index is not None:
            try:
                obj = obj[int(index)]
            except:
                return None

    return obj

class ShellCheck:

    def __init__(self, config):
        self.config = config

    def run(self):
        try:
            output = subprocess.check_output(self.config["args"], shell=True)
            if "expect" in self.config:
                return str(self.config["expect"]) == output.decode().strip()
            return True
        except subprocess.CalledProcessError as err:
            raise TestError(err)

class StatsCheck:

    def __init__(self, config, outdir):
        self.config = config
        self.outdir = outdir

    def run(self):
        stats = None
        with open("eve.json", "r") as fileobj:
            for line in fileobj:
                event = json.loads(line)
                if event["event_type"] == "stats":
                    stats = event["stats"]
        for key in self.config:
            val = find_value(key, stats)
            if val != self.config[key]:
                raise TestError("stats.%s: expected %s; got %s" % (
                    key, str(self.config[key]), str(val)))
        return True

class FilterCheck:

    def __init__(self, config, outdir):
        self.config = config
        self.outdir = outdir

    def run(self):
        if "filename" in self.config:
            json_filename = self.config["filename"]
        else:
            json_filename = "eve.json"
        if not os.path.exists(json_filename):
            raise TestError("%s does not exist" % (json_filename))

        count = 0
        with open(json_filename, "r") as fileobj:
            for line in fileobj:
                event = json.loads(line)
                if self.match(event):
                    count += 1
        if count == self.config["count"]:
            return True
        if "comment" in self.config:
            raise TestError("%s: expected %d, got %d" % (
                self.config["comment"], self.config["count"], count))
        raise TestError("expected %d matches; got %d for filter %s" % (
            self.config["count"], count, str(self.config)))

    def match(self, event):
        for key, expected in self.config["match"].items():
            if key == "has-key":
                val = find_value(expected, event)
                if val is None:
                    return False
            elif key == "not-has-key":
                val = find_value(expected, event)
                if val is not None:
                    return False
            else:
                val = find_value(key, event)
                if val != expected:
                    return False
        return True

class TestRunner:

    def __init__(self, cwd, directory, outdir, suricata_config, verbose=False):
        self.cwd = cwd
        self.directory = directory
        self.suricata_config = suricata_config
        self.verbose = verbose
        self.output = outdir

        # The name is just the directory name.
        self.name = os.path.basename(self.directory)

        # List of thread readers.
        self.readers = []

        # Load the test configuration.
        self.load_config()

    def load_config(self):
        if os.path.exists(os.path.join(self.directory, "test.yaml")):
            self.config = yaml.safe_load(
                open(os.path.join(self.directory, "test.yaml"), "rb"))
        else:
            self.config = {}

    def setup(self):
        if "setup" in self.config:
            for setup in self.config["setup"]:
                for command in setup:
                    if command == "script":
                        subprocess.check_call(
                            "%s" % setup[command],
                            shell=True,
                            cwd=self.output)

    def check_skip(self):
        if not "skip" in self.config:
            return
        for skip in self.config["skip"]:

            if "uid" in skip:
                if os.getuid() == skip["uid"]:
                    if "msg" in skip:
                        msg = skip["msg"]
                    else:
                        msg = "not for uid %d" % (skip["uid"])
                    raise UnsatisfiedRequirementError(msg)

            if "feature" in skip:
                if self.suricata_config.has_feature(skip["feature"]):
                    if "msg" in skip:
                        msg = skip["msg"]
                    else:
                        msg = "not for feature %s" % (skip["feature"])
                    raise UnsatisfiedRequirementError(msg)

    def check_requires(self):
        if "requires" in self.config:
            requires = self.config["requires"]
            if not requires:
                return True
        else:
            requires = {}

        if "min-version" in requires:
            min_version = parse_suricata_version(requires["min-version"])
            suri_version = self.suricata_config.version
            if not version_gte(suri_version, min_version):
                raise UnsatisfiedRequirementError(
                    "requires at least version %s" % (requires["min-version"]))

        if "version" in requires:
            requires_version = parse_suricata_version(requires["version"])
            if not version_equal(
                    self.suricata_config.version,
                    requires_version):
                raise UnsatisfiedRequirementError(
                    "only for version %s" % (requires["version"]))

        if "features" in requires:
            for feature in requires["features"]:
                if not self.suricata_config.has_feature(feature):
                    raise UnsatisfiedRequirementError(
                        "requires feature %s" % (feature))

        if "env" in requires:
            for env in requires["env"]:
                if not env in os.environ:
                    raise UnsatisfiedRequirementError(
                        "requires env var %s" % (env))

        if "files" in requires:
            for filename in requires["files"]:
                if not os.path.exists(filename):
                    raise UnsatisfiedRequirementError(
                        "requires file %s" % (filename))

        if "script" in requires:
            for script in requires["script"]:
                try:
                    subprocess.check_call("%s" % script, shell=True)
                except:
                    raise UnsatisfiedRequirementError(
                        "requires script returned false")

        # Check if a pcap is required or not. By default a pcap is
        # required unless a "command" has been provided.
        if not "command" in self.config:
            if "pcap" in requires:
                pcap_required = requires["pcap"]
            else:
                pcap_required = True
            if pcap_required and not "pcap" in self.config:
                if not glob.glob(os.path.join(self.directory, "*.pcap")) + \
                   glob.glob(os.path.join(self.directory, "*.pcapng")):
                    raise UnsatisfiedRequirementError("No pcap file found")

    def run(self):

        sys.stdout.write("===> %s: " % os.path.basename(self.directory))
        sys.stdout.flush()

        self.check_requires()
        self.check_skip()

        shell = False

        if "command" in self.config:
            args = self.config["command"]
            shell = True
        else:
            args = self.default_args()

        env = {
            # The suricata source directory.
            "SRCDIR": self.cwd,
            "TZ": "UTC",
            "TEST_DIR": self.directory,
            "OUTPUT_DIR": self.output,
            "ASAN_OPTIONS": "detect_leaks=0",
        }

        if "count" in self.config:
            count = self.config["count"]
        else:
            count = 1

        if "exit-code" in self.config:
            expected_exit_code = self.config["exit-code"]
        else:
            expected_exit_code = 0

        for _ in range(count):

            # Cleanup the output directory.
            if os.path.exists(self.output):
                shutil.rmtree(self.output)
            os.makedirs(self.output)
            self.setup()

            stdout = open(os.path.join(self.output, "stdout"), "w")
            stderr = open(os.path.join(self.output, "stderr"), "w")

            open(os.path.join(self.output, "cmdline"), "w").write(
                " ".join(args) + "\n")

            p = subprocess.Popen(
                args, shell=shell, cwd=self.directory, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            self.start_reader(p.stdout, stdout)
            self.start_reader(p.stderr, stderr)

            for r in self.readers:
                r.join()

            r = p.wait()

            if r != expected_exit_code:
                raise TestError("got exit code %d, expected %d" % (
                    r, expected_exit_code));

            if not self.check():
                return False

        print("OK%s" % (" (%dx)" % count if count > 1 else ""))
        return True

    def pre_check(self):
        if "pre-check" in self.config:
            subprocess.call(self.config["pre-check"], shell=True)

    def check(self):

        pdir = os.getcwd()
        os.chdir(self.output)
        try:
            self.pre_check()
            if "checks" in self.config:
                for check in self.config["checks"]:
                    for key in check:
                        if key == "filter":
                            if not FilterCheck(check[key], self.output).run():
                                raise TestError("filter did not match: %s" % (
                                    str(check[key])))
                        elif key == "shell":
                            if not ShellCheck(check[key]).run():
                                raise TestError(
                                    "shell output did not match: %s" % (
                                        str(check[key])))
                        elif key == "stats":
                            if not StatsCheck(check[key], self.output).run():
                                raise TestError("stats check did not pass")
                        else:
                            raise TestError("Unknown check type: %s" % (key))
        finally:
            os.chdir(pdir)

        # Old style check script.
        pdir = os.getcwd()
        os.chdir(self.output)
        try:
            if not os.path.exists(os.path.join(self.directory, "check.sh")):
                return True
            env = {
                # The suricata source directory.
                "SRCDIR": self.cwd,
                "TZ": "UTC",
                "TEST_DIR": self.directory,
                "TOPDIR": TOPDIR,
                "ASAN_OPTIONS": "detect_leaks=0",
            }
            r = subprocess.call(
                [os.path.join(self.directory, "check.sh")], env=env)
            if r != 0:
                print("FAILED: verification failed")
                return False
            return True
        finally:
            os.chdir(pdir)

    def default_args(self):
        args = []
        if self.suricata_config.valgrind:
            suppression_opt = "--suppressions=%s" % os.path.join(self.cwd, "qa/valgrind.suppress")
            args += [ "valgrind", "-v", "--error-exitcode=255", suppression_opt ]

        args += [
            os.path.join(self.cwd, "src/suricata"),
        ]

        # Load args from config file.
        if "args" in self.config:
            assert(type(self.config["args"]) == type([]))
            for arg in self.config["args"]:
                args += re.split("\s", arg)

        # Add other fixed arguments.
        args += [
            "--set", "classification-file=%s" % os.path.join(
                self.cwd, "classification.config"),
            "--set", "reference-config-file=%s" % os.path.join(
                self.cwd, "reference.config"),
            "--init-errors-fatal",
            "-l", self.output,
        ]

        if "ips" in self.name:
            args.append("--simulate-ips")

        if os.path.exists(os.path.join(self.directory, "suricata.yaml")):
            args += ["-c", os.path.join(self.directory, "suricata.yaml")]
        else:
            args += ["-c", os.path.join(self.cwd, "suricata.yaml")]

        # Find pcaps.
        if "pcap" in self.config:
            args += ["-r", self.config["pcap"]]
        else:
            pcaps = glob.glob(os.path.join(self.directory, "*.pcap"))
            pcaps += glob.glob(os.path.join(self.directory, "*.pcapng"))
            if len(pcaps) > 1:
                raise TestError("More than 1 pcap file found")
            if pcaps:
                args += ["-r", pcaps[0]]

        # Find rules.
        rules = glob.glob(os.path.join(self.directory, "*.rules"))
        if not rules:
            args += ["-S", "/dev/null"]
        elif len(rules) == 1:
            args += ["-S", rules[0]]
        else:
            raise TestError("More than 1 rule file found")

        return args

    def start_reader(self, input, output):
        t = threading.Thread(
            target=pipe_reader, args=(input, output, self.verbose))
        t.start()
        self.readers.append(t)

def check_deps():
    try:
        subprocess.check_call("jq --version > /dev/null 2>&1", shell=True)
    except:
        print("error: jq is required")
        return False

    try:
        subprocess.check_call("echo | xargs > /dev/null 2>&1", shell=True)
    except:
        print("error: xargs is required")
        return False

    return True

def main():
    global TOPDIR

    if not check_deps():
        return 1

    parser = argparse.ArgumentParser(description="Verification test runner.")
    parser.add_argument("-v", dest="verbose", action="store_true")
    parser.add_argument("--force", dest="force", action="store_true",
                        help="Force running of skipped tests")
    parser.add_argument("--fail", action="store_true",
                        help="Exit on test failure")
    parser.add_argument("--testdir", action="store",
                        help="Runs tests from custom directory")
    parser.add_argument("--outdir", action="store",
                        help="Outputs to custom directory")
    parser.add_argument("--valgrind", dest="valgrind", action="store_true",
                        help="Run tests in with valgrind")
    parser.add_argument("patterns", nargs="*", default=[])
    args = parser.parse_args()

    TOPDIR = os.path.abspath(os.path.dirname(sys.argv[0]))

    skipped = 0
    passed = 0
    failed = 0

    # Get the current working directory, which should be the top
    # suricata source directory.
    cwd = os.getcwd()
    if not (os.path.exists("./suricata.yaml") and
            os.path.exists("./src/suricata")):
        print("error: this is not a suricata source directory or " +
              "suricata is not built")
        return 1

    # Create a SuricataConfig object that is passed to all tests.
    suricata_config = SuricataConfig(get_suricata_version())
    suricata_config.valgrind = args.valgrind

    tdir = os.path.join(TOPDIR, "tests")
    if args.testdir:
        tdir = os.path.abspath(args.testdir)

    # First gather the tests so we can run them in alphabetic order.
    tests = []
    for dirpath, dirnames, filenames in os.walk(tdir):
        # The top directory is not a test...
        if dirpath == os.path.join(TOPDIR, "tests"):
            continue
        if dirpath == tdir:
            continue

        # We only want to go one level deep.
        dirnames[0:] = []

        if not args.patterns:
            tests.append(dirpath)
        else:
            for pattern in args.patterns:
                if os.path.basename(dirpath).find(pattern) > -1:
                    tests.append(dirpath)

    # Sort alphabetically.
    tests.sort()

    for dirpath in tests:
        name = os.path.basename(dirpath)

        outdir = os.path.join(dirpath, "output")
        if args.outdir:
            outdir = os.path.join(os.path.realpath(args.outdir), name, "output")

        test_runner = TestRunner(
            cwd, dirpath, outdir, suricata_config, args.verbose)
        try:
            if test_runner.run():
                passed += 1
            else:
                failed += 1
                if args.fail:
                    return 1
        except UnsatisfiedRequirementError as err:
            print("SKIPPED: %s" % (str(err)))
            skipped += 1
        except TestError as err:
            print("FAIL: %s" % (str(err)))
            failed += 1
            if args.fail:
                return 1
        except Exception as err:
            raise

    print("")
    print("PASSED:  %d" % (passed))
    print("FAILED:  %d" % (failed))
    print("SKIPPED: %d" % (skipped))

    if failed > 0:
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
