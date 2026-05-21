"""
SOVEREIGN QRNG — Path Configuration
=====================================
All vault/stats paths are derived from QRNG_POOL_DIR.
Set the environment variable to relocate everything:

  Windows:  set QRNG_POOL_DIR=D:\\MyVault
  Linux:    export QRNG_POOL_DIR=/data/qrng_pool
  Mac:      export QRNG_POOL_DIR=~/qrng_pool

RTL-SDR binary path can also be overridden:
  set RTL_SDR_EXE=C:\\path\\to\\rtl_sdr.exe
"""
import os, sys
from pathlib import Path

# ── Vault/pool directory ──────────────────────────────────────────────────────
if 'QRNG_POOL_DIR' in os.environ:
    QRNG_POOL_DIR = Path(os.environ['QRNG_POOL_DIR'])
elif sys.platform == 'win32':
    QRNG_POOL_DIR = Path(r'C:\QRNG_Pool')
else:
    QRNG_POOL_DIR = Path.home() / '.qrng_pool'

# ── File paths (all relative to QRNG_POOL_DIR) ───────────────────────────────
NEAR_VAULT    = QRNG_POOL_DIR / 'near_vault.bin'
TRUE_VAULT    = QRNG_POOL_DIR / 'true_vault.bin'
NEAR_OFFSET   = QRNG_POOL_DIR / 'near_offset.txt'
TRUE_OFFSET   = QRNG_POOL_DIR / 'true_offset.txt'
NEAR_STATS    = QRNG_POOL_DIR / 'near_stats.json'
TRUE_STATS    = QRNG_POOL_DIR / 'true_stats.json'
NEG_STATS     = QRNG_POOL_DIR / 'neg_stats.json'
NEG_SEED_F    = QRNG_POOL_DIR / 'neg_seed.bin'
CONTRIB_LOG   = QRNG_POOL_DIR / 'contributions.jsonl'
NEAR_LOG      = QRNG_POOL_DIR / 'near_entropy.log'
NEAR_POOL_DIR = QRNG_POOL_DIR / 'near_pool'

# ── RTL-SDR binary (download separately from rtl-sdr.com/tag/pre-release/) ───
_default_rtl = QRNG_POOL_DIR / 'rtlsdr_tools' / 'x64' / 'rtl_sdr.exe'
RTL_SDR_EXE  = Path(os.environ.get('RTL_SDR_EXE', str(_default_rtl)))
RTL_TCP_EXE  = RTL_SDR_EXE.parent / 'rtl_tcp.exe'

# ── Entropy rig directory (auto-detected from this file's location) ───────────
RIG_DIR = Path(__file__).parent
