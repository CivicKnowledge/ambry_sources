# -*- coding: utf-8 -*-

from .accessors import CsvSource, TsvSource, FixedSource, PartitionSource, ExcelSource,\
    GoogleSource, GeneratorSource, MPRSource, AspwCursorSource, PandasDataframeSource, \
    SocrataSource
from .exceptions import SourceError
from .spec import ColumnSpec, SourceSpec
from .util import DelayedOpen, DelayedDownload, RowProxy, GeoRowProxy

__all__ = [
    SourceError, ColumnSpec, SourceSpec,
    CsvSource, TsvSource, FixedSource, PartitionSource,
    ExcelSource, GoogleSource, AspwCursorSource, SocrataSource,
    DelayedOpen, DelayedDownload, RowProxy, GeoRowProxy, GeneratorSource]

try:
    # Only if the underlying fiona and shapely libraries are installed with the [geo] extra
    from .accessors import  ShapefileSource
    __all__.append('ShapefileSource')
except ImportError:
    pass
