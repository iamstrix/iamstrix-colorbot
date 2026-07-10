import cv2
import win32gui
import win32con
import time

def print_children(hwnd, level=0):
    class_name = win32gui.GetClassName(hwnd)
    text = win32gui.GetWindowText(hwnd)
    print("  " * level + f"HWND: {hwnd}, Class: {class_name}, Text: '{text}'")
    
    # Get children
    child = win32gui.GetWindow(hwnd, win32con.GW_CHILD)
    while child:
        print_children(child, level + 1)
        child = win32gui.GetWindow(child, win32con.GW_HWNDNEXT)

def main():
    cv2.namedWindow("Color Tuning", cv2.WINDOW_NORMAL)
    cv2.createTrackbar("Low H", "Color Tuning", 5, 179, lambda x: None)
    
    # Show window for a brief moment to let Windows initialize controls
    img = np.zeros((100, 100, 3), np.uint8)
    cv2.imshow("Color Tuning", img)
    cv2.waitKey(100)
    
    hwnd = win32gui.FindWindow(None, "Color Tuning")
    if hwnd:
        print(f"\nWindow 'Color Tuning' found (HWND: {hwnd})")
        print("Child window hierarchy:")
        print_children(hwnd)
    else:
        print("Window 'Color Tuning' not found!")
        
    cv2.destroyAllWindows()

if __name__ == "__main__":
    import numpy as np
    main()
