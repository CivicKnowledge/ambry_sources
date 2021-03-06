# -*- coding: utf-8 -*-
import datetime
from itertools import islice
from operator import itemgetter
import string

from fs.opener import fsopendir

import pytest

from six import u

from ambry_sources import get_source
from ambry_sources.hdf_partitions import HDFPartition
from ambry_sources.intuit import TypeIntuiter, RowIntuiter

from tests import TestBase


class Test(TestBase):

    @classmethod
    def setUpClass(cls):
        super(Test, cls).setUpClass()
        cls.sources = cls.load_sources()

    # helpers
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
        from ambry_sources import head, tail

        cache_fs = fsopendir('temp://')

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

        for source_name, spec in self.sources.items():
            s = get_source(spec, cache_fs, callback=lambda x, y: (x, y))

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
                    ri = RowIntuiter().run(head(s, 20), tail(s, 20), w.n_rows)
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
        from ambry_sources import head, tail
        cache_fs = fsopendir(self.setup_temp_dir())
        spec = self.sources['simple_fixed']
        assert spec.has_rowspec is False
        s = get_source(spec, cache_fs, callback=lambda x, y: (x, y))

        # prepare HDFPartition.
        f = HDFPartition(cache_fs, spec.name)

        ri = RowIntuiter().run(head(s, 100), tail(s, 100))
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
        from ambry_sources import head, tail
        cache_fs = fsopendir(self.setup_temp_dir())

        def gen():

            yield list('abcde')

            for i in range(10):
                yield [i, i + 1, i + 2, i + 3, i + 4]

        f = HDFPartition(cache_fs, 'foobar')

        s = GeneratorSource(SourceSpec('foobar'), gen())

        ri = RowIntuiter().run(head(s, 100), tail(s, 100))
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

    def test_headers(self):

        fs = fsopendir('temp://')

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

    def test_stats(self):
        """Check that the sources can be loaded and analyzed without exceptions and that the
        guesses for headers and start are as expected"""
        from ambry_sources import head, tail

        cache_fs = fsopendir('temp://')

        source = get_source(self.sources['simple_stats'], cache_fs, callback=lambda x, y: (x, y))

        f = HDFPartition(cache_fs, source.spec.name)

        with f.writer as w:
            ri = RowIntuiter().run(head(source, 100), tail(source, 100))
            row_spec = self._row_intuiter_to_dict(ri)
            ti = TypeIntuiter().process_header(ri.headers).run(source)
            w.set_row_spec(row_spec, ri.headers)
            w.set_types(ti)

        f.load_rows(source, run_stats=True)

        expected = {
            u('str_a'):   (30, None, None, None, 10),
            u('str_b'):   (30, None, None, None, 10),
            u('float_a'): (30, 1.0, 5.5, 10.0, 10),
            u('float_b'): (30, 1.1, 5.5, 9.9, 10),
            u('float_c'): (30, 1.1, 5.5, 9.9, 10),
            u('int_b'):   (30, 1.0, 5.0, 9.0, 10),
            u('int_a'):   (30, 1.0, 5.5, 10.0, 10)}

        with f.reader as r:

            for col in r.columns:
                stats = (col.stat_count, col.min, round(col.mean, 1) if col.mean else None,
                         col.max,
                         col.nuniques)
                for a, b in zip(expected[col.name], stats):
                    self.assertEqual(
                        a, b,
                        'Saved stat ({}) does not match to expected ({}) for {}'.format(a, b, col.name))
