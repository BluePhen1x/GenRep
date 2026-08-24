"""
CRITICAL: This file runs the REAL GenRep multi-agent system.
It does NOT make a fake single LLM call - it invokes the actual
Manus agent pipeline (Planning -> Execution agents -> aggregation).

Because Manus.run() is an async coroutine, this wrapper drives it
through asyncio and collects whatever report artifacts the agent
writes into the OpenManus workspace.
"""

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from config import config

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None



REPORT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
    ".csv",
    ".json",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
}


class OpenManusWrapper:
    """
    Wrapper that runs the actual GenRep multi-agent system.
    """

    def __init__(self):
        self.openmanus_path = config.OPENMANUS_PATH
        self.workspace_path = self.openmanus_path / "workspace"
        self._verify_openmanus()

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def _verify_openmanus(self) -> None:
        """Verify OpenManus is properly installed and configured."""
        if not self.openmanus_path.exists():
            raise Exception(f"OpenManus not found at {self.openmanus_path}")

        # Check for the real agent module
        manus_py = self.openmanus_path / "app" / "agent" / "manus.py"
        if not manus_py.exists():
            raise Exception("app/agent/manus.py not found - OpenManus agent missing")

        # Check for config
        config_toml = self.openmanus_path / "config" / "config.toml"
        if not config_toml.exists():
            raise Exception(
                "config.toml not found. Please copy config.example.toml "
                "and add your API key."
            )

        # Check if API key is set
        try:
            if tomllib is not None:
                with open(config_toml, "rb") as f:
                    cfg = tomllib.load(f)
                llm = cfg.get("llm", {})
                api_key = llm.get("api_key", "")
                if not api_key or api_key in ("sk-...", "your_api_key_here"):
                    raise Exception(
                        "API key not set in OpenManus/config/config.toml"
                    )
            else:
                raise Exception("TOML parser (tomllib/tomli) not available")
        except Exception as e:
            if isinstance(e, Exception) and "API key not set" in str(e):
                raise
            raise Exception(f"Config error: {e}")

        self.config_toml = config_toml
        print(f"[GenRepWrapper] Verified at {self.openmanus_path}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_report(
        self,
        prompt: str,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        page_limit: int = 2,
    ) -> Dict:
        """
        Run OpenManus with verification loop.

        Args:
            prompt: The user's report request.
            progress_cb: Optional callback(progress_pct, message).

        Returns:
            Dict with keys: text, summary, files, workspace_dir, duration.
        """
        print(f"[GenRepWrapper] Running multi-agent system: {prompt[:60]}...")
        start = time.time()

        def _emit(pct: int, msg: str):
            print(f"[GenRepWrapper] {pct}% - {msg}")
            if progress_cb:
                progress_cb(pct, msg)

        max_attempts = 3
        current_prompt = prompt
        last_report = None
        attempt = 0

        while attempt < max_attempts:
            attempt += 1
            print(f"[GenRepWrapper] Report generation attempt {attempt}/{max_attempts}")
            _emit(10 + (attempt - 1) * 30, f"Attempt {attempt}/{max_attempts}: Generating report...")

            before = self._snapshot_workspace()
            _emit(20 + (attempt - 1) * 30, "Execution Agents: researching and writing...")

            result = self._run_via_import(current_prompt, _emit, page_limit=page_limit)

            after = self._snapshot_workspace()
            new_files = self._diff_workspace(before, after)

            duration = time.time() - start
            text = self._collect_text(result, new_files)
            last_report = text

            # Verify the report
            _emit(80 + (attempt - 1) * 10, "Verifying report quality...")
            passed, feedback = self._verify_report(text, prompt)

            if passed:
                print("[GenRepWrapper] Report passed verification")
                _emit(95, "Report passed all quality checks!")
                return {
                    "text": text,
                    "summary": result,
                    "files": new_files,
                    "workspace_dir": str(self.workspace_path),
                    "duration": round(duration, 1),
                    "verification": f"Passed (attempt {attempt})",
                }
            else:
                print(f"[GenRepWrapper] Verification failed: {feedback}")
                if attempt < max_attempts:
                    _emit(85, f"Revision needed: {feedback[:100]}...")
                    current_prompt = f"""
ORIGINAL REQUEST: {prompt}

PREVIOUS REPORT:
{text}

FEEDBACK FOR REVISION:
{feedback}

Please generate a revised report that addresses ALL feedback points above.
Keep all good content from the previous version.
Return the complete revised report.
"""
                else:
                    print("[GenRepWrapper] Max attempts reached. Returning best available.")
                    _emit(95, "Max attempts reached. Returning best available report.")
                    return {
                        "text": text,
                        "summary": result,
                        "files": new_files,
                        "workspace_dir": str(self.workspace_path),
                        "duration": round(duration, 1),
                        "verification": f"Max attempts reached (attempt {attempt}). Feedback: {feedback}",
                    }

        return {
            "text": last_report or "",
            "summary": "",
            "files": [],
            "workspace_dir": str(self.workspace_path),
            "duration": round(time.time() - start, 1),
            "verification": "Max attempts reached",
        }

    def get_status(self) -> Dict:
        """Get GenRep status."""
        try:
            self._verify_openmanus()
            return {
                "status": "ready",
                "message": "GenRep multi-agent system is ready",
                "path": str(self.openmanus_path),
                "workspace": str(self.workspace_path),
                "model": self._get_llm_model(),
            }
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Workspace bookkeeping
    # ------------------------------------------------------------------
    def _snapshot_workspace(self) -> Dict[str, float]:
        snap = {}
        if self.workspace_path.exists():
            for p in self.workspace_path.rglob("*"):
                if p.is_file():
                    try:
                        snap[str(p)] = p.stat().st_mtime
                    except OSError:
                        pass
        return snap

    def _diff_workspace(
        self, before: Dict[str, float], after: Dict[str, float]
    ) -> List[str]:
        """Return newly created / modified report files."""
        changed = []
        for path, mtime in after.items():
            if path not in before or before[path] != mtime:
                suffix = Path(path).suffix.lower()
                if suffix in REPORT_EXTENSIONS:
                    changed.append(path)
        return changed

    def _collect_text(self, run_summary: str, new_files: List[str]) -> str:
        """Build the best-effort report body from agent output."""
        sections = [run_summary]

        # Try to find a markdown/text report among the artifacts first
        for path in sorted(new_files):
            suffix = Path(path).suffix.lower()
            if suffix in {".md", ".markdown", ".txt"}:
                try:
                    content = Path(path).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if content.strip() and len(content.strip()) > 200:
                    sections = [content]  # Prefer the largest written report
                    break

        return "\n\n".join(s for s in sections if s and s.strip())

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _run_via_import(self, prompt: str, emit: Callable, page_limit: int = 2) -> str:
        """Run GenRep by importing its modules and driving the agent."""
        sys.path.insert(0, str(self.openmanus_path))

        # Convert user prompt to structured specification
        spec = self._create_spec(prompt, page_limit=page_limit)

        async def _run() -> str:
            from app.agent.manus import Manus

            agent = await Manus.create()
            try:
                emit(30, "Execution Agents running (research, code, files)...")
                result = await agent.run(spec)
                # Clean the output
                cleaned = self._clean_report(result or "")
                return cleaned
            finally:
                try:
                    await agent.cleanup()
                except Exception as exc:  # noqa: BLE001
                    print(f"[GenRepWrapper] cleanup warning: {exc}")

        try:
            return asyncio.run(_run())
        except ImportError as e:
            print(f"[GenRepWrapper] Import error: {e}")
            raise
        finally:
            if str(self.openmanus_path) in sys.path:
                sys.path.remove(str(self.openmanus_path))

    def _create_spec(self, prompt: str, page_limit: int = 2) -> str:
        """Convert user prompt into a structured report specification."""
        # Scale word count to page limit (roughly 600 words per page)
        word_target = page_limit * 600
        word_min = int(word_target * 0.8)
        word_max = int(word_target * 1.2)

        return f"""You are a professional technical writer. Write a formal report.

SPECIFICATION:
- Subject: {prompt}
- Deliverable: Technical report
- Length: Approximately {page_limit} pages (~{word_min}-{word_max} words)
- Audience: Technical reader with limited specialist knowledge
- Tone: Clear, objective, educational, cautious about claims

SCOPE:
Focus on these questions:
1. What is this system?
2. How does it work technically?
3. What are the key components?
4. How is data processed?
5. What are the practical benefits and limitations?

CENTRAL MESSAGE:
Write one sentence that captures the balanced evaluation.

RESEARCH INSTRUCTIONS:
1. Find at least 3 authoritative sources from this hierarchy:
   - Manufacturer documentation (preferred)
   - Official platform documentation
   - Peer-reviewed research
   - Educational tutorials (last resort)
2. For each source, extract specific facts (not full text)
3. Cross-reference sources for consistency
4. Only use a source if it directly supports a claim
5. Use browser go_to_url to visit relevant websites
6. Use browser extract_content to get text from each page

WRITING INSTRUCTIONS:
1. Each section must have a clear purpose
2. Transitions between sections must be smooth
3. Define ALL technical terms at first use
4. Use examples to clarify concepts
5. Avoid generic statements - be specific
6. Connect each paragraph to the central message
7. Use the active voice where possible
8. Compare alternatives, don't just list

CONCLUSION INSTRUCTIONS:
1. Synthesize key findings (not just summarize)
2. State practical implications
3. Acknowledge limitations honestly
4. Suggest future directions or improvements
5. Connect back to the central message

SECTION BUDGET:
- Executive Summary: 60-80 words
- Introduction: 120-150 words
- System Architecture: 250-300 words
- Working Principle: 200-250 words
- Implementation: 200-250 words
- Applications and Limitations: 250-300 words
- Conclusion: 100-130 words
- Total: ~{word_min}-{word_max} words

OUTPUT STRUCTURE:
## Executive Summary
[2-3 sentences summarizing the entire report]

## Introduction
[Define topic, establish significance, state central message]

## System Architecture
[Hardware blocks, component comparison with table]

## Working Principle
[Step-by-step technical explanation]

## Implementation
[How it's built, key decisions]

## Applications and Limitations
[Uses, reliability, safety, validation]

## Conclusion
[Synthesis, implications, future directions]

## References
[At least 3 cited sources with URLs]

RULES:
1. Define ALL technical terms at first use
2. Compare alternatives, don't just list
3. Use examples to clarify
4. Every claim needs a source
5. Professional, academic tone
6. No code, no execution, no file operations
7. No error messages or logs in the output
8. Minimum 3 references with URLs

Write the report now. Use your training knowledge and the browser tool to research.
"""

    def _clean_report(self, raw_text: str) -> str:
        """Remove errors, extract only the report, structure it."""
        import re

        if not raw_text:
            return raw_text

        # Step 1: Remove all "Observed output" lines
        cleaned = re.sub(r'Step \d+:.*?executed:.*?\n', '', raw_text)

        # Step 2: Remove all "Navigated to" lines
        cleaned = re.sub(r'Navigated to.*?\n', '', cleaned)

        # Step 3: Remove "No content was extracted" lines
        cleaned = re.sub(r'No content was extracted.*?\n', '', cleaned)

        # Step 4: Remove "Scrolled down" lines
        cleaned = re.sub(r'Scrolled down.*?\n', '', cleaned)
        cleaned = re.sub(r'Scrolled up.*?\n', '', cleaned)

        # Step 5: Remove "Error:" lines
        cleaned = re.sub(r'Error:.*?\n', '', cleaned)

        # Step 6: Remove "Input" lines
        cleaned = re.sub(r"Input '.*?\n", '', cleaned)

        # Step 7: Remove "Clicked element" lines
        cleaned = re.sub(r'Clicked element.*?\n', '', cleaned)

        # Step 8: Remove "The interaction has been completed" lines
        cleaned = re.sub(r'The interaction has been completed.*?\n', '', cleaned)

        # Step 9: Remove "This report was produced autonomously" lines
        cleaned = re.sub(r'This report was produced autonomously.*', '', cleaned)

        # Step 10: Remove "Facts and figures" lines
        cleaned = re.sub(r'Facts and figures should be independently verified.*', '', cleaned)

        # Step 11: Extract only the report content (from first heading to end)
        heading_match = re.search(r'^(#+ .*?)$', cleaned, re.MULTILINE)
        if heading_match:
            cleaned = cleaned[heading_match.start():]
        else:
            # If no heading found, try to find Executive Summary or Introduction
            alt_match = re.search(r'(Executive Summary|Introduction).*', cleaned, re.DOTALL)
            if alt_match:
                cleaned = alt_match.group(0)

        # Step 12: Remove any remaining raw log artifacts
        cleaned = re.sub(r'\[.*?\]', '', cleaned)
        cleaned = re.sub(r'\b\d{1,2}:\d{2}:\d{2}\b', '', cleaned)

        # Step 13: Ensure proper spacing
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

        return cleaned.strip()

    def _verify_report(self, report_text: str, prompt: str = "") -> tuple:
        """
        Verify report quality and return revision instructions if needed.
        Returns: (passed: bool, feedback: str)
        """
        import re

        checks = []
        passed = True

        # Check 1: Minimum length (500 words minimum)
        word_count = len(report_text.split())
        if word_count < 500:
            passed = False
            checks.append(f"Report is too short: {word_count} words. Minimum 500 words required. Expand each section with more detail.")

        # Check 2: Required sections
        required_sections = [
            ("Executive Summary", "Add an Executive Summary section at the beginning."),
            ("Introduction", "Add an Introduction section that defines the topic and states its significance."),
            ("Conclusion", "Add a Conclusion section that synthesizes key findings."),
            ("References", "Add a References section with at least 3 cited sources.")
        ]

        for section, feedback in required_sections:
            if section not in report_text:
                passed = False
                checks.append(feedback)

        # Check 3: Citations (at least 3)
        citations = re.findall(r'\[\d+\]|\(Source:|\(https?://', report_text)
        if len(citations) < 3:
            passed = False
            checks.append(f"Only {len(citations)} citations found. Add at least 3 citations with source URLs.")

        # Check 4: Technical depth (look for evidence of research)
        research_indicators = [
            "source", "according to", "research", "study", "documentation",
            "specification", "data", "analysis", "example"
        ]
        indicator_count = sum(1 for word in research_indicators if word in report_text.lower())
        if indicator_count < 10:
            passed = False
            checks.append("Report lacks technical depth. Add specific technical details, examples, and data.")

        # Check 5: "Observed output" / raw logs (should not be in report)
        if "Observed output" in report_text or ("Step" in report_text and "executed" in report_text):
            passed = False
            checks.append("Remove all 'Observed output' logs. The report should be clean text only.")

        # Check 6: Tone (should not be promotional)
        promotional_words = ["amazing", "incredible", "revolutionary", "life-changing", "unbelievable"]
        promo_count = sum(1 for word in promotional_words if word in report_text.lower())
        if promo_count > 2:
            passed = False
            checks.append("Tone is too promotional. Use objective, professional language.")

        # If failed, generate revision instructions
        if not passed:
            feedback = "\n".join(checks)
            return (False, feedback)
        else:
            return (True, "Report passes all quality checks.")

    def _revise_report(self, original_prompt: str, current_report: str, feedback: str, progress_cb: Optional[Callable] = None, page_limit: int = 2) -> str:
        """
        Generate revision instructions and run OpenManus again.
        """
        revision_prompt = f"""
The following report needs revision based on quality feedback.

ORIGINAL REQUEST: {original_prompt}

CURRENT REPORT:
{current_report}

QUALITY FEEDBACK:
{feedback}

INSTRUCTIONS:
1. Address ALL feedback points above
2. Keep the existing good content
3. Improve the quality as requested
4. Return the complete revised report

Generate the revised report now.
"""
        return self.run_report(revision_prompt, progress_cb=progress_cb, page_limit=page_limit)

    def _run_via_cli(self, prompt: str) -> str:
        """Fallback: run GenRep via its CLI in a subprocess."""
        import subprocess

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            cmd = [
                sys.executable,
                str(self.openmanus_path / "main.py"),
                "--prompt",
                prompt,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self.openmanus_path),
                timeout=config.AGENT_TIMEOUT,
            )
            if result.returncode == 0:
                return result.stdout
            raise Exception(f"GenRep CLI error: {result.stderr}")
        finally:
            if prompt_file and Path(prompt_file).exists():
                try:
                    Path(prompt_file).unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_llm_model(self) -> str:
        try:
            if tomllib is not None:
                with open(self.config_toml, "rb") as f:
                    cfg = tomllib.load(f)
                llm = cfg.get("llm", {})
                return f"{llm.get('api_type', '?')} / {llm.get('model', '?')}"
            return "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"



class _LazyWrapper:
    """
    Lazy proxy for OpenManusWrapper.

    Defers construction (and _verify_openmanus) until the first attribute
    access so that importing this module never raises — even when
    config.toml is missing or the API key is not set.  Failures surface
    inside the Celery task where they are caught and reported properly.
    """

    _instance: "OpenManusWrapper | None" = None

    def _get(self) -> "OpenManusWrapper":
        if self._instance is None:
            self._instance = OpenManusWrapper()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


# Module-level singleton — safe to import even without a valid config.toml
wrapper = _LazyWrapper()