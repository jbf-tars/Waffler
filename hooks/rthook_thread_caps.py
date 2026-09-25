"""PyInstaller runtime hook: cap NumPy's BLAS thread pools before any import.

Registered in both Waffler_windows.spec and Waffler_mac.spec, so it runs in
the main process and in the overlay subprocess before app.py itself.

NumPy's OpenBLAS starts an idle worker thread per CPU core when it is
imported, and reserves memory for each. Waffler uses NumPy only for RMS sums
on short audio buffers, which never touch BLAS. Measured on a 28-thread PC:
"import numpy" committed 754 MB across 27 threads, against 15 MB and 4 threads
with the caps. The installed app had about 878 MB (main) and 811 MB (overlay)
private, almost all of it this pool. VECLIB_MAXIMUM_THREADS is the same cap
for Apple's Accelerate, which the Mac build's NumPy links instead.

setdefault keeps any value a user set on purpose. app.py sets the same caps
at its top for runs from source.
"""
import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")
