import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.report_agent as report_agent_module
from app.services.report_agent import ReportAgent, ReportManager, ReportOutline, ReportSection, ReportStatus


def _make_outline():
    return ReportOutline(
        title="Simulation Forecast Report",
        summary="A concise forecast for the active simulation.",
        sections=[
            ReportSection(title="Market Overview"),
            ReportSection(title="Strategic Risks"),
        ],
    )


@pytest.fixture()
def isolated_report_storage(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    reports_dir = upload_dir / "reports"

    monkeypatch.setattr(report_agent_module.Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(report_agent_module.ReportManager, "REPORTS_DIR", str(reports_dir))

    reports_dir.mkdir(parents=True, exist_ok=True)
    yield reports_dir


def test_generate_report_can_resume_after_cancellation(isolated_report_storage, monkeypatch):
    report_id = "report_resume_case"

    monkeypatch.setattr(ReportAgent, "plan_outline", lambda self, **kwargs: _make_outline())

    canceled_calls = []

    def cancel_after_first_section(
        self,
        section,
        outline,
        previous_sections,
        progress_callback=None,
        section_index=0,
        cancel_event=None,
    ):
        canceled_calls.append(section_index)
        if section_index == 1 and cancel_event is not None:
            cancel_event.set()
        return f"Body for {section.title}"

    monkeypatch.setattr(ReportAgent, "_generate_section_react", cancel_after_first_section)

    canceled_report = ReportAgent(
        graph_id="graph_test",
        simulation_id="sim_test",
        simulation_requirement="Test simulation requirement",
        llm_client=object(),
        graph_tools=SimpleNamespace(),
    ).generate_report(
        report_id=report_id,
        cancel_event=threading.Event(),
    )

    assert canceled_report.status == ReportStatus.CANCELED
    assert canceled_calls == [1]

    canceled_progress = ReportManager.get_progress(report_id)
    assert canceled_progress["status"] == "canceled"
    assert canceled_progress["completed_sections"] == ["Market Overview"]

    saved_sections = ReportManager.get_generated_sections(report_id)
    assert [section["section_index"] for section in saved_sections] == [1]
    assert "Body for Market Overview" in saved_sections[0]["content"]

    resumed_calls = []

    def resume_remaining_sections(
        self,
        section,
        outline,
        previous_sections,
        progress_callback=None,
        section_index=0,
        cancel_event=None,
    ):
        resumed_calls.append(section_index)
        return f"Resumed body for {section.title}"

    monkeypatch.setattr(ReportAgent, "_generate_section_react", resume_remaining_sections)

    resumed_report = ReportAgent(
        graph_id="graph_test",
        simulation_id="sim_test",
        simulation_requirement="Test simulation requirement",
        llm_client=object(),
        graph_tools=SimpleNamespace(),
    ).generate_report(
        report_id=report_id,
        cancel_event=threading.Event(),
        resume_existing=True,
    )

    assert resumed_report.status == ReportStatus.COMPLETED
    assert resumed_report.report_id == report_id
    assert resumed_calls == [2]
    assert resumed_report.outline.sections[0].content == "Body for Market Overview"
    assert resumed_report.outline.sections[1].content == "Resumed body for Strategic Risks"

    resumed_progress = ReportManager.get_progress(report_id)
    assert resumed_progress["status"] == "completed"
    assert resumed_progress["completed_sections"] == ["Market Overview", "Strategic Risks"]

    full_report = Path(ReportManager._get_report_markdown_path(report_id)).read_text(encoding="utf-8")
    assert "Body for Market Overview" in full_report
    assert "Resumed body for Strategic Risks" in full_report

    log_actions = [entry["action"] for entry in ReportManager.get_agent_log(report_id)["logs"]]
    assert "report_start" in log_actions
    assert "report_canceled" in log_actions
    assert "report_resume" in log_actions
    assert "report_complete" in log_actions
    assert log_actions.index("report_resume") > log_actions.index("report_canceled")
