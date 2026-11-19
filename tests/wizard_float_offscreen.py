"""Offscreen: FLOAT 1e-4 keeps four decimals, and wizard hooks are public."""

from __future__ import annotations

import os


def main() -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import (
        QApplication,
        QDoubleSpinBox,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from voxprint_common.ui.presets import reference_presets
    from voxprint_common.ui.safety import ReviewedOutput
    from voxprint_common.ui.sections import (
        BodyKind,
        Section,
        SectionAction,
        SectionSet,
        StepKind,
        Wizard,
        WizardStep,
    )
    from voxprint_common.ui.settings import SettingSpec, SettingType, reference_settings
    from voxprint_common.ui.simple_image import SimpleImageLayout, SimpleImageWidget

    class Gate:
        def review_prompt(self, prompt: str) -> str | None:
            del prompt
            return None

        def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
            del prompt, item_id, output
            return None

    settings = reference_settings()
    settings.add(
        SettingSpec(
            "train.learning_rate",
            "setting.shell.guidance",
            "setting.shell.guidance.help",
            SettingType.FLOAT,
            1e-4,
            expert=True,
            minimum=1e-6,
            maximum=1.0,
            decimals=4,
            single_step=1e-4,
            section="train",
        )
    )
    document = SectionSet(
        sections=(
            Section(
                "train",
                "setting.shell.steps",
                BodyKind.SCREEN,
                actions=(SectionAction("open", "wizard.next", "train"),),
            ),
            Section("extra", "setting.shell.guidance", BodyKind.SCREEN),
        ),
        wizards=(
            Wizard(
                "train",
                "setting.shell.steps",
                "train",
                (
                    WizardStep("train.sample", "setting.shell.sampler"),
                    WizardStep("train.run", "setting.shell.steps", StepKind.RUNNING),
                ),
            ),
        ),
        settings=settings,
    )
    started: list[str] = []
    filled: list[tuple[str, str]] = []
    view: SimpleImageWidget

    def on_start() -> None:
        started.append("start")
        view.report_wizard_progress(1, 4, "Learning")

    def on_fill(wizard_id: str, step_id: str, body: QWidget) -> bool:
        filled.append((wizard_id, step_id))
        if step_id != "train.sample":
            return False
        mark = QLabel("custom-step")
        mark.setObjectName("customStep")
        layout = body.layout()
        if layout is None:
            layout = QVBoxLayout(body)
        layout.addWidget(mark)
        return True

    app = QApplication([])
    layout = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=Gate(),
        section_set=document,
    )
    view = SimpleImageWidget(
        layout,
        settings,
        on_wizard_start=on_start,
        on_fill_wizard_step=on_fill,
    )
    view.root.resize(1100, 800)
    view.set_expert(True)
    view.root.show()
    app.processEvents()
    spin = view.root.findChild(QDoubleSpinBox, "train.learning_rate")
    assert spin is not None
    assert spin.decimals() == 4
    assert spin.singleStep() == 1e-4
    assert spin.value() == 1e-4
    assert "0.0001" in spin.text()

    layout.sections.open_wizard("train")
    view.refresh()
    app.processEvents()
    assert filled == [("train", "train.sample")]
    custom = view.root.findChild(QLabel, "customStep")
    assert custom is not None and custom.text() == "custom-step"

    layout.sections.advance()
    view.refresh()
    app.processEvents()
    start = view.root.findChild(QPushButton, "wizardStart")
    assert start is not None and start.isVisible()
    start.click()
    app.processEvents()
    assert started == ["start"]
    assert layout.sections.running is True
    assert layout.sections.progress == (1, 4, "Learning")
    percent = view.root.findChild(QLabel, "wizardPercent")
    assert percent is not None and percent.text() != ""


if __name__ == "__main__":
    main()
