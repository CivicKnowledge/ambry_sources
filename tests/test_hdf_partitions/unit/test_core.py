# -*- coding: utf-8 -*-
import json
import math
import unittest

import numpy as np

from fs.opener import fsopendir
from tables import open_file, Float64Col, StringCol, Int64Col, Int32Col

from six import b

from ambry_sources.hdf_partitions.core import _serialize, _deserialize
from ambry_sources.mpf import MPRowsFile
from ambry_sources.sources.util import RowProxy

try:
    # py3
    from unittest.mock import MagicMock, patch, call, PropertyMock
except ImportError:
    # py2
    from mock import MagicMock, patch, call, PropertyMock

from ambry_sources.hdf_partitions.core import HDFWriter, HDFPartition, HDFReader

from tests import TestBase


class HDFPartitionTest(TestBase):

    # _info tests
    def test_returns_dict_with_description(self):
        temp_fs = fsopendir('temp://')
        reader = MagicMock()
        hdf_partition = HDFPartition(temp_fs, path='temp.h5')
        ret = hdf_partition._info(reader)
        self.assertIn('version', ret)
        self.assertIn('data_start_pos', ret)

    # exists tests
    def test_returns_true_if_file_exists(self):
        temp_fs = fsopendir('temp://')
        temp_fs.createfile('temp.h5')
        hdf_partition = HDFPartition(temp_fs, path='temp.h5')
        self.assertTrue(hdf_partition.exists)

    # remove tests
    def test_removes_files(self):
        temp_fs = fsopendir('temp://')
        temp_fs.createfile('temp.h5')
        hdf_partition = HDFPartition(temp_fs, path='temp.h5')

        self.assertTrue(temp_fs.exists('temp.h5'))
        hdf_partition.remove()
        self.assertFalse(temp_fs.exists('temp.h5'))

    # meta tests
    def test_contains_meta_from_reader(self):
        temp_fs = fsopendir('temp://')
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        hdf_partition = HDFPartition(temp_fs, path='temp.h5')

        with patch.object(HDFReader, 'meta', {'a': ''}):
            self.assertEqual(hdf_partition.meta, {'a': ''})

    # stats tests
    def test_returns_stat_from_meta(self):
        temp_fs = fsopendir('temp://')

        hdf_partition = HDFPartition(temp_fs, path='temp.h5')

        with patch.object(HDFPartition, 'meta', new_callable=PropertyMock) as fake_meta:
            fake_meta.return_value = {'stats': 22}
            self.assertEqual(hdf_partition.stats, 22)

    # run_stats tests
    @patch('ambry_sources.hdf_partitions.core.Stats.run')
    @patch('ambry_sources.hdf_partitions.core.Stats.__init__')
    def test_creates_stat_from_reader(self, fake_init, fake_run):
        fake_init.return_value = None
        fake_run.return_value = {'a': 1}
        temp_fs = fsopendir('temp://')

        hdf_partition = HDFPartition(temp_fs, path='temp.h5')

        with patch.object(hdf_partition, '_reader', MagicMock()):
            with patch.object(hdf_partition, '_writer', MagicMock()):
                ret = hdf_partition.run_stats()
                self.assertEqual(ret, {'a': 1})

    @patch('ambry_sources.hdf_partitions.core.Stats.run')
    @patch('ambry_sources.hdf_partitions.core.Stats.__init__')
    def test_writes_stat_to_writer(self, fake_init, fake_run):
        fake_run.return_value = {'stat': 1}
        fake_init.return_value = None
        temp_fs = fsopendir('temp://')

        hdf_partition = HDFPartition(temp_fs, path='temp.h5')

        fake_reader = MagicMock()
        fake_writer = MagicMock(spec=HDFWriter)
        fake_set_stats = MagicMock()
        fake_writer.__enter__ = lambda x: fake_set_stats
        # FIXME: So complicated. Refactor.

        with patch.object(hdf_partition, '_reader', fake_reader):
            with patch.object(hdf_partition, '_writer', fake_writer):
                hdf_partition.run_stats()
                self.assertEqual(
                    fake_set_stats.mock_calls,
                    [call.set_stats({'stat': 1})])


