# -*- coding: utf-8 -*-
"""
Writing data to a partition. The MPR file format is a conversion format that stores tabular data in rows and associates
it with metadata

Copyright (c) 2015 Civic Knowledge. This file is licensed under the terms of the
Revised BSD License, included in this distribution as LICENSE.txt
"""

import datetime
import gzip
import math
import time
from six import string_types
import msgpack
import struct


def new_mpr(fs, path, stats=None):
    from os.path import split, splitext

    assert bool(fs)

    dn, file_ext = split(path)
    fn, ext = splitext(file_ext)

    if fs and not fs.exists(dn):
        fs.makedir(dn, recursive=True)

    if not ext:
        ext = '.msg'

    return MPRowsFile(fs, path)


class MPRError(Exception):
    pass


class GzipFile(gzip.GzipFile):
    """A Hacked GzipFile that will read only one gzip member and properly handle extra data afterward,
    by ignoring it"""

    def __init__(self, filename=None, mode=None, compresslevel=9, fileobj=None, mtime=None, end_of_data=None):
        super(GzipFile, self).__init__(filename, mode, compresslevel, fileobj, mtime)
        self._end_of_data = end_of_data

    def _read(self, size=1024):
        """Alters the _read method to stop reading new gzip members when we've reached the end of the row data. """

        if self._new_member and self._end_of_data and self.fileobj.tell() >= self._end_of_data:
            raise EOFError('Reached EOF')
        else:
            return super(GzipFile, self)._read(size)

