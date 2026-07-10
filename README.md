# 🎯 High-Performance Game Cursor Lock Tracker

This project implements a real-time computer vision system that captures the screen, detects a target entity based on its color, and locks the mouse cursor onto the target when toggling a hotkey (`ALT`).

## 🚀 Feasibility & Architecture Options (July 2026 Research)

Depending on the complexity of the target game and your performance requirements, there are two primary methods for implementing this:

### 1. Classical Computer Vision (Our Implementation)
*   **How it works**: Uses color thresholding (HSV color space conversion) and contour detection to identify the target's shape and center.
*   **Feasibility**: **Extremely High**. It runs in under 5ms per frame, requires no training data, and allows for real-time adjustments via trackbars.
*   **Best for**: Bright environments, solid color enemies (like the brown mammoth on white snow), or unique color palettes.

### 2. Deep Learning / Object Detection (Neural Network)
*   **How it works**: Uses modern object detection models (e.g., **YOLO26**, released in early 2026 for edge devices). You annotate 50-100 screenshots of the mammoth, train the model, and run it in real-time.
*   **Feasibility**: **Moderate to High**. It is highly robust to animations, lighting, rotations, and partial blockage. However, it requires setting up PyTorch/CUDA, labeling dataset frames, and increases CPU/GPU usage.
*   **Best for**: Games where colors blend into the background, enemies have complex patterns, or anti-cheat engines are highly sophisticated.

---

## 🛠️ Performance-Optimized Setup

To achieve the lowest latency and highest FPS (up to 240 FPS for gaming), we use:
1.  **DXcam** (DirectX Desktop Duplication API) for screen capture (falls back to **MSS** if unavailable).
2.  **Win32 APIs** (`pywin32`) for low-overhead, fast cursor positioning.

### 📦 Installation

To run this tool, open your command prompt (cmd/PowerShell) and install the required libraries:

```bash
pip install opencv-python numpy mss pyautogui pywin32 dxcam
```

> [!NOTE]
> `dxcam` is highly recommended for Windows gaming. If it fails to initialize, the script will gracefully fall back to `mss`.

---

## 🎮 How to Use

1.  **Run the script**:
    ```bash
    python tracker.py
    ```
2.  **Drag-to-Calibrate (Left-Click Drag)**:
    *   Left-click and **drag a box** around the mammoth (or any other entity you want to track) directly inside the video window.
    *   A blue rectangle will appear showing your selection.
    *   Release the click. The software will instantly analyze the pixels in your cropped box, reject background snow/shadows, and **auto-set all trackbars** to target that color.
3.  **Mouse-Lock Boundary (Right-Click Drag)**:
    *   Right-click and **drag a box** over any region of the image canvas.
    *   An orange rectangle will appear.
    *   Once set, the mouse cursor will **only lock onto targets inside this boundary box**. Any mammoths outside this boundary are ignored.
    *   **Single right-click** anywhere on the canvas to instantly clear this boundary and track the full screen again.
4.  **Integrated Auto-Clicker (Click Speed Slider)**:
    *   To prevent Windows "Drag-and-Drop" coordinate conflicts, slide the **`Click Speed (CPS)`** trackbar to your desired rate (e.g. `10` or `20` clicks per second).
    *   Setting this to `0` disables the clicker.
    *   The clicks are executed atomically in the loop while the cursor coordinates are steady, avoiding the drag-and-drop cursor bug entirely.
5.  **Adjust the sliders (Optional Manual Override)**:
    *   Two windows will open: **Color Tuning** (containing the capture canvas and sliders) and **Color Mask (Tuning View)** (the black & white threshold map).
    *   If needed, fine-tune the `Low H`, `Low S`, `Low V` and `High H`, `High S`, `High V` sliders until **only the mammoth is white** on the black background.
    *   Use `Min Area` to filter out tiny particles or background noise.
    *   Use `Smoothing` to control how fast or smooth the cursor glides toward the target (lower = faster snap, higher = smoother drift).
6.  **Native Slider Tooltips**:
    *   **Hover your mouse cursor over any trackbar label (e.g., `Low H: 5`) or the slider itself for $\ge 1$ second**.
    *   A premium, semi-transparent guide card will overlay the top of the screen canvas containing a detailed description of what that setting does and how to calibrate it.
7.  **Monitor / Screen Toggle (Multi-Monitor Setup)**:
    *   If you have multiple displays connected, a **`Monitor`** slider will dynamically appear.
    *   Slide it (`0`, `1`, `2`, etc.) to switch the screen being captured in real-time.
    *   The cursor absolute coordinates will automatically offset to match the selected display coordinates.
8.  **Active Target Locking (Toggle)**:
    *   Press the **`ALT`** key once to turn the target locking **ON**.
    *   Press the **`ALT`** key again to turn it **OFF** and regain manual mouse control immediately.
9.  **Quit**: Press **`q`** with the window active to close the program.
