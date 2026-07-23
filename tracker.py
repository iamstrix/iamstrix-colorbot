import cv2
import numpy as np
import mss
import pyautogui
import time
import win32api
import win32gui
import win32con
import ctypes
import threading

# Attempt to import dxcam for ultra-high FPS DirectX capture
DXCAM_AVAILABLE = False
try:
    import dxcam
    DXCAM_AVAILABLE = True
except ImportError:
    pass

# Set pyautogui safety settings
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# Hotkey to enable mouse lock (ALT key)
LOCK_HOTKEY = win32con.VK_MENU

# Ctypes structures for SendInput (allows grouping move + down + up into an atomic transaction)
PUL = ctypes.POINTER(ctypes.c_ulong)

class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL)
    ]

class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
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
        ("mi", MouseInput),
        ("ki", KeyboardInput),
        ("hi", HardwareInput)
    ]

class Input(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", Input_I)
    ]

def send_atomic_click(x, y):
    """Snaps the cursor to (x, y) and performs a click as a single atomic transaction using SendInput."""
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    
    # 0xFFFF = 65535. Windows expects coordinates normalized inside 65535.
    norm_x = int((x * 65535) / (screen_w - 1))
    norm_y = int((y * 65535) / (screen_h - 1))
    
    # Pack three actions: Move, LeftDown, LeftUp
    events = (Input * 3)()
    
    # Event 1: Absolute Move
    events[0].type = win32con.INPUT_MOUSE
    events[0].ii.mi.dx = norm_x
    events[0].ii.mi.dy = norm_y
    events[0].ii.mi.dwFlags = win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
    
    # Event 2: Left Down
    events[1].type = win32con.INPUT_MOUSE
    events[1].ii.mi.dwFlags = win32con.MOUSEEVENTF_LEFTDOWN
    
    # Event 3: Left Up
    events[2].type = win32con.INPUT_MOUSE
    events[2].ii.mi.dwFlags = win32con.MOUSEEVENTF_LEFTUP
    
    # Inject all events together atomically
    ctypes.windll.user32.SendInput(3, ctypes.byref(events), ctypes.sizeof(Input))

def send_glide_move(x, y):
    """Moves the cursor smoothly to absolute coordinates using SendInput."""
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    norm_x = int((x * 65535) / (screen_w - 1))
    norm_y = int((y * 65535) / (screen_h - 1))
    
    event = Input()
    event.type = win32con.INPUT_MOUSE
    event.ii.mi.dx = norm_x
    event.ii.mi.dy = norm_y
    event.ii.mi.dwFlags = win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
    
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))

# Mouse tracking globals for drag-to-calibrate (left-click) and mouse-lock area (right-click)
mouse_x = -1
mouse_y = -1

# Left-click drag (Color Calibration)
drag_start = None
drag_end = None
drawing_rect = False
calibrate_request = False

# Right-click drag (Mouse-Lock Area Boundary)
lock_area_start = None
lock_area_end = None
drawing_lock_area = False
lock_area_active = False

# Keyboard Scan Codes for WASD
SCAN_W = 0x11
SCAN_A = 0x1E
SCAN_S = 0x1F
SCAN_D = 0x20

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

def press_key(scan_code):
    event = Input()
    event.type = INPUT_KEYBOARD
    event.ii.ki.wScan = scan_code
    event.ii.ki.dwFlags = KEYEVENTF_SCANCODE
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))

def release_key(scan_code):
    event = Input()
    event.type = INPUT_KEYBOARD
    event.ii.ki.wScan = scan_code
    event.ii.ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))

def release_all_keys():
    for sc in [SCAN_W, SCAN_A, SCAN_S, SCAN_D]:
        release_key(sc)

# Multi-color targeting state
color_slots = [
    {"active": True, "hsv": (8, 18, 80, 255, 40, 110)},
    {"active": False, "hsv": (0, 0, 0, 255, 0, 255)},
    {"active": False, "hsv": (0, 0, 0, 255, 0, 255)}
]
selected_slot = 0

# Movement Macro State
macro_drawing = False
macro_path_cells = []       # List of (col, row) cells in the drawn path
macro_last_cell = None      # Last cell registered during drag
macro_steps = []            # List of (scan_code, num_cells, label) after path finalized

macro_running = False
macro_thread = None
macro_stop_event = None
macro_current_step = -1

# Layout Dimensions
PREVIEW_W = 800
PREVIEW_H = 450
SLOTS_H = 45
MACRO_H = 260

GRID_SIZE = 9
CELL_SIZE = 22
GRID_PX = GRID_SIZE * CELL_SIZE  # 198px
GRID_LEFT = 1080                 # Column 3 Grid Left in main canvas (x = 1080)
GRID_TOP = PREVIEW_H + SLOTS_H + 35  # 530px in main canvas

# Custom Dark Mode Sliders State
sliders = {
    "Low H":             {"val": 8,    "min": 0, "max": 179, "desc": "Low limit for Hue (color type)."},
    "High H":            {"val": 18,   "min": 0, "max": 179, "desc": "High limit for Hue (color type)."},
    "Low S":             {"val": 80,   "min": 0, "max": 255, "desc": "Low limit for Saturation (color intensity)."},
    "High S":            {"val": 255,  "min": 0, "max": 255, "desc": "High limit for Saturation."},
    "Low V":             {"val": 40,   "min": 0, "max": 255, "desc": "Low limit for Value (brightness)."},
    "High V":            {"val": 110,  "min": 0, "max": 255, "desc": "High limit for Value."},
    "Min Area":          {"val": 1000, "min": 0, "max": 5000, "desc": "Minimum target size in pixels."},
    "Smoothing":         {"val": 3,    "min": 1, "max": 20,  "desc": "Divisor for cursor glide interpolation."},
    "Click Speed (CPS)": {"val": 0,    "min": 0, "max": 50,  "desc": "Auto-click rate."},
    "ms/cell":           {"val": 100,  "min": 10, "max": 500, "desc": "Duration per grid cell for WASD macro."},
    "Monitor":           {"val": 0,    "min": 0, "max": 0,   "desc": "Index of display screen to capture."}
}

