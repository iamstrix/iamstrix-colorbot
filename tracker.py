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
import json
import os

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

def send_atomic_down(x, y):
    """Moves to absolute coordinates and holds left click down."""
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    norm_x = int((x * 65535) / (screen_w - 1))
    norm_y = int((y * 65535) / (screen_h - 1))
    
    events = (Input * 2)()
    events[0].type = win32con.INPUT_MOUSE
    events[0].ii.mi.dx = norm_x
    events[0].ii.mi.dy = norm_y
    events[0].ii.mi.dwFlags = win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
    
    events[1].type = win32con.INPUT_MOUSE
    events[1].ii.mi.dwFlags = win32con.MOUSEEVENTF_LEFTDOWN
    
    ctypes.windll.user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(Input))

def send_mouse_up():
    """Releases the left click."""
    event = Input()
    event.type = win32con.INPUT_MOUSE
    event.ii.mi.dwFlags = win32con.MOUSEEVENTF_LEFTUP
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))

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

# Middle-click drag (Deadzone Exclusion Regions)
deadzones = []  # List of (x1, y1, x2, y2) in preview coordinates
dz_start = None
dz_end = None
dz_drawing = False
exact_input_request = None

# ROI tracking mode toggle
high_res_enabled = True
full_native_mode = False

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
    {"active": True, "hsv": (8, 18, 80, 255, 40, 110), "min_area": 1000},
    {"active": False, "hsv": (0, 0, 0, 255, 0, 255), "min_area": 1000},
    {"active": False, "hsv": (0, 0, 0, 255, 0, 255), "min_area": 1000}
]
selected_slot = 0

prioritized_slots = []

def get_ordered_slots():
    order = []
    for idx in prioritized_slots:
        order.append(idx)
    for i in range(len(color_slots)):
        if i not in prioritized_slots:
            order.append(i)
    return order

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
GRID_LEFT = 1070                 # Column 3 Grid Left in main canvas (x = 1070)
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
    "Click Speed (CPS)": {"val": 0,    "min": -1, "max": 50,  "desc": "-1=Hold, 0=Off, 1-50=Auto-click rate."},
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
    global active_slider_drag, drag_start, drag_end, drawing_rect, calibrate_request
    global lock_area_start, lock_area_end, drawing_lock_area, lock_area_active
    global deadzones, dz_start, dz_end, dz_drawing, exact_input_request
    global macro_drawing, macro_path_cells, macro_last_cell, macro_steps
    global selected_slot, color_slots, prioritized_slots
    
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
        elif dz_drawing:
            dz_end = (x, y)
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
                    c_ma = color_slots[i].get("min_area", 1000)
                    set_val("Low H", lh)
                    set_val("High H", hh)
                    set_val("Low S", ls)
                    set_val("High S", hs)
                    set_val("Low V", lv)
                    set_val("High V", hv)
                    set_val("Min Area", c_ma)
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
        hit = check_slider_hit(x, y)
        if hit:
            exact_input_request = hit
            return
            
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
                    if i in prioritized_slots:
                        prioritized_slots.remove(i)
                        print(f"[INFO] Removed Color Slot {i+1} from priority list.")
                    else:
                        prioritized_slots.append(i)
                        print(f"[INFO] Prioritized Color Slot {i+1} (Rank: {len(prioritized_slots)}).")
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
                
    elif event == cv2.EVENT_MBUTTONDOWN:
        if x < PREVIEW_W and y < PREVIEW_H:
            dz_start = (x, y)
            dz_end = (x, y)
            dz_drawing = True
        elif y >= PREVIEW_H:
            box_width = 100
            box_spacing = 30
            start_x = (PREVIEW_W * 2 - (3 * box_width + 2 * box_spacing)) // 2
            
            for i in range(3):
                box_x = start_x + i * (box_width + box_spacing)
                if box_x <= x <= box_x + box_width:
                    color_slots[i]["active"] = False
                    if i in prioritized_slots:
                        prioritized_slots.remove(i)
                    if i == 0:
                        color_slots[i]["hsv"] = (8, 18, 80, 255, 40, 110)
                    else:
                        color_slots[i]["hsv"] = (0, 0, 0, 255, 0, 255)
                    color_slots[i]["active"] = (i == 0)
                    color_slots[i]["min_area"] = 1000
                    if selected_slot == i:
                        min_h, max_h, min_s, max_s, min_v, max_v = color_slots[i]["hsv"]
                        set_val("Low H", min_h)
                        set_val("High H", max_h)
                        set_val("Low S", min_s)
                        set_val("High S", max_s)
                        set_val("Low V", min_v)
                        set_val("High V", max_v)
                        set_val("Min Area", 1000)
                        
                    print(f"[INFO] Cleared & Reset Color Slot {i+1} to default settings.")
                    break
            
    elif event == cv2.EVENT_MBUTTONUP:
        if dz_drawing:
            dz_end = (x, y)
            dz_drawing = False
            if dz_start is not None:
                x1, y1 = dz_start
                x2, y2 = dz_end
                x_min, x_max = min(x1, x2), max(x1, x2)
                y_min, y_max = min(y1, y2), max(y1, y2)
                if (x_max - x_min) > 5 and (y_max - y_min) > 5:
                    deadzones.append((x_min, y_min, x_max, y_max))
                    print(f"[SUCCESS] Added Deadzone #{len(deadzones)}: X={x_min}-{x_max}, Y={y_min}-{y_max}")
                else:
                    # Single middle-click: delete the deadzone box under cursor (top-most / most recent first)
                    deleted = False
                    for idx in range(len(deadzones) - 1, -1, -1):
                        dz_x1, dz_y1, dz_x2, dz_y2 = deadzones[idx]
                        if dz_x1 <= x <= dz_x2 and dz_y1 <= y <= dz_y2:
                            removed_dz = deadzones.pop(idx)
                            print(f"[INFO] Deleted Deadzone #{idx + 1}: X={removed_dz[0]}-{removed_dz[2]}, Y={removed_dz[1]}-{removed_dz[3]}")
                            deleted = True
                            break
                    if not deleted:
                        print("[INFO] No deadzone found under cursor to delete.")

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

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
MAX_PROFILES = 5
LAST_FILE = os.path.join(CONFIG_DIR, ".last")

