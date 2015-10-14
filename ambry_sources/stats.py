# coding: utf-8
"""

Computing stats on the fly for data written to a partition

Copyright (c) 2015 Civic Knowledge. This file is licensed under the terms of the
Revised BSD License, included in this distribution as LICENSE.txt
"""

from collections import Counter, OrderedDict
from livestats import livestats
import logging

from six import iteritems, iterkeys, u, string_types, binary_type, text_type

from .sources import SourceError

logger = logging.getLogger(__name__)


def text_hist(nums, ascii=False):

    if ascii:
        parts = u(' _.,,-=T#')
    else:
        parts = u(' ▁▂▃▄▅▆▇▉')

    nums = list(nums)
    fraction = max(nums) / float(len(parts) - 1)
    if fraction:
        return ''.join(parts[int(round(x / fraction))] for x in nums)
    else:
        return ''


class Constant:
    """Organizes constants in a class."""

    class ConstError(TypeError):
        pass

    def __setattr__(self, name, value):
        if name in self.__dict__:
            raise self.ConstError("Can't rebind const(%s)" % name)
        self.__dict__[name] = value


class StatSet(object):
    LOM = Constant()  # Level of Measurement, More or Less

    LOM.NOMINAL = 'n'  # Categorical, usually strings.
    LOM.ORDINAL = 'o'  # A number which counts or ranks. Subtraction is not defined. Times and Dates
    LOM.INTERVAL = 'i'  # A number, for which subtraction is defined, but not division
    LOM.RATIO = 'r'  # A number, for which division is defined and zero means "nothing". Kelvin, but not Celsius

    def __init__(self, name, typ):

        if isinstance(typ, string_types):
            import datetime
            m = dict(list(__builtins__.items()) + list(datetime.__dict__.items()))
            if typ == 'unknown':
                typ = binary_type
            else:
                typ = m[typ]

        from datetime import date, time, datetime

        self.is_gvid = bool('gvid' in name)  # A special name in Ambry
        self.is_year = bool('year' in name)
        self.is_time = typ == time
        self.is_date = typ == date or typ == datetime

        # Tricky hack, indexing with a bool.
        self.flags = " G"[self.is_gvid] + " Y"[self.is_year] + " T"[self.is_time] + " D"[self.is_date]

        if self.is_year or self.is_time or self.is_date:
            lom = StatSet.LOM.ORDINAL
        elif typ == binary_type or typ == text_type:
            lom = StatSet.LOM.NOMINAL
        elif typ == int or typ == float:
            lom = StatSet.LOM.INTERVAL
        else:
            lom = StatSet.LOM.NOMINAL

        self.column_name = name

        self.lom = lom
        self.n = 0
        self.counts = Counter()
        self.size = None
        self.stats = livestats.LiveStats([0.25, 0.5, 0.75])  # runstats.Statistics()

        self.bin_min = None
        self.bin_max = None
        self.bin_width = None
        self.bin_primer_count = 5000  # how many points to collect before creating hist bins
        self.num_bins = 16
        self.bins = [0] * self.num_bins

    @property
    def is_numeric(self):
        return self.lom == self.LOM.INTERVAL or self.lom == self.LOM.RATIO

    def add(self, v):
        from math import sqrt

        self.n += 1

        try:
            unival = u('{}').format(v)
        except UnicodeError:
            unival = v.decode('ascii', 'ignore')

        self.size = max(self.size or 0, len(unival))

        if self.lom == self.LOM.NOMINAL or self.lom == self.LOM.ORDINAL:
            if self.is_time or self.is_date:
                self.counts[unival] += 1
            else:
                self.counts[unival] += 1

        elif self.is_numeric:

            # To build the histogram, we need to collect counts, but would rather
            # not collect all of the values. So, collect the first 5K, then use that
            # to determine the 4sigma range of the histogram.
            # HACK There are probably a lot of 1-off errors in this
            float_v = _force_float(v)

            if self.n < self.bin_primer_count:
                self.counts[unival] += 1

            elif self.n == self.bin_primer_count:
                # If less than 1% are unique, assume that this number is actually an ordinal
                if self.nuniques < (self.bin_primer_count/100):
                    self.lom = self.LOM.ORDINAL
                    self.stats = livestats.LiveStats()
                else:
                    self.bin_min = self.stats.mean() - sqrt(self.stats.variance()) * 2
                    self.bin_max = self.stats.mean() + sqrt(self.stats.variance()) * 2
                    self.bin_width = (self.bin_max - self.bin_min) / self.num_bins

                    for v, count in iteritems(self.counts):
                        float_v = _force_float(v)
                        if float_v >= self.bin_min and float_v <= self.bin_max:
                            bin_ = int((float_v - self.bin_min) / self.bin_width)
                            self.bins[bin_] += count

                self.counts = Counter()

            elif self.n > self.bin_primer_count and float_v >= self.bin_min and float_v <= self.bin_max:
                bin_ = int((float_v - self.bin_min) / self.bin_width)
                self.bins[bin_] += 1
            try:
                self.stats.add(float(v))
            except (ValueError, TypeError):
                self.counts[unival] += 1
        else:
            assert False, 'Really should be one or the other ... '

    @property
    def uniques(self):
        return list(self.counts)

    @property
    def nuniques(self):
        return len(list(self.counts.items()))

    @property
    def mean(self):
        return self.stats.mean() if self.is_numeric else None

    @property
    def stddev(self):
        from math import sqrt
        return sqrt(self.stats.variance()) if self.is_numeric else None

    @property
    def min(self):
        return self.stats.minimum() if self.is_numeric else None

    @property
    def p25(self):
        try:
            return self.stats.quantiles()[0][1]
        except IndexError:
            return None

    @property
    def median(self):
        try:
            return self.stats.quantiles()[1][1]
        except IndexError:
            return None

    @property
    def p50(self):
        try:
            return self.stats.quantiles()[1][1]
        except IndexError:
            return None

    @property
    def p75(self):
        try:
            return self.stats.quantiles()[2][1]
        except IndexError:
            return None

    @property
    def max(self):
        return self.stats.maximum() if self.is_numeric else None

    @property
    def skewness(self):
        return self.stats.skewness() if self.is_numeric else None

    @property
    def kurtosis(self):
        return self.stats.kurtosis() if self.is_numeric else None

    @property
    def hist(self):
        return text_hist(self.bins) if self.is_numeric else None

    @property
    def dict(self):
        """Return a  dict that can be passed into the ColumnStats constructor"""

        try:
            skewness = self.skewness
            kurtosis = self.kurtosis
        except ZeroDivisionError:
            skewness = kurtosis = float('nan')

        return OrderedDict([
            ('name', self.column_name),
            ('flags', self.flags),
            ('lom', self.lom),
            ('count', self.n),
            ('nuniques', self.nuniques),
            ('mean', self.mean),
            ('std', self.stddev),
            ('min', self.min),
            ('p25', self.p25),
            ('p50', self.p50),
            ('p75', self.p75),
            ('max', self.max),
            ('skewness', skewness),
            ('kurtosis', kurtosis),
            ('hist', self.bins),
            ('text_hist',  text_hist(self.bins)),
            ('uvalues', dict(self.counts.most_common(100)))
        ])


