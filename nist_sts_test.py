import os
import math
import numpy as np

def frequency_test(bits):
    n = len(bits)
    s_n = np.sum(2 * bits - 1)
    s_obs = abs(s_n) / math.sqrt(n)
    p_val = math.erfc(s_obs / math.sqrt(2))
    return p_val

def runs_test(bits):
    n = len(bits)
    pi = np.sum(bits) / n
    if abs(pi - 0.5) >= (2 / math.sqrt(n)):
        return 0.0
    v_n = np.sum(bits[:-1] != bits[1:]) + 1
    p_val = math.erfc(abs(v_n - 2 * n * pi * (1 - pi)) / (2 * math.sqrt(2 * n) * pi * (1 - pi)))
    return p_val

def run_suite(file_path, block_size=1000000):
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return
    print(f"--- NIST STS LITE: {os.path.basename(file_path)} ---")
    with open(file_path, 'rb') as f:
        data = f.read(block_size // 8)
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    p_freq = frequency_test(bits)
    p_runs = runs_test(bits)
    print(f"Bits Tested:   {len(bits)}")
    print(f"Frequency:     p-val = {p_freq:.6f} {'[PASS]' if p_freq > 0.01 else '[FAIL]'}")
    print(f"Runs:          p-val = {p_runs:.6f} {'[PASS]' if p_runs > 0.01 else '[FAIL]'}")

if __name__ == '__main__':
    from config import NEAR_VAULT
    run_suite(str(NEAR_VAULT))
