# utils.py
import baostock as bs
import os
import sys
import logging
from contextlib import contextmanager
from .data_source_interface import LoginError

logger = logging.getLogger(__name__)

def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

@contextmanager
def baostock_login_context():
    """
    Context manager that handles Baostock login/logout mechanics 
    and suppresses its noisy stdout output.
    """
    # 1. Suppress Stdout
    original_stdout_fd = sys.stdout.fileno()
    saved_stdout_fd = os.dup(original_stdout_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)

    try:
        os.dup2(devnull_fd, original_stdout_fd)
        
        # 2. Login
        lg = bs.login()
        
        # Restore stdout immediately after login operation to verify result
        # (Optional: keep suppressed if you want strict silence)
        sys.stdout.flush()
        os.dup2(saved_stdout_fd, original_stdout_fd)
        
        if lg.error_code != '0':
            raise LoginError(f"Baostock login failed: {lg.error_msg}")
        
        logger.debug(f"Baostock login success: {lg.error_msg}")
        
        yield # Execution happens here

    finally:
        # 3. Logout (Suppress stdout again)
        sys.stdout.flush()
        os.dup2(devnull_fd, original_stdout_fd)
        
        bs.logout()
        
        # Restore stdout finally
        sys.stdout.flush()
        os.dup2(saved_stdout_fd, original_stdout_fd)
        os.close(devnull_fd)
        os.close(saved_stdout_fd)