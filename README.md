# 🎯 iamstrix-colorbot

A real-time color-based screen tracker that detects entities by HSV color thresholding, locks the mouse cursor onto detected targets, and provides an integrated auto-clicker — all running externally via screen capture with no game memory injection.

## ✨ Features

### Core Tracking
- **HSV Color Detection** — Captures screen frames in real-time, converts to HSV color space, and isolates targets by configurable hue, saturation, and value ranges
- **Contour-Based Targeting** — Uses OpenCV morphological filtering and contour detection to find the largest matching entity, with a configurable minimum area threshold to reject noise
- **Smoothed Cursor Lock** — Linear interpolation (lerp) smoothing with a configurable divisor (`1` = instant snap, `20` = slow glide) for natural-looking cursor movement

### Calibration
- **Drag-to-Calibrate** — Left-click and drag a selection box around any target in the live preview. The tool analyzes the cropped HSV pixels, filters out background noise (snow, shadows) using 4th–96th percentile cuts, and auto-sets all trackbar values instantly
- **Live Trackbar Tuning** — 9 configurable sliders: `Low H`, `High H`, `Low S`, `High S`, `Low V`, `High V`, `Min Area`, `Smoothing`, `Click Speed (CPS)`
- **Hover Tooltips** — Hover over any trackbar label or slider for 1 second to display a contextual guide card explaining what the parameter does

### Lock Boundary (FOV Restriction)
- **Right-Click Drag Boundary** — Right-click and drag to define an orange boundary box. Only targets whose centers fall inside this box will be tracked; everything outside is ignored
- **Single Right-Click to Clear** — Instantly removes the boundary and reverts to full-screen tracking

### Integrated Auto-Clicker
- **Atomic SendInput Clicks** — Click events are packed as grouped `SendInput` transactions (Move + LeftDown + LeftUp in a single kernel call), preventing Windows from interpreting cursor movement as drag-and-drop
- **Configurable CPS** — Adjustable from `0` (disabled) to `50` clicks per second via trackbar
- **Synchronized with Tracking** — Clicks only fire when a target is detected and lock is active

### Screen Capture
- **DXcam (Primary)** — Uses DirectX Desktop Duplication API for ultra-low-latency, high-FPS screen capture
- **MSS (Fallback)** — Automatically falls back to cross-platform MSS screenshot capture if DXcam is unavailable

### Multi-Monitor Support
- **Dynamic Display Switching** — A `Monitor` trackbar appears automatically when multiple displays are detected; slide to switch capture source in real-time
- **Per-Monitor DPI Awareness** — Declares the process as Per-Monitor DPI Aware (`SetProcessDpiAwareness(2)`) to prevent coordinate mismatches on mixed-scaling setups (e.g., 1080p desktop + high-DPI laptop)
- **Automatic Coordinate Offsets** — Maps target coordinates from the capture frame to absolute virtual screen space using per-monitor offset calculations

### Hotkey
- **ALT Toggle** — Press `ALT` once to enable cursor lock, press again to disable. Edge-triggered (no key holding required)

---

## 📦 Installation

Requires **Python 3.8+** on **Windows**.

```bash
pip install opencv-python numpy mss pyautogui pywin32 dxcam
```

> **Note:** `dxcam` requires Windows and a DirectX-compatible GPU. If it fails to initialize, the tool falls back to `mss` automatically.

---

## 🚀 Usage

```bash
python tracker.py
```

Two windows will open:
- **Color Tuning** — Live screen capture preview with trackbar sliders and detection overlays
- **Color Mask (Tuning View)** — Black and white threshold mask (white = detected, black = rejected)

### Quick Start
1. Run `python tracker.py`
2. **Left-click drag** a box around the entity you want to track → sliders auto-calibrate
3. Verify only the target appears white in the Color Mask window
4. *(Optional)* **Right-click drag** to restrict tracking to a specific screen region
5. Press **`ALT`** to toggle cursor lock on
6. Set **Click Speed (CPS)** to your desired auto-click rate
7. Press **`q`** to quit

### Controls

| Input | Action |
|:---|:---|
| `Left-click drag` (on preview) | Select calibration area — auto-sets HSV trackbars |
| `Right-click drag` (on preview) | Set lock boundary — only track targets inside this box |
| `Right-click` (single) | Clear lock boundary |
| `ALT` | Toggle cursor lock on/off |
| `q` | Quit |

### Trackbar Reference

| Slider | Range | Description |
|:---|:---:|:---|
| Low H / High H | 0–179 | Hue range (color type) |
| Low S / High S | 0–255 | Saturation range (color intensity) |
| Low V / High V | 0–255 | Value range (brightness) |
| Min Area | 0–5000 | Minimum contour area in pixels to qualify as a target |
| Smoothing | 1–20 | Lerp divisor for cursor movement (`1` = instant, `20` = slow glide) |
| Click Speed (CPS) | 0–50 | Auto-clicks per second (`0` = disabled) |
| Monitor | 0–N | Display index to capture (only shown with 2+ monitors) |

---

## 🏗️ Architecture

```
Screen → DXcam/MSS capture → BGR frame
  → cv2.resize(800×450) → cv2.cvtColor(HSV)
  → cv2.inRange(lower, upper) → morphological open/close
  → cv2.findContours → largest contour by area
  → centroid calculation → coordinate scaling + monitor offset
  → SendInput(MOVE + LEFTDOWN + LEFTUP) atomic transaction
```

### Key Technical Decisions

- **SendInput over SetCursorPos** — `SetCursorPos` and `mouse_event` are separate syscalls with gaps between them where the OS can splice other mouse events, triggering drag-and-drop states. `SendInput` accepts an array of `INPUT` structures processed as a single kernel transaction.
- **HSV over RGB** — HSV separates color (hue) from lighting (value), making detection robust to shadows and highlights without needing per-lighting-condition tuning.
- **Percentile-based calibration** — The drag-to-calibrate algorithm uses 4th–96th percentile cuts instead of min/max to reject outlier pixels (snow glare, shadow edges) in the crop region.

---

## 📁 Project Structure

```
├── tracker.py              # Main application
├── README.md               # This file
├── draft.txt               # Development notes
└── scratch/
    ├── inspect_window.py   # Win32 window handle debug tool
    └── list_windows.py     # OpenCV window control hierarchy dumper
```

---

## ⚠️ Disclaimer

This project is built for **educational purposes** — exploring real-time computer vision, Win32 input APIs, and multi-monitor coordinate systems. Using automation tools in online multiplayer games violates most Terms of Service and may result in account bans. Use responsibly.
