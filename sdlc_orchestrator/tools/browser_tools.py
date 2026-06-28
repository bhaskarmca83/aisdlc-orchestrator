"""sdlc_orchestrator/tools/browser_tools.py
Playwright-based E2E browser automation helpers.
"""
import os
import json
import asyncio
from typing import Any

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8080")


async def run_playwright_test(
    test_code: str,
    test_file_name: str = "e2e_test.spec.ts",
    timeout_ms: int = 60000,
) -> dict:
    """Write test_code to a temp file and execute it via Playwright."""
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as tmp:
        test_path = pathlib.Path(tmp) / test_file_name
        test_path.write_text(test_code)

        cmd = [
            "npx", "playwright", "test",
            str(test_path),
            "--reporter=json",
            f"--timeout={timeout_ms}",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp,
            env={**os.environ, "BASE_URL": BASE_URL},
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_ms / 1000 + 30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"passed": False, "error": "timeout", "tests": []}

        try:
            report = json.loads(stdout.decode())
        except json.JSONDecodeError:
            report = {"raw": stdout.decode()}

        return {
            "passed":   proc.returncode == 0,
            "returncode": proc.returncode,
            "report":   report,
            "stderr":   stderr.decode(),
        }


async def screenshot_page(url: str, output_path: str = "/tmp/screenshot.png") -> str:
    """Capture a screenshot of a URL using Playwright headless."""
    script = f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('{url}', {{ waitUntil: 'networkidle' }});
  await page.screenshot({{ path: '{output_path}', fullPage: true }});
  await browser.close();
}})();
"""
    import tempfile, pathlib
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(script)
        script_path = f.name

    proc = await asyncio.create_subprocess_exec(
        "node", script_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    os.unlink(script_path)
    return output_path


async def check_page_loads(url: str, timeout_ms: int = 10000) -> dict:
    """Return HTTP status and title of a page."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=timeout_ms / 1000, follow_redirects=True)
            return {"status": r.status_code, "ok": r.status_code < 400, "url": str(r.url)}
    except Exception as e:
        return {"status": 0, "ok": False, "error": str(e), "url": url}


async def generate_playwright_spec(
    story_description: str,
    base_url: str,
    test_scenarios: list[dict],
) -> str:
    """Return a Playwright TypeScript spec string for the given scenarios."""
    lines = [
        "import { test, expect } from '@playwright/test';",
        "",
        f"const BASE_URL = process.env.BASE_URL || '{base_url}';",
        "",
    ]
    for i, scenario in enumerate(test_scenarios):
        name = scenario.get("name", f"scenario_{i+1}")
        steps = scenario.get("steps", [])
        lines.append(f"test('{name}', async ({{ page }}) => {{")
        for step in steps:
            lines.append(f"  {step}")
        lines.append("});")
        lines.append("")
    return "\n".join(lines)