class MPRowsFile(object):
    """The Message Pack Rows File format holds a collection of arrays, in message pack format, along with a
    dictionary of values. The format is designed for holding tabular data in an efficient, compressed form,
    and for associating it with metadata. """

    EXTENSION = '.mpr'
    VERSION = 1
    MAGIC = 'AMBRMPDF'

    # 8s: Magic Number, H: Version,  I: Number of rows, I: number of columns
    # Q: Position of end of rows / Start of meta,
    # I: Data start row, I: Data end row
    FILE_HEADER_FORMAT = struct.Struct('>8sHIIQII')

    FILE_HEADER_FORMAT_SIZE = FILE_HEADER_FORMAT.size

    # These are all of the keys for the  schema. The schema is a collection of rows, with these
    # keys being the first, followed by one row per column.
    SCHEMA_TEMPLATE = [
        'pos',
        'name',
        'type',
        'description',
        'start',
        'width',

        # types
        'position',
        'header',
        'length',
        'has_codes',
        'type_count',  # Note! Row Intuiter object call this 'count'

        'ints',
        'floats',
        'strs',
        'unicode',
        'nones',
        'datetimes',
        'dates',
        'times',
        'strvals',

        # Stats
        'flags',
        'lom',
        'resolved_type',
        'stat_count',  # Note! Stat object calls this 'count'
        'nuniques',
        'mean',
        'std',
        'min',
        'p25',
        'p50',
        'p75',
        'max',
        'skewness',
        'kurtosis',
        'hist',
        'text_hist',
        'uvalues']

    META_TEMPLATE = {

        'schema': [SCHEMA_TEMPLATE],
        'about': {
            'create_time': None,  # Timestamp when file was  created.
            'load_time': None  # Length of time MPRowsFile.load_rows ran, in seconds()
        },
        'geo': {
            'srs': None,
            'bb': None
        },
        'excel': {
            'datemode': None,
            'worksheet': None
        },
        'source': {
            'url': None,
            'fetch_time': None,
            'file_type': None,
            'url_type': None,
            'inner_file': None,
            'encoding': None
        },
        'row_spec': {
            'header_rows': None,
            'comment_rows': None,
            'start_row': None,
            'end_row': None,
            'data_pattern': None
        },
        'comments': {
            'header': None,
            'footer': None
        }
    }



    def __init__(self,  url_or_fs, path=None):
        """

        :param url_or_fs:
        :param path:
        :return:
        """

        from fs.opener import opener

        if path:
            self._fs, self._path = url_or_fs, path
        else:
            self._fs, self._path = opener.parse(url_or_fs)

        self._writer = None
        self._reader = None

        self._compress = True

        self._process = None  # Process name for report_progress
        self._start_time = 0

        if not self._path.endswith(self.EXTENSION):
            self._path = self._path + self.EXTENSION

    @property
    def path(self):
        return self._path

    @staticmethod
    def encode_obj(obj):

        if isinstance(obj, datetime.datetime):
            return {'__datetime__': True, 'as_str': obj.isoformat()}
        elif isinstance(obj, datetime.date):
            return {'__date__': True, 'as_str': obj.isoformat()}
        elif isinstance(obj, datetime.time):
            return {'__time__': True, 'as_str': obj.strftime("%H:%M:%S")}
        elif hasattr(obj, 'render'):
            return obj.render()
        elif hasattr(obj, '__str__'):
            return str(obj)
        else:
            raise Exception("Unknown type on encode: {}, {}".format(type(obj), obj))

    @staticmethod
    def decode_obj(obj):

        if b'__datetime__' in obj:
            try:
                obj = datetime.datetime.strptime(obj["as_str"], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                # The preferred format is without the microseconds, but there are some lingering
                # bundle that still have it.
                obj = datetime.datetime.strptime(obj["as_str"], "%Y-%m-%dT%H:%M:%S.%f")
        elif b'__time__' in obj:
            obj = datetime.time(*list(time.strptime(obj["as_str"], "%H:%M:%S"))[3:6])
        elif b'__date__' in obj:
            obj = datetime.datetime.strptime(obj["as_str"], "%Y-%m-%d").date()
        else:
            raise Exception("Unknown type on decode: {} ".format(obj))

        return obj

    @classmethod
    def read_file_header(cls, o, fh):
        try:
            o.magic, o.version, o.n_rows, o.n_cols, o.meta_start, o.data_start_row, o.data_end_row = \
                cls.FILE_HEADER_FORMAT.unpack(fh.read(cls.FILE_HEADER_FORMAT_SIZE))
        except struct.error as e:
            raise IOError("Failed to read file header; {}; path = {}".format(e, o.parent.path))

    @classmethod
    def write_file_header(cls, o, fh):
        """Write the magic number, version and the file_header dictionary.  """

        hdf = cls.FILE_HEADER_FORMAT.pack(cls.MAGIC, cls.VERSION, o.n_rows, o.n_cols, o.meta_start,
                                          o.data_start_row, o.data_end_row)

        assert len(hdf) == cls.FILE_HEADER_FORMAT_SIZE

        fh.seek(0)

        fh.write(hdf)

        assert fh.tell() == cls.FILE_HEADER_FORMAT_SIZE, (fh.tell(), cls.FILE_HEADER_FORMAT_SIZE)

    @classmethod
    def read_meta(cls, o, fh):

        pos = fh.tell()

        fh.seek(o.meta_start)

        # Using the _fh b/c I suspect that the GzipFile attached to self._zfh has state that would
        # get screwed up if you read from a new position

        data = fh.read()

        if data:

            meta = msgpack.unpackb(data.decode('zlib'), encoding='utf-8')

        else:
            meta = {}

        fh.seek(pos)

        return meta

    @classmethod
    def write_meta(cls, o, fh):

        o.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE

        fh.seek(o.meta_start)  # Should probably already be there.

        fhb = msgpack.packb(o.meta, encoding='utf-8').encode('zlib')
        fh.write(fhb)

    @classmethod
    def _columns(cls, o, n_cols = 0):

        from ambry_sources.sources.util import RowProxy

        s = o.meta['schema']

        assert len(s) >= 1  # Should always have header row.
        assert o.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE, (o.meta['schema'][0], MPRowsFile.SCHEMA_TEMPLATE)

        # n_cols here is for columns in the data table, which are rows in the headers table
        n_cols = max(n_cols, o.n_cols, len(s)-1)

        for i in range(1, n_cols+1):
            # Normally, we'd only create one of these, and set the row on the singleton for
            # each row. But in this case, the caller may turn the output of the method into a list,
            # in which case all of the rows would have the values of the last one.
            rp = RowProxy(s[0])
            try:
                row = s[i]
            except IndexError:
                # Extend the row, but make sure the pos value is set property.
                ext_row = [i, 'col{}'.format(i)] + [None] * (len(s[0]) - 2)
                s.append(ext_row)
                row = s[i]

            yield rp.set_row(row)

        assert o.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE

    @property
    def info(self):
        return self._info(self.reader)

    @classmethod
    def _info(cls, o):

        return dict(
            version=o.version,
            data_start_pos=o.data_start,
            meta_start_pos=o.meta_start,
            rows=o.n_rows,
            cols=o.n_cols,
            header_rows=o.meta['row_spec']['header_rows'],
            data_start_row=o.data_start_row,
            data_end_row=o.data_end_row,
            comment_rows=o.meta['row_spec']['comment_rows'],
            headers=o.headers
        )

    @property
    def exists(self):
        return self._fs.exists(self.path)

    def remove(self):
        if self.exists:
            self._fs.remove(self.path)

    @property
    def meta(self):

        if not self.exists:
            return None

        with self.reader as r:
            return r.meta


    @property
    def stats(self):
        return (self.meta or {}).get('stats')

    @property
    def n_rows(self):

        if not self.exists:
            return None

        with self.reader as r:
            return r.n_rows

    @property
    def headers(self):

        if not self.exists:
            return None

        with self.reader as r:
            return r.headers

    def run_type_intuiter(self):
        """Run the Type Intuiter and store the results back into the metadata"""
        from .intuit import TypeIntuiter

        try:
            self._process = 'intuit_type'
            self._start_time = time.time()

            with self.reader as r:
                ti = TypeIntuiter().process_header(r.headers).run(r.rows, r.n_rows)

            with self.writer as w:
                w.set_types(ti)
        finally:
            self._process = 'none'

    def run_row_intuiter(self):
        """Run the row intuiter and store the results back into the metadata"""
        from .intuit import RowIntuiter

        try:
            self._process = 'intuit_rows'
            self._start_time = time.time()

            with self.reader as r:
                ri = RowIntuiter().run(r.raw, r.n_rows)

            with self.writer as w:
                w.set_row_spec(ri)

        finally:
            self._process = 'none'

    def run_stats(self):
        """Run the stats process and store the results back in the metadata"""
        from .stats import Stats

        try:
            self._process = 'run_stats'
            self._start_time = time.time()

            with self.reader as r:
                stats = Stats([(c.name, c.type) for c in r.columns ]).run(r, sample_from=r.n_rows)

            with self.writer as w:
                w.set_stats(stats)

        finally:
            self._process = 'none'

        return stats

    def load_rows(self, source, spec=None, intuit_rows=None, intuit_type=True, run_stats=True):
        try:
            self._load_rows(source, spec=spec, intuit_rows=intuit_rows,
                            intuit_type=intuit_type, run_stats=run_stats)
        except:
            self.writer.close()
            self.remove()
            raise

        return self

    def _load_rows(self, source, spec=None, intuit_rows=None, intuit_type=True, run_stats=True):
        from .exceptions import RowIntuitError
        if self.n_rows:
            raise MPRError("Can't load_rows; rows already loaded. n_rows = {}".format(self.n_rows))

        # None means to determine True or False from the existence of a row spec
        if intuit_rows is None:

            if spec is None:
                intuit_rows = True
            elif spec.has_rowspec:
                intuit_rows = False
            else:
                intuit_rows = True

        try:

            self._process = 'load_rows'
            self._start_time = time.time()

            with self.writer as w:

                w.load_rows(source)

                if spec:
                    w.set_source_spec(spec)



                w.close()

            with self.reader as r:
                assert r.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE

            if intuit_rows:
                try:
                    self.run_row_intuiter()
                except RowIntuitError:
                    # FIXME Need to report this, but there is currently no way to get
                    # the higher level logger.
                    pass

            elif spec:

                with self.writer as w:
                    w.set_row_spec(spec)
                    assert w.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE

            if intuit_type:
                self.run_type_intuiter()

            if run_stats:
                self.run_stats()

        finally:
            self._process = None

        return self

    def open(self,  mode='rb'):
        return self._fs.open(self.path, mode=mode)

    @property
    def reader(self):
        if not self._reader:
            self._reader = MPRReader(self, self._fs.open(self.path, mode='rb'), compress=self._compress)
        return self._reader

    @property
    def writer(self):
        from os.path import dirname
        if not self._writer:
            self._process = 'write'
            if self._fs.exists(self.path):
                mode = 'r+b'
            else:
                mode = 'wb'

            if not self._fs.exists(dirname(self.path)):
                self._fs.makedir(dirname(self.path), recursive=True)

            self._writer = MPRWriter(self, self._fs.open(self.path, mode=mode), compress=self._compress)

        return self._writer

    def report_progress(self):
        """
        This function can be called from a higher level to report progress. It is usually called from an alarm
        signal handler which is installed just before starting a load_rows operation:

        >>> import signal
        >>> f = MPRowsFile('tmp://foobar')
        >>> def handler(signum, frame):
        >>>     print "Loading: %s, %s rows" % f.report_progress()
        >>> f.load_rows( [i,i,i] for i in range(1000))

        :return: Tuple: (process description, #records, #total records, #rate)
        """

        rec = total = rate = 0

        if self._process in ('load_rows', 'write') and self._writer:
            rec = self._writer.n_rows
            rate = round(float(rec) / float(time.time() - self._start_time), 2)

        elif self._reader:
            rec = self._reader.pos
            total = self._reader.data_end_row
            rate = round(float(rec) / float(time.time() - self._start_time), 2)

        return (self._process, rec, total, rate)


class MPRWriter(object):

    MAGIC = MPRowsFile.MAGIC
    VERSION = MPRowsFile.VERSION
    FILE_HEADER_FORMAT = MPRowsFile.FILE_HEADER_FORMAT
    FILE_HEADER_FORMAT_SIZE = MPRowsFile.FILE_HEADER_FORMAT.size
    META_TEMPLATE = MPRowsFile.META_TEMPLATE
    SCHEMA_TEMPLATE = MPRowsFile.SCHEMA_TEMPLATE

    def __init__(self, parent, fh, compress=True):

        from copy import deepcopy
        import re

        assert fh

        self.parent = parent
        self._fh = fh
        self._compress = compress

        self._zfh = None  # Compressor for writing rows
        self.version = self.VERSION
        self.magic = self.MAGIC
        self.data_start = self.FILE_HEADER_FORMAT_SIZE
        self.meta_start = 0
        self.data_start_row = 0
        self.data_end_row = 0

        self.n_rows = 0
        self.n_cols = 0

        self.cache = []

        try:
           #  Try to read an existing file
            MPRowsFile.read_file_header(self, self._fh)

            self._fh.seek(self.meta_start)

            data = self._fh.read()

            self.meta = msgpack.unpackb(data.decode('zlib'), encoding='utf-8')

            self._fh.seek(self.meta_start)

        except IOError:
            # No, doesn exist, or is corrupt
            self._fh.seek(0)

            self.meta_start = self.data_start

            self.meta = deepcopy(self.META_TEMPLATE)

            self.write_file_header()  # Get moved to the start of row data.

        # Creating the GzipFile object will also write the Gzip header, about 21 bytes of data.
        if self._compress:
            self._zfh = GzipFile(fileobj=self._fh, compresslevel=9)  # Compressor for writing rows
        else:
            self._zfh = self._fh

        self.header_mangler = lambda name: re.sub('_+', '_', re.sub('[^\w_]', '_', name).lower()).rstrip('_')

        if self.n_rows == 0:
            self.meta['about']['create_time'] = time.time()


    @property
    def info(self):
        return MPRowsFile._info(self)

    def set_col_val(name_or_pos, **kwargs):
        pass

    @property
    def headers(self):
        """Return the headers rows

        """
        return [ e.name for e in MPRowsFile._columns(self) ]

    @headers.setter
    def headers(self, headers):
        """Set column names"""

        assert isinstance(headers,  (tuple, list)), headers

        for i, row in enumerate(MPRowsFile._columns(self, len(headers))):
            assert isinstance(headers[i], string_types)
            row.name = headers[i]

        assert self.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE

    @property
    def columns(self):
        """Return the headers rows

        """
        return MPRowsFile._columns(self)

    @columns.setter
    def columns(self, headers):

        for i, row in enumerate(MPRowsFile._columns(self, len(headers))):

            h = headers[i]

            if isinstance(h, dict):
                d = dict(h.items())
                raise NotImplementedError()
            else:
                row.name = h

    def column(self, name_or_pos):

        for h in self.columns:

            if name_or_pos == h.pos or name_or_pos == h.name:
                return h

        raise KeyError("Didn't find '{}' as either a name nor a position ".format(name_or_pos))

    def _write_rows(self, rows = None):

        rows, clear_cache = (self.cache, True) if not rows else (rows, False )

        if not rows:
            return

        try:
            self._zfh.write(msgpack.packb(rows, default=MPRowsFile.encode_obj, encoding='utf-8'))
        except IOError as e:
            raise IOError("Can't write row to file: {}".format(e))

        # Hope that the max # of cols is found in the first 100 rows
        # FIXME! This won't work if rows is an interator.
        self.n_cols = reduce(max, ( len(e) for e in rows[:100]), self.n_cols)

        if clear_cache:
            self.cache = []

    def insert_row(self, row):

        self.n_rows += 1
        self.data_end_row = self.n_rows

        self.cache.append(row)

        if len(self.cache) >= 10000:
            self._write_rows()

    def insert_rows(self, rows):
        '''Insert a list of rows. Don't insert iterators'''

        self.n_rows += len(rows)

        self.data_end_row = self.n_rows

        self._write_rows(rows)

    def load_rows(self, source, first_is_header=False):
        """Load rows from an iterator"""

        try:
            if source.headers:
                self.headers = source.headers
        except AttributeError:
            pass

        for row in iter(source):
            self.insert_row(row)

            # If the source has a headers property, and it's defined, then
            # use it for the headers. This often has to be called after iteration, because
            # the source may have the header as the first row


    def close(self):

        if len(self.cache):
            self._write_rows()

        if self._fh:
            # First close the Gzip file, so it can flush, etc.

            if self._compress and self._zfh:
                self._zfh.close()

            self._zfh = None

            self.meta_start = self._fh.tell()

            self.write_file_header()
            self._fh.seek(self.meta_start)

            self.write_meta()

            self._fh.close()
            self._fh = None

            if self.parent:
                self.parent._writer = None

    def write_file_header(self):
        """Write the magic number, version and the file_header dictionary.  """
        MPRowsFile.write_file_header(self, self._fh)

    def write_meta(self):
        MPRowsFile.write_meta(self, self._fh)

    def set_types(self, ti):
        """Set Types from a type intuiter object"""

        results = {int(r['position']): r for r in ti._dump()}
        for i in range(len(results)):

            for k, v in results[i].items():
                k = {'count': 'type_count'}.get(k,k)
                self.column(i+1)[k] = v

            if not self.column(i+1).type:
                self.column(i+1).type = results[i]['resolved_type']

    def set_stats(self, stats):
        """Copy stats into the schema"""

        for name, stat_set in stats.dict.items():
            row = self.column(name)

            for k, v in stat_set.dict.items():
                k = {'count': 'stat_count'}.get(k, k)
                row[k] = v


    def set_source_spec(self, spec):
        """Set the metadata coresponding to the SourceSpec, excluding the row spec parts. """

        ms = self.meta['source']

        ms['url'] = spec.url
        ms['fetch_time'] = spec.download_time
        ms['file_type'] = spec.filetype
        ms['url_type'] = spec.urltype
        ms['encoding'] = spec.encoding

        me = self.meta['excel']
        me['workbook'] = spec.segment

    def set_row_spec(self, ri_or_ss):
        """Set the row spec and schema from a RowIntuiter object or a SourceSpec"""

        from itertools import islice
        from operator import itemgetter
        from ambry_sources.intuit import RowIntuiter

        if isinstance(ri_or_ss, RowIntuiter):
            ri = ri_or_ss

            with self.parent.writer as w:

                w.data_start_row = ri.start_line

                w.meta['row_spec']['header_rows'] = ri.header_lines
                w.meta['row_spec']['comment_rows'] = ri.comment_lines
                w.meta['row_spec']['start_row'] = ri.start_line
                w.meta['row_spec']['end_row'] = ri.end_line
                w.meta['row_spec']['data_pattern'] = ri.data_pattern_source

                w.headers = [self.header_mangler(h) for h in ri.headers]

        else:
            ss = ri_or_ss

            with self.parent.reader as r:
                # If the header lines are specified, we need to also coalesce them ad
                # set the header
                if ss.header_lines:

                    max_header_line = max(ss.header_lines)
                    rows = list(islice(r.raw, max_header_line + 1))

                    header_lines = itemgetter(*ss.header_lines)(rows)

                    if not isinstance(header_lines[0], (list, tuple)):
                        header_lines = [header_lines]

                else:
                    header_lines = None

            with self.parent.writer as w:

                w.data_start_row = ss.start_line

                w.meta['row_spec']['header_rows'] = ss.header_lines
                w.meta['row_spec']['comment_rows'] = None
                w.meta['row_spec']['start_row'] = ss.start_line
                w.meta['row_spec']['end_row'] = ss.end_line
                w.meta['row_spec']['data_pattern'] = None

                if header_lines:
                    w.headers = [self.header_mangler(h) for h in RowIntuiter.coalesce_headers(header_lines)]

        # Now, look for the end line.
        if False:
            # FIXME: Maybe later ...
            r = self.parent.reader
            # Look at the last 100 rows, but don't start before the start row.
            test_rows = 100
            start = max(r.data_start_row, r.data_end_row - test_rows)

            end_rows = list(islice(r.raw, start, None))

            ri.find_end(end_rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

        if exc_val:
            return False


class MPRReader(object):
    """
    Read an MPR file

    """
    MAGIC = MPRowsFile.MAGIC
    VERSION = MPRowsFile.VERSION
    FILE_HEADER_FORMAT = MPRowsFile.FILE_HEADER_FORMAT
    FILE_HEADER_FORMAT_SIZE = MPRowsFile.FILE_HEADER_FORMAT.size
    META_TEMPLATE = MPRowsFile.META_TEMPLATE
    SCHEMA_TEMPLATE = MPRowsFile.SCHEMA_TEMPLATE

    def __init__(self, parent, fh, compress=True):
        """Reads the file_header and prepares for iterating over rows"""

        self.parent = parent
        self._fh = fh
        self._compress = compress
        self._headers = None
        self.data_start = 0
        self.meta_start = 0
        self.data_start_row = 0
        self.data_end_row = 0

        self.pos = 0  # Row position for next read, starts at 1, since header is always 0

        self.n_rows = 0
        self.n_cols = 0

        self._in_iteration = False

        MPRowsFile.read_file_header(self, self._fh)

        self.data_start = int(self._fh.tell())

        assert self.data_start == self.FILE_HEADER_FORMAT_SIZE

        if self._compress:
            self._zfh = GzipFile(fileobj=self._fh, end_of_data=self.meta_start)
        else:
            self._zfh = self._fh

        self.unpacker = msgpack.Unpacker(self._zfh, object_hook=MPRowsFile.decode_obj,
                                         use_list = False,
                                         encoding='utf-8')

        self._meta = None

    @property
    def info(self):
        return MPRowsFile._info(self)

    @property
    def meta(self):

        if self._meta is None:

            # Using the _fh b/c I suspect that the GzipFile attached to self._zfh has state that would
            # get screwed up if you read from a new position
            self._meta = MPRowsFile.read_meta(self, self._fh)

        return self._meta

    @property
    def columns(self):
        """Return the headers rows

        """
        return MPRowsFile._columns(self)

    @property
    def headers(self):
        """Return the headers rows

        """

        return [e.name for e in MPRowsFile._columns(self)]


    @property
    def raw(self):
        """A raw iterator, which ignores the data start and stop rows and returns all rows, as rows"""

        self._fh.seek(self.data_start)

        try:
            self._in_iteration = True

            for rows in self.unpacker:
                for row in rows:
                    yield row
                    self.pos += 1

        finally:
            self._in_iteration = False
            self.close()

    @property
    def meta_raw(self):
        """self self.raw interator, but returns a tuple with the rows classified"""

        rs = self.meta['row_spec']

        hr = rs['header_rows'] or []
        cr = rs['comment_rows'] or []
        sr = rs['start_row'] or self.data_start_row
        er = rs['end_row'] or self.data_end_row

        for i, row in enumerate(self.raw):

            if i in hr:
                label = 'H'
            elif i in cr:
                label = 'C'
            elif sr <= i <= er:
                label = 'D'
            else:
                label = 'B'

            yield (i, self.pos, label), row

    @property
    def rows(self):
        """Iterator for reading rows"""

        self._fh.seek(self.data_start)

        _ = self.headers  # Get the header, but don't return it.

        try:
            self._in_iteration = True

            while True:
                for row in  next(self.unpacker):
                    if self.data_start_row <= self.pos <= self.data_end_row:
                        yield row

                    self.pos += 1

        finally:
            self._in_iteration = False


    def __iter__(self):
        """Iterator for reading rows as RowProxy objects"""
        from ambry_sources.sources import RowProxy

        self._fh.seek(self.data_start)

        rp = RowProxy(self.headers)

        try:
            self._in_iteration = True
            while True:
                rows = next(self.unpacker)

                for row in rows:
                    if self.data_start_row <= self.pos <= self.data_end_row:
                        yield rp.set_row(row)

                    self.pos += 1

                #if self._fh.tell() >= self.meta_start:
                #    break


        finally:
            self._in_iteration = False

    def close(self):
        if self._fh:
            self.meta  # In case caller wants to read mea after close.
            self._fh.close()
            self._fh = None
            if self.parent:
                self.parent._reader = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

        if exc_val:
            return False
