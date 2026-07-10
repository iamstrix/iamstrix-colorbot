import cv2
import win32gui
import win32api
import time

def nothing(x):
    pass

def print_child_windows(hwnd, l):
    class_name = win32gui.GetClassName(hwnd)
    text = win32gui.GetWindowText(hwnd)
    l.append((hwnd, class_name, text))

def main():
    # Create a dummy OpenCV window with trackbars
    cv2.namedWindow("Test Window", cv2.WINDOW_NORMAL)
    cv2.createTrackbar("Low H", "Test Window", 5, 179, nothing)
    cv2.createTrackbar("High H", "Test Window", 20, 179, nothing)
    
    print("OpenCV Window created. Moving mouse over window to inspect controls...")
    
    # Run loop for 5 seconds to inspect
    start_time = time.time()
    while time.time() - start_time < 5.0:
        # Show dummy image
        img = np.zeros((100, 100, 3), np.uint8)
        cv2.imshow("Test Window", img)
        cv2.waitKey(10)
        
        # Get mouse position
        x, y = win32api.GetCursorPos()
        hwnd = win32gui.WindowFromPoint((x, y))
        
        if hwnd:
            class_name = win32gui.GetClassName(hwnd)
            text = win32gui.GetWindowText(hwnd)
            print(f"Hovering over HWND: {hwnd}, Class: {class_name}, Text: '{text}'")
            
            # Find parent to verify it's under Test Window
            parent = hwnd
            is_our_window = False
            while parent:
                parent_text = win32gui.GetWindowText(parent)
                if "Test Window" in parent_text:
                    is_our_window = True
                    break
                parent = win32gui.GetParent(parent)
            
            if is_our_window:
                print("  => Under Test Window!")
                
        time.sleep(0.5)
        
    cv2.destroyAllWindows()

if __name__ == "__main__":
    import numpy as np
    main()