active_slider_drag = None

def get_val(name):
    return sliders[name]["val"]

def set_val(name, val):
    info = sliders[name]
    sliders[name]["val"] = max(info["min"], min(info["max"], int(val)))

SLIDER_LAYOUT = [
    ("Low H", 25, 45, 210),
    ("High H", 265, 45, 210),
    ("Low S", 25, 90, 210),
    ("High S", 265, 90, 210),
    ("Low V", 25, 135, 210),
    ("High V", 265, 135, 210),
    ("Min Area", 25, 180, 210),
    ("Smoothing", 265, 180, 210),
    ("Click Speed (CPS)", 25, 225, 210),
    ("ms/cell", 265, 225, 210),
]

def check_slider_hit(x, y):
    panel_y_start = PREVIEW_H + SLOTS_H
    for name, lx, ly, sw in SLIDER_LAYOUT:
        sy = panel_y_start + ly
        if lx <= x <= lx + sw and sy - 15 <= y <= sy + 15:
            ratio = max(0.0, min(1.0, (x - lx) / float(sw)))
            info = sliders[name]
            val = info["min"] + int(ratio * (info["max"] - info["min"]))
            set_val(name, val)
            return name
    return None

# Direction mapping: (delta_col, delta_row) -> (scan_code, label)
DIR_MAP = {
    (0, -1): (SCAN_W, "W (Up)"),
    (1, 0):  (SCAN_D, "D (Right)"),
    (0, 1):  (SCAN_S, "S (Down)"),
    (-1, 0): (SCAN_A, "A (Left)"),
}
DIR_COLORS = {
    "W (Up)":    (0, 200, 0),
    "D (Right)": (200, 200, 0),
    "S (Down)":  (0, 100, 255),
    "A (Left)":  (200, 0, 200),
}

def path_to_steps(path_cells):
    """Convert a list of (col, row) cells into merged directional steps."""
    if len(path_cells) < 2:
        return []
    steps = []
    prev = path_cells[0]
    for cell in path_cells[1:]:
        dc = cell[0] - prev[0]
        dr = cell[1] - prev[1]
        info = DIR_MAP.get((dc, dr))
        if info is None:
            prev = cell
            continue
        scan, label = info
        if steps and steps[-1][2] == label:
            steps[-1] = (steps[-1][0], steps[-1][1] + 1, steps[-1][2])
        else:
            steps.append((scan, 1, label))
        prev = cell
    return steps

def trace_orthogonal(from_cell, to_cell):
    """Trace an orthogonal path from from_cell to to_cell, one cell at a time."""
    cells = []
    curr = list(from_cell)
    target = list(to_cell)
    while curr != target:
        dc = target[0] - curr[0]
        dr = target[1] - curr[1]
        if abs(dc) >= abs(dr) and dc != 0:
            curr[0] += 1 if dc > 0 else -1
        elif dr != 0:
            curr[1] += 1 if dr > 0 else -1
        else:
            break
        cells.append(tuple(curr))
    return cells

