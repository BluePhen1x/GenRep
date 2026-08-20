"""
Celery tasks that invoke the REAL GenRep multi-agent system
and turn its output into a downloadable PDF report.
"""

from celery import Task

from celery_app import celery
from config import config
from openmanus_wrapper import wrapper
from pdf_generator import generate_report_pdf


class ReportTask(Task):
    """Custom task with progress tracking."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: ARG002
        print(f"[tasks] Task {task_id} FAILED: {exc}")

    def on_success(self, retval, task_id, args, kwargs):  # noqa: ARG002
        print(f"[tasks] Task {task_id} completed OK")


@celery.task(base=ReportTask, bind=True)
def generate_report(self, prompt, job_id, page_limit=2):  # noqa: ANN001
    """
    Generate a report using the REAL GenRep multi-agent system.

    Pipeline:
      1. Verify GenRep is ready
      2. Planning stage (GenRep decomposes the task)
      3. Execution stage (GenRep runs its tool-calling agents)
      4. Verify + aggregate outputs
      5. Render PDF
      6. Expose download URL
    """
    task_id = self.request.id

    def _progress(pct: int, message: str):
        self.update_state(
            state="RUNNING",
            meta={"progress": pct, "message": message},
        )

    # 1. Initialize
    _progress(0, "Initializing GenRep multi-agent system...")

    # 2. Check GenRep status
    status = wrapper.get_status()
    if status["status"] != "ready":
        msg = f"GenRep error: {status['message']}"
        _progress(0, msg)
        self.update_state(
            state="FAILED",
            meta={"progress": 0, "message": msg, "error": status["message"]},
        )
        raise RuntimeError(status["message"])

    # 3. Planning
    _progress(5, "Planning Agent: breaking your report into sections...")

    try:
        # 4. Execute - THIS triggers the real GenRep multi-agent pipeline
        result = wrapper.run_report(prompt, progress_cb=_progress, page_limit=page_limit)

        # 5. Verify / aggregate
        _progress(85, "Verification Agent: quality-checking the report...")

        # 6. Render PDF
        _progress(90, "Creating PDF with ReportLab...")
        report_text = result["text"] or result["summary"]
        output_path = config.TEMP_DIR / f"report_{job_id}.pdf"
        generate_report_pdf(
            report_text,
            output_path,
            title=config.PDF_TITLE,
            images=result.get("files", []),
        )

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("PDF generation produced an empty file")

        download_url = f"/download/{job_id}"

        # 7. Complete
        _progress(100, "Report complete! Download your PDF.")
        self.update_state(
            state="SUCCESS",
            meta={
                "progress": 100,
                "message": "Report complete! Download your PDF.",
                "result": report_text[:20000],
                "download_url": download_url,
                "files": len(result.get("files", [])),
                "duration": result.get("duration"),
            },
        )
        return {
            "status": "complete",
            "result": report_text[:20000],
            "download_url": download_url,
            "duration": result.get("duration"),
        }

    except Exception as e:  # noqa: BLE001
        msg = f"GenRep error: {e}"
        self.update_state(
            state="FAILED",
            meta={"progress": 0, "message": msg, "error": str(e)},
        )
        raise