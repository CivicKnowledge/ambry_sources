# -*- coding: utf-8 -*-
import datetime
from itertools import islice
from operator import itemgetter
import string
import unittest

from fs.opener import fsopendir

import pytest

import six
from six import u

from ambry_sources import get_source
from ambry_sources.hdf_partitions import HDFPartition
from ambry_sources.intuit import TypeIntuiter, RowIntuiter

from tests import TestBase


class Test(TestBase):

    def _row_intuiter_to_dict(self, ri):
        """ Converts row intuiter to dict. """
        ret = {
            'header_rows': ri.header_lines,
            'comment_rows': ri.comment_lines,
            'start_row': ri.start_line,
            'end_row': ri.end_line,
            'data_pattern': ri.data_pattern_source
        }
        return ret

    def _get_headers(self, source, spec):
        """ Collects headers from spec and returns them. """
        if spec.header_lines:
            max_header_line = max(spec.header_lines)
            rows = list(islice(source, max_header_line + 1))
            header_lines = itemgetter(*spec.header_lines)(rows)
            if not isinstance(header_lines[0], (list, tuple)):
                header_lines = [header_lines]
        else:
            header_lines = None

        if header_lines:
            return [h for h in RowIntuiter.coalesce_headers(header_lines)]
        return []

    def _spec_to_dict(self, spec):
        """ Converts row spec to dict. """

        ret = {
            'header_rows': spec.header_lines,
            'comment_rows': None,
            'start_row': spec.start_line,
            'end_row': spec.end_line,
            'data_pattern': None
        }
        return ret

    def _generate_rows(self, N):

        rs = string.ascii_letters

        row = lambda x: [x, x * 2, x % 17, rs[x % 19:x % 19 + 20],
                         datetime.date(2000 + x % 15, 1 + x % 12, 10),
                         datetime.date(2000 + (x + 1) % 15, 1 + (x + 1) % 12, 10)]

        headers = list('abcdefghi')[:len(row(0))]

        rows = [row(i) for i in range(1, N+1)]

        return rows, headers


    @pytest.mark.slow
    def test_load_and_headers(self):
        """ Just checks that all of the sources can be loaded without exceptions. """

        cache_fs = fsopendir('temp://')

        sources = self.load_sources()

        source_headers = {
            'mz_with_zip_xl': [
                u('id'), u('gvid'), u('renter_cost_gt_30'), u('renter_cost_gt_30_cv'),
                u('owner_cost_gt_30_pct'), u('owner_cost_gt_30_pct_cv')],
            'mz_no_zip': [u('id'), u('uuid'), u('int'), u('float')],
            'namesu8': [u('origin_english'), u('name_english'), u('origin_native'), u('name_native')],
            'sf_zip': [u('id'), u('uuid'), u('int'), u('float')],
            'simple': [u('id'), u('uuid'), u('int'), u('float')],
            'csv_no_csv': [u('id'), u('uuid'), u('int'), u('float')],
            'mz_with_zip': [u('id'), u('uuid'), u('int'), u('float')],
            'rpeople': [u('name'), u('size')],
            'rent07': [
                u('id'), u('gvid'), u('renter_cost_gt_30'), u('renter_cost_gt_30_cv'),
                u('owner_cost_gt_30_pct'), u('owner_cost_gt_30_pct_cv')],
            'simple_fixed': [u('id'), u('uuid'), u('int'), u('float')],
            'altname': [u('id'), u('foo'), u('bar'), u('baz')],
            'rentcsv': [
                u('id'), u('gvid'), u('renter_cost_gt_30'), u('renter_cost_gt_30_cv'),
                u('owner_cost_gt_30_pct'), u('owner_cost_gt_30_pct_cv')],
            'renttab': [
                u('id'), u('gvid'), u('renter_cost_gt_30'), u('renter_cost_gt_30_cv'),
                u('owner_cost_gt_30_pct'), u('owner_cost_gt_30_pct_cv')],
            'multiexcel': [
                u('id'), u('gvid'), u('renter_cost_gt_30'), u('renter_cost_gt_30_cv'),
                u('owner_cost_gt_30_pct'), u('owner_cost_gt_30_pct_cv')],
            'rent97': [
                u('id'), u('gvid'), u('renter_cost_gt_30'), u('renter_cost_gt_30_cv'),
                u('owner_cost_gt_30_pct'), u('owner_cost_gt_30_pct_cv')]
        }

        for source_name, spec in sources.items():
            print('\n------\n{}\n-------'.format(source_name))
            #if source_name != 'namesu8':
            #    continue
            s = get_source(spec, cache_fs)

            f = HDFPartition(cache_fs, spec.name)
            if f.exists:
                f.remove()

            # FIXME: This is really complicated setup for HDFPartition file. Try to simplify.
            with f.writer as w:
                if spec.has_rowspec:
                    row_spec = self._spec_to_dict(spec)
                    headers = self._get_headers(s, spec)
                    ti = TypeIntuiter().process_header(headers).run(s)
                    w.set_row_spec(row_spec, headers)
                    w.set_types(ti)
                else:
                    ri = RowIntuiter().run(s)
                    row_spec = self._row_intuiter_to_dict(ri)
                    ti = TypeIntuiter().process_header(ri.headers).run(s)
                    w.set_row_spec(row_spec, ri.headers)
                    w.set_types(ti)
            f.load_rows(s)

            with f.reader as r:
                if spec.name in source_headers:
                    self.assertEqual(source_headers[spec.name], r.headers)
                # FIXME: test head, middle and tail rows.

    def test_fixed(self):
        cache_fs = fsopendir(self.setup_temp_dir())
        sources = self.load_sources()
        spec = sources['simple_fixed']
        assert spec.has_rowspec is False
        s = get_source(spec, cache_fs)

        # prepare HDFPartition.
        f = HDFPartition(cache_fs, spec.name)
        ri = RowIntuiter().run(s)
        row_spec = self._row_intuiter_to_dict(ri)
        ti = TypeIntuiter().process_header(ri.headers).run(s)
        with f.writer as w:
            w.set_row_spec(row_spec, ri.headers)
            w.set_types(ti)
        f.load_rows(s)
        self.assertEqual(f.headers, ['id', 'uuid', 'int', 'float'])
        # FIXME: Check first and last rows.

    def test_generator(self):
        from ambry_sources.sources import GeneratorSource, SourceSpec

        cache_fs = fsopendir(self.setup_temp_dir())

        def gen():

            yield list('abcde')

            for i in range(10):
                yield [i, i + 1, i + 2, i + 3, i + 4]

        f = HDFPartition(cache_fs, 'foobar')

        ri = RowIntuiter().run(GeneratorSource(SourceSpec('foobar'), gen()))
        row_spec = self._row_intuiter_to_dict(ri)
        ti = TypeIntuiter().process_header(ri.headers).run(GeneratorSource(SourceSpec('foobar'), gen()))
        with f.writer as w:
            w.set_row_spec(row_spec, ri.headers)
            w.set_types(ti)

        f.load_rows(GeneratorSource(SourceSpec('foobar'), gen()))

        self.assertEqual(f.headers, list('abcde'))
        rows = []

        for row in f.select():
            rows.append(row.dict)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], {'a': 0, 'b': 1, 'c': 2, 'd': 3, 'e': 4})
        self.assertEqual(rows[-1], {'a': 9, 'b': 10, 'c': 11, 'd': 12, 'e': 13})

    @unittest.skip('Not ready')
    def test_headers(self):

        fs = fsopendir('mem://')

        df = HDFPartition(fs, 'foobar')

        with df.writer as w:

            schema = lambda row, col: w.meta['schema'][row][col]

            w.headers = list('abcdefghi')

            self.assertEqual('a', schema(1, 1))
            self.assertEqual('e', schema(5, 1))
            self.assertEqual('i', schema(9, 1))

            for h in w.columns:
                h.description = '{}-{}'.format(h.pos, h.name)

            self.assertEqual('1-a', schema(1, 3))
            self.assertEqual('5-e', schema(5, 3))
            self.assertEqual('9-i', schema(9, 3))

            w.column(1).description = 'one'
            w.column(2).description = 'two'
            w.column('c').description = 'C'
            w.column('d')['description'] = 'D'

            self.assertEqual('one', schema(1, 3))
            self.assertEqual('two', schema(2, 3))
            self.assertEqual('C', schema(3, 3))
            self.assertEqual('D', schema(4, 3))

        with df.reader as r:
            schema = lambda row, col: r.meta['schema'][row][col]

            self.assertEqual(
                [u('a'), u('b'), u('c'), u('d'), u('e'), u('f'), u('g'), u('h'), u('i')],
                r.headers)

            self.assertEqual('one', schema(1, 3))
            self.assertEqual('two', schema(2, 3))
            self.assertEqual('C', schema(3, 3))
            self.assertEqual('D', schema(4, 3))

    @unittest.skip('Not ready')
    def test_stats(self):
        """Check that the sources can be loaded and analyzed without exceptions and that the
        guesses for headers and start are as expected"""

        cache_fs = fsopendir('temp://')
        #cache_fs = fsopendir('/tmp/ritest/')

        sources = self.load_sources('sources.csv')

        s = get_source(sources['simple_stats'], cache_fs)

        f = HDFPartition(cache_fs, s.spec.name).load_rows(s, run_stats=True)

        vals = {u'str_a':   (30, None, None, None, 10),
                u'str_b':   (30, None, None, None, 10),
                u'float_a': (30, 1.0, 5.5, 10.0, 10),
                u'float_b': (30, 1.1, 5.5, 9.9, 10),
                u'float_c': (30, 1.1, 5.5, 9.9, 10),
                u'int_b':   (30, 1.0, 5.0, 9.0, 10),
                u'int_a':   (30, 1.0, 5.5, 10.0, 10)}

        with f.reader as r:

            for col in r.columns:
                for a, b in zip(vals[col.name], (col.stat_count, col.min, round(col.mean, 1) if col.mean else None,
                                               col.max, col.nuniques)):
                    self.assertEqual(a, b, col.name)

    @unittest.skip('Not ready')
    def test_datafile(self):
        """
        Test Loading and interating over data files, exercising the three header cases, and the use
        of data start and end lines.

        :return:
        """
        from itertools import islice

        N = 500

        rows, headers = self._generate_rows(N)

        def first_row_header(data_start_row=None, data_end_row=None):

            # Normal Headers
            f = HDFPartition('mem://frh')
            w = f.writer

            w.columns = headers

            for row in rows:
                w.insert_row(row)

            if data_start_row is not None:
                w.data_start_row = data_start_row

            if data_end_row is not None:
                w.data_end_row = data_end_row

            w.close()

            self.assertEqual(
                (u('a'), u('b'), u('c'), u('d'), u('e'), u('f')),
                tuple(w.parent.reader.headers))

            w.parent.reader.close()

            return f

        def no_header(data_start_row=None, data_end_row=None):

            # No header, column labels.
            f = HDFPartition('mem://nh')
            w = f.writer

            for row in rows:
                w.insert_row(row)

            if data_start_row is not None:
                w.data_start_row = data_start_row

            if data_end_row is not None:
                w.data_end_row = data_end_row

            w.close()

            self.assertEqual(['col1', 'col2', 'col3', 'col4', 'col5', 'col6'], w.parent.reader.headers)

            w.parent.reader.close()

            return f

        def schema_header(data_start_row=None, data_end_row=None):
            # Set the schema
            f = HDFPartition('mem://sh')
            w = f.writer

            w.headers = ['x' + str(e) for e in range(len(headers))]

            for row in rows:
                w.insert_row(row)

            if data_start_row is not None:
                w.data_start_row = data_start_row

            if data_end_row is not None:
                w.data_end_row = data_end_row

            w.close()

            self.assertEqual(
                (u('x0'), u('x1'), u('x2'), u('x3'), u('x4'), u('x5')),
                tuple(w.parent.reader.headers))

            w.parent.reader.close()

            return f

        # Try a few header start / data start values.

        for ff in (first_row_header, schema_header, no_header):
            f = ff()

            with f.reader as r:
                l = list(r.rows)

                self.assertEqual(N, len(l))

            with f.reader as r:
                # Check that the first row value starts at one and goes up from there.
                map(lambda f: self.assertEqual(f[0], f[1][0]), enumerate(islice(r.rows, 5), 1))

        for ff in (first_row_header, schema_header, no_header):
            data_start_row = 5
            data_end_row = 15
            f = ff(data_start_row, data_end_row)

            with f.reader as r:
                l = list(r.rows)
                self.assertEqual(11, len(l))

    @unittest.skip('Not ready')
    def test_spec_load(self):
        """Test that setting a SourceSpec propertly sets the header_lines data start position"""

        from ambry_sources.sources import SourceSpec
        import string

        rs = string.ascii_letters

        n = 500

        rows, headers = self._generate_rows(n)

        blank = ['' for e in rows[0]]

        # Append a complex header, to give the RowIntuiter something to do.
        rows = [
            ['Dataset Title'] + blank[1:],
            blank,
            blank,
            [rs[i] for i, e in enumerate(rows[0])],
            [rs[i+1] for i, e in enumerate(rows[0])],
            [rs[i+2] for i, e in enumerate(rows[0])],
        ] + rows

        f = HDFPartition('mem://frh').load_rows(rows)

        d = f.info

        self.assertEqual(6, d['data_start_row'])
        self.assertEqual(506, d['data_end_row'])
        self.assertEqual([3, 4, 5], d['header_rows'])
        self.assertEqual(
            [u('a_b_c'), u('b_c_d'), u('c_d_e'), u('d_e_f'), u('e_f_g'), u('f_g_h')],
            d['headers'])

        class Rows(object):
            spec = SourceSpec(None, header_lines=(3, 4), start_line=5)

            def __iter__(self):
                return iter(rows)

        f = HDFPartition('mem://frh').load_rows(Rows())

        d = f.info

        self.assertEqual(5, d['data_start_row'])
        self.assertEqual(506, d['data_end_row'])
        self.assertEqual([3, 4], d['header_rows'])
        self.assertEqual(
            [u('a_b'), u('b_c'), u('c_d'), u('d_e'), u('e_f'), u('f_g')],
            d['headers'])