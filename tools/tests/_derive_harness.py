from __future__ import annotations

from pathlib import Path
import shutil
import tempfile


CPU_LINE_SCAFFOLD = r"""

/* Test-only CPU derivation line. */
type Task {
    initial_state: State::Online;
    state State::Online { transitions {
        on Transition::Resume -> State::OnCpu {
            resumes self.ResumeTargetRef.Action::Enter;
        }
    } }
    state State::OnCpu { transitions {
        on Transition::Suspend -> State::Online {}
    } }
}
type TaskFlow {
    continuation: true;
    initial_state: State::Online;
    state State::Online { actions { on Action::Enter; } }
}
object __HarnessBootTask: Task {}
object __HarnessBootTaskFlow: TaskFlow {
    parent: __HarnessBootTask;
    state State::Online { actions {
        override on Action::Enter { drives {} }
    } }
}
type __HarnessScheduler {
    sched_core: true;
    initial_state: State::Online;
    state State::Online {}
}
object __HarnessCpu0Scheduler: __HarnessScheduler {
    idle_task: __HarnessBootTask;
}
"""


def with_cpu_line(body: str) -> str:
    return body + CPU_LINE_SCAFFOLD


def copy_case_with_cpu_line(
    case: Path,
) -> tuple[tempfile.TemporaryDirectory, Path]:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    for source in case.iterdir():
        target = root / source.name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    module = root / "root.spec"
    module.write_text(with_cpu_line(module.read_text(encoding="utf-8")), encoding="utf-8")
    return directory, root / "main.spec"