class HDFWriterTest(TestBase):

    def _get_column(self, name, type_, predefined=None):
        if not predefined:
            predefined = {}

        col = []
        for el in MPRowsFile.SCHEMA_TEMPLATE:
            if el == 'name':
                col.append(name)
            elif el == 'type':
                col.append(type_)
            else:
                col.append(predefined.get(el, ''))
        return col

    # __init__ tests
    def test_raises_value_error_if_not_string_filename_given(self):
        parent = MagicMock()
        try:
            HDFWriter(parent, MagicMock())
            raise AssertionError('ValueError exception was not raised.')
        except ValueError as exc:
            self.assertIn('requires filename parameter as string', str(exc))

    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta')
    @patch('ambry_sources.hdf_partitions.core.open_file')
    def test_opens_existing_file_in_append_mode(self, fake_open, fake_read):
        fake_read.return_value = {'about': {}}
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)
        HDFWriter(parent, filename)
        self.assertIn(call(filename, mode='a'), fake_open.mock_calls)

    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta')
    @patch('ambry_sources.hdf_partitions.core.open_file')
    def test_reads_meta_from_existing_file(self, fake_open, fake_read):
        fake_read.return_value = {'about': {}}
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)
        HDFWriter(parent, filename)
        self.assertTrue(fake_read.called)

    # _write_rows test
    def test_writes_given_rows(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        # add two columns
        writer.meta['schema'].append(self._get_column('col1', 'int'))
        writer.meta['schema'].append(self._get_column('col2', 'str'))
        writer._write_rows(
            rows=[[1, 'row1'], [2, 'row2']])

        # rows are written
        self.assertEqual(writer._h5_file.root.partition.rows.nrows, 2)
        self.assertEqual(
            [x['col1'] for x in writer._h5_file.root.partition.rows.iterrows()],
            [1, 2])
        self.assertEqual(
            [x['col2'] for x in writer._h5_file.root.partition.rows.iterrows()],
            [b('row1'), b('row2')])

    def test_writes_cached_rows(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        # add two columns
        writer.meta['schema'].append(self._get_column('col1', 'int'))
        writer.meta['schema'].append(self._get_column('col2', 'str'))
        writer.cache = [[1, 'row1'], [2, 'row2']]
        writer._write_rows()

        self.assertEqual(writer.cache, [])
        # rows are written
        self.assertEqual(writer._h5_file.root.partition.rows.nrows, 2)
        self.assertEqual(
            [x['col1'] for x in writer._h5_file.root.partition.rows.iterrows()],
            [1, 2])
        self.assertEqual(
            [x['col2'] for x in writer._h5_file.root.partition.rows.iterrows()],
            [b('row1'), b('row2')])

    # insert_row test
    @patch('ambry_sources.hdf_partitions.core.HDFWriter._write_rows')
    def test_inserts_row_to_the_cache(self, fake_write_rows):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))

        writer.insert_row(['row1'])
        self.assertEqual(writer.n_rows, 1)
        self.assertEqual(writer.cache, [['row1']])
        fake_write_rows.assert_not_called()

    @patch('ambry_sources.hdf_partitions.core.HDFWriter._write_rows')
    def test_writes_rows_is_cache_is_large(self, fake_write_rows):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.cache = [[] for i in range(10000)]
        writer.insert_row(['row1'])
        fake_write_rows.assert_called_once_with()

    # load_rows test
    @patch('ambry_sources.hdf_partitions.core.HDFWriter.insert_row')
    @patch('ambry_sources.hdf_partitions.core.HDFWriter._write_rows')
    def test_inserts_and_writes_all_rows_from_source(self, fake_write_rows, fake_insert):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))

        writer.load_rows([['row1'], ['row2']])
        fake_write_rows.assert_called_once_with()
        self.assertEqual(
            fake_insert.mock_calls, [call(['row1']), call(['row2'])])

    # close tests
    def test_writes_rows_and_closes_file(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        h5_file = writer._h5_file
        with patch.object(writer, '_write_rows') as fake_write:
            writer.close()
            fake_write.assert_called_once_with()
        self.assertIsNone(writer._h5_file)
        self.assertEqual(h5_file.isopen, 0)

    # write_file_header tests
    def test_writes_file_header_to_table(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer._validate_groups()
        writer.n_rows = 2
        writer.n_cols = 3
        writer.write_file_header()

        self.assertIn('file_header', writer._h5_file.root.partition)
        table = writer._h5_file.root.partition.file_header
        self.assertEqual(
            [(x['version'], x['n_rows'], x['n_cols']) for x in table.iterrows()],
            [(1, 2, 3)])

    # _write_meta tests
    def test_writes_meta(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')

    def test_writes_meta_about(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.meta['about']['load_time'] = 123.12
        writer.meta['about']['create_time'] = 111.11
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')
        self.assertTrue(writer._h5_file.root.partition.meta, 'about')
        self.assertEqual(
            [(x['load_time'], x['create_time']) for x in writer._h5_file.root.partition.meta.about.iterrows()],
            [(123.12, 111.11)])

    def test_writes_meta_comments(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.meta['comments']['header'] = 'header'
        writer.meta['comments']['footer'] = 'footer'
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')
        self.assertTrue(writer._h5_file.root.partition.meta, 'comments')
        self.assertEqual(
            [(x['header'], x['footer']) for x in writer._h5_file.root.partition.meta.comments.iterrows()],
            [(b('header'), b('footer'))])

    def test_writes_meta_excel(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.meta['excel']['worksheet'] = 'sheet1'
        writer.meta['excel']['datemode'] = 1
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')
        self.assertTrue(writer._h5_file.root.partition.meta, 'excel')
        self.assertEqual(
            [(x['worksheet'], x['datemode']) for x in writer._h5_file.root.partition.meta.excel.iterrows()],
            [(b('sheet1'), 1)])

    def test_writes_meta_geo(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.meta['geo']['srs'] = 11
        writer.meta['geo']['bb'] = 22
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')
        self.assertTrue(writer._h5_file.root.partition.meta, 'geo')
        self.assertEqual(
            [(x['srs'], x['bb']) for x in writer._h5_file.root.partition.meta.geo.iterrows()],
            [(11, 22)])

    def test_writes_meta_row_spec(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.meta['row_spec']['data_pattern'] = 'pattern'
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')
        self.assertTrue(writer._h5_file.root.partition.meta, 'row_spec')
        self.assertEqual(
            [x['data_pattern'] for x in writer._h5_file.root.partition.meta.row_spec.iterrows()],
            [b('pattern')])

    def test_writes_meta_schema(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.meta['schema'].append(self._get_column('col1', 'int'))
        writer.meta['schema'].append(self._get_column('col2', 'str'))
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')
        self.assertTrue(writer._h5_file.root.partition.meta, 'schema')

        # check saved values.
        saved = [(x['name'], x['type']) for x in writer._h5_file.root.partition.meta.schema.iterrows()]
        self.assertEqual(len(saved), len(writer.meta['schema']) - 1)
        self.assertEqual(saved, [(b('col1'), b('int')), (b('col2'), b('str'))])

    def test_writes_meta_source(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer.meta['source']['encoding'] = 'utf-8'
        writer.meta['source']['url'] = 'http://example.com'
        writer._write_meta()

        self.assertEqual(writer.cache, [])
        self.assertTrue(writer._h5_file.root.partition, 'meta')
        self.assertTrue(writer._h5_file.root.partition.meta, 'source')
        self.assertEqual(
            [(x['encoding'], x['url']) for x in writer._h5_file.root.partition.meta.source.iterrows()],
            [(b('utf-8'), b('http://example.com'))])

    # _save_meta_child tests
    def test_saves_header_rows_and_comment_rows_as_json_string(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer._validate_groups()
        writer.meta['row_spec']['comment_rows'] = [0, 1]
        writer.meta['row_spec']['header_rows'] = [2, 3]
        descriptor = {
            'end_row': Int32Col(),
            'header_rows': StringCol(itemsize=255),
            'start_row': Int32Col(),
            'comment_rows': StringCol(itemsize=255),
            'data_pattern': StringCol(itemsize=255)
        }
        writer._save_meta_child('row_spec', descriptor)

        self.assertEqual(
            [(x['comment_rows'], x['header_rows']) for x in writer._h5_file.root.partition.meta.row_spec.iterrows()],
            [(b('[0, 1]'), b('[2, 3]'))])

    # _save_schema tests
    def test_saves_hist_and_uvalues_as_json_string(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        writer = HDFWriter(parent, temp_fs.getsyspath('temp.h5'))
        writer._validate_groups()
        writer.meta['schema'].append(
            self._get_column(
                'col1', 'str',
                predefined={'hist': [1, 2, 3], 'uvalues': ['1', '2', '3']}))
        writer._save_schema()

        self.assertEqual(
            [(x['hist'], x['uvalues']) for x in writer._h5_file.root.partition.meta.schema.iterrows()],
            [(b('[1, 2, 3]'), b('["1", "2", "3"]'))])


class HDFReaderTest(TestBase):

    # helpers

    def _write_test_meta(self, fs, child, descriptor, rows):
        with open_file(fs.getsyspath('temp.h5'), 'w') as h5:
            h5.create_group('/partition', 'meta', createparents=True)
            table = h5.create_table('/partition/meta', child, descriptor, 'meta.{}'.format(child))
            hdf_row = table.row
            for row in rows:
                for field, value in row:
                    hdf_row[field] = value
                hdf_row.append()
            table.flush()

    # _write_rows test
    def test_raises_ValueError_if_file_like_given(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        try:
            HDFReader(parent, temp_fs.open('temp.h5', 'w'))
            raise AssertionError('ValueError was not raised.')
        except ValueError:
            pass

    # meta tests
    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta')
    def test_reads_meta_if_cache_is_empty(self, fake_read):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        reader = HDFReader(parent, filename)
        reader.meta
        self.assertEqual(len(fake_read.mock_calls), 1)

    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta')
    def test_uses_cached_meta(self, fake_read):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        reader = HDFReader(parent, filename)
        reader._meta = {}
        reader.meta
        fake_read.assert_not_called()

    # _read_meta tests
    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta_child')
    def test_returns_default_template(self, fake_child):
        fake_child.return_value = {}
        temp_fs = fsopendir('temp://')
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        with open_file(filename, mode='r') as h5_file:
            ret = HDFReader._read_meta(h5_file)
            expected_keys = ['about', 'excel', 'row_spec', 'source', 'comments', 'geo', 'schema']
            self.assertEqual(sorted(expected_keys), sorted(ret.keys()))

    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta_child')
    def test_reads_meta_children(self, fake_child):
        fake_child.return_value = {}
        temp_fs = fsopendir('temp://')
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        with open_file(filename, mode='r') as h5_file:
            HDFReader._read_meta(h5_file)

            # _read_meta_child was called properly
            self.assertEqual(len(fake_child.mock_calls), 7)
            self.assertIn(call(h5_file, 'about'), fake_child.mock_calls)
            self.assertIn(call(h5_file, 'excel'), fake_child.mock_calls)
            self.assertIn(call(h5_file, 'row_spec'), fake_child.mock_calls)
            self.assertIn(call(h5_file, 'source'), fake_child.mock_calls)
            self.assertIn(call(h5_file, 'comments'), fake_child.mock_calls)
            self.assertIn(call(h5_file, 'geo'), fake_child.mock_calls)
            self.assertIn(call(h5_file, 'schema'), fake_child.mock_calls)

    def test_reads_meta_schema(self):
        temp_fs = fsopendir('temp://')
        filename = temp_fs.getsyspath('temp.h5')

        # use minimal descriptor to make the test simplier.
        descriptor = {
            'pos': Int64Col(),
            'name': StringCol(itemsize=255),
            'type': StringCol(itemsize=255)
        }
        rows = [[('pos', float(i)), ('name', str(i)), ('type', str(i))] for i in range(2)]
        self._write_test_meta(temp_fs, 'schema', descriptor, rows)

        with open_file(filename, mode='r') as h5_file:
            ret = HDFReader._read_meta(h5_file)
            self.assertIn('schema', ret)
            self.assertEqual(len(ret['schema']), 3)  # One for template, other for columns.
            self.assertEqual(ret['schema'][0], MPRowsFile.SCHEMA_TEMPLATE)
            self.assertEqual(len(ret['schema'][1]), len(MPRowsFile.SCHEMA_TEMPLATE))
            self.assertEqual(len(ret['schema'][0]), len(MPRowsFile.SCHEMA_TEMPLATE))

            pos_index = MPRowsFile.SCHEMA_TEMPLATE.index('pos')
            name_index = MPRowsFile.SCHEMA_TEMPLATE.index('name')
            self.assertEqual(ret['schema'][1][pos_index], 0)
            self.assertEqual(ret['schema'][2][pos_index], 1.0)

            self.assertEqual(ret['schema'][1][name_index], '0')
            self.assertEqual(ret['schema'][2][name_index], '1')

    # _read_meta_child tests

    def test_reads_lines_to_dict(self):
        temp_fs = fsopendir('temp://')

        # save meta.about to the file.
        descriptor = {
            'load_time': Float64Col(),
            'create_time': Float64Col()
        }
        self._write_test_meta(
            temp_fs, 'about', descriptor, [[('load_time', 1.0), ('create_time', 1.1)]])

        # now read it from file.
        with open_file(temp_fs.getsyspath('temp.h5'), 'r') as h5:
            ret = HDFReader._read_meta_child(h5, 'about')
            self.assertIsInstance(ret, list)
            first = ret[0]
            self.assertIn('load_time', first)
            self.assertEqual(first['load_time'], 1.0)

            self.assertIn('create_time', first)
            self.assertEqual(first['create_time'], 1.1)

    def test_converts_comment_rows_and_header_rows_json_to_list(self):
        temp_fs = fsopendir('temp://')

        # save meta.row_spec to the file.
        descriptor = {  # this is not valid descriptor, but I do not need it to be valid here.
            'header_rows': StringCol(itemsize=255),
            'comment_rows': StringCol(itemsize=255),
        }
        self._write_test_meta(
            temp_fs, 'row_spec', descriptor,
            [[('comment_rows', json.dumps([0, 1])), ('header_rows', json.dumps([2, 3]))]])

        # now read it from file.
        with open_file(temp_fs.getsyspath('temp.h5'), 'r') as h5:
            ret = HDFReader._read_meta_child(h5, 'row_spec')
            self.assertEqual(ret[0]['comment_rows'], [0, 1])
            self.assertEqual(ret[0]['header_rows'], [2, 3])

    def test_converts_hist_and_uvalues_json_to_list(self):
        temp_fs = fsopendir('temp://')

        # save meta.schema minimal table to the file.
        descriptor = {  # this is not valid descriptor, but I do not need it to be valid here.
            'hist': StringCol(itemsize=255),
            'uvalues': StringCol(itemsize=255),
        }
        self._write_test_meta(
            temp_fs, 'schema', descriptor,
            [[('hist', json.dumps([0, 1])), ('uvalues', json.dumps(['a', 'b']))]])

        # now read it from file.
        with open_file(temp_fs.getsyspath('temp.h5'), 'r') as h5:
            ret = HDFReader._read_meta_child(h5, 'schema')
            self.assertEqual(ret[0]['hist'], [0, 1])
            self.assertEqual(ret[0]['uvalues'], ['a', 'b'])

    # raw tests
    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta')
    def test_contains_all_rows(self, fake_read):
        fake_read.return_value = {}
        temp_fs = fsopendir('temp://')
        parent = MagicMock()

        # save meta.about to the file.
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        # now read it from file.
        reader = HDFReader(parent, filename)
        raw_iter = reader.raw
        first = next(raw_iter)
        self.assertEqual(first, [0.0, 0.0, 0.0])
        self.assertTrue(reader._in_iteration)
        rows = list(raw_iter)
        self.assertEqual(len(rows), 4)
        self.assertFalse(reader._in_iteration)

    # rows tests
    @patch('ambry_sources.hdf_partitions.core.HDFReader._read_meta')
    def test_generates_all_rows(self, fake_read):
        fake_read.return_value = {}
        temp_fs = fsopendir('temp://')
        parent = MagicMock()

        # save meta.about to the file.
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        # now read it from file.
        reader = HDFReader(parent, filename)
        rows_iter = reader.rows
        first = next(rows_iter)
        self.assertEqual(first, [0.0, 0.0, 0.0])
        self.assertTrue(reader._in_iteration)

        rows = list(rows_iter)
        self.assertEqual(len(rows), 4)
        self.assertFalse(reader._in_iteration)

    # __iter__ tests
    def test_generates_rows_as_RowProxy_instances(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        reader = HDFReader(parent, filename)
        with patch.object(HDFReader, 'headers', ['field1', 'field2', 'field3']):
            rows_iter = iter(reader)
            first = next(rows_iter)
            self.assertIsInstance(first, RowProxy)
            self.assertEqual(first.field1, 0.0)
            self.assertEqual(first.field2, 0.0)
            self.assertEqual(first.field3, 0.0)
            self.assertTrue(reader._in_iteration)

            rows = []
            for row in rows_iter:
                self.assertIsInstance(row, RowProxy)
                rows.append(row)
            self.assertEqual(len(rows), 4)
            self.assertFalse(reader._in_iteration)

    # __enter__ tests
    def test_returns_self(self):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        reader = HDFReader(parent, filename)
        self.assertEqual(
            reader, reader.__enter__())

    # __exit__ tests
    @patch('ambry_sources.hdf_partitions.core.HDFReader.close')
    def test_closes_reader(self, fake_close):
        temp_fs = fsopendir('temp://')
        parent = MagicMock()
        filename = temp_fs.getsyspath('temp.h5')
        _create_h5(filename)

        reader = HDFReader(parent, filename)
        reader.__exit__(None, None, None)
        fake_close.assert_called_once_with()


def _create_h5(filename):
    # save meta.about to the file.
    with open_file(filename, 'w') as h5:
        descriptor1 = {
            'field1': Float64Col(),
            'field2': Float64Col(),
            'field3': Float64Col()
        }
        descriptor2 = {
            'version': Int32Col(),
            'n_rows': Int32Col(),
            'n_cols': Int32Col()
        }
        h5.create_group('/partition', 'meta', createparents=True)
        rows_table = h5.create_table('/partition', 'rows', descriptor1)
        file_header_table = h5.create_table('/partition', 'file_header', descriptor2)
        for i in range(5):
            rows_table.row['field1'] = float(i)
            rows_table.row['field2'] = float(i)
            rows_table.row['field2'] = float(i)
            rows_table.row.append()
        rows_table.flush()

        file_header_table.row['version'] = 1
        file_header_table.row['n_rows'] = 1
        file_header_table.row['n_cols'] = 1
        file_header_table.row.append()
        file_header_table.flush()


class TestSerialize(unittest.TestCase):
    """ Tests _serialize function. """

    def test_converts_invalid_float_to_nan(self):
        ret = _serialize(Float64Col, 'invalid-float')
        self.assertTrue(math.isnan(ret))

    def test_converts_invalid_int32_to_minimal_int(self):
        ret = _serialize(Int32Col, 'invalid-int')
        self.assertEqual(ret, np.iinfo(np.int32).min)

    def test_converts_invalid_int64_to_minimal_int(self):
        ret = _serialize(Int64Col, 'invalid-int')
        self.assertEqual(ret, np.iinfo(np.int64).min)

    def test_converts_NA_to_empty_string(self):
        ret = _serialize(StringCol, 'NA')
        self.assertEqual(ret, '')

    def test_converts_StringCol_None_to_empty_string(self):
        ret = _serialize(StringCol, None)
        self.assertEqual(ret, '')

    def test_converts_Int32Col_None_to_minimal_int(self):
        ret = _serialize(Int32Col, None)
        self.assertEqual(ret, np.iinfo(np.int32).min)

    def test_converts_Int64Col_None_to_minimal_int(self):
        ret = _serialize(Int64Col, None)
        self.assertEqual(ret, np.iinfo(np.int64).min)

    def test_converts_Float64Col_None_to_nan(self):
        ret = _serialize(Float64Col, None)
        self.assertTrue(math.isnan(ret))

    def test_returns_valid_value_as_is(self):
        ret = _serialize(Float64Col, 1.1)
        self.assertEqual(ret, 1.1)


class TestDeserialize(unittest.TestCase):
    """ Tests _deserialize function. """

    def test_converts_min_int32_to_None(self):
        ret = _deserialize(np.iinfo(np.int32).min)
        self.assertIsNone(ret)

    def test_converts_min_int64_to_None(self):
        ret = _deserialize(np.iinfo(np.int64).min)
        self.assertIsNone(ret)

    def test_converts_nan_to_None(self):
        ret = _deserialize(float('nan'))
        self.assertIsNone(ret)

    def test_returns_value_as_is(self):
        ret = _deserialize(11.0)
        self.assertEqual(ret, 11.0)
