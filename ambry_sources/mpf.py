# -*- coding: utf-8 -*-
"""
Writing data to a partition. The MPR file format is a conversion format that stores
tabular data in rows and associates it with metadata.

Copyright (c) 2015 Civic Knowledge. This file is licensed under the terms of the
Revised BSD License, included in this distribution as LICENSE.txt
"""

import datetime
import gzip
from functools import reduce
import os
import stat
import struct
import time
import zlib

import six
from six import iteritems, text_type

import msgpack

from ambry_sources.util import get_perm, is_group_readable


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
            if six.PY3:
                return None
            else:
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
        },
        'process': {
            'finalized': False
        },
        'warnings': []
    }

    def __init__(self, url_or_fs, path=None):
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

    @property
    def syspath(self):
        if self.exists and self._fs.hassyspath(self.path):
            return self._fs.getsyspath(self.path)
        else:
            return None

    @property
    def url(self):
        from fs.errors import NoPathURLError

        try:
            self._fs.getpathurl(self.path)
        except NoPathURLError:
            return self._fs.getsyspath(self.path)


    @staticmethod
    def encode_obj(obj):

        try:
            if isinstance(obj, datetime.datetime):
                return {'__datetime__': True, 'value': tuple(obj.timetuple()[:6])}
            elif isinstance(obj, datetime.date):
                return {'__date__': True, 'value': (obj.year, obj.month, obj.day)}
            elif isinstance(obj, datetime.time):
                return {'__time__': True, 'value': (obj.hour, obj.minute, obj.second)}
        except ValueError as e:
            # Pandas time series can have a "Not A Time" value of 'NaT', but I don't want to have this
            # module depend on pandas

            if str(obj) == 'NaT':
                return None
            else:
                raise


        if hasattr(obj, 'render'):
            return obj.render()
        elif hasattr(obj, '__str__'):
            return str(obj)
        else:
            raise Exception('Unknown type on encode: {}, {}'.format(type(obj), obj))

    @staticmethod
    def decode_obj(obj):

        if '__datetime__' in obj:
            obj = datetime.datetime(*obj['value'])
        elif '__time__' in obj:
            obj = datetime.time(*obj['value'])
        elif '__date__' in obj:
            obj = datetime.date(*obj['value'])
        else:
            raise Exception('Unknown type on decode: {} '.format(obj))

        return obj

    @classmethod
    def read_file_header(cls, o, fh):
        try:
            o.magic, o.version, o.n_rows, o.n_cols, o.meta_start, o.data_start_row, o.data_end_row = \
                cls.FILE_HEADER_FORMAT.unpack(fh.read(cls.FILE_HEADER_FORMAT_SIZE))
        except struct.error as e:
            raise IOError('Failed to read file header; {}; path = {}'.format(e, o.parent.path))

    @classmethod
    def write_file_header(cls, o, fh):
        """Write the magic number, version and the file_header dictionary.  """

        int(o.data_start_row)
        magic = cls.MAGIC
        if isinstance(magic, text_type):
            magic = magic.encode('utf-8')

        hdf = cls.FILE_HEADER_FORMAT.pack(magic, cls.VERSION, o.n_rows, o.n_cols, o.meta_start,
                                          o.data_start_row,  o.data_end_row if o.data_end_row else o.n_rows)

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
            meta = msgpack.unpackb(zlib.decompress(data), encoding='utf-8')
        else:
            meta = {}
        fh.seek(pos)
        return meta

    @classmethod
    def write_meta(cls, o, fh):

        o.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE

        fh.seek(o.meta_start)  # Should probably already be there.
        fhb = zlib.compress(msgpack.packb(o.meta, encoding='utf-8'))
        fh.write(fhb)

    @classmethod
    def _columns(cls, o, n_cols=0):
        """ Wraps columns from meta['schema'] with RowProxy and generates them.

        Args:
            o (any having .meta dict attr):

        Generates:
            RowProxy: column wrapped with RowProxy

        """

        from ambry_sources.sources.util import RowProxy

        s = o.meta['schema']

        assert len(s) >= 1  # Should always have header row.
        if o.meta['schema'][0] != MPRowsFile.SCHEMA_TEMPLATE:
            raise AssertionError(
                'Object schema does not match to template. object schema: {}, template: {}'
                .format(o.meta['schema'][0], MPRowsFile.SCHEMA_TEMPLATE))

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
        """ Returns True if mpr file (self.path) exists in the filesystem (self._fs). False otherwise. """

        return self._fs.exists(self.path)

    def remove(self):
        if self.exists:
            from fs.s3fs import S3FS
            assert not isinstance(self._fs, S3FS) # Let's not be deleteing from remotes.
            self._fs.remove(self.path)
            self.close()

    def close(self):

        if self._reader:
            self._reader.close()

        if self._writer:
            self._reader.close()

    @property
    def meta(self):

        if not self.exists:
            return None

        with self.reader as r:
            return r.meta

    @property
    def is_finalized(self):
        with self.reader as r:
            return r.is_finalized

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
        from itertools import islice

        try:
            self._process = 'intuit_rows'
            self._start_time = time.time()

            with self.reader as r:
                if r.n_rows == 0:
                    return

                head = list(islice(r.raw, RowIntuiter.N_TEST_ROWS))
                n_rows = r.n_rows

            with self.reader as r:
                # Reset the iterator to get the tail
                if RowIntuiter.N_TEST_ROWS < r.n_rows:
                    tail = list(islice(r.raw, r.n_rows - RowIntuiter.N_TEST_ROWS, r.n_rows))
                else:
                    tail = list(islice(r.raw, 0, r.n_rows))

            ri = RowIntuiter().run(head, tail, n_rows)

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
                if r.n_rows == 0:
                    return
                columns = [(c.name, c.type) for c in r.columns]
                stats = Stats(columns, r.n_rows).run(r, sample_from=r.n_rows)

            with self.writer as w:
                w.set_stats(stats)

        finally:
            self._process = 'none'

        return stats

    def load_rows(self, source, spec=None, intuit_rows=None,
                  intuit_type=True, run_stats=True, callback=False, limit=None):
        try:

            # The spec should always be part of the source
            assert spec is None

            self._load_rows(source,
                            intuit_rows=intuit_rows,
                            intuit_type=intuit_type, run_stats=run_stats,
                            callback=callback, limit=limit)
        except:
            raise
            self.writer.close()
            self.remove()
            raise

        return self

    def _load_rows(self, source, intuit_rows=None, intuit_type=True, run_stats=True,
                   callback=None, limit=None):
        from .exceptions import RowIntuitError

        if self.n_rows:
            raise MPRError(
                "Can't load_rows into {}; rows already loaded. n_rows = {}"
                .format(self.path, self.n_rows))

        spec = getattr(source, 'spec', None)

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

                w.load_rows(source, callback=callback, limit=limit)

                if spec:
                    w.set_source_spec(spec)

            if intuit_rows:
                try:
                    self.run_row_intuiter()
                except RowIntuitError:
                    with self.writer as w:
                        w.meta['warnings'].append('Failed to intuit rows. Should set row classifications manually. ')

                    pass

            elif spec:

                with self.writer as w:
                    w.set_row_spec(spec)
                    assert w.meta['schema'][0] == MPRowsFile.SCHEMA_TEMPLATE

            if source.meta:
                with self.writer as w:
                    for c, m in zip(w.columns, source.meta['columns']):
                        assert c.pos == m['position']

                        #assert c.name == m['name'] # True for SocrataSource, maybe not if there are others in the future

                        col = w.column(c.name)

                        col.description = m['description']


            if intuit_type:
                self.run_type_intuiter()

            if run_stats:
                self.run_stats()

            with self.writer as w:

                if not w.data_end_row:
                    w.data_end_row = w.n_rows

                w.finalize()

        finally:
            self._process = None

        return self

    def open(self, mode='rb'):
        """Open the file, and return a file-like pyfilesystem object"""
        return self._fs.open(self.path, mode=mode)

    def set_contents(self, data='', errors=None, chunk_size=65536):
        """Pass-though to the PySilesystem setcontents function"""
        return self._fs.setcontents(self.path,  data,  errors=errors, chunk_size=chunk_size)

    @property
    def reader(self):
        if not self._reader:
            self._reader = MPRReader(self, self._fs.open(self.path, mode='rb'), compress=self._compress)
        return self._reader

    def __iter__(self):
        """Iterate over a reader"""

        # There is probably a more efficient way in python 2 to do this than to have another yield loop,
        # but just returning the reader iterator doesn't work. It should probably be yield from in Python 3
        with self.reader as r:
            for row in r:
                yield row

    def select(self, predicate=None, headers=None):
        """Iterate the results from the reader's select() method"""

        with self.reader as r:
            for row in r.select(predicate, headers):
                yield row

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
                self._fs.makedir(dirname(self.path), recursive=True, allow_recreate=True)

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
        >>> f.load_rows([i,i,i] for i in range(1000))

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

    # In most tests, the block size doesn't matter much, with 1000 row blocks having the same performance of
    # 10 row blocks. This seems to be because for the test rows, the cost of managing the cache is similar to the
    # cost of writing.
    # There is, however, a very large gain from writing a collection of rows as a single block with insert_rows()

    BLOCK_SIZE = 1000  # Size of blocks of rows to write

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
        self.data_end_row = None

        self.n_rows = 0
        self.n_cols = 0

        self.cache = []

        try:
            #  Try to read an existing file
            MPRowsFile.read_file_header(self, self._fh)

            self._fh.seek(self.meta_start)

            data = self._fh.read()

            self.meta = msgpack.unpackb(zlib.decompress(data), encoding='utf-8')

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

        self.header_mangler = lambda name: re.sub('_+', '_', re.sub('[^\w_]', '_', name.strip()).lower()).rstrip('_')

        if self.n_rows == 0:
            self.meta['about']['create_time'] = time.time()

    @property
    def info(self):
        return MPRowsFile._info(self)

    @property
    def path(self):
        return self.parent.path

    @property
    def syspath(self):
        return self.parent.syspath

    def set_col_val(name_or_pos, **kwargs):
        pass

    @property
    def headers(self):
        """Return the headers rows

        """
        return [e.name for e in MPRowsFile._columns(self)]

    @headers.setter
    def headers(self, headers):
        """Set column names"""

        if not headers:
            return

        assert isinstance(headers,  (tuple, list)), headers

        for i, row in enumerate(MPRowsFile._columns(self, len(headers))):
            try:
                row.name = headers[i]
            except KeyError:
                row.name = 'col{}'.format(i)

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
                raise NotImplementedError()
            else:
                row.name = h if h else 'column{}'.format(i)

    def column(self, name_or_pos):

        for h in self.columns:

            if name_or_pos == h.pos or name_or_pos == h.name:
                return h

        raise KeyError("Didn't find '{}' as either a name nor a position in file '{}' "
                       .format(name_or_pos, self.path))

    def _write_rows(self, rows=None):

        rows, clear_cache = (self.cache, True) if not rows else (rows, False)

        if not rows:
            return

        try:
            self._zfh.write(msgpack.packb(rows, default=MPRowsFile.encode_obj, encoding='utf-8'))
        except IOError as e:
            raise IOError("Can't write row to file '{}': {}".format(self.syspath, e))

        # Hope that the max # of cols is found in the first 100 rows
        # FIXME! This won't work if rows is an interator.
        self.n_cols = reduce(max, (len(e) for e in rows[:100]), self.n_cols)

        if clear_cache:
            self.cache = []

        self._fix_permissions()

    def _fix_permissions(self):
        """ Adds read permission to each directory in the mpr path to user group.

        Note:
            This is the required thing for postgres FDW. Also you need to add postgres system user to group of
            the user who executes ambry_sources.

        """
        syspath = self.syspath
        if syspath:
            parts = syspath.split(os.sep)
            parts[0] = os.sep
            for i, dir_ in enumerate(parts):
                if dir_ == '/':
                    continue
                path = parts[:i]
                path.append(dir_)
                path = os.path.join(*path)
                if not is_group_readable(path):
                    os.chmod(path, get_perm(path) | stat.S_IRGRP | stat.S_IXGRP)

    def insert_row(self, row):

        self.n_rows += 1

        self.cache.append(row)

        if True or len(self.cache) >= self.BLOCK_SIZE:
            self._write_rows()

    def insert_rows(self, rows):
        """ Insert a list of rows. Don't insert iterators. """

        self.n_rows += len(rows)

        self._write_rows(rows)

    def load_rows(self, source, callback=None, limit=None):
        """Load rows from an iterator"""

        for i, row in enumerate(iter(source), 1):
            self.insert_row(row)
            if callback:
                callback(i)
            if limit and i > limit:
                break

        self._write_rows()

        # If the source has a headers property, and it's defined, then
        # use it for the headers. This often has to be called after iteration, because
        # the source may have the header as the first row
        try:
            if source.headers:
                self.headers = [self.header_mangler(h) for h in source.headers]

        except AttributeError:
            pass

    def finalize(self):
        """Mark the loading of the file as finished. """
        self.meta['process']['finalized'] = True

    @property
    def is_finalized(self):
        return self.meta['process']['finalized'] is True

    def close(self):

        if self._fh:

            self._write_rows()

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
        """ Set Types from a type intuiter object. """

        results = {int(r['position']): r for r in ti._dump()}
        for i in range(len(results)):

            for k, v in iteritems(results[i]):
                k = {'count': 'type_count'}.get(k, k)
                self.column(i + 1)[k] = v

            if not self.column(i + 1).type:
                self.column(i + 1).type = results[i]['resolved_type']

    def set_stats(self, stats):
        """Copy stats into the schema"""

        for name, stat_set in iteritems(stats.dict):
            row = self.column(name)

            for k, v in iteritems(stat_set.dict):
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

        if spec.columns:

            for i, sc in enumerate(spec.columns, 1):
                c = self.column(i)

                if c.name:
                    assert self.header_mangler(sc.name) == c.name, \
                        '`{}` column name from spec does not match to `{}` column'.format(sc.name, c.name)

                c.start = sc.start
                c.width = sc.width

    def set_row_spec(self, ri_or_ss):
        """Set the row spec and schema from a RowIntuiter object or a SourceSpec"""

        from itertools import islice
        from operator import itemgetter
        from ambry_sources.intuit import RowIntuiter

        def set_descriptions(w, descriptions):

            for c, d in zip(w.columns, descriptions):
                col = w.column(c.name)
                d = d.replace('\n', ' ').replace('\r', ' ')
                col.description = d

        if isinstance(ri_or_ss, RowIntuiter):
            ri = ri_or_ss

            with self.parent.writer as w:

                w.data_start_row = ri.start_line
                w.data_end_row = ri.end_line if ri.end_line else None

                w.meta['row_spec']['header_rows'] = ri.header_lines
                w.meta['row_spec']['comment_rows'] = ri.comment_lines
                w.meta['row_spec']['start_row'] = ri.start_line
                w.meta['row_spec']['end_row'] = ri.end_line
                w.meta['row_spec']['data_pattern'] = ri.data_pattern_source

                set_descriptions(w, [h for h in ri.headers])

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
                w.data_end_row = ss.end_line if ss.end_line else None

                w.meta['row_spec']['header_rows'] = ss.header_lines
                w.meta['row_spec']['comment_rows'] = None
                w.meta['row_spec']['start_row'] = ss.start_line
                w.meta['row_spec']['end_row'] = ss.end_line
                w.meta['row_spec']['data_pattern'] = None

                if header_lines:
                    set_descriptions(w, [h for h in RowIntuiter.coalesce_headers(header_lines)])
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

        try:
            self.data_start = int(self._fh.tell())

            assert self.data_start == self.FILE_HEADER_FORMAT_SIZE
        except AttributeError:
            # The pyfs HTTP filesystem doesn't have tell()
            self.data_start = self.FILE_HEADER_FORMAT_SIZE

        if self._compress:
            self._zfh = GzipFile(fileobj=self._fh, end_of_data=self.meta_start)
        else:
            self._zfh = self._fh

        self.unpacker = msgpack.Unpacker(self._zfh, object_hook=MPRowsFile.decode_obj,
                                         use_list=False,
                                         encoding='utf-8')

        self._meta = None

    @property
    def path(self):
        return self.parent.path

    @property
    def syspath(self):
        return self.parent.syspath

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
    def is_finalized(self):
        try:
            return self.meta['process']['finalized']
        except KeyError:  # Old version, doesn't have 'process' key
            return False

    @property
    def columns(self):
        """Return columns."""
        return MPRowsFile._columns(self)

    @property
    def headers(self):
        """Return the headers (column names)."""
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
                for row in next(self.unpacker):
                    if self.data_start_row <= self.pos <= self.data_end_row:
                        yield row

                    self.pos += 1

        finally:
            self._in_iteration = False

    def _get_row_proxy(self):
        from ambry_sources.sources import RowProxy, GeoRowProxy
        if 'geometry' in self.headers:
            rp = GeoRowProxy(self.headers)
        else:
            rp = RowProxy(self.headers)

        return rp

    def __iter__(self):
        """Iterator for reading rows as RowProxy objects

        WARNING: This routine returns RowProxy objects. RowProxy objects
        are reused, so if you construct a list directly from the output from this method, the list will have
        multiple copies of a single RowProxy, which will have as an inner row the last result row. If you will
        be directly constructing a list, use a getter that extracts the inner row, or which converted the RowProxy
        to a dict.

        """

        self._fh.seek(self.data_start)

        rp = self._get_row_proxy()

        try:
            self._in_iteration = True
            while True:
                rows = next(self.unpacker)

                for row in rows:
                    if self.data_start_row <= self.pos <= self.data_end_row:
                        yield rp.set_row(row)

                    self.pos += 1

        finally:
            self._in_iteration = False

    def select(self, predicate=None, headers=None):
        """
        Select rows from the reader using a predicate to select rows and and itemgetter to return a
        subset of elements
        :param predicate: If defined, a callable that is called for each row and if it returns true, the
        row is included in the output.
        :param getter: If defined, a list or tuple of header names to return from each row
        :return: iterable of results

        WARNING: This routine works from the reader iterator, which returns RowProxy objects. RowProxy objects
        are reused, so if you construct a list directly from the output from this method, the list will have
        multiple copies of a single RowProxy, which will have as an inner row the last result row. If you will
        be directly constructing a list, use a getter that extracts the inner row, or which converted the RowProxy
        to a dict:

            list(s.datafile.select(lambda r: r.stusab == 'CA' ))

        """

        if headers:

            from operator import itemgetter

            ig = itemgetter(*headers)

            rp = self._get_row_proxy()

            getter = lambda r: rp.set_row(ig(r.dict))

        else:

            getter = None

        if getter is not None and predicate is not None:
            return six.moves.map(getter, filter(predicate, iter(self)))

        elif getter is not None and predicate is None:
            return six.moves.map(getter, iter(self))

        elif getter is None and predicate is not None:
            return six.moves.filter(predicate, self)

        else:
            return iter(self)

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
