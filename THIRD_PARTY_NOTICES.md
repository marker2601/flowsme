# Third-party software

FlowSME uses these open-source packages. Their licences allow commercial use; the notices below must travel
with the apps and the server.

| Package | Used in | Licence |
|---|---|---|
| [marked](https://github.com/markedjs/marked) 12.0.2 | Report pages (built into every page; full text in [flowsme/assets/marked.LICENSE.md](flowsme/assets/marked.LICENSE.md)) | MIT |
| [mermaid](https://github.com/mermaid-js/mermaid) 10.9.1 | Diagrams on report pages (loaded from jsDelivr, pinned by hash) | MIT |
| [pynput](https://github.com/moses-palmer/pynput) | Recorder: mouse and keyboard events | LGPL-3.0 |
| [mss](https://github.com/BoboTiG/python-mss) | Recorder: screenshots | MIT |
| [Pillow](https://github.com/python-pillow/Pillow) | Screenshots and report images | MIT-CMU |
| [psutil](https://github.com/giampaolo/psutil) | Recorder: which application is active | BSD-3-Clause |
| [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) | Recorder: button, field and cell names | Apache-2.0 |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) | Recorder: narration | MIT |
| [sv-ttk](https://github.com/rdbende/Sun-Valley-ttk-theme) | Windows 11 look of the apps | MIT |
| [openai](https://github.com/openai/openai-python) | Calls to Meta's OpenAI-compatible Model API | Apache-2.0 |
| [ddgs](https://github.com/deedy5/ddgs) | Web search for tools (fallback) | MIT |
| [FastAPI](https://github.com/fastapi/fastapi), [Starlette](https://github.com/encode/starlette) | Server API | MIT, BSD-3-Clause |
| [Uvicorn](https://github.com/encode/uvicorn) | Server | BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | Server: uploads | Apache-2.0 |
| [ffmpeg](https://ffmpeg.org) | Reading screen videos (installed separately, or in the server's Docker image) | LGPL-2.1+ / GPL |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | Building the Windows apps (bootloader exception) | GPL-2.0 with exception |

**pynput (LGPL-3.0)** is shipped unmodified inside FlowSME Recorder. Under the LGPL, a customer may replace it
with their own build; on request, FlowSME provides the files needed to do that (pynput's source is at the link
above). The Docker image's ffmpeg is the Debian package, with its source available from Debian.
