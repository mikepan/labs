"""
eval.common - Shared helpers, logging formatters, and utility functions.
"""

import logging
import sys

class ColorFormatter(logging.Formatter):
    """Zero-dependency ANSI terminal color formatter for structured logging."""
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    
    LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, "")
        time_str = f"{self.DIM}{self.formatTime(record, '%H:%M:%S')}{self.RESET}"
        level_str = f"{color}{self.BOLD}[{record.levelname}]{self.RESET}"
        
        msg = record.getMessage()
        # Highlight checkmarks and crossmarks
        if "✓" in msg:
            msg = msg.replace("✓", "\033[32m✓\033[0m")
        if "✗" in msg:
            msg = msg.replace("✗", "\033[31m✗\033[0m")
            
        return f"{time_str} {level_str} {msg}"


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Create a configured logger with ANSI color output."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(ColorFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger
