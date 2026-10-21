import cv2, sys
p = sys.argv[1]
cap = cv2.VideoCapture(p)
print("opened", cap.isOpened())
fps = cap.get(cv2.CAP_PROP_FPS); n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
print("fps", fps, "frames", n, "dur", n/fps if fps else None)
print("size", cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
