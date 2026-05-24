import os
import math
import numpy as np


def frequency_test(bits):
    """NIST SP 800-22 §2.1 — monobit frequency."""
    n = len(bits)
    s_n = np.sum(2 * bits - 1)
    s_obs = abs(s_n) / math.sqrt(n)
    return math.erfc(s_obs / math.sqrt(2))


def runs_test(bits):
    """NIST SP 800-22 §2.3 — runs of consecutive identical bits."""
    n = len(bits)
    pi = np.sum(bits) / n
    if abs(pi - 0.5) >= (2 / math.sqrt(n)):
        return 0.0
    v_n = np.sum(bits[:-1] != bits[1:]) + 1
    return math.erfc(abs(v_n - 2 * n * pi * (1 - pi)) / (2 * math.sqrt(2 * n) * pi * (1 - pi)))


def block_frequency_test(bits, block_size=128):
    """NIST SP 800-22 §2.2 — frequency within equal-sized blocks."""
    n = len(bits)
    N = n // block_size
    if N < 1:
        return None
    proportions = [np.sum(bits[i * block_size:(i + 1) * block_size]) / block_size for i in range(N)]
    chi_sq = 4 * block_size * sum((p - 0.5) ** 2 for p in proportions)
    try:
        from scipy.special import gammaincc
        return float(gammaincc(N / 2.0, chi_sq / 2.0))
    except ImportError:
        return None


def autocorrelation_test(bits, d=1):
    """Bit autocorrelation at lag d — detects serial correlation between adjacent bits."""
    n = len(bits)
    if n <= d:
        return None
    a = int(np.sum(bits[:n - d] ^ bits[d:]))
    v = 2.0 * (a - (n - d) / 2.0) / math.sqrt(n - d)
    return math.erfc(abs(v) / math.sqrt(2))


def longest_run_test(bits, block_size=128):
    """NIST SP 800-22 §2.4 — longest run of ones within blocks."""
    n = len(bits)
    N = n // block_size
    if N < 1:
        return None
    runs = []
    for i in range(N):
        block = bits[i * block_size:(i + 1) * block_size]
        max_run = cur = 0
        for b in block:
            cur = cur + 1 if b else 0
            max_run = max(max_run, cur)
        runs.append(max_run)
    mean_run = np.mean(runs)
    expected = block_size / 2 * (1 - (0.5) ** block_size)
    return float(mean_run), float(expected)


def run_suite(file_path, block_size=1_000_000):
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return
    print(f"--- NIST STS LITE: {os.path.basename(file_path)} ---")
    with open(file_path, 'rb') as f:
        data = f.read(block_size // 8)
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))

    p_freq  = frequency_test(bits)
    p_runs  = runs_test(bits)
    p_block = block_frequency_test(bits)
    p_ac1   = autocorrelation_test(bits, d=1)
    p_ac2   = autocorrelation_test(bits, d=2)
    p_ac8   = autocorrelation_test(bits, d=8)
    lr      = longest_run_test(bits)

    print(f"Bits Tested:        {len(bits)}")
    print(f"Frequency:          p = {p_freq:.6f}  {'[PASS]' if p_freq  > 0.01 else '[FAIL]'}")
    print(f"Runs:               p = {p_runs:.6f}  {'[PASS]' if p_runs  > 0.01 else '[FAIL]'}")
    if p_block is not None:
        print(f"Block Frequency:    p = {p_block:.6f}  {'[PASS]' if p_block > 0.01 else '[FAIL]'}")
    else:
        print(f"Block Frequency:    [SKIP — scipy not installed]")
    if p_ac1 is not None:
        print(f"Autocorr lag=1:     p = {p_ac1:.6f}  {'[PASS]' if p_ac1   > 0.01 else '[FAIL — serial correlation]'}")
        print(f"Autocorr lag=2:     p = {p_ac2:.6f}  {'[PASS]' if p_ac2   > 0.01 else '[FAIL]'}")
        print(f"Autocorr lag=8:     p = {p_ac8:.6f}  {'[PASS]' if p_ac8   > 0.01 else '[FAIL]'}")
    if lr is not None:
        print(f"Longest Run:        mean={lr[0]:.1f}  expected≈{lr[1]:.1f}")


if __name__ == '__main__':
    from config import NEAR_VAULT
    run_suite(str(NEAR_VAULT))
