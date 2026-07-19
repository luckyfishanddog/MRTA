"""Minimal cold-process entry that records every Primary incumbent observation.

Instrumentation changes only the checkpoint schedule of the solver-neutral
``IncumbentTrace`` object.  Candidate generation, acceptance and evaluation
are delegated unchanged to the selected authoritative solver module.
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", choices=("abma1", "abma20", "hga"), required=True)
    parser.add_argument("--trace-evaluations", type=int, required=True)
    args, remainder = parser.parse_known_args()
    checkpoints = tuple(range(1, args.trace_evaluations + 1))
    if args.solver in ("abma1", "abma20"):
        import MRTA_ABMA as source

        original_trace = source.IncumbentTrace
        original_digest = source.ABMASolver._digest_update
        original_solve = source.ABMASolver.solve
        counters = {"trial_decisions": 0, "accepted_trials": 0}

        def instrumented_digest(digest, values):
            if len(values) == 5 and isinstance(values[2], bool):
                counters["trial_decisions"] += 1
                counters["accepted_trials"] += int(values[2])
            return original_digest(digest, values)

        def instrumented_solve(solver):
            result = original_solve(solver)
            result.diagnostics["trial_decision_count"] = counters["trial_decisions"]
            result.diagnostics["accepted_trial_count"] = counters["accepted_trials"]
            return result

        source.IncumbentTrace = lambda: original_trace(checkpoints=checkpoints)
        source.ABMASolver._digest_update = staticmethod(instrumented_digest)
        source.ABMASolver.solve = instrumented_solve
        try:
            if args.solver == "abma1":
                import MRTA_ABMA1
                MRTA_ABMA1.main(remainder)
            else:
                old_argv = sys.argv
                try:
                    sys.argv = ["MRTA_ABMA.py", *remainder]
                    source.main()
                finally:
                    sys.argv = old_argv
        finally:
            source.IncumbentTrace = original_trace
            source.ABMASolver._digest_update = staticmethod(original_digest)
            source.ABMASolver.solve = original_solve
    else:
        import MRTA_HGA_PAPER_ALIGNED_CONTROL as hga

        original_trace = hga.IncumbentTrace
        hga.IncumbentTrace = lambda: original_trace(checkpoints=checkpoints)
        old_argv = sys.argv
        try:
            sys.argv = ["MRTA_HGA_PAPER_ALIGNED_CONTROL.py", *remainder]
            hga.main()
        finally:
            sys.argv = old_argv
            hga.IncumbentTrace = original_trace


if __name__ == "__main__":
    main()