class Stats(object):
    """ Stats object reads rows from the input iterator, processes the row, and yields it back out"""

    def __init__(self, schema):

        self._stats = {}
        self._func = None
        self._func_code = None

        for col_name, col_type in schema:
            self._stats[col_name] = StatSet(col_name, col_type)

        self._func, self._func_code = self.build()

    @property
    def dict(self):
        return self._stats

    def __getitem__(self, item):
        return self._stats[item]

    def __contains__(self, item):
        return item in self._stats

    def build(self):

        parts = []

        for name in iterkeys(self._stats):
            if self._stats[name] is not None:
                parts.append("stats['{name}'].add(row['{name}'])".format(name=name))

        if not parts:
            error_msg = 'Did not get any stats variables for table {}. Was add() or init() called first?'\
                .format(self.table.name)
            raise SourceError(error_msg)

        code = 'def _process_row(stats, row):\n    {}'.format('\n    '.join(parts))

        exec(code)

        f = locals()['_process_row']

        return f, code

    def stats(self):
        return [(name, self._stats[name]) for name, stat in iteritems(self._stats)]

    def run(self, source, sample_from=None):
        """
         Run the stats. The source must yield Row proxies

        :param source:
        :param sample_from: If not None, an integer givning the total number of rows. The
            run will sample 10,000 rows.
        :return:
        """

        self._func, self._func_code = self.build()

        def process_row(row):

            try:
                self._func(self._stats, row)
            except TypeError as e:
                raise TypeError("Failed for '{}'; {}".format(self._func_code, e))
            except KeyError:
                raise KeyError(
                    'Failed to find key in row. headers = "{}", code = "{}" '
                    .format(list(row.keys()), self._func_code))

        if sample_from is None:
            for row in source:
                process_row(row)
        else:

            SAMPLE_ROWS = 10000
            average_skip = sample_from / SAMPLE_ROWS

            if average_skip > 4:
                for i, row in enumerate(source):

                    if i % average_skip == 0:
                        process_row(row)

            else:
                for row in source:
                    process_row(row)

        return self

    def __str__(self):
        from tabulate import tabulate

        rows = []

        for name, stats in iteritems(self._stats):
            stats_dict = stats.dict
            del stats_dict["uvalues"]
            stats_dict["hist"] = text_hist(stats_dict["hist"], True)
            if not rows:
                rows.append(list(stats_dict.keys()))

            rows.append(list(stats_dict.values()))
        if rows:
            return 'Statistics \n' + binary_type(tabulate(rows[1:], rows[0], tablefmt='pipe'))
        else:
            return 'Statistics: None \n'


def _force_float(v):
    """ Converts given argument to float. On fail logs warning and returns 0.0.

    Args:
        v (any): value to convert to float

    Returns:
        float: converted v or 0.0 if conversion failed.

    """
    try:
        return float(v)
    except Exception as exc:
        logger.warning(
            'Failed to convert {} to float with {} error. Using 0 instead.'.format(v, exc))
    return 0.0
