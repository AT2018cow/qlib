"""Run independently recoverable signal and website publication stages."""

from __future__ import annotations


class DailyPipelineError(RuntimeError):
    """Expose partial completion without hiding a failed daily stage."""

    def __init__(self, stages):
        self.stages = stages
        failed = ", ".join(name for name, result in stages.items() if result["status"] == "FAILED")
        super().__init__(f"daily publication stages failed: {failed}")

    def __reduce__(self):
        return type(self), (self.stages,)


def run_daily_pipeline(signal_runner, website_runner):
    """Attempt both stages; their own journals make subsequent runs idempotent."""
    stages = {}
    first_error = None
    for name, runner in (("signals", signal_runner), ("website", website_runner)):
        try:
            result = runner()
            stages[name] = {"status": "SUCCEEDED", "result": result}
            print(f"[daily] {name}: SUCCEEDED", flush=True)
        except Exception as exc:
            first_error = first_error or exc
            stages[name] = {"status": "FAILED", "error_type": type(exc).__name__}
            print(f"[daily] {name}: FAILED ({type(exc).__name__})", flush=True)
    if first_error is not None:
        raise DailyPipelineError(stages) from first_error
    return {"status": "COMPLETED", "stages": stages}