def mouse_callback(event, x, y, flags, param):
    global mouse_x, mouse_y
    global drag_start, drag_end, drawing_rect, calibrate_request
    global lock_area_start, lock_area_end, drawing_lock_area, lock_area_active
    global color_slots, selected_slot
    global macro_drawing, macro_path_cells, macro_last_cell, macro_steps
    global active_slider_drag
    
    if event == cv2.EVENT_MOUSEMOVE:
        mouse_x = x
        mouse_y = y
        if active_slider_drag:
            for name, lx, ly, sw in SLIDER_LAYOUT:
                if name == active_slider_drag:
                    ratio = max(0.0, min(1.0, (x - lx) / float(sw)))
                    info = sliders[name]
                    val = info["min"] + int(ratio * (info["max"] - info["min"]))
                    set_val(name, val)
                    break
        elif drawing_rect:
            drag_end = (x, y)
        elif drawing_lock_area:
            lock_area_end = (x, y)
        elif macro_drawing and macro_last_cell is not None:
            gx = x - GRID_LEFT
            gy = y - GRID_TOP
            if 0 <= gx < GRID_PX and 0 <= gy < GRID_PX:
                col = max(0, min(GRID_SIZE - 1, gx // CELL_SIZE))
                row = max(0, min(GRID_SIZE - 1, gy // CELL_SIZE))
                curr = (col, row)
                if curr != macro_last_cell:
                    new_cells = trace_orthogonal(macro_last_cell, curr)
                    macro_path_cells.extend(new_cells)
                    macro_last_cell = curr
            
    elif event == cv2.EVENT_LBUTTONDOWN:
        hit = check_slider_hit(x, y)
        if hit:
            active_slider_drag = hit
        elif y >= PREVIEW_H + SLOTS_H:
            if GRID_LEFT <= x < GRID_LEFT + GRID_PX and GRID_TOP <= y < GRID_TOP + GRID_PX:
                col = (x - GRID_LEFT) // CELL_SIZE
                row = (y - GRID_TOP) // CELL_SIZE
                macro_path_cells = [(col, row)]
                macro_last_cell = (col, row)
                macro_steps = []
                macro_drawing = True
        elif y >= PREVIEW_H:
            box_width = 100
            box_spacing = 30
            start_x = (PREVIEW_W * 2 - (3 * box_width + 2 * box_spacing)) // 2
            
            for i in range(3):
                box_x = start_x + i * (box_width + box_spacing)
                if box_x <= x <= box_x + box_width:
                    selected_slot = i
                    color_slots[i]["active"] = True
                    lh, hh, ls, hs, lv, hv = color_slots[i]["hsv"]
                    set_val("Low H", lh)
                    set_val("High H", hh)
                    set_val("Low S", ls)
                    set_val("High S", hs)
                    set_val("Low V", lv)
                    set_val("High V", hv)
                    print(f"[INFO] Selected Color Slot {i+1}")
                    break
        elif x < PREVIEW_W and y < PREVIEW_H:
            drag_start = (x, y)
            drag_end = (x, y)
            drawing_rect = True
            
    elif event == cv2.EVENT_LBUTTONUP:
        active_slider_drag = None
        if drawing_rect:
            drag_end = (x, y)
            drawing_rect = False
            calibrate_request = True
        elif macro_drawing:
            macro_drawing = False
            macro_steps = path_to_steps(macro_path_cells)
            if macro_steps:
                total_cells = sum(s[1] for s in macro_steps)
                print(f"[SUCCESS] Path drawn: {len(macro_steps)} steps, {total_cells} cells")
            else:
                print("[INFO] Path too short. Draw across at least 2 cells.")
            
    elif event == cv2.EVENT_RBUTTONDOWN:
        if y >= PREVIEW_H + SLOTS_H:
            if GRID_LEFT <= x < GRID_LEFT + GRID_PX and GRID_TOP <= y < GRID_TOP + GRID_PX:
                macro_path_cells = []
                macro_steps = []
                macro_last_cell = None
                macro_drawing = False
                print("[INFO] Patrol path cleared.")
        elif y >= PREVIEW_H:
            box_width = 100
            box_spacing = 30
            start_x = (PREVIEW_W * 2 - (3 * box_width + 2 * box_spacing)) // 2
            
            for i in range(3):
                box_x = start_x + i * (box_width + box_spacing)
                if box_x <= x <= box_x + box_width:
                    color_slots[i]["active"] = False
                    print(f"[INFO] Cleared Color Slot {i+1}")
                    break
        elif x < PREVIEW_W and y < PREVIEW_H:
            lock_area_start = (x, y)
            lock_area_end = (x, y)
            drawing_lock_area = True
            lock_area_active = False
            
    elif event == cv2.EVENT_RBUTTONUP:
        if drawing_lock_area:
            lock_area_end = (x, y)
            drawing_lock_area = False
            
            x1, y1 = lock_area_start
            x2, y2 = lock_area_end
            w = abs(x2 - x1)
            h = abs(y2 - y1)
            
            if w > 5 and h > 5:
                lock_area_active = True
                print(f"[SUCCESS] Lock boundary area active: X={min(x1,x2)}-{max(x1,x2)} | Y={min(y1,y2)}-{max(y1,y2)}")
            else:
                lock_area_active = False
                lock_area_start = None
                lock_area_end = None
                print("[INFO] Lock boundary cleared. Full screen tracking active.")

def nothing(x):
    pass

def set_dpi_awareness():
    """Declares the Python process as DPI-aware to prevent coordinate scaling mismatches."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        print("[SUCCESS] Set Per-Monitor DPI Awareness.")
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            print("[SUCCESS] Set System DPI Awareness.")
        except Exception as e:
            print(f"[WARNING] Could not set DPI awareness: {e}. Multi-monitor offsets may be misaligned.")

def init_dxcam(output_idx=0):
    """Initializes DXcam if available for the given monitor index."""
    if not DXCAM_AVAILABLE:
        return None
    try:
        print(f"[INFO] Initializing DXcam (DirectX Desktop Duplication) for Monitor {output_idx}...")
        camera = dxcam.create(output_idx=output_idx)
        if camera:
            print(f"[SUCCESS] DXcam initialized successfully for Monitor {output_idx}!")
            return camera
    except Exception as e:
        print(f"[WARNING] DXcam initialization failed for Monitor {output_idx}: {e}. Falling back to MSS.")
    return None

def draw_tooltip_banner(img, name, desc):
    """Draws a premium semi-transparent guide card at the top of the image canvas."""
    box_x = 15
    box_y = 15
    box_w = img.shape[1] - 30  # Fits screen width
    box_h = 55
    
    # Create overlay for blending
    overlay = img.copy()
    cv2.rectangle(overlay, (box_x, box_y), (box_x + box_w, box_y + box_h), (25, 25, 25), -1)
    cv2.rectangle(overlay, (box_x, box_y), (box_x + box_w, box_y + box_h), (180, 180, 180), 1)
    
    # Blend overlay with transparency
    cv2.addWeighted(overlay, 0.92, img, 0.08, 0, img)
    
    # Draw text overlay
    cv2.putText(img, f"GUIDE: {name}", (box_x + 15, box_y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 2)
    cv2.putText(img, desc, (box_x + 15, box_y + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (240, 240, 240), 1)

def calibrate_color_range(hsv_crop):
    """Calculates optimal HSV bounds from a cropped region, ignoring background noise."""
    pixels = hsv_crop.reshape(-1, 3)
    
    valid_pixels = []
    for p in pixels:
        h, s, v = p
        if s < 45 and v > 180:
            continue
        if v < 30:
            continue
        valid_pixels.append(p)
        
    if len(valid_pixels) < 15:
        valid_pixels = pixels
        
    h_vals = [p[0] for p in valid_pixels]
    s_vals = [p[1] for p in valid_pixels]
    v_vals = [p[2] for p in valid_pixels]
    
    min_h = max(0, int(np.percentile(h_vals, 4)))
    max_h = min(179, int(np.percentile(h_vals, 96)))
    
    min_s = max(0, int(np.percentile(s_vals, 4)))
    max_s = min(255, int(np.percentile(s_vals, 96)))
    
    min_v = max(0, int(np.percentile(v_vals, 4)))
    max_v = min(255, int(np.percentile(v_vals, 96)))
    
    min_h = max(0, min_h - 3)
    max_h = min(179, max_h + 3)
    min_s = max(0, min_s - 15)
    max_s = min(255, max_s + 15)
    min_v = max(0, min_v - 15)
    max_v = min(255, max_v + 15)
    
    return min_h, max_h, min_s, max_s, min_v, max_v
def macro_loop(steps, ms_per_cell, stop_event):
    """Loops through a list of (scan_code, num_cells, label) steps."""
    global macro_current_step
    total = sum(s[1] * ms_per_cell for s in steps)
    step_summary = " -> ".join(f"{s[2]}:{s[1]*ms_per_cell}ms" for s in steps)
    print(f"[MACRO] Starting path loop: {step_summary} | Cycle={total}ms ({total/1000:.1f}s)")

    try:
        while not stop_event.is_set():
            for i, (scan, cells, name) in enumerate(steps):
                if stop_event.is_set():
                    break
                macro_current_step = i
                duration_ms = cells * ms_per_cell
                press_key(scan)
                elapsed = 0
                while elapsed < duration_ms and not stop_event.is_set():
                    time.sleep(0.01)
                    elapsed += 10
                release_key(scan)
    finally:
        release_all_keys()
        macro_current_step = -1
        print("[MACRO] Patrol loop stopped.")
def main():
    global drag_start, drag_end, drawing_rect, calibrate_request
    global lock_area_start, lock_area_end, drawing_lock_area, lock_area_active
    global color_slots, selected_slot
    global macro_running, macro_thread, macro_stop_event, macro_current_step
    global macro_drawing, macro_path_cells, macro_last_cell, macro_steps
    
    set_dpi_awareness()
    
    print("=== iamstrix-colorbot ===")
    print("Instructions:")
    print("1. A unified window will open with the live preview and color mask side-by-side.")
    print("2. Left-click & drag on the LIVE PREVIEW to select a color calibration area.")
    print("3. Right-click & drag on the LIVE PREVIEW to restrict mouse locking to a boundary.")
    print("   * Single right-click clears the boundary and reverts to full-screen tracking.")
    print("4. Press the ALT key to TOGGLE cursor lock ON/OFF.")
    print("5. Press 'f' or SPACEBAR to FREEZE / UNFREEZE preview for easy crop calibration.")
    print("6. Set Click Speed (CPS) to automate clicks without drag-and-drop bugs.")
    print("   * NOTE: Make sure to DISABLE any external auto-clicker macros to avoid conflicts!")
    print("7. Hover your mouse over any trackbar label/slider for 1 second to view description.")
    print("8. Press 'q' in the window to quit.")
    print("=========================")

    # Detect number of monitors and their coordinates
    num_monitors = 1
    monitor_offsets = [(0, 0)]
    try:
        sct_detect = mss.mss()
        monitors_list = sct_detect.monitors[1:]
        num_monitors = max(1, len(monitors_list))
        monitor_offsets = [(m["left"], m["top"]) for m in monitors_list]
        print(f"[INFO] Detected {num_monitors} display(s):")
        for idx, m in enumerate(monitors_list):
            print(f"  Display {idx}: Left={m['left']}, Top={m['top']}, Width={m['width']}, Height={m['height']}")
    except Exception as e:
        print(f"[WARNING] Failed to auto-detect display coordinates: {e}. Defaulting to 1 display.")

    current_monitor = 0

    camera = init_dxcam(current_monitor)
    sct = None
    monitor = None
    
    if camera is None:
        print("[INFO] Initializing MSS capture engine...")
        sct = mss.mss()
        monitor = sct.monitors[current_monitor + 1]
        print(f"[SUCCESS] MSS initialized for monitor index {current_monitor}: {monitor}")

    # Window name constant
    WIN_NAME = "iamstrix-colorbot"

    # Create unified window for preview, mask, and controls
    cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN_NAME, PREVIEW_W * 2, PREVIEW_H + SLOTS_H + MACRO_H)
    cv2.setMouseCallback(WIN_NAME, mouse_callback)

    if num_monitors > 1:
        sliders["Monitor"]["max"] = num_monitors - 1

    hover_labels = [
        {"prefix": "Low H:", "name": "Low H", "desc": "Low limit for Hue (color type). Warm brown tones usually start around 5."},
        {"prefix": "High H:", "name": "High H", "desc": "High limit for Hue (color type). Warm brown tones usually end around 20."},
        {"prefix": "Low S:", "name": "Low S", "desc": "Low limit for Saturation (color intensity). Higher values filter out gray snow."},
        {"prefix": "High S:", "name": "High S", "desc": "High limit for Saturation. Keep at 255 to capture full intensity."},
        {"prefix": "Low V:", "name": "Low V", "desc": "Low limit for Value (brightness). Lower values capture shadowed regions."},
        {"prefix": "High V:", "name": "High V", "desc": "High limit for Value. Higher values capture highlighted regions."},
        {"prefix": "Min Area:", "name": "Min Area", "desc": "Minimum target size in pixels. Filters out small background noise particles."},
        {"prefix": "Smoothing:", "name": "Smoothing", "desc": "Divisor for cursor glide interpolation. Higher is smoother; 1 is instant snap."},
        {"prefix": "Click Speed (CPS):", "name": "Click Speed (CPS)", "desc": "Auto-click rate. Synchronizes clicks with tracking to prevent dragging bugs."},
        {"prefix": "ms/cell:", "name": "ms/cell", "desc": "Duration in milliseconds per grid cell for WASD patrol macro movement."},
        {"prefix": "Monitor:", "name": "Monitor", "desc": "Index of display screen to capture and offset mouse cursor tracking coordinates."}
    ]

    hovered_variable = None
    hover_start_time = 0
    last_click_time = 0

    last_frame = None
    lock_enabled = False
    key_was_down = False
    is_frozen = False
    frozen_frame = None
    f5_was_down = False

    # ROI (Region of Interest) tracking state for high-resolution target preservation
    roi_center_native = None  # (cx_native, cy_native)
    roi_frames_count = 0
    MAX_ROI_FRAMES = 60
    ROI_SIZE_NATIVE = 260

    # Capture loop
    while True:
        try:
            if cv2.getWindowProperty(WIN_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break

        # Handle screen transition logic
        selected_monitor = get_val("Monitor") if num_monitors > 1 else 0
        if selected_monitor != current_monitor:
            print(f"[INFO] Switching capture source to Monitor {selected_monitor}...")
            if DXCAM_AVAILABLE:
                try:
                    if camera is not None:
                        del camera
                        camera = None
                    camera = init_dxcam(selected_monitor)
                except Exception as e:
                    print(f"[WARNING] DXcam switch error: {e}. Falling back to MSS.")
                    camera = None
            
            if camera is None:
                if sct is None:
                    sct = mss.mss()
                monitor = sct.monitors[selected_monitor + 1]
                print(f"[SUCCESS] MSS switched to Monitor {selected_monitor}: {monitor}")
            
            current_monitor = selected_monitor
            last_frame = None  # Reset cached frame

        frame = None
        
        # 1. Grab screen frame depending on the active engine (unless frozen)
        if is_frozen and frozen_frame is not None:
            frame = frozen_frame.copy()
        else:
            if camera is not None:
                dxcam_frame = camera.grab()
                if dxcam_frame is not None:
                    frame = cv2.cvtColor(dxcam_frame, cv2.COLOR_RGB2BGR)
                    last_frame = frame.copy()
                else:
                    if last_frame is not None:
                        frame = last_frame.copy()
            else:
                screenshot = sct.grab(monitor)
                frame = np.array(screenshot)[:, :, :3]
            
        if frame is None or frame.size == 0 or len(frame.shape) < 2 or frame.shape[0] == 0 or frame.shape[1] == 0:
            time.sleep(0.001)
            continue
        
        resized_frame = cv2.resize(frame, (PREVIEW_W, PREVIEW_H))
        hsv = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2HSV)
        
        # Handle drag-to-calibrate (smart crop) request
        if calibrate_request:
            if drag_start is not None and drag_end is not None:
                x1, y1 = drag_start
                x2, y2 = drag_end
                x_start, x_end = min(x1, x2), max(x1, x2)
                y_start, y_end = min(y1, y2), max(y1, y2)
                
                if (x_end - x_start) > 5 and (y_end - y_start) > 5:
                    hsv_crop = hsv[y_start:y_end, x_start:x_end]
                    min_h, max_h, min_s, max_s, min_v, max_v = calibrate_color_range(hsv_crop)
                    
                    set_val("Low H", min_h)
                    set_val("High H", max_h)
                    set_val("Low S", min_s)
                    set_val("High S", max_s)
                    set_val("Low V", min_v)
                    set_val("High V", max_v)
                    
                    color_slots[selected_slot]["hsv"] = (min_h, max_h, min_s, max_s, min_v, max_v)
                    print(f"[SUCCESS] Calibrated Slot {selected_slot+1} from crop selection: H={min_h}-{max_h}, S={min_s}-{max_s}, V={min_v}-{max_v}")
            calibrate_request = False

        # Read current slider positions
        l_h = get_val("Low H")
        h_h = get_val("High H")
        l_s = get_val("Low S")
        h_s = get_val("High S")
        l_v = get_val("Low V")
        h_v = get_val("High V")
        
        # Save to currently selected slot
        color_slots[selected_slot]["hsv"] = (l_h, h_h, l_s, h_s, l_v, h_v)

        min_area = get_val("Min Area")
        smoothing = max(1, get_val("Smoothing"))
        cps = get_val("Click Speed (CPS)")
        
        orig_h, orig_w = frame.shape[:2]
        scale_x = orig_w / float(PREVIEW_W)
        scale_y = orig_h / float(PREVIEW_H)

        target_center = None
        best_contour = None
        max_area = 0
        using_roi_mode = False

        # 1. Attempt High-Res Native ROI Crop detection if a previous target location exists
        if roi_center_native is not None and roi_frames_count < MAX_ROI_FRAMES:
            rx, ry = roi_center_native
            half_size = ROI_SIZE_NATIVE // 2
            x1 = max(0, rx - half_size)
            y1 = max(0, ry - half_size)
            x2 = min(orig_w, rx + half_size)
            y2 = min(orig_h, ry + half_size)

            if (x2 - x1) > 20 and (y2 - y1) > 20:
                crop_frame = frame[y1:y2, x1:x2]
                crop_hsv = cv2.cvtColor(crop_frame, cv2.COLOR_BGR2HSV)
                
                crop_mask = None
                for slot in color_slots:
                    if slot["active"]:
                        sl_h, sh_h, sl_s, sh_s, sl_v, sh_v = slot["hsv"]
                        m = cv2.inRange(crop_hsv, np.array([sl_h, sl_s, sl_v]), np.array([sh_h, sh_s, sh_v]))
                        if crop_mask is None:
                            crop_mask = m
                        else:
                            crop_mask = cv2.bitwise_or(crop_mask, m)
                
                if crop_mask is not None:
                    kernel_roi = np.ones((3, 3), np.uint8)
                    crop_mask = cv2.morphologyEx(crop_mask, cv2.MORPH_OPEN, kernel_roi)
                    crop_mask = cv2.morphologyEx(crop_mask, cv2.MORPH_CLOSE, kernel_roi)
                    
                    roi_contours, _ = cv2.findContours(crop_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    
                    for contour in roi_contours:
                        area = cv2.contourArea(contour)
                        # Effective min area threshold for native cropped region
                        if area >= max(5, int(min_area / (scale_x * scale_y))):
                            M = cv2.moments(contour)
                            if M["m00"] > 0:
                                cx_crop = int(M["m10"] / M["m00"])
                                cy_crop = int(M["m01"] / M["m00"])
                                cx_native = x1 + cx_crop
                                cy_native = y1 + cy_crop
                                
                                cx_prev = int(cx_native / scale_x)
                                cy_prev = int(cy_native / scale_y)
                                
                                if lock_area_active and lock_area_start is not None and lock_area_end is not None:
                                    lx1, ly1 = lock_area_start
                                    lx2, ly2 = lock_area_end
                                    min_x, max_x = min(lx1, lx2), max(lx1, lx2)
                                    min_y, max_y = min(ly1, ly2), max(ly1, ly2)
                                    if not (min_x <= cx_prev <= max_x and min_y <= cy_prev <= max_y):
                                        continue
                                        
                                if area > max_area:
                                    max_area = area
                                    best_contour = contour
                                    target_center = (cx_prev, cy_prev)
                                    roi_center_native = (cx_native, cy_native)
                                    using_roi_mode = True

        if using_roi_mode and target_center is not None:
            roi_frames_count += 1
            # Draw High-Res ROI boundary on preview window
            rx1, ry1 = int(x1 / scale_x), int(y1 / scale_y)
            rx2, ry2 = int(x2 / scale_x), int(y2 / scale_y)
            cv2.rectangle(resized_frame, (rx1, ry1), (rx2, ry2), (255, 255, 0), 1)
            cv2.putText(resized_frame, "HIGH-RES ROI", (rx1, max(15, ry1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
        else:
            # 2. Fallback / Periodic Full-Screen Search Mode
            roi_frames_count = 0
            mask = None
            for slot in color_slots:
                if slot["active"]:
                    sl_h, sh_h, sl_s, sh_s, sl_v, sh_v = slot["hsv"]
                    m = cv2.inRange(hsv, np.array([sl_h, sl_s, sl_v]), np.array([sh_h, sh_s, sh_v]))
                    if mask is None:
                        mask = m
                    else:
                        mask = cv2.bitwise_or(mask, m)
                        
            if mask is None:
                mask = np.zeros(hsv.shape[:2], dtype=np.uint8)

            kernel = np.ones((3, 3), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area > min_area:
                    M = cv2.moments(contour)
                    if M["m00"] > 0:
                        cx = int(M["m10"] / M["m00"])
                        cy = int(M["m01"] / M["m00"])
                        
                        if lock_area_active and lock_area_start is not None and lock_area_end is not None:
                            lx1, ly1 = lock_area_start
                            lx2, ly2 = lock_area_end
                            min_x, max_x = min(lx1, lx2), max(lx1, lx2)
                            min_y, max_y = min(ly1, ly2), max(ly1, ly2)
                            if not (min_x <= cx <= max_x and min_y <= cy <= max_y):
                                continue
                                
                        if area > max_area:
                            max_area = area
                            best_contour = contour
                            target_center = (cx, cy)
            
            if target_center is not None:
                roi_center_native = (int(target_center[0] * scale_x), int(target_center[1] * scale_y))
            else:
                roi_center_native = None

        if best_contour is not None and target_center is not None:
            if not using_roi_mode:
                x, y, w, h = cv2.boundingRect(best_contour)
                cv2.rectangle(resized_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.circle(resized_frame, target_center, 5, (0, 0, 255), -1)
            mode_tag = "ROI Native" if using_roi_mode else "Full Search"
            cv2.putText(resized_frame, f"Target ({mode_tag} Area: {int(max_area)})", (target_center[0] - 20, max(20, target_center[1] - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 2)
            
        key_state = win32api.GetAsyncKeyState(LOCK_HOTKEY) & 0x8000
        key_is_down = bool(key_state)
        
        if key_is_down and not key_was_down:
            lock_enabled = not lock_enabled
            print(f"[INFO] Lock state toggled: {'ENABLED' if lock_enabled else 'DISABLED'}")
        key_was_down = key_is_down
        
        # Absolute cursor locks and clicks using SendInput
        if lock_enabled and target_center is not None:
            curr_x, curr_y = win32api.GetCursorPos()
            
            orig_h, orig_w = frame.shape[:2]
            scale_x = orig_w / float(PREVIEW_W)
            scale_y = orig_h / float(PREVIEW_H)
            
            mapped_cx = int(target_center[0] * scale_x)
            mapped_cy = int(target_center[1] * scale_y)
            
            offset_x, offset_y = monitor_offsets[current_monitor]
            tx = mapped_cx + offset_x
            ty = mapped_cy + offset_y
            
            # Auto-click sequence
            should_click = False
            if cps > 0:
                interval = 1.0 / cps
                if time.time() - last_click_time >= interval:
                    should_click = True
            
            if should_click:
                # Group absolute snap coordinates, left click down, and left click up as ONE atomic transaction
                send_atomic_click(tx, ty)
                last_click_time = time.time()
            else:
                # Move cursor smoothly using SendInput absolute movement
                new_x = curr_x + (tx - curr_x) / smoothing
                new_y = curr_y + (ty - curr_y) / smoothing
                send_glide_move(new_x, new_y)
            
            cv2.putText(resized_frame, "LOCK ACTIVE", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        elif lock_enabled:
            cv2.putText(resized_frame, "LOCK ACTIVE (No Target)", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)
        else:
            cv2.putText(resized_frame, "LOCK INACTIVE (ALT to Toggle)", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        if drawing_rect and drag_start is not None and drag_end is not None:
            cv2.rectangle(resized_frame, drag_start, drag_end, (255, 0, 0), 2)
            cv2.putText(resized_frame, "Selecting Calibration Area...", (drag_start[0], drag_start[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 1)

        if drawing_lock_area and lock_area_start is not None and lock_area_end is not None:
            cv2.rectangle(resized_frame, lock_area_start, lock_area_end, (0, 165, 255), 2)
            cv2.putText(resized_frame, "Setting Lock Boundary...", (lock_area_start[0], lock_area_start[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1)

        if lock_area_active and lock_area_start is not None and lock_area_end is not None:
            lx1, ly1 = lock_area_start
            lx2, ly2 = lock_area_end
            cv2.rectangle(resized_frame, (min(lx1, lx2), min(ly1, ly2)), (max(lx1, lx2), max(ly1, ly2)), (0, 165, 255), 2)
            cv2.putText(resized_frame, "Lock Boundary Active (Right-click to clear)", (20, PREVIEW_H - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 165, 255), 1)
        else:
            cv2.putText(resized_frame, "Tip: Hover over trackbar labels for guides", (20, PREVIEW_H - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (150, 150, 150), 1)

        # ----------------------------------------------------
        # NATIVE WIN32 HOVER HOOK FOR TRACKBARS
        # ----------------------------------------------------
        hovered_text = None
        try:
            cursor_pos = win32api.GetCursorPos()
            hwnd = win32gui.WindowFromPoint(cursor_pos)
            if hwnd:
                parent = hwnd
                is_our_window = False
                while parent:
                    try:
                        t = win32gui.GetWindowText(parent)
                        if "iamstrix-colorbot" in t:
                            is_our_window = True
                            break
                        parent = win32gui.GetParent(parent)
                    except Exception:
                        break
                
                if is_our_window:
                    try:
                        class_name = win32gui.GetClassName(hwnd)
                        if "Static" in class_name:
                            hovered_text = win32gui.GetWindowText(hwnd)
                        elif "msctls_trackbar32" in class_name:
                            for gw_dir in [win32con.GW_HWNDPREV, win32con.GW_HWNDNEXT]:
                                try:
                                    sibling = win32gui.GetWindow(hwnd, gw_dir)
                                    if sibling:
                                        sib_class = win32gui.GetClassName(sibling)
                                        if "Static" in sib_class:
                                            hovered_text = win32gui.GetWindowText(sibling)
                                            if hovered_text:
                                                break
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        # Identify which parameter matches the hovered text prefix
        matched_label = None
        if hovered_text:
            hovered_text_clean = hovered_text.strip()
            for hl in hover_labels:
                if hovered_text_clean.startswith(hl["prefix"]):
                    matched_label = hl
                    break
        
        # Display Tooltip Card if hovering threshold is met
        if matched_label is not None:
            if hovered_variable != matched_label["name"]:
                hovered_variable = matched_label["name"]
                hover_start_time = time.time()
                print(f"[DEBUG] Hovering over: {hovered_variable}")
            else:
                elapsed = time.time() - hover_start_time
                if elapsed >= 1.0:
                    draw_tooltip_banner(resized_frame, matched_label["name"], matched_label["desc"])
        else:
            if hovered_variable is not None:
                print("[DEBUG] Stopped hovering")
            hovered_variable = None
            hover_start_time = 0

        # --- Composite the unified canvas ---
        # Convert single-channel mask to 3-channel BGR for side-by-side display
        mask_resized = cv2.resize(mask, (PREVIEW_W, PREVIEW_H))
        mask_bgr = cv2.cvtColor(mask_resized, cv2.COLOR_GRAY2BGR)

        # Draw section labels on each panel
        preview_label = "LIVE PREVIEW [FROZEN - Press F/SPACE]" if is_frozen else "LIVE PREVIEW"
        label_color = (0, 0, 255) if is_frozen else (0, 255, 255)
        cv2.putText(resized_frame, preview_label, (10, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, label_color, 1 if not is_frozen else 2)
        cv2.putText(mask_bgr, "COLOR MASK", (10, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

        # Horizontally stack both panels into a single canvas
        top_canvas = np.hstack((resized_frame, mask_bgr))

        # Draw a thin vertical divider line between the two panels
        cv2.line(top_canvas, (PREVIEW_W, 0), (PREVIEW_W, PREVIEW_H), (80, 80, 80), 2)

        # Build bottom panel for color slots
        bottom_panel = np.zeros((SLOTS_H, PREVIEW_W * 2, 3), dtype=np.uint8)
        cv2.putText(bottom_panel, "Color Targets:", (20, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (200, 200, 200), 1)
                    
        box_width = 100
        box_spacing = 30
        start_x = (PREVIEW_W * 2 - (3 * box_width + 2 * box_spacing)) // 2
        
        for i in range(3):
            box_x = start_x + i * (box_width + box_spacing)
            box_y = 6
            
            # Fill color logic
            if color_slots[i]["active"]:
                lh, hh, ls, hs, lv, hv = color_slots[i]["hsv"]
                avg_h = int((lh + hh) / 2)
                avg_s = max(150, int((ls + hs) / 2))
                avg_v = max(150, int((lv + hv) / 2))
                
                bgr_color = cv2.cvtColor(np.uint8([[[avg_h, avg_s, avg_v]]]), cv2.COLOR_HSV2BGR)[0][0]
                bgr_color = (int(bgr_color[0]), int(bgr_color[1]), int(bgr_color[2]))
                cv2.rectangle(bottom_panel, (box_x, box_y), (box_x + box_width, box_y + 36), bgr_color, -1)
            else:
                cv2.rectangle(bottom_panel, (box_x, box_y), (box_x + box_width, box_y + 36), (40, 40, 40), -1)
                cv2.putText(bottom_panel, "EMPTY", (box_x + 24, box_y + 23), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
            
            # Border
            border_color = (0, 255, 255) if i == selected_slot else (100, 100, 100)
            border_thickness = 2 if i == selected_slot else 1
            cv2.rectangle(bottom_panel, (box_x, box_y), (box_x + box_width, box_y + 36), border_color, border_thickness)

        # Build movement macro panel (three-column layout: Sliders | Controls | Grid & Steps)
        macro_panel = np.full((MACRO_H, PREVIEW_W * 2, 3), (28, 28, 28), dtype=np.uint8)

        # Column Dividers
        cv2.line(macro_panel, (510, 0), (510, MACRO_H), (60, 60, 60), 1)
        cv2.line(macro_panel, (1050, 0), (1050, MACRO_H), (60, 60, 60), 1)

        # Column 1: Custom Dark Sliders
        cv2.putText(macro_panel, "PARAMETER TUNING", (30, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

        for name, lx, ly, sw in SLIDER_LAYOUT:
            info = sliders[name]
            val = info["val"]
            min_v, max_v = info["min"], info["max"]
            ratio = (val - min_v) / float(max_v - min_v) if max_v > min_v else 0.0

            cv2.putText(macro_panel, f"{name}: {val}", (lx, ly - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 220), 1)
            cv2.line(macro_panel, (lx, ly), (lx + sw, ly), (60, 60, 60), 3)
            fill_w = int(ratio * sw)
            if fill_w > 0:
                cv2.line(macro_panel, (lx, ly), (lx + fill_w, ly), (0, 200, 255), 3)
            knob_x = lx + fill_w
            cv2.circle(macro_panel, (knob_x, ly), 5, (0, 255, 255), -1)
            cv2.circle(macro_panel, (knob_x, ly), 6, (255, 255, 255), 1)

        # Column 2: Patrol Macro Controls & Status
        mid_x = 530
        cv2.putText(macro_panel, "PATROL MACRO CONTROLS", (mid_x, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        cv2.putText(macro_panel, "1. Drag path on 9x9 grid (right)", (mid_x, 58),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)
        cv2.putText(macro_panel, "2. Right-click grid to clear path", (mid_x, 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)
        cv2.putText(macro_panel, "3. Adjust ms/cell slider on left", (mid_x, 112),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)
        cv2.putText(macro_panel, "4. Press F5 to Start / Stop macro", (mid_x, 139),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

        ms_per_cell = max(10, get_val("ms/cell"))

        display_path = macro_path_cells
        display_steps = macro_steps if not macro_drawing else path_to_steps(macro_path_cells)

        if macro_running:
            step_text = display_steps[macro_current_step][2] if 0 <= macro_current_step < len(display_steps) else "..."
            cv2.putText(macro_panel, "STATUS: MACRO ACTIVE (F5 to Stop)", (mid_x, 180),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 0, 255), 2)
            cv2.putText(macro_panel, f"Active Step: {step_text}", (mid_x, 208),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0, 255, 0), 1)
        elif display_steps:
            cv2.putText(macro_panel, "STATUS: READY (F5 to Start)", (mid_x, 180),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 255, 0), 1)
        else:
            cv2.putText(macro_panel, "STATUS: NO PATH DRAWN", (mid_x, 180),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.46, (120, 120, 120), 1)

        # Column 3: 9x9 Grid & Sequence List
        grid_local_x = GRID_LEFT   # relative to macro_panel full width
        grid_local_y = GRID_TOP - (PREVIEW_H + SLOTS_H)  # relative to macro_panel

        for i in range(GRID_SIZE + 1):
            gx = grid_local_x + i * CELL_SIZE
            gy = grid_local_y + i * CELL_SIZE
            cv2.line(macro_panel, (gx, grid_local_y), (gx, grid_local_y + GRID_PX), (70, 70, 70), 1)
            cv2.line(macro_panel, (grid_local_x, gy), (grid_local_x + GRID_PX, gy), (70, 70, 70), 1)
        cv2.rectangle(macro_panel, (grid_local_x, grid_local_y), (grid_local_x + GRID_PX, grid_local_y + GRID_PX), (120, 120, 120), 2)

        if len(display_path) >= 2:
            step_idx_for_segment = []
            temp_steps = path_to_steps(display_path)
            for si, (sc, n, lbl) in enumerate(temp_steps):
                for _ in range(n):
                    step_idx_for_segment.append((si, lbl))

            for i in range(len(display_path) - 1):
                c1, r1 = display_path[i]
                c2, r2 = display_path[i + 1]
                px1 = grid_local_x + c1 * CELL_SIZE + CELL_SIZE // 2
                py1 = grid_local_y + r1 * CELL_SIZE + CELL_SIZE // 2
                px2 = grid_local_x + c2 * CELL_SIZE + CELL_SIZE // 2
                py2 = grid_local_y + r2 * CELL_SIZE + CELL_SIZE // 2

                if i < len(step_idx_for_segment):
                    si, lbl = step_idx_for_segment[i]
                    color = DIR_COLORS.get(lbl, (200, 200, 200))
                    thickness = 3 if macro_current_step == si else 2
                else:
                    color = (200, 200, 200)
                    thickness = 2

                cv2.arrowedLine(macro_panel, (px1, py1), (px2, py2), color, thickness, tipLength=0.35)

            sc, sr = display_path[0]
            sx = grid_local_x + sc * CELL_SIZE + CELL_SIZE // 2
            sy = grid_local_y + sr * CELL_SIZE + CELL_SIZE // 2
            cv2.circle(macro_panel, (sx, sy), 5, (0, 255, 0), -1)
            cv2.circle(macro_panel, (sx, sy), 7, (255, 255, 255), 1)

        seq_x = grid_local_x + GRID_PX + 25
        cv2.putText(macro_panel, "Step Sequence:", (seq_x, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

        if display_steps:
            max_display = 7
            for i, (sc, n, lbl) in enumerate(display_steps[:max_display]):
                dur = n * ms_per_cell
                color = DIR_COLORS.get(lbl, (200, 200, 200))
                marker = ">" if macro_current_step == i else " "
                cv2.putText(macro_panel, f"{marker}{i+1}. {lbl} {dur}ms", (seq_x, 54 + i * 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1)
            if len(display_steps) > max_display:
                cv2.putText(macro_panel, f"  +{len(display_steps) - max_display} more", (seq_x, 54 + max_display * 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (140, 140, 140), 1)
            total_ms = sum(s[1] * ms_per_cell for s in display_steps)
            cv2.putText(macro_panel, f"Cycle: {total_ms}ms ({total_ms/1000:.2f}s)", (seq_x, 218),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1)

        # Vertically stack top_canvas, bottom_panel, and macro_panel
        canvas = np.vstack((top_canvas, bottom_panel, macro_panel))

        # Display the unified canvas in the single window
        cv2.imshow(WIN_NAME, canvas)
        
        # Handle F5 key for Patrol Macro Toggle
        f5_state = win32api.GetAsyncKeyState(win32con.VK_F5) & 0x8000
        f5_is_down = bool(f5_state)

        if f5_is_down and not f5_was_down:
            if not macro_running:
                if macro_steps:
                    macro_stop_event = threading.Event()
                    macro_running = True
                    macro_thread = threading.Thread(
                        target=macro_loop,
                        args=(list(macro_steps), ms_per_cell, macro_stop_event),
                        daemon=True
                    )
                    macro_thread.start()
                else:
                    print("[WARNING] No path drawn. Draw a path on the 9x9 grid first!")
            else:
                if macro_stop_event:
                    macro_stop_event.set()
                macro_running = False
                if macro_thread:
                    macro_thread.join(timeout=2)
                release_all_keys()
                macro_current_step = -1
                print("[INFO] Patrol macro stopped.")

        f5_was_down = f5_is_down

        # Press 'q' to exit, 'f' or SPACEBAR to freeze/unfreeze frame
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            if macro_running and macro_stop_event:
                macro_stop_event.set()
                if macro_thread:
                    macro_thread.join(timeout=2)
                release_all_keys()
            break
        elif key == ord('f') or key == 32:  # 'f' or SPACEBAR
            is_frozen = not is_frozen
            if is_frozen:
                if frame is not None:
                    frozen_frame = frame.copy()
                print("[INFO] Screen preview FROZEN. Drag to crop color calibration at your leisure.")
            else:
                frozen_frame = None
                print("[INFO] Screen preview UNFROZEN. Live feed resumed.")

    cv2.destroyAllWindows()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        release_all_keys()
        cv2.destroyAllWindows()
        print("\n[INFO] Exited cleanly via Ctrl+C.")
