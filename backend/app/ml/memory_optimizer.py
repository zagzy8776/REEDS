"""Memory optimization utilities for REEDS prediction system.

This module provides memory-efficient data handling to prevent OOM errors
on Render's 512MB free tier.
"""

import gc
import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def optimize_dataframe_memory(df: pd.DataFrame) -> pd.DataFrame:
    """Optimize DataFrame memory usage by downcasting numeric types.
    
    Args:
        df: Input DataFrame
        
    Returns:
        Memory-optimized DataFrame
    """
    # Track memory before
    mem_before = df.memory_usage(deep=True).sum() / 1024**2
    
    # Optimize numeric columns
    for col in df.select_dtypes(include=['int', 'float']).columns:
        col_type = df[col].dtype
        
        if col_type == np.int64 or col_type == np.int32:
            c_min = df[col].min()
            c_max = df[col].max()
            if c_min >= np.iinfo(np.int8).min and c_max <= np.iinfo(np.int8).max:
                df[col] = df[col].astype(np.int8)
            elif c_min >= np.iinfo(np.int16).min and c_max <= np.iinfo(np.int16).max:
                df[col] = df[col].astype(np.int16)
            elif c_min >= np.iinfo(np.int32).min and c_max <= np.iinfo(np.int32).max:
                df[col] = df[col].astype(np.int32)
            else:
                df[col] = df[col].astype(np.int64)
        
        elif col_type == np.float64 or col_type == np.float32:
            c_min = df[col].min()
            c_max = df[col].max()
            if c_min >= np.finfo(np.float16).min and c_max <= np.finfo(np.float16).max:
                df[col] = df[col].astype(np.float16)
            elif c_min >= np.finfo(np.float32).min and c_max <= np.finfo(np.float32).max:
                df[col] = df[col].astype(np.float32)
            else:
                df[col] = df[col].astype(np.float64)
    
    # Optimize object columns to category if appropriate
    for col in df.select_dtypes(include=['object']).columns:
        num_unique_values = df[col].nunique()
        num_total_values = len(df[col])
        if num_unique_values / num_total_values < 0.5:  # Less than 50% unique
            df[col] = df[col].astype('category')
    
    # Track memory after
    mem_after = df.memory_usage(deep=True).sum() / 1024**2
    savings = mem_before - mem_after
    savings_pct = (savings / mem_before * 100) if mem_before > 0 else 0
    
    logger.info(f"Memory optimization: {mem_before:.2f}MB -> {mem_after:.2f}MB (saved {savings_pct:.1f}%)")
    
    return df


def clear_memory():
    """Force garbage collection to free memory."""
    gc.collect()
    logger.info("Memory cleared via garbage collection")


def get_memory_usage() -> Dict[str, float]:
    """Get current memory usage statistics.
    
    Returns:
        Dictionary with memory usage in MB
    """
    import tracemalloc
    
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    
    snapshot = tracemalloc.take_snapshot()
    stats = snapshot.statistics('lineno')
    
    total_current = sum(stat.size for stat in stats) / 1024**2
    total_peak = sum(stat.size_diff for stat in stats) / 1024**2
    
    return {
        'current_mb': round(total_current, 2),
        'peak_mb': round(total_peak, 2)
    }


class MemoryEfficientDataLoader:
    """Memory-efficient data loader with chunking and caching."""
    
    def __init__(self, max_rows: int = 50000, chunk_size: int = 10000):
        """Initialize data loader.
        
        Args:
            max_rows: Maximum rows to keep in memory
            chunk_size: Size of chunks for processing
        """
        self.max_rows = max_rows
        self.chunk_size = chunk_size
        self._cache = {}
    
    def load_dataframe_chunked(
        self,
        df: pd.DataFrame,
        optimize: bool = True
    ) -> pd.DataFrame:
        """Load DataFrame with memory optimization.
        
        Args:
            df: Input DataFrame
            optimize: Whether to optimize memory
            
        Returns:
            Memory-optimized DataFrame (possibly truncated)
        """
        # Limit rows if necessary
        if len(df) > self.max_rows:
            logger.warning(f"Truncating DataFrame from {len(df)} to {self.max_rows} rows")
            df = df.tail(self.max_rows)
        
        # Optimize memory if requested
        if optimize:
            df = optimize_dataframe_memory(df)
        
        return df
    
    def process_in_chunks(self, df: pd.DataFrame, process_func, **kwargs):
        """Process DataFrame in chunks to avoid memory spikes.
        
        Args:
            df: Input DataFrame
            process_func: Function to apply to each chunk
            **kwargs: Additional arguments for process_func
            
        Yields:
            Processed chunks
        """
        for start in range(0, len(df), self.chunk_size):
            end = min(start + self.chunk_size, len(df))
            chunk = df.iloc[start:end]
            
            # Process chunk
            result = process_func(chunk, **kwargs)
            
            yield result
            
            # Clear memory after each chunk
            del chunk
            clear_memory()
    
    def get_cached(self, key: str) -> Optional[pd.DataFrame]:
        """Get DataFrame from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached DataFrame or None
        """
        return self._cache.get(key)
    
    def set_cached(self, key: str, df: pd.DataFrame, optimize: bool = True):
        """Cache a DataFrame.
        
        Args:
            key: Cache key
            df: DataFrame to cache
            optimize: Whether to optimize before caching
        """
        if optimize:
            df = optimize_dataframe_memory(df)
        self._cache[key] = df
    
    def clear_cache(self):
        """Clear all cached DataFrames."""
        self._cache.clear()
        clear_memory()


# Global memory optimizer instance
memory_optimizer = MemoryEfficientDataLoader(max_rows=50000, chunk_size=10000)