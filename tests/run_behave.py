"""Run the behave scenarios under tests/features/, and fail if none of them ran.

    python -m tests.run_behave                  # every feature
    python -m tests.run_behave --tags=@wip      # any behave option passes through
    python -m tests.run_behave some/dir         # a different features directory

`test.sh` and both CI test paths call this, never bare `behave` (#356).

⚠️ WHY IT EXISTS. A run that executes nothing must not look like a pass. This
repo has been bitten twice by exactly that — #218's classifier skipped the
in-image suite and reported green, and #309 tranche 8b found five tests that had
quietly stopped running for twelve days a year. Measured against behave 1.3.3
rather than assumed (the tracking issue assumed wrongly in one direction):

    features dir exists, holds no .feature   -> exit 1 (ConfigError)
    features dir missing                      -> exit 1 (ConfigError)
    every scenario filtered out by a tag      -> exit 0, "0 scenarios passed"
    a feature file with no scenarios in it    -> exit 0, nothing ran

So behave does catch a wrong PATH, and does NOT catch a run that selected
nothing. This closes the second case: if behave succeeds and no scenario passed
or failed, the run fails and says so.

⚠️ It counts from behave's own model objects, not by parsing the summary text.
A summary line that changed wording would make a text parser read zero or read
nothing, and the fail-closed version of that is still a mystery to debug.

⚠️ Not a maintained count. "More than zero" is the whole rule — a hand-kept
expected number is the other failure this repo keeps finding (docs/testing.md
records the test count nowhere, because it was wrong five times running).
"""
import sys
from pathlib import Path

from behave.__main__ import run_behave
from behave.configuration import Configuration
from behave.model_core import Status
from behave.runner import Runner

FEATURES = Path(__file__).resolve().parent / "features"

# A scenario in any other state — skipped, untested, undefined — did not run.
# `undefined` and `error` already make behave fail on their own; they are not
# counted as "ran" so that a run of nothing BUT them is also reported here.
_RAN = {Status.passed, Status.failed}


class _RecordingRunner(Runner):
    """The stock runner, remembering itself so its features can be counted."""

    last = None

    def __init__(self, config):
        super().__init__(config)
        _RecordingRunner.last = self


def scenarios_that_ran(features):
    """How many scenarios actually executed.

    ⚠️ `walk_scenarios()` with its DEFAULTS, measured rather than read off the
    parameter names: it already descends into `Rule:` blocks and expands
    outlines. `with_rules=True` does not add the rules' scenarios — it adds the
    Rule OBJECTS too, which carry a status of their own and would be counted
    as scenarios that ran. `feature.scenarios` is the other tempting shortcut,
    and it is the one that misses scenarios under a Rule.
    """
    return sum(
        1
        for feature in features
        for scenario in feature.walk_scenarios()
        if scenario.status in _RAN
    )


# One line per scenario, and a failure printed inline with its step, file:line
# and assertion message. `progress` drops the message and `pretty` prints every
# step of every scenario; this is the one that reads as a spec when green and
# as a diagnosis when red. Pass your own --format to override it.
DEFAULT_FORMAT = "progress3"

# ⚠️ behave's log capture sets the ROOT logger to INFO around every scenario
# (behave/log_capture.py, measured on 1.3.3), where production's stays at
# WARNING. That made a logger stuck at WARNING invisible to every scenario:
# #404's own defect survived its own test until this was added. The app's
# handler writes to stderr, which behave still captures and prints on failure.
NO_LOGCAPTURE = "--no-logcapture"


def main(argv):
    if not any(not arg.startswith("-") and Path(arg).exists() for arg in argv):
        argv = [str(FEATURES), *argv]
    if not any(arg in ("-f", "--format") or arg.startswith(("-f", "--format=")) for arg in argv):
        argv = ["--format", DEFAULT_FORMAT, *argv]
    if NO_LOGCAPTURE not in argv:
        argv = [NO_LOGCAPTURE, *argv]

    code = run_behave(Configuration(command_args=argv), runner_class=_RecordingRunner)
    if code != 0:
        return code

    runner = _RecordingRunner.last
    ran = scenarios_that_ran(runner.features) if runner else 0
    if ran == 0:
        print(
            "✗ behave collected ZERO scenarios that ran, and would have exited 0.\n"
            "  A tag filter that matched nothing, or a feature with no scenarios,\n"
            "  looks exactly like a pass — see tests/run_behave.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
