from pathlib import Path


BRAIN_MODEL = (
    Path(__file__).parents[1]
    / "apps"
    / "menubar-macos"
    / "Sources"
    / "SHawnBrain"
    / "BrainModel.swift"
)


def test_timer_tasks_copy_weak_self_into_concurrent_closure():
    """Every Timer -> Task hop must copy weak self into the Task capture list.

    Without the inner capture list Swift 6 reports that the outer closure's
    weak ``self`` variable is referenced from concurrently executing code.
    """
    text = BRAIN_MODEL.read_text(encoding="utf-8")

    assert text.count("Task { @MainActor [weak self] in") == 2
    assert "Task { @MainActor in self?.animStep() }" not in text
    assert "Task { @MainActor in self?.refresh() }" not in text
