"""Save a screenshot of the X display (inside the throwaway container) as PNG: python grab.py out.png"""
import sys
from PySide6.QtGui import QGuiApplication
app = QGuiApplication(["grab", "-platform", "xcb"])
QGuiApplication.primaryScreen().grabWindow(0).save(sys.argv[1], "PNG")
