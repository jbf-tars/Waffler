import ctypes, time
print("Press keys one at a time. Ctrl+C to quit.")
prev = set()
while True:
    curr = {i for i in range(256) if ctypes.windll.user32.GetAsyncKeyState(i) & 0x8000}
    new = curr - prev
    if new:
        print("Keys down:", [hex(k) for k in sorted(curr)])
    prev = curr
    time.sleep(0.1)