def get_profile_path(slot_id):
    return os.path.join(CONFIG_DIR, f"profile_{slot_id + 1}.json")

def save_profile(slot_id):
    """Saves current configuration to profile JSON file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    
    # Save active slot's sliders back into color_slots first
    lh = get_val("Low H")
    hh = get_val("High H")
    ls = get_val("Low S")
    hs = get_val("High S")
    lv = get_val("Low V")
    hv = get_val("High V")
    c_ma = get_val("Min Area")
    if 0 <= selected_slot < len(color_slots):
        color_slots[selected_slot]["hsv"] = (lh, hh, ls, hs, lv, hv)
        color_slots[selected_slot]["min_area"] = c_ma

    config_data = {
        "color_slots": color_slots,
        "selected_slot": selected_slot,
        "sliders": {
            "Min Area": get_val("Min Area"),
            "Smoothing": get_val("Smoothing"),
            "Click Speed (CPS)": get_val("Click Speed (CPS)"),
            "ms/cell": get_val("ms/cell"),
            "Monitor": get_val("Monitor")
        },
        "macro_path_cells": [list(cell) for cell in macro_path_cells],
        "lock_area": {
            "start": list(lock_area_start) if lock_area_start else None,
            "end": list(lock_area_end) if lock_area_end else None,
            "active": lock_area_active
        },
        "deadzones": [list(dz) for dz in deadzones],
        "high_res_enabled": high_res_enabled,
        "full_native_mode": full_native_mode
    }
    
    file_path = get_profile_path(slot_id)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
        with open(LAST_FILE, "w", encoding="utf-8") as f:
            f.write(str(slot_id))
        print(f"[SUCCESS] Saved profile {slot_id + 1} to {file_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save profile {slot_id + 1}: {e}")

def load_profile(slot_id):
    """Loads configuration from profile JSON file if it exists."""
    global selected_slot, color_slots, macro_path_cells, macro_steps
    global lock_area_start, lock_area_end, lock_area_active, deadzones
    global high_res_enabled, full_native_mode
    
    file_path = get_profile_path(slot_id)
    if not os.path.exists(file_path):
        deadzones = []
        full_native_mode = False
        lock_area_start = None
        lock_area_end = None
        lock_area_active = False
        macro_path_cells = []
        macro_steps = []
        print(f"[INFO] Profile {slot_id + 1} does not exist yet. Defaulting to clean settings.")
        return False
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        if "color_slots" in config_data:
            for i, slot in enumerate(config_data["color_slots"]):
                if i < len(color_slots):
                    color_slots[i]["active"] = slot.get("active", False)
                    hsv = slot.get("hsv", (0, 0, 0, 255, 0, 255))
                    color_slots[i]["hsv"] = tuple(hsv)
                    color_slots[i]["min_area"] = slot.get("min_area", 1000)
                    
        if "selected_slot" in config_data:
            selected_slot = max(0, min(len(color_slots) - 1, config_data["selected_slot"]))
            
        # Update sliders with loaded slot HSV
        lh, hh, ls, hs, lv, hv = color_slots[selected_slot]["hsv"]
        set_val("Low H", lh)
        set_val("High H", hh)
        set_val("Low S", ls)
        set_val("High S", hs)
        set_val("Low V", lv)
        set_val("High V", hv)
        set_val("Min Area", color_slots[selected_slot].get("min_area", 1000))
        
        if "sliders" in config_data:
            for sname, sval in config_data["sliders"].items():
                if sname in sliders:
                    set_val(sname, sval)
                    
        if "macro_path_cells" in config_data:
            macro_path_cells = [tuple(cell) for cell in config_data["macro_path_cells"]]
            macro_steps = path_to_steps(macro_path_cells)
            
        if "lock_area" in config_data:
            la = config_data["lock_area"]
            lock_area_start = tuple(la["start"]) if la.get("start") else None
            lock_area_end = tuple(la["end"]) if la.get("end") else None
            lock_area_active = la.get("active", False)

        if "deadzones" in config_data:
            deadzones = [tuple(dz) for dz in config_data["deadzones"]]
            
        if "high_res_enabled" in config_data:
            high_res_enabled = config_data.get("high_res_enabled", True)

        if "full_native_mode" in config_data:
            full_native_mode = config_data.get("full_native_mode", False)
        else:
            deadzones = []
            
        high_res_enabled = config_data.get("high_res_enabled", True)
            
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LAST_FILE, "w", encoding="utf-8") as f:
            f.write(str(slot_id))
            
        print(f"[SUCCESS] Loaded profile {slot_id + 1} from {file_path}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to load profile {slot_id + 1}: {e}")
        return False

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
    global lock_area_start, lock_area_end, lock_area_active
    global deadzones, dz_start, dz_end, dz_drawing, exact_input_request
    global current_monitor, target_rect
    global macro_current_step, macro_running, macro_stop_event, macro_thread
    global macro_drawing, macro_path_cells, macro_last_cell, macro_steps
    global high_res_enabled, full_native_mode
    
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
    print("8. Press F2 to CYCLE profiles (1-5), F3 to SAVE current profile.")
    print("9. Press 'q' in the window to quit (auto-saves current profile).")
    print("=========================")

    # Initialize / Load active profile
    current_profile = 0
    if os.path.exists(LAST_FILE):
        try:
            with open(LAST_FILE, "r", encoding="utf-8") as f:
                last_idx = int(f.read().strip())
                if 0 <= last_idx < MAX_PROFILES:
                    current_profile = last_idx
        except Exception:
            pass

    load_profile(current_profile)

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
        {"prefix": "Smoothing:", "name": "Smoothing", "desc": "Divisor for cursor glide interpolation. Higher = smoother/slower."},
        {"prefix": "Click Speed (CPS):", "name": "Click Speed (CPS)", "desc": "-1: Hold LClick, 0: Off, 1-50: Auto-click rate."},
        {"prefix": "ms/cell:", "name": "ms/cell", "desc": "Duration in milliseconds per grid cell for WASD patrol macro movement."},
        {"prefix": "Monitor:", "name": "Monitor", "desc": "Index of display screen to capture and offset mouse cursor tracking coordinates."}
    ]

    hovered_variable = None
    hover_start_time = 0
    last_click_time = 0
    mouse_is_held = False

    last_frame = None
    lock_enabled = False
    key_was_down = False
    is_frozen = False
    f2_was_down = False
    f3_was_down = False
    f4_was_down = False
    f5_was_down = False
    f6_was_down = False
    f7_was_down = False

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
        c_ma = get_val("Min Area")
        
        # Save to currently selected slot
        color_slots[selected_slot]["hsv"] = (l_h, h_h, l_s, h_s, l_v, h_v)
        color_slots[selected_slot]["min_area"] = c_ma

        smoothing = max(1, get_val("Smoothing"))
        cps = get_val("Click Speed (CPS)")
        
        orig_h, orig_w = frame.shape[:2]
        scale_x = orig_w / float(PREVIEW_W)
        scale_y = orig_h / float(PREVIEW_H)

        target_center = None
        best_contour = None
        max_area = 0
        using_roi_mode = False
        using_full_native = False

        crop_active = False
        cx1, cy1, cx2, cy2 = 0, 0, orig_w, orig_h # Native bounds
        px1, py1, px2, py2 = 0, 0, PREVIEW_W, PREVIEW_H # Preview bounds

        if lock_area_active and lock_area_start is not None and lock_area_end is not None:
            crop_active = True
            lx1, ly1 = lock_area_start
            lx2, ly2 = lock_area_end
            
            # Preview bounds
            px1, px2 = max(0, min(lx1, lx2)), min(PREVIEW_W, max(lx1, lx2))
            py1, py2 = max(0, min(ly1, ly2)), min(PREVIEW_H, max(ly1, ly2))
            
            # Native bounds
            cx1, cx2 = int(px1 * scale_x), int(px2 * scale_x)
            cy1, cy2 = int(py1 * scale_y), int(py2 * scale_y)

        display_mask = np.zeros((PREVIEW_H, PREVIEW_W), dtype=np.uint8)

        if full_native_mode:
            using_full_native = True
            
            crop_frame = frame[cy1:cy2, cx1:cx2]
            native_hsv = cv2.cvtColor(crop_frame, cv2.COLOR_BGR2HSV)
            combined_display_mask = None
            
            kernel_native = np.ones((3, 3), np.uint8)
            
            target_found = False
            
            for slot_idx in get_ordered_slots():
                slot = color_slots[slot_idx]
                if not slot["active"]: continue
                
                sl_h, sh_h, sl_s, sh_s, sl_v, sh_v = slot["hsv"]
                slot_min_area = slot.get("min_area", 1000)
                native_mask = cv2.inRange(native_hsv, np.array([sl_h, sl_s, sl_v]), np.array([sh_h, sh_s, sh_v]))
                
                # Zero out deadzones directly on native mask
                for (dz_x1, dz_y1, dz_x2, dz_y2) in deadzones:
                    ndz_x1 = int(dz_x1 * scale_x)
                    ndz_y1 = int(dz_y1 * scale_y)
                    ndz_x2 = int(dz_x2 * scale_x)
                    ndz_y2 = int(dz_y2 * scale_y)
                    
                    ldz_x1 = max(0, ndz_x1 - cx1)
                    ldz_y1 = max(0, ndz_y1 - cy1)
                    ldz_x2 = min(cx2 - cx1, ndz_x2 - cx1)
                    ldz_y2 = min(cy2 - cy1, ndz_y2 - cy1)
                    if ldz_x2 > ldz_x1 and ldz_y2 > ldz_y1:
                        native_mask[ldz_y1:ldz_y2, ldz_x1:ldz_x2] = 0

                native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_OPEN, kernel_native)
                native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_CLOSE, kernel_native)
                
                if combined_display_mask is None:
                    combined_display_mask = native_mask.copy()
                else:
                    combined_display_mask = cv2.bitwise_or(combined_display_mask, native_mask)
                
                if target_found: 
                    continue # keep looping just to build the combined mask for visual preview
                
                native_contours, _ = cv2.findContours(native_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for contour in native_contours:
                    area = cv2.contourArea(contour)
                    if area > slot_min_area:
                        M = cv2.moments(contour)
                        if M["m00"] > 0:
                            cx_native = int(M["m10"] / M["m00"]) + cx1
                            cy_native = int(M["m01"] / M["m00"]) + cy1
                            
                            cx_prev = int(cx_native / scale_x)
                            cy_prev = int(cy_native / scale_y)
                            
                            if lock_area_active and lock_area_start is not None and lock_area_end is not None:
                                lx1, ly1 = lock_area_start
                                lx2, ly2 = lock_area_end
                                min_x, max_x = min(lx1, lx2), max(lx1, lx2)
                                min_y, max_y = min(ly1, ly2), max(ly1, ly2)
                                if not (min_x <= cx_prev <= max_x and min_y <= cy_prev <= max_y):
                                    continue

                            in_deadzone = False
                            for (dz_x1, dz_y1, dz_x2, dz_y2) in deadzones:
                                if dz_x1 <= cx_prev <= dz_x2 and dz_y1 <= cy_prev <= dz_y2:
                                    in_deadzone = True
                                    break
                            if in_deadzone:
                                continue
                                    
                            if area > max_area:
                                max_area = area
                                best_contour = contour
                                target_center = (cx_prev, cy_prev)
                                roi_center_native = (cx_native, cy_native)
                
                if best_contour is not None:
                    target_found = True
                    
            if combined_display_mask is not None:
                if (px2 - px1) > 0 and (py2 - py1) > 0:
                    preview_sized_mask = cv2.resize(combined_display_mask, (px2 - px1, py2 - py1))
                    display_mask[py1:py2, px1:px2] = preview_sized_mask

        # 1. Attempt High-Res Native ROI Crop detection if a previous target location exists
        elif high_res_enabled and roi_center_native is not None and roi_frames_count < MAX_ROI_FRAMES:
            rx, ry = roi_center_native
            half_size = ROI_SIZE_NATIVE // 2
            x1 = max(cx1, rx - half_size)
            y1 = max(cy1, ry - half_size)
            x2 = min(cx2, rx + half_size)
            y2 = min(cy2, ry + half_size)

            if (x2 - x1) > 20 and (y2 - y1) > 20:
                crop_frame = frame[y1:y2, x1:x2]
                crop_hsv = cv2.cvtColor(crop_frame, cv2.COLOR_BGR2HSV)
                
                combined_display_mask = None
                target_found = False
                
                kernel_roi = np.ones((3, 3), np.uint8)
                
                for slot_idx in get_ordered_slots():
                    slot = color_slots[slot_idx]
                    if not slot["active"]: continue
                    
                    sl_h, sh_h, sl_s, sh_s, sl_v, sh_v = slot["hsv"]
                    slot_min_area = slot.get("min_area", 1000)
                    crop_mask = cv2.inRange(crop_hsv, np.array([sl_h, sl_s, sl_v]), np.array([sh_h, sh_s, sh_v]))
                    
                    # Zero out deadzones on the ROI crop mask (convert preview->native->crop-local coords)
                    for (dz_x1, dz_y1, dz_x2, dz_y2) in deadzones:
                        # Preview coords -> Native coords
                        ndz_x1 = int(dz_x1 * scale_x)
                        ndz_y1 = int(dz_y1 * scale_y)
                        ndz_x2 = int(dz_x2 * scale_x)
                        ndz_y2 = int(dz_y2 * scale_y)
                        # Native coords -> Crop-local coords (clamped)
                        ldz_x1 = max(0, ndz_x1 - x1)
                        ldz_y1 = max(0, ndz_y1 - y1)
                        ldz_x2 = min(x2 - x1, ndz_x2 - x1)
                        ldz_y2 = min(y2 - y1, ndz_y2 - y1)
                        if ldz_x2 > ldz_x1 and ldz_y2 > ldz_y1:
                            crop_mask[ldz_y1:ldz_y2, ldz_x1:ldz_x2] = 0

                    crop_mask = cv2.morphologyEx(crop_mask, cv2.MORPH_OPEN, kernel_roi)
                    crop_mask = cv2.morphologyEx(crop_mask, cv2.MORPH_CLOSE, kernel_roi)
                    
                    if combined_display_mask is None:
                        combined_display_mask = crop_mask.copy()
                    else:
                        combined_display_mask = cv2.bitwise_or(combined_display_mask, crop_mask)
                        
                    if target_found:
                        continue
                        
                    roi_contours, _ = cv2.findContours(crop_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    
                    for contour in roi_contours:
                        area = cv2.contourArea(contour)
                        # Effective min area threshold for native cropped region
                        if area >= max(5, int(slot_min_area / (scale_x * scale_y))):
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

                                # Skip targets inside deadzones
                                in_deadzone = False
                                for (dz_x1, dz_y1, dz_x2, dz_y2) in deadzones:
                                    if dz_x1 <= cx_prev <= dz_x2 and dz_y1 <= cy_prev <= dz_y2:
                                        in_deadzone = True
                                        break
                                if in_deadzone:
                                    continue
                                        
                                if area > max_area:
                                    max_area = area
                                    best_contour = contour
                                    target_center = (cx_prev, cy_prev)
                                    roi_center_native = (cx_native, cy_native)
                                    using_roi_mode = True
                                    
                    if best_contour is not None:
                        target_found = True
                        
                if combined_display_mask is not None:
                    rx1_p, ry1_p = int(x1 / scale_x), int(y1 / scale_y)
                    rx2_p, ry2_p = int(x2 / scale_x), int(y2 / scale_y)
                    if (rx2_p - rx1_p) > 0 and (ry2_p - ry1_p) > 0:
                        preview_sized_roi = cv2.resize(combined_display_mask, (rx2_p - rx1_p, ry2_p - ry1_p))
                        display_mask[ry1_p:ry2_p, rx1_p:rx2_p] = preview_sized_roi

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
            
            crop_hsv = hsv[py1:py2, px1:px2]
            combined_display_mask = None
            target_found = False
            kernel = np.ones((3, 3), np.uint8)
            
            for slot_idx in get_ordered_slots():
                slot = color_slots[slot_idx]
                if not slot["active"]: continue
                
                sl_h, sh_h, sl_s, sh_s, sl_v, sh_v = slot["hsv"]
                slot_min_area = slot.get("min_area", 1000)
                mask = cv2.inRange(crop_hsv, np.array([sl_h, sl_s, sl_v]), np.array([sh_h, sh_s, sh_v]))
                
                # Zero out deadzones directly on the mask
                for (dz_x1, dz_y1, dz_x2, dz_y2) in deadzones:
                    ldz_x1 = max(0, dz_x1 - px1)
                    ldz_y1 = max(0, dz_y1 - py1)
                    ldz_x2 = min(px2 - px1, dz_x2 - px1)
                    ldz_y2 = min(py2 - py1, dz_y2 - py1)
                    if ldz_x2 > ldz_x1 and ldz_y2 > ldz_y1:
                        mask[ldz_y1:ldz_y2, ldz_x1:ldz_x2] = 0

                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
                
                if combined_display_mask is None:
                    combined_display_mask = mask.copy()
                else:
                    combined_display_mask = cv2.bitwise_or(combined_display_mask, mask)
                    
                if target_found:
                    continue
                    
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > slot_min_area:
                        M = cv2.moments(contour)
                        if M["m00"] > 0:
                            cx = int(M["m10"] / M["m00"]) + px1
                            cy = int(M["m01"] / M["m00"]) + py1
                            
                            if lock_area_active and lock_area_start is not None and lock_area_end is not None:
                                lx1, ly1 = lock_area_start
                                lx2, ly2 = lock_area_end
                                min_x, max_x = min(lx1, lx2), max(lx1, lx2)
                                min_y, max_y = min(ly1, ly2), max(ly1, ly2)
                                if not (min_x <= cx <= max_x and min_y <= cy <= max_y):
                                    continue

                            # Explicitly skip if centroid is inside a deadzone
                            in_deadzone = False
                            for (dz_x1, dz_y1, dz_x2, dz_y2) in deadzones:
                                if dz_x1 <= cx <= dz_x2 and dz_y1 <= cy <= dz_y2:
                                    in_deadzone = True
                                    break
                            if in_deadzone:
                                continue
                                    
                            if area > max_area:
                                max_area = area
                                best_contour = contour
                                target_center = (cx, cy)
                                
                if best_contour is not None:
                    target_found = True
            
            if combined_display_mask is not None:
                if (px2 - px1) > 0 and (py2 - py1) > 0:
                    display_mask[py1:py2, px1:px2] = combined_display_mask
            
            if target_center is not None:
                roi_center_native = (int(target_center[0] * scale_x), int(target_center[1] * scale_y))
            else:
                roi_center_native = None

        if full_native_mode:
            cv2.putText(resized_frame, "[FULL NATIVE SCAN ACTIVE]", (15, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)

        if best_contour is not None and target_center is not None:
            if using_full_native:
                nx, ny, nw, nh = cv2.boundingRect(best_contour)
                nx += cx1
                ny += cy1
                px, py = int(nx / scale_x), int(ny / scale_y)
                pw, ph = int(nw / scale_x), int(nh / scale_y)
                cv2.rectangle(resized_frame, (px, py), (px + pw, py + ph), (255, 0, 255), 2)
            elif not using_roi_mode:
                x, y, w, h = cv2.boundingRect(best_contour)
                x += px1
                y += py1
                cv2.rectangle(resized_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            cv2.circle(resized_frame, target_center, 5, (0, 0, 255), -1)
            
            if using_full_native:
                mode_tag = "Full Native"
            elif using_roi_mode:
                mode_tag = "ROI Native"
            else:
                mode_tag = "Full Search"
                
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
            
            if cps == -1:
                # Continuous Hold mode
                send_atomic_down(tx, ty)
                mouse_is_held = True
            elif should_click:
                # Group absolute snap coordinates, left click down, and left click up as ONE atomic transaction
                if mouse_is_held:
                    send_mouse_up()
                    mouse_is_held = False
                send_atomic_click(tx, ty)
                last_click_time = time.time()
            else:
                if mouse_is_held and cps != -1:
                    send_mouse_up()
                    mouse_is_held = False
                # Move cursor smoothly using SendInput absolute movement
                new_x = curr_x + (tx - curr_x) / smoothing
                new_y = curr_y + (ty - curr_y) / smoothing
                send_glide_move(new_x, new_y)
            
            cv2.putText(resized_frame, "LOCK ACTIVE", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        elif lock_enabled:
            if mouse_is_held:
                send_mouse_up()
                mouse_is_held = False
            cv2.putText(resized_frame, "LOCK ACTIVE (No Target)", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)
        else:
            if mouse_is_held:
                send_mouse_up()
                mouse_is_held = False
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

        if dz_drawing and dz_start is not None and dz_end is not None:
            cv2.rectangle(resized_frame, dz_start, dz_end, (0, 0, 255), 2)
            cv2.putText(resized_frame, "Drawing Deadzone (Exclusion Area)...", (dz_start[0], dz_start[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)

        # Render active deadzone exclusion regions
        if deadzones:
            dz_overlay = resized_frame.copy()
            for idx, (dz_x1, dz_y1, dz_x2, dz_y2) in enumerate(deadzones):
                cv2.rectangle(dz_overlay, (dz_x1, dz_y1), (dz_x2, dz_y2), (0, 0, 180), -1)
                cv2.rectangle(resized_frame, (dz_x1, dz_y1), (dz_x2, dz_y2), (0, 0, 255), 1)
                cx_dz = (dz_x1 + dz_x2) // 2
                cy_dz = (dz_y1 + dz_y2) // 2
                cv2.putText(resized_frame, "X", (cx_dz - 5, cy_dz + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
            cv2.addWeighted(dz_overlay, 0.25, resized_frame, 0.75, 0, resized_frame)

        if lock_area_active and lock_area_start is not None and lock_area_end is not None:
            lx1, ly1 = lock_area_start
            lx2, ly2 = lock_area_end
            cv2.rectangle(resized_frame, (min(lx1, lx2), min(ly1, ly2)), (max(lx1, lx2), max(ly1, ly2)), (0, 165, 255), 2)
            cv2.putText(resized_frame, "Lock Boundary Active (Right-click to clear)", (20, PREVIEW_H - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 165, 255), 1)
        elif deadzones:
            cv2.putText(resized_frame, f"Deadzones: {len(deadzones)} Active (F4 to clear)", (20, PREVIEW_H - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
        else:
            cv2.putText(resized_frame, "Tip: Middle-click drag to set Deadzones | Hover labels for guides", (20, PREVIEW_H - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (150, 150, 150), 1)

        if not high_res_enabled:
            cv2.putText(resized_frame, "HIGH-RES SCAN OFF", (PREVIEW_W - 140, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

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
        mask_bgr = cv2.cvtColor(display_mask, cv2.COLOR_GRAY2BGR)

        # Hover tooltip and exact pixel outline for color mask
        if PREVIEW_W <= mouse_x < PREVIEW_W * 2 and 0 <= mouse_y < PREVIEW_H:
            mx, my = mouse_x - PREVIEW_W, mouse_y
            # Red outline on the exact pixel(s) hovered (a small 3x3 hollow box for visibility)
            cv2.rectangle(mask_bgr, (mx - 1, my - 1), (mx + 1, my + 1), (0, 0, 255), 1)
            
            # Find which contour we are hovering over to show exact native area
            ui_contours, _ = cv2.findContours(display_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in ui_contours:
                if cv2.pointPolygonTest(contour, (mx, my), False) >= 0:
                    hover_area = cv2.contourArea(contour)
                    native_hover_area = int(hover_area * scale_x * scale_y)
                    
                    # Draw tooltip near cursor
                    tt_x, tt_y = mx + 15, my + 15
                    if tt_x + 120 > PREVIEW_W: tt_x = mx - 130 # flip if clipping right
                    if tt_y + 15 > PREVIEW_H: tt_y = my - 25 # flip if clipping bottom
                    
                    cv2.rectangle(mask_bgr, (tt_x, tt_y - 15), (tt_x + 115, tt_y + 10), (30, 30, 30), -1)
                    cv2.rectangle(mask_bgr, (tt_x, tt_y - 15), (tt_x + 115, tt_y + 10), (0, 255, 255), 1)
                    cv2.putText(mask_bgr, f"Area: {native_hover_area}", (tt_x + 5, tt_y + 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
                    break

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
            
            # Priority Badge
            if i in prioritized_slots:
                rank = prioritized_slots.index(i) + 1
                badge_text = f"P{rank}"
                cv2.rectangle(bottom_panel, (box_x + box_width - 25, box_y), (box_x + box_width, box_y + 15), (20, 120, 220), -1)
                cv2.putText(bottom_panel, badge_text, (box_x + box_width - 21, box_y + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)

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

        # Column 2: Hotkey & Controls Manual
        col2_l = 525
        col2_r = 785
        cv2.putText(macro_panel, "HOTKEY & CONTROLS MANUAL", (col2_l, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1)

        # Sub-Column 1: Keyboard Hotkeys
        cv2.putText(macro_panel, "[KEYBOARD]", (col2_l, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1)
        cv2.putText(macro_panel, "ALT      : Toggle Mouse Lock", (col2_l, 72),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "F / SPACE: Freeze Preview", (col2_l, 94),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "F2       : Cycle Profile (1-5)", (col2_l, 116),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "F3       : Save Active Profile", (col2_l, 138),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "F4       : Clear Deadzones", (col2_l, 160),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "F5       : Toggle Patrol Macro", (col2_l, 182),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "F6       : Toggle High-Res ROI", (col2_l, 204),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "F7       : Toggle Full Native Scan", (col2_l, 224),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "q        : Save & Quit", (col2_l, 244),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)

        # Sub-Column 2: Mouse Actions
        cv2.putText(macro_panel, "[MOUSE ACTIONS]", (col2_r, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1)
        cv2.putText(macro_panel, "L-Drag (Preview) : Calibrate HSV", (col2_r, 72),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "R-Drag (Preview) : Lock Boundary", (col2_r, 94),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "R-Click (Preview): Clear Boundary", (col2_r, 116),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "M-Drag/Click: Add/Del Deadzone", (col2_r, 138),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "L-Drag (Grid)    : Draw WASD Path", (col2_r, 160),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "R-Click (Grid)   : Clear Path", (col2_r, 182),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        cv2.putText(macro_panel, "L-Click (Slots)  : Select Slot", (col2_r, 204),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)

        # Column 3: 9x9 Grid & Patrol Controls
        grid_local_x = GRID_LEFT
        grid_local_y = GRID_TOP - (PREVIEW_H + SLOTS_H)

        for i in range(GRID_SIZE + 1):
            gx = grid_local_x + i * CELL_SIZE
            gy = grid_local_y + i * CELL_SIZE
            cv2.line(macro_panel, (gx, grid_local_y), (gx, grid_local_y + GRID_PX), (70, 70, 70), 1)
            cv2.line(macro_panel, (grid_local_x, gy), (grid_local_x + GRID_PX, gy), (70, 70, 70), 1)
        cv2.rectangle(macro_panel, (grid_local_x, grid_local_y), (grid_local_x + GRID_PX, grid_local_y + GRID_PX), (120, 120, 120), 2)

        ms_per_cell = max(10, get_val("ms/cell"))
        display_path = macro_path_cells
        display_steps = macro_steps if not macro_drawing else path_to_steps(macro_path_cells)

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

        seq_x = 1290
        cv2.putText(macro_panel, "PATROL CONTROLS", (seq_x, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1)
        cv2.putText(macro_panel, "1. Drag path on 9x9 grid", (seq_x, 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)
        cv2.putText(macro_panel, "2. R-Click grid to clear", (seq_x, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)
        cv2.putText(macro_panel, "3. F5 to Start / Stop macro", (seq_x, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)

        if macro_running:
            step_text = display_steps[macro_current_step][2] if 0 <= macro_current_step < len(display_steps) else "..."
            cv2.putText(macro_panel, "STATUS: RUNNING (F5 Stop)", (seq_x, 96),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 2)
        elif display_steps:
            cv2.putText(macro_panel, "STATUS: READY (F5 Start)", (seq_x, 96),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        else:
            cv2.putText(macro_panel, "STATUS: NO PATH DRAWN", (seq_x, 96),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)

        cv2.putText(macro_panel, "Step Sequence:", (seq_x, 116),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        if display_steps:
            max_display = 5
            for i, (sc, n, lbl) in enumerate(display_steps[:max_display]):
                dur = n * ms_per_cell
                color = DIR_COLORS.get(lbl, (200, 200, 200))
                marker = ">" if macro_current_step == i else " "
                cv2.putText(macro_panel, f"{marker}{i+1}. {lbl} {dur}ms", (seq_x, 134 + i * 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.36, color, 1)
            if len(display_steps) > max_display:
                cv2.putText(macro_panel, f"  +{len(display_steps) - max_display} more", (seq_x, 134 + max_display * 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.34, (140, 140, 140), 1)
            total_ms = sum(s[1] * ms_per_cell for s in display_steps)
            cv2.putText(macro_panel, f"Cycle: {total_ms}ms ({total_ms/1000:.2f}s)", (seq_x, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

        # Vertically stack top_canvas, bottom_panel, and macro_panel
        canvas = np.vstack((top_canvas, bottom_panel, macro_panel))

        # Render profile status overlay on top-right of canvas
        prof_path = get_profile_path(current_profile)
        prof_saved = os.path.exists(prof_path)
        prof_label = f"Profile {current_profile + 1}/{MAX_PROFILES}" + (" [Saved]" if prof_saved else " [New]")
        cv2.putText(canvas, f"{prof_label}  (F2: Cycle | F3: Save)", (canvas.shape[1] - 370, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 200), 1, cv2.LINE_AA)

        # Display the unified canvas in the single window
        cv2.imshow(WIN_NAME, canvas)

        # Handle F2 key for Profile Cycle
        f2_state = win32api.GetAsyncKeyState(win32con.VK_F2) & 0x8000
        f2_is_down = bool(f2_state)
        if f2_is_down and not f2_was_down:
            save_profile(current_profile)
            current_profile = (current_profile + 1) % MAX_PROFILES
            load_profile(current_profile)
        f2_was_down = f2_is_down

        # Handle F3 key for Profile Save
        f3_state = win32api.GetAsyncKeyState(win32con.VK_F3) & 0x8000
        f3_is_down = bool(f3_state)
        if f3_is_down and not f3_was_down:
            save_profile(current_profile)
        f3_was_down = f3_is_down

        # Handle F4 key for Clearing Deadzones
        f4_state = win32api.GetAsyncKeyState(win32con.VK_F4) & 0x8000
        f4_is_down = bool(f4_state)
        if f4_is_down and not f4_was_down:
            if deadzones:
                deadzones = []
                print("[INFO] All deadzones cleared.")
            else:
                print("[INFO] No active deadzones to clear.")
        f4_was_down = f4_is_down

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

        # Handle F6 key for High-Res Scan Toggle
        f6_state = win32api.GetAsyncKeyState(win32con.VK_F6) & 0x8000
        f6_is_down = bool(f6_state)
        if f6_is_down and not f6_was_down:
            high_res_enabled = not high_res_enabled
            print(f"[INFO] High-Res HSV Scanning {'ENABLED' if high_res_enabled else 'DISABLED'}.")
        f6_was_down = f6_is_down

        # Handle F7 key for Full Native Scan Toggle
        f7_state = win32api.GetAsyncKeyState(win32con.VK_F7) & 0x8000
        f7_is_down = bool(f7_state)
        if f7_is_down and not f7_was_down:
            full_native_mode = not full_native_mode
            print(f"[INFO] Full Native Screen Scanning {'ENABLED' if full_native_mode else 'DISABLED'}.")
        f7_was_down = f7_is_down

        # Handle exact input requests from UI right-clicks safely on main thread
        if exact_input_request:
            import tkinter as tk
            from tkinter import simpledialog
            
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            
            info = sliders[exact_input_request]
            val = simpledialog.askinteger("Exact Input", f"Enter exact value for {exact_input_request} ({info['min']} - {info['max']}):",
                                          initialvalue=get_val(exact_input_request), minvalue=info["min"], maxvalue=info["max"], parent=root)
            root.destroy()
            if val is not None:
                set_val(exact_input_request, val)
                print(f"[INFO] Set {exact_input_request} to {val} via exact input.")
            exact_input_request = None

        # Press 'q' to exit, 'f' or SPACEBAR to freeze/unfreeze frame
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            save_profile(current_profile)
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
