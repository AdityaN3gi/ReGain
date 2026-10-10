"""
calibrate.py - record RELAXED and MAX squeeze, save to calibration.json, then show a live % bar.

    python calibrate.py              # full calibration, then live test
    python calibrate.py --live       # skip calibration, just test with the saved one
    python calibrate.py /dev/ttyACM0 # give the port yourself

Close the Arduino Serial Monitor and live_plot.py first (only one program can use the port).
"""
import argparse
import sys
import time

from emg_core import EMGSource


def countdown(msg, n=3):
    for i in range(n, 0, -1):
        print(f"{msg} {i}...", flush=True)
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--live", action="store_true", help="skip calibration, just show the live %% bar")
    args = ap.parse_args()

    src = EMGSource(args.port)
    src.start()
    print("Connecting... keep your arm still.")
    src.wait_ready()

    if args.live:
        if not src.load_calibration():
            sys.exit("No calibration.json yet. Run without --live first.")
    else:
        input("Electrodes on, arm resting on the table. Press Enter to start... ")
        countdown("RELAX completely. Recording rest in")
        print(">> Recording REST (3 s). Stay relaxed.")
        rest = src.calibrate_rest(3)
        countdown("Get ready to SQUEEZE as hard as is comfortable (no pain). Starting in")
        print(">> SQUEEZE and HOLD (3 s)!")
        mx = src.calibrate_max(3)
        print(f"\nrest = {rest:.1f}    max = {mx:.1f}")
        if mx < rest * 2:
            print("WARNING: max is not much bigger than rest. Check electrode contact/placement and redo.")
        src.save_calibration()
        print("Saved to calibration.json")
        input("Press Enter to test the live bar (Ctrl+C to quit)... ")

    try:
        while True:
            a = src.activation
            print(f"\r{a * 100:5.1f}% |{'#' * int(a * 40):<40}|", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nBye")
    src.stop()


if __name__ == "__main__":
    main()
