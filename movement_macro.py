"""
Movement Macro - 9x9 Grid Patrol Path Editor
Draw a rectangle on the grid to define a WASD patrol loop.
Press F5 to start/stop the macro.
"""

import cv2
import numpy as np
import ctypes
import win32con
import win32api
import threading
import time

# ── Grid Constants ──────────────────────────────────────────
GRID_SIZE = 9
CELL_SIZE = 50
GRID_PX = GRID_SIZE * CELL_SIZE  # 450px

# Canvas padding so WASD labels fit outside the grid
PAD_LEFT = 85
PAD_TOP = 35
PAD_RIGHT = 85
PAD_BOTTOM = 80

CANVAS_W = PAD_LEFT + GRID_PX + PAD_RIGHT
CANVAS_H = PAD_TOP + GRID_PX + PAD_BOTTOM

# ── Keyboard Scan Codes (for SendInput) ─────────────────────
SCAN_W = 0x11
SCAN_A = 0x1E
SCAN_S = 0x1F
SCAN_D = 0x20

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

# ── Ctypes Structures for SendInput ─────────────────────────
PUL = ctypes.POINTER(ctypes.c_ulong)

class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL)
    ]

class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL)
    ]

class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort)
    ]

class Input_I(ctypes.Union):
    _fields_ = [
        ("ki", KeyboardInput),
        ("mi", MouseInput),
        ("hi", HardwareInput)
    ]

class Input(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", Input_I)
    ]

# ── Key Press Helpers ───────────────────────────────────────

def press_key(scan_code):
    """Press a key down using SendInput with scan code."""
    event = Input()
    event.type = INPUT_KEYBOARD
    event.ii.ki.wScan = scan_code
    event.ii.ki.dwFlags = KEYEVENTF_SCANCODE
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))

def release_key(scan_code):
    """Release a key using SendInput with scan code."""
    event = Input()
    event.type = INPUT_KEYBOARD
    event.ii.ki.wScan = scan_code
    event.ii.ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))

def release_all_keys():
    """Safety: release all WASD keys to prevent stuck keys."""
    for sc in [SCAN_W, SCAN_A, SCAN_S, SCAN_D]:
        release_key(sc)

# ── Drawing State ───────────────────────────────────────────
rect_start = None       # Grid cell (col, row) where drag started
rect_end = None         # Grid cell (col, row) where drag is currently
drawing = False         # True while mouse is held down
rect_cells = None       # Finalized rectangle: (min_col, min_row, max_col, max_row)

# ── Macro State ─────────────────────────────────────────────
macro_running = False
macro_thread = None
current_step = -1       # 0=W, 1=D, 2=S, 3=A

