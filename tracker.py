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
macro_rect_start = None
macro_rect_end = None
macro_drawing = False
macro_rect_cells = None

macro_running = False
macro_thread = None
macro_stop_event = None
macro_current_step = -1

GRID_SIZE = 9
CELL_SIZE = 26
GRID_PX = GRID_SIZE * CELL_SIZE  # 234px
GRID_LEFT = (640 * 2 - GRID_PX) // 2  # 523
GRID_TOP = 420 + 30  # 450

# Preview panel dimensions within the composited canvas
PREVIEW_W = 640
PREVIEW_H = 360

def mouse_callback(event, x, y, flags, param):
    global mouse_x, mouse_y
    global drag_start, drag_end, drawing_rect, calibrate_request
    global lock_area_start, lock_area_end, drawing_lock_area, lock_area_active
    global color_slots, selected_slot
    global macro_rect_start, macro_rect_end, macro_drawing, macro_rect_cells
    
    if event == cv2.EVENT_MOUSEMOVE:
        mouse_x = x
        mouse_y = y
        if drawing_rect:
            drag_end = (x, y)
        elif drawing_lock_area:
            lock_area_end = (x, y)
        elif macro_drawing:
            col = max(0, min(GRID_SIZE - 1, (x - GRID_LEFT) // CELL_SIZE))
            row = max(0, min(GRID_SIZE - 1, (y - GRID_TOP) // CELL_SIZE))
            macro_rect_end = (col, row)
            
    elif event == cv2.EVENT_LBUTTONDOWN:
        # Check if click is in Movement Macro Grid panel (y >= 420)
        if y >= 420:
            if GRID_LEFT <= x < GRID_LEFT + GRID_PX and GRID_TOP <= y < GRID_TOP + GRID_PX:
                col = (x - GRID_LEFT) // CELL_SIZE
                row = (y - GRID_TOP) // CELL_SIZE
                macro_rect_start = (col, row)
                macro_rect_end = (col, row)
                macro_drawing = True
        # Check if click is on the color slots panel (360 <= y < 420)
        elif y >= PREVIEW_H:
            box_width = 80
            box_spacing = 20
            start_x = (PREVIEW_W * 2 - (3 * box_width + 2 * box_spacing)) // 2
            
            for i in range(3):
                box_x = start_x + i * (box_width + box_spacing)
                if box_x <= x <= box_x + box_width:
                    selected_slot = i
                    color_slots[i]["active"] = True
                    lh, hh, ls, hs, lv, hv = color_slots[i]["hsv"]
                    cv2.setTrackbarPos("Low H", "iamstrix-colorbot", lh)
                    cv2.setTrackbarPos("High H", "iamstrix-colorbot", hh)
                    cv2.setTrackbarPos("Low S", "iamstrix-colorbot", ls)
                    cv2.setTrackbarPos("High S", "iamstrix-colorbot", hs)
                    cv2.setTrackbarPos("Low V", "iamstrix-colorbot", lv)
                    cv2.setTrackbarPos("High V", "iamstrix-colorbot", hv)
                    print(f"[INFO] Selected Color Slot {i+1}")
                    break
        # Only allow drag inside the live preview region (left half)
        elif x < PREVIEW_W and y < PREVIEW_H:
            drag_start = (x, y)
            drag_end = (x, y)
            drawing_rect = True
            
    elif event == cv2.EVENT_LBUTTONUP:
        if drawing_rect:
            drag_end = (x, y)
            drawing_rect = False
            calibrate_request = True
        elif macro_drawing:
            col = max(0, min(GRID_SIZE - 1, (x - GRID_LEFT) // CELL_SIZE))
            row = max(0, min(GRID_SIZE - 1, (y - GRID_TOP) // CELL_SIZE))
            macro_rect_end = (col, row)
            macro_drawing = False

            c1, r1 = macro_rect_start
            c2, r2 = macro_rect_end
            min_c, max_c = min(c1, c2), max(c1, c2)
            min_r, max_r = min(r1, r2), max(r1, r2)
            w = max_c - min_c + 1
            h = max_r - min_r + 1

            if w >= 2 and h >= 2:
                macro_rect_cells = (min_c, min_r, max_c, max_r)
                print(f"[SUCCESS] Patrol macro rectangle set: {w} cells wide x {h} cells tall")
            else:
                macro_rect_cells = None
                print("[INFO] Rectangle too small (need at least 2x2).")
            
    elif event == cv2.EVENT_RBUTTONDOWN:
        if y >= 420:
            macro_rect_cells = None
            macro_rect_start = None
            macro_rect_end = None
            macro_drawing = False
            print("[INFO] Patrol macro rectangle cleared.")
        elif y >= PREVIEW_H:
            box_width = 80
            box_spacing = 20
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
def macro_loop(w_cells, h_cells, ms_per_cell, stop_event):
    global macro_current_step
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
    global macro_rect_start, macro_rect_end, macro_drawing, macro_rect_cells
    
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
    cv2.resizeWindow(WIN_NAME, PREVIEW_W * 2, 800)
    cv2.setMouseCallback(WIN_NAME, mouse_callback)
    
    # Create trackbars for HSV tuning
    cv2.createTrackbar("Low H", WIN_NAME, 8, 179, nothing)
    cv2.createTrackbar("High H", WIN_NAME, 18, 179, nothing)
    cv2.createTrackbar("Low S", WIN_NAME, 80, 255, nothing)
    cv2.createTrackbar("High S", WIN_NAME, 255, 255, nothing)
    cv2.createTrackbar("Low V", WIN_NAME, 40, 255, nothing)
    cv2.createTrackbar("High V", WIN_NAME, 110, 255, nothing)
    cv2.createTrackbar("Min Area", WIN_NAME, 1000, 5000, nothing)
    cv2.createTrackbar("Smoothing", WIN_NAME, 3, 20, nothing)
    cv2.createTrackbar("Click Speed (CPS)", WIN_NAME, 0, 50, nothing)
    cv2.createTrackbar("ms/cell", WIN_NAME, 100, 500, nothing)

    # Only show display toggle slider if there is more than 1 display detected
    show_monitor_slider = num_monitors > 1
    if show_monitor_slider:
        cv2.createTrackbar("Monitor", WIN_NAME, 0, num_monitors - 1, nothing)

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

    # Capture loop
    while True:
        try:
            if cv2.getWindowProperty(WIN_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break

        if show_monitor_slider:
            selected_monitor = cv2.getTrackbarPos("Monitor", WIN_NAME)
        else:
            selected_monitor = 0

        # Handle screen transition logic
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
                    
                    cv2.setTrackbarPos("Low H", WIN_NAME, min_h)
                    cv2.setTrackbarPos("High H", WIN_NAME, max_h)
                    cv2.setTrackbarPos("Low S", WIN_NAME, min_s)
                    cv2.setTrackbarPos("High S", WIN_NAME, max_s)
                    cv2.setTrackbarPos("Low V", WIN_NAME, min_v)
                    cv2.setTrackbarPos("High V", WIN_NAME, max_v)
                    
                    color_slots[selected_slot]["hsv"] = (min_h, max_h, min_s, max_s, min_v, max_v)
                    print(f"[SUCCESS] Calibrated Slot {selected_slot+1} from crop selection: H={min_h}-{max_h}, S={min_s}-{max_s}, V={min_v}-{max_v}")
            calibrate_request = False

        # Read current trackbar positions
        l_h = cv2.getTrackbarPos("Low H", WIN_NAME)
        h_h = cv2.getTrackbarPos("High H", WIN_NAME)
        l_s = cv2.getTrackbarPos("Low S", WIN_NAME)
        h_s = cv2.getTrackbarPos("High S", WIN_NAME)
        l_v = cv2.getTrackbarPos("Low V", WIN_NAME)
        h_v = cv2.getTrackbarPos("High V", WIN_NAME)
        
        # Save to currently selected slot
        color_slots[selected_slot]["hsv"] = (l_h, h_h, l_s, h_s, l_v, h_v)

        min_area = cv2.getTrackbarPos("Min Area", WIN_NAME)
        smoothing = max(1, cv2.getTrackbarPos("Smoothing", WIN_NAME))
        cps = cv2.getTrackbarPos("Click Speed (CPS)", WIN_NAME)
        
        # Combine masks for all active color slots
        mask = None
        for i, slot in enumerate(color_slots):
            if slot["active"]:
                sl_h, sh_h, sl_s, sh_s, sl_v, sh_v = slot["hsv"]
                m = cv2.inRange(hsv, np.array([sl_h, sl_s, sl_v]), np.array([sh_h, sh_s, sh_v]))
                if mask is None:
                    mask = m
                else:
                    mask = cv2.bitwise_or(mask, m)
                    
        if mask is None:
            mask = np.zeros(hsv.shape[:2], dtype=np.uint8)

        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        target_center = None
        best_contour = None
        max_area = 0
        
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
                    
        if best_contour is not None and target_center is not None:
            x, y, w, h = cv2.boundingRect(best_contour)
            cv2.rectangle(resized_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.circle(resized_frame, target_center, 5, (0, 0, 255), -1)
            cv2.putText(resized_frame, f"Target (Area: {int(max_area)})", (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            
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
        bottom_h = 60
        bottom_panel = np.zeros((bottom_h, PREVIEW_W * 2, 3), dtype=np.uint8)
        cv2.putText(bottom_panel, "Color Targets (Left-click to select, Right-click to clear):", (10, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
                    
        box_width = 80
        box_spacing = 20
        start_x = (PREVIEW_W * 2 - (3 * box_width + 2 * box_spacing)) // 2
        
        for i in range(3):
            box_x = start_x + i * (box_width + box_spacing)
            box_y = 10
            
            # Fill color logic
            if color_slots[i]["active"]:
                lh, hh, ls, hs, lv, hv = color_slots[i]["hsv"]
                avg_h = int((lh + hh) / 2)
                avg_s = max(150, int((ls + hs) / 2))
                avg_v = max(150, int((lv + hv) / 2))
                
                bgr_color = cv2.cvtColor(np.uint8([[[avg_h, avg_s, avg_v]]]), cv2.COLOR_HSV2BGR)[0][0]
                bgr_color = (int(bgr_color[0]), int(bgr_color[1]), int(bgr_color[2]))
                cv2.rectangle(bottom_panel, (box_x, box_y), (box_x + box_width, box_y + 40), bgr_color, -1)
            else:
                cv2.rectangle(bottom_panel, (box_x, box_y), (box_x + box_width, box_y + 40), (40, 40, 40), -1)
                cv2.putText(bottom_panel, "EMPTY", (box_x + 18, box_y + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
            
            # Border
            border_color = (0, 255, 255) if i == selected_slot else (100, 100, 100)
            border_thickness = 2 if i == selected_slot else 1
            cv2.rectangle(bottom_panel, (box_x, box_y), (box_x + box_width, box_y + 40), border_color, border_thickness)

        # Build movement macro panel (300px height)
        macro_h = 300
        macro_panel = np.full((macro_h, PREVIEW_W * 2, 3), (35, 35, 35), dtype=np.uint8)

        cv2.putText(macro_panel, "PATROL MACRO (Press F5 to Start/Stop | Right-click grid to clear)", (15, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1)

        grid_local_x = GRID_LEFT
        grid_local_y = 30

        for i in range(GRID_SIZE + 1):
            gx = grid_local_x + i * CELL_SIZE
            gy = grid_local_y + i * CELL_SIZE
            cv2.line(macro_panel, (gx, grid_local_y), (gx, grid_local_y + GRID_PX), (80, 80, 80), 1)
            cv2.line(macro_panel, (grid_local_x, gy), (grid_local_x + GRID_PX, gy), (80, 80, 80), 1)
        cv2.rectangle(macro_panel, (grid_local_x, grid_local_y), (grid_local_x + GRID_PX, grid_local_y + GRID_PX), (120, 120, 120), 2)

        ms_per_cell = max(10, cv2.getTrackbarPos("ms/cell", WIN_NAME))

        def draw_grid_rect(panel, min_c, min_r, max_c, max_r, is_preview=False):
            px1 = grid_local_x + min_c * CELL_SIZE
            py1 = grid_local_y + min_r * CELL_SIZE
            px2 = grid_local_x + (max_c + 1) * CELL_SIZE
            py2 = grid_local_y + (max_r + 1) * CELL_SIZE

            overlay = panel.copy()
            fill_color = (80, 130, 80) if not is_preview else (100, 180, 100)
            cv2.rectangle(overlay, (px1, py1), (px2, py2), fill_color, -1)
            cv2.addWeighted(overlay, 0.25, panel, 0.75, 0, panel)

            step_colors = [(0, 200, 0), (200, 200, 0), (0, 100, 255), (200, 0, 200)]
            edges = [
                ((px1, py2), (px1, py1), 0),
                ((px1, py1), (px2, py1), 1),
                ((px2, py1), (px2, py2), 2),
                ((px2, py2), (px1, py2), 3),
            ]
            for (p1, p2, step_idx) in edges:
                color = step_colors[step_idx]
                thickness = 4 if macro_current_step == step_idx else 2
                cv2.line(panel, p1, p2, color, thickness)

        if macro_drawing and macro_rect_start and macro_rect_end:
            c1, r1 = macro_rect_start
            c2, r2 = macro_rect_end
            min_c, max_c = min(c1, c2), max(c1, c2)
            min_r, max_r = min(r1, r2), max(r1, r2)
            draw_grid_rect(macro_panel, min_c, min_r, max_c, max_r, is_preview=True)

        if macro_rect_cells and not macro_drawing:
            min_c, min_r, max_c, max_r = macro_rect_cells
            draw_grid_rect(macro_panel, min_c, min_r, max_c, max_r, is_preview=False)

        left_x = 20
        cv2.putText(macro_panel, "WASD Patrol Macro", (left_x, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.putText(macro_panel, "1. Drag rectangle on grid", (left_x, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1)
        cv2.putText(macro_panel, "2. Set 'ms/cell' trackbar", (left_x, 115),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1)
        cv2.putText(macro_panel, "3. Press F5 to Start/Stop", (left_x, 140),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1)

        step_labels = ["W (Up)", "D (Right)", "S (Down)", "A (Left)"]
        if macro_running:
            step_text = step_labels[macro_current_step] if 0 <= macro_current_step < 4 else "..."
            cv2.putText(macro_panel, "STATUS: MACRO ACTIVE", (left_x, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 0, 255), 2)
            cv2.putText(macro_panel, f"Active: {step_text}", (left_x, 215),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        elif macro_rect_cells:
            cv2.putText(macro_panel, "STATUS: READY (F5 to Start)", (left_x, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 0), 1)
        else:
            cv2.putText(macro_panel, "STATUS: NO PATH DRAWN", (left_x, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (120, 120, 120), 1)

        right_x = 800
        cv2.putText(macro_panel, "Patrol Step Durations:", (right_x, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        if macro_rect_cells:
            min_c, min_r, max_c, max_r = macro_rect_cells
            w_cells = max_c - min_c + 1
            h_cells = max_r - min_r + 1
            w_dur = h_cells * ms_per_cell
            d_dur = w_cells * ms_per_cell
            s_dur = h_cells * ms_per_cell
            a_dur = w_cells * ms_per_cell
            total_cycle = 2 * (w_cells + h_cells) * ms_per_cell

            step_colors = [(0, 200, 0), (200, 200, 0), (0, 100, 255), (200, 0, 200)]
            cv2.putText(macro_panel, f"W (Up):    {w_dur} ms ({h_cells} cells)", (right_x, 95),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, step_colors[0], 1)
            cv2.putText(macro_panel, f"D (Right): {d_dur} ms ({w_cells} cells)", (right_x, 125),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, step_colors[1], 1)
            cv2.putText(macro_panel, f"S (Down):  {s_dur} ms ({h_cells} cells)", (right_x, 155),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, step_colors[2], 1)
            cv2.putText(macro_panel, f"A (Left):  {a_dur} ms ({w_cells} cells)", (right_x, 185),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, step_colors[3], 1)

            cv2.putText(macro_panel, f"Total Cycle: {total_cycle} ms ({total_cycle/1000:.2f} s)", (right_x, 225),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1)

        # Vertically stack top_canvas, bottom_panel, and macro_panel
        canvas = np.vstack((top_canvas, bottom_panel, macro_panel))

        # Display the unified canvas in the single window
        cv2.imshow(WIN_NAME, canvas)
        
        # Handle F5 key for Patrol Macro Toggle
        f5_state = win32api.GetAsyncKeyState(win32con.VK_F5) & 0x8000
        f5_is_down = bool(f5_state)

        if f5_is_down and not f5_was_down:
            if not macro_running:
                if macro_rect_cells:
                    min_c, min_r, max_c, max_r = macro_rect_cells
                    w_cells = max_c - min_c + 1
                    h_cells = max_r - min_r + 1

                    macro_stop_event = threading.Event()
                    macro_running = True
                    macro_thread = threading.Thread(
                        target=macro_loop,
                        args=(w_cells, h_cells, ms_per_cell, macro_stop_event),
                        daemon=True
                    )
                    macro_thread.start()
                else:
                    print("[WARNING] No patrol rectangle drawn. Draw one on the 9x9 grid first!")
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
    main()
