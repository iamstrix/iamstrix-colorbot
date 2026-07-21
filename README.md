# iamstrix-colorbot

A real-time color-based screen tracker that detects entities by HSV color thresholding, locks the mouse cursor onto targets, provides an auto-clicker, and includes a spatial WASD path macro. Runs externally via screen capture with no game memory injection.

## Features

### Unified Tracking Interface
- Single Window: All preview, mask, target slots, and movement macro controls consolidated in tracker.py.
- HSV Color Detection: Real-time screen capture converted to HSV color space for lighting-independent tracking.
- Contour Targeting: Uses OpenCV morphological filtering and contour detection to isolate entities above a minimum area threshold.
- Smoothed Cursor Lock: Lerp smoothing with configurable divisor (1 for instant snap, 20 for slow glide).

### Multi-Color Targeting
- 3 Color Slots: Track up to 3 distinct HSV profiles simultaneously in a combined detection mask.
- Slot Selection: Left-click a slot box to select and tune it; right-click to clear.

### Calibration and Controls
- Freeze-Frame Calibration: Press F or SPACEBAR to pause the preview feed for precise crop selection of moving targets.
- Drag-to-Calibrate: Left-click and drag over a target to auto-calculate optimal HSV bounds using percentile filtering.
- Hover Tooltips: Hover over any trackbar label for 1 second to display contextual usage guides.
- Lock Boundary: Right-click and drag on the preview to restrict cursor locking to a specific region. Right-click once to clear.

### Freeform WASD Path Macro
- Spatial 9x9 Grid: Draw custom movement routes directly on the grid.
- Dynamic Step Generation: Converts cell-by-cell path drawings into ordered WASD key sequences with color-coded direction arrows.
- Configurable Timing: Set duration per cell via the ms/cell trackbar.
- Toggle Control: Press F5 to start or stop the background macro loop with automatic key-release safety.

### Auto-Clicker and Capture Hardware
- Atomic SendInput Clicks: Groups mouse move, down, and up events into atomic kernel transactions to prevent drag-and-drop glitches.
- Adjustable CPS: Configurable from 0 to 50 clicks per second.
- Capture Engines: Primary DirectX Desktop Duplication via dxcam, automatic fallback to mss.
- Multi-Monitor Support: Automatic coordinate scaling, per-monitor DPI awareness, and dynamic display switching.

## Installation

Requires Python 3.8+ on Windows.

```bash
pip install opencv-python numpy mss pyautogui pywin32 dxcam
```

Note: dxcam requires a DirectX-compatible GPU on Windows. If initialization fails, the tool falls back to mss.

## Usage

```bash
python tracker.py
```

### Quick Start
1. Run python tracker.py.
2. Left-click and drag over a target in the live preview to auto-calibrate HSV values.
3. (Optional) Press F or SPACEBAR to freeze the frame if the target moves quickly.
4. Press ALT to toggle cursor lock on or off.
5. Draw a movement route on the bottom 9x9 grid and press F5 to run the patrol macro.
6. Press q to exit.

### Controls

| Input | Action |
|:---|:---|
| Left-click drag (preview) | Select calibration crop area |
| Right-click drag (preview) | Set lock boundary area |
| Right-click (preview) | Clear lock boundary |
| F / SPACEBAR | Toggle freeze-frame preview |
| ALT | Toggle cursor lock on/off |
| Left-click drag (grid) | Draw freeform WASD patrol path |
| Right-click (grid) | Clear patrol path |
| F5 | Start or stop patrol macro |
| q | Quit application |

## Project Structure

```
├── tracker.py              # Main unified application
├── README.md               # Documentation
└── scratch/
    ├── inspect_window.py   # Win32 window debug tool
    └── list_windows.py     # OpenCV window handle utility
```

## Disclaimer

This project is created for educational purposes to explore computer vision, Win32 input APIs, and input automation. Use responsibly.