def pixel_to_grid(x, y):
    """Convert canvas pixel coordinates to grid cell (col, row), accounting for padding."""
    gx = x - PAD_LEFT
    gy = y - PAD_TOP
    col = max(0, min(GRID_SIZE - 1, gx // CELL_SIZE))
    row = max(0, min(GRID_SIZE - 1, gy // CELL_SIZE))
    return col, row

def is_inside_grid(x, y):
    """Check if the pixel coordinate is within the grid area."""
    return (PAD_LEFT <= x < PAD_LEFT + GRID_PX) and (PAD_TOP <= y < PAD_TOP + GRID_PX)

def mouse_callback(event, x, y, flags, param):
    global rect_start, rect_end, drawing, rect_cells

    if event == cv2.EVENT_LBUTTONDOWN:
        if is_inside_grid(x, y):
            col, row = pixel_to_grid(x, y)
            rect_start = (col, row)
            rect_end = (col, row)
            drawing = True

    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing:
            col, row = pixel_to_grid(x, y)
            rect_end = (col, row)

    elif event == cv2.EVENT_LBUTTONUP:
        if drawing:
            col, row = pixel_to_grid(x, y)
            rect_end = (col, row)
            drawing = False

            c1, r1 = rect_start
            c2, r2 = rect_end
            min_c, max_c = min(c1, c2), max(c1, c2)
            min_r, max_r = min(r1, r2), max(r1, r2)

            w = max_c - min_c + 1
            h = max_r - min_r + 1

            if w >= 2 and h >= 2:
                rect_cells = (min_c, min_r, max_c, max_r)
                print(f"[SUCCESS] Rectangle set: {w} cells wide x {h} cells tall")
            else:
                rect_cells = None
                print("[INFO] Rectangle too small (need at least 2x2). Try again.")

    elif event == cv2.EVENT_RBUTTONDOWN:
        rect_cells = None
        rect_start = None
        rect_end = None
        drawing = False
        print("[INFO] Rectangle cleared.")

# ── Macro Background Thread ─────────────────────────────────

def macro_loop(w_cells, h_cells, ms_per_cell, stop_event):
    """Loops WASD key presses to create a rectangular patrol path.

    Path order (clockwise):
        W (up)  -> D (right) -> S (down) -> A (left) -> repeat
    """
    global current_step

    steps = [
        (SCAN_W, h_cells, "W (Up)"),
        (SCAN_D, w_cells, "D (Right)"),
        (SCAN_S, h_cells, "S (Down)"),
        (SCAN_A, w_cells, "A (Left)")
    ]

    w_dur = h_cells * ms_per_cell
    d_dur = w_cells * ms_per_cell
    s_dur = h_cells * ms_per_cell
    a_dur = w_cells * ms_per_cell
    total = w_dur + d_dur + s_dur + a_dur
    print(f"[MACRO] Starting patrol loop: W={w_dur}ms, D={d_dur}ms, S={s_dur}ms, A={a_dur}ms | Cycle={total}ms ({total/1000:.1f}s)")

    try:
        while not stop_event.is_set():
            for i, (scan, cells, name) in enumerate(steps):
                if stop_event.is_set():
                    break
                current_step = i
                duration_ms = cells * ms_per_cell

                press_key(scan)

                # Sleep in small increments so we can respond to stop quickly
                elapsed = 0
                while elapsed < duration_ms and not stop_event.is_set():
                    time.sleep(0.01)  # 10ms tick
                    elapsed += 10

                release_key(scan)
    finally:
        # Safety: make sure nothing is stuck held down
        release_all_keys()
        current_step = -1
        print("[MACRO] Patrol loop stopped.")

# ── Grid Drawing Helpers ────────────────────────────────────

# Edge colors: W=Green, D=Cyan, S=Red(ish-orange), A=Magenta
STEP_COLORS = [
    (0, 200, 0),     # W - Green
    (200, 200, 0),   # D - Cyan
    (0, 100, 255),   # S - Orange
    (200, 0, 200)    # A - Magenta
]
STEP_LABELS = ["W (Up)", "D (Right)", "S (Down)", "A (Left)"]

def draw_arrow(canvas, start, end, color, thickness=2, tip_length=0.3):
    """Draw a line with an arrowhead."""
    cv2.arrowedLine(canvas, start, end, color, thickness, tipLength=tip_length)

def draw_grid(canvas):
    """Draw the 9x9 grid with cell lines."""
    for i in range(GRID_SIZE + 1):
        x = PAD_LEFT + i * CELL_SIZE
        y = PAD_TOP + i * CELL_SIZE
        # Vertical lines
        cv2.line(canvas, (x, PAD_TOP), (x, PAD_TOP + GRID_PX), (80, 80, 80), 1)
        # Horizontal lines
        cv2.line(canvas, (PAD_LEFT, y), (PAD_LEFT + GRID_PX, y), (80, 80, 80), 1)

    # Grid border
    cv2.rectangle(canvas, (PAD_LEFT, PAD_TOP), (PAD_LEFT + GRID_PX, PAD_TOP + GRID_PX), (120, 120, 120), 2)

def draw_rect_on_grid(canvas, min_c, min_r, max_c, max_r, ms_per_cell, is_preview=False):
    """Draw the patrol rectangle with colored edges, WASD labels, and direction arrows."""
    w_cells = max_c - min_c + 1
    h_cells = max_r - min_r + 1

    # Pixel coordinates on canvas
    px1 = PAD_LEFT + min_c * CELL_SIZE
    py1 = PAD_TOP + min_r * CELL_SIZE
    px2 = PAD_LEFT + (max_c + 1) * CELL_SIZE
    py2 = PAD_TOP + (max_r + 1) * CELL_SIZE

    # Semi-transparent fill
    overlay = canvas.copy()
    fill_color = (80, 130, 80) if not is_preview else (100, 180, 100)
    cv2.rectangle(overlay, (px1, py1), (px2, py2), fill_color, -1)
    cv2.addWeighted(overlay, 0.25, canvas, 0.75, 0, canvas)

    # Draw edges with direction colors
    # Clockwise path: Left edge (W up), Top edge (D right), Right edge (S down), Bottom edge (A left)
    edges = [
        ((px1, py2), (px1, py1), 0),   # Left edge - W (going up)
        ((px1, py1), (px2, py1), 1),   # Top edge  - D (going right)
        ((px2, py1), (px2, py2), 2),   # Right edge - S (going down)
        ((px2, py2), (px1, py2), 3),   # Bottom edge - A (going left)
    ]

    for (p1, p2, step_idx) in edges:
        color = STEP_COLORS[step_idx]
        thickness = 4 if current_step == step_idx else 2
        cv2.line(canvas, p1, p2, color, thickness)

    if not is_preview:
        # Draw direction arrows at the midpoints of each edge
        mid_x = (px1 + px2) // 2
        mid_y = (py1 + py2) // 2

        arrow_len = 15
        # W arrow (up) on left edge
        draw_arrow(canvas, (px1 - 3, mid_y + arrow_len), (px1 - 3, mid_y - arrow_len), STEP_COLORS[0], 2)
        # D arrow (right) on top edge
        draw_arrow(canvas, (mid_x - arrow_len, py1 - 3), (mid_x + arrow_len, py1 - 3), STEP_COLORS[1], 2)
        # S arrow (down) on right edge
        draw_arrow(canvas, (px2 + 3, mid_y - arrow_len), (px2 + 3, mid_y + arrow_len), STEP_COLORS[2], 2)
        # A arrow (left) on bottom edge
        draw_arrow(canvas, (mid_x + arrow_len, py2 + 3), (mid_x - arrow_len, py2 + 3), STEP_COLORS[3], 2)

        # WASD duration labels outside the grid edges
        w_dur = h_cells * ms_per_cell
        d_dur = w_cells * ms_per_cell
        s_dur = h_cells * ms_per_cell
        a_dur = w_cells * ms_per_cell

        # W label (left side)
        cv2.putText(canvas, f"W", (px1 - 55, mid_y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, STEP_COLORS[0], 2)
        cv2.putText(canvas, f"{w_dur}ms", (px1 - 70, mid_y + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

        # D label (top side)
        cv2.putText(canvas, f"D", (mid_x - 5, py1 - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, STEP_COLORS[1], 2)
        cv2.putText(canvas, f"{d_dur}ms", (mid_x - 20, py1 - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)

        # S label (right side)
        cv2.putText(canvas, f"S", (px2 + 18, mid_y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, STEP_COLORS[2], 2)
        cv2.putText(canvas, f"{s_dur}ms", (px2 + 12, mid_y + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

        # A label (bottom side)
        cv2.putText(canvas, f"A", (mid_x - 5, py2 + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, STEP_COLORS[3], 2)
        cv2.putText(canvas, f"{a_dur}ms", (mid_x - 20, py2 + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)

# ── Main ────────────────────────────────────────────────────

def main():
    global macro_running, macro_thread, current_step, rect_cells

    WIN_NAME = "Movement Macro"

    cv2.namedWindow(WIN_NAME, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN_NAME, mouse_callback)
    cv2.createTrackbar("ms/cell", WIN_NAME, 100, 500, lambda x: None)

    print("=== Movement Macro ===")
    print("Instructions:")
    print("1. Left-click & drag to draw a patrol rectangle on the 9x9 grid.")
    print("2. Right-click to clear the rectangle.")
    print("3. Adjust 'ms/cell' trackbar to set the time per grid cell (default: 100ms).")
    print("4. Press F5 to START / STOP the patrol macro.")
    print("5. Press 'q' to quit.")
    print("======================")

    stop_event = threading.Event()
    f5_was_down = False

    while True:
        try:
            if cv2.getWindowProperty(WIN_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break

        # Create dark gray canvas
        canvas = np.full((CANVAS_H, CANVAS_W, 3), (40, 40, 40), dtype=np.uint8)

        # Title
        cv2.putText(canvas, "Movement Macro - Patrol Grid", (PAD_LEFT, PAD_TOP - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)

        # Draw grid
        draw_grid(canvas)

        ms_per_cell = max(10, cv2.getTrackbarPos("ms/cell", WIN_NAME))

        # Draw preview rectangle while dragging
        if drawing and rect_start and rect_end:
            c1, r1 = rect_start
            c2, r2 = rect_end
            min_c, max_c = min(c1, c2), max(c1, c2)
            min_r, max_r = min(r1, r2), max(r1, r2)
            draw_rect_on_grid(canvas, min_c, min_r, max_c, max_r, ms_per_cell, is_preview=True)

        # Draw finalized rectangle
        if rect_cells and not drawing:
            min_c, min_r, max_c, max_r = rect_cells
            draw_rect_on_grid(canvas, min_c, min_r, max_c, max_r, ms_per_cell, is_preview=False)

        # ── Status Bar ──
        status_y = PAD_TOP + GRID_PX + 15

        if macro_running:
            step_text = STEP_LABELS[current_step] if 0 <= current_step < 4 else "..."
            # Pulsing effect for active indicator
            pulse = int(128 + 127 * np.sin(time.time() * 6))
            cv2.putText(canvas, f"MACRO RUNNING [{step_text}]", (PAD_LEFT, status_y + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, pulse), 2)
            cv2.putText(canvas, "Press F5 to Stop", (PAD_LEFT, status_y + 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
        elif rect_cells:
            min_c, min_r, max_c, max_r = rect_cells
            w_cells = max_c - min_c + 1
            h_cells = max_r - min_r + 1
            total_cycle = 2 * (w_cells + h_cells) * ms_per_cell

            cv2.putText(canvas, f"Ready. Press F5 to Start. | Cycle: {total_cycle}ms ({total_cycle/1000:.1f}s)",
                        (PAD_LEFT, status_y + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 200, 0), 1)
            cv2.putText(canvas, "Right-click to clear rectangle.", (PAD_LEFT, status_y + 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (150, 150, 150), 1)
        else:
            cv2.putText(canvas, "Draw a rectangle on the grid (Left-click & drag).",
                        (PAD_LEFT, status_y + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 180, 180), 1)

        cv2.imshow(WIN_NAME, canvas)

        # ── F5 Hotkey: Start / Stop Macro ──
        f5_state = win32api.GetAsyncKeyState(win32con.VK_F5) & 0x8000
        f5_is_down = bool(f5_state)

        if f5_is_down and not f5_was_down:
            if not macro_running:
                if rect_cells:
                    min_c, min_r, max_c, max_r = rect_cells
                    w_cells = max_c - min_c + 1
                    h_cells = max_r - min_r + 1

                    stop_event.clear()
                    macro_running = True
                    macro_thread = threading.Thread(
                        target=macro_loop,
                        args=(w_cells, h_cells, ms_per_cell, stop_event),
                        daemon=True
                    )
                    macro_thread.start()
                else:
                    print("[WARNING] No rectangle drawn. Draw one first!")
            else:
                stop_event.set()
                macro_running = False
                if macro_thread:
                    macro_thread.join(timeout=2)
                release_all_keys()
                current_step = -1
                print("[INFO] Macro stopped by user.")

        f5_was_down = f5_is_down

        # ── Key handling ──
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            if macro_running:
                stop_event.set()
                if macro_thread:
                    macro_thread.join(timeout=2)
                release_all_keys()
            break

    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
