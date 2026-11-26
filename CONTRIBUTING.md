# Contributing to Nexus

Thanks for helping! Nexus is a small project, so the process is light.

## Setup

```powershell
git clone https://github.com/Manuelcarvajal09/nexus.git
cd nexus
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

The test suite runs offscreen and never touches your real clipboard, data or global hotkeys. Please keep it that way: tests use temporary databases and fake clipboard providers (see `tests/conftest.py`).

To see the interface with sample data (no clipboard, no hotkeys):

```powershell
.venv\Scripts\python tools\windows_design_review.py --output review --theme liquid_glass
```

## Guidelines

- Keep changes small and focused; one topic per pull request.
- Add or update a test for every behaviour change.
- Never log clipboard content. Diagnostics may contain counts, ids and result codes only.
- Interface text goes through `tr()` (`src/nexus_linux/i18n.py`): write the Spanish source text and add its English translation to `i18n_en.py`; `tests/test_i18n.py` fails if one is missing. New languages are welcome.
- Windows-specific code lives in `src/nexus_linux/windows_*.py`. Shared modules (`storage.py`, `web_preview.py`, …) are also used by the Linux build, so keep them platform-neutral.

## Good first issues

- A third interface language.
- New card types (for example phone numbers or emails).
- Testing on different Windows versions, DPI settings and multi-monitor setups.

## Reporting bugs

Open an issue with your Windows version, display scaling, the steps to reproduce and, if relevant, the lines of `%LOCALAPPDATA%\Nexus\cache\nexus.log` around the time of the problem (the log never contains clipboard content).
