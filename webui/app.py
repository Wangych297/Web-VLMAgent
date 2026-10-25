from __future__ import annotations

from pathlib import Path

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Streamlit is not installed. Run: python -m pip install -r requirements.txt") from exc

from eval.metrics import load_json


def main() -> None:
    st.set_page_config(page_title="Web-VLMAgent Trace Replay", layout="wide")
    st.title("Web-VLMAgent Trace Replay")

    trace_root = Path(st.text_input("Trace root", "traces/task_run"))
    run_dirs = sorted([path for path in trace_root.glob("*") if (path / "run_summary.json").exists()])

    if not run_dirs:
        st.info("No runs found. Run scripts/run_task.py or scripts/run_batch_eval.py first.")
        return

    selected_run = st.selectbox("Run", run_dirs, format_func=lambda path: path.name)
    summary = load_json(selected_run / "run_summary.json", {})
    config = load_json(selected_run / "run_config.json", {})

    st.subheader("Run Summary")
    st.json(summary)
    with st.expander("Run Config"):
        st.json(config)

    steps_used = int(summary.get("steps_used") or 0)
    step_dirs = [selected_run / f"step_{index:03d}" for index in range(steps_used)]
    if not step_dirs:
        step_dirs = sorted(path for path in selected_run.glob("step_*") if path.is_dir())

    selected_step = st.selectbox("Step", step_dirs, format_func=lambda path: path.name)
    show_step(selected_step)


def show_step(step_dir: Path) -> None:
    st.subheader(step_dir.name)
    left, right = st.columns(2)
    screenshot = step_dir / "screenshot.png"
    marked = step_dir / "marked_screenshot.png"

    with left:
        st.caption("screenshot.png")
        if screenshot.exists():
            st.image(str(screenshot))
    with right:
        st.caption("marked_screenshot.png")
        if marked.exists():
            st.image(str(marked))

    tabs = st.tabs(
        [
            "Candidates",
            "Accessibility Tree",
            "Page Text",
            "Prompt",
            "Raw Response",
            "Parsed Action",
            "Execution",
            "Progress",
            "Final Verification",
        ]
    )
    with tabs[0]:
        st.json(load_json(step_dir / "candidates.json", []))
    with tabs[1]:
        st.text(
            (step_dir / "accessibility_tree.txt").read_text(encoding="utf-8")
            if (step_dir / "accessibility_tree.txt").exists()
            else ""
        )
    with tabs[2]:
        st.text(
            (step_dir / "page_text.txt").read_text(encoding="utf-8")
            if (step_dir / "page_text.txt").exists()
            else ""
        )
    with tabs[3]:
        st.text((step_dir / "prompt.txt").read_text(encoding="utf-8") if (step_dir / "prompt.txt").exists() else "")
    with tabs[4]:
        st.text(
            (step_dir / "raw_response.txt").read_text(encoding="utf-8")
            if (step_dir / "raw_response.txt").exists()
            else ""
        )
    with tabs[5]:
        st.json(load_json(step_dir / "parsed_action.json", {}))
    with tabs[6]:
        st.json(load_json(step_dir / "execution_result.json", {}))
    with tabs[7]:
        st.json(load_json(step_dir / "progress_check.json", {}))
    with tabs[8]:
        st.json(load_json(step_dir / "final_verification.json", {}))


if __name__ == "__main__":
    main()
