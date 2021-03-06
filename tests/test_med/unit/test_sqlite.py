# -*- coding: utf-8 -*-

from datetime import datetime
import unittest

from attrdict import AttrDict

import six

try:
    # py3
    from unittest.mock import Mock, patch, PropertyMock
except ImportError:
    # py2
    from mock import Mock, patch, PropertyMock

from ambry_sources.med.sqlite import add_partition, _get_module_instance, Table, Cursor
from ambry_sources.mpf import MPRowsFile


class TestTable(unittest.TestCase):
    def test_returns_cursor(self):
        columns = []
        partition = AttrDict({
            'reader': {
                'rows': [[1]]}})
        table = Table(columns, partition)
        cursor = table.Open()
        self.assertTrue(hasattr(cursor, 'Next'))
        self.assertTrue(hasattr(cursor, 'Column'))


class TestCursor(unittest.TestCase):

    def _get_fake_table(self, rows=None, reader=None):
        if not rows:
            rows = [[1.1, 1.2], [2.1, 2.2], [3.1, 3.2]]
        if not reader:
            reader = {'rows': rows, 'close': lambda x: None}

        table = AttrDict({
            'mprows': {
                'reader': reader}})
        return table

    # Eof test
    def test_returns_False_if_current_row_is_not_empty(self):
        table = self._get_fake_table()
        self.assertTrue(Cursor(table).Eof)

    def test_returns_True_if_there_is_now_no_current_row(self):
        table = self._get_fake_table()
        cursor = Cursor(table)
        cursor._current_row = None

    # Rowid test
    def test_Rowid_returns_row_number(self):
        table = self._get_fake_table()
        cursor = Cursor(table)
        self.assertEqual(cursor.Rowid(), 1)

    # Column test
    def test_returns_value_by_column_index(self):
        table = self._get_fake_table()
        cursor = Cursor(table)
        self.assertEqual(cursor.Column(0), 1.1)
        self.assertEqual(cursor.Column(1), 1.2)

    def test_converts_datetime_value_to_isoformat(self):
        dt = datetime(2010, 10, 10, 10, 10, 10)
        table = self._get_fake_table(rows=[[dt]])
        cursor = Cursor(table)
        self.assertEqual(cursor.Column(0), '2010-10-10T10:10:10')

    # Next tests
    def test_calling_next_moves_cursor_to_next_row(self):
        table = self._get_fake_table()
        cursor = Cursor(table)
        self.assertEqual(cursor.Column(0), 1.1)
        cursor.Next()
        self.assertEqual(cursor.Column(0), 2.1)

    def test_calling_next_empties_current_row_if_there_is_no_next_row(self):
        table = self._get_fake_table(rows=[[1.1]])
        cursor = Cursor(table)
        self.assertEqual(cursor.Column(0), 1.1)
        self.assertIsNotNone(cursor._current_row)
        cursor.Next()
        self.assertIsNone(cursor._current_row)

    # Close tests
    def test_closes_and_empties_reader(self):
        table = self._get_fake_table()
        cursor = Cursor(table)
        with patch.object(cursor._reader, 'close') as fake_close:
            cursor.Close()
            fake_close.assert_called_once_with()
        self.assertIsNone(cursor._reader)


class AddPartitionTest(unittest.TestCase):

    @patch('ambry_sources.med.sqlite._get_module_instance')
    def test_creates_sqlite_module(self, fake_get):
        fake_connection = Mock()
        fake_mprows = _get_fake_mprows('int')
        add_partition(fake_connection, fake_mprows, 'vid1')
        fake_get.assert_called_once_with()

    @patch('ambry_sources.med.sqlite._get_module_instance')
    def test_creates_virtual_table(self, fake_get):
        fake_mprows = _get_fake_mprows('int')

        fake_connection = Mock()
        fake_execute = Mock()
        fake_connection.cursor = lambda: AttrDict({'execute': fake_execute})

        fake_mprows = _get_fake_mprows('int')

        add_partition(fake_connection, fake_mprows, 'vid1')
        fake_get.assert_called_once_with()
        fake_execute.assert_called_once_with('CREATE VIRTUAL TABLE vid1 using mod_partition(/tmp, tmp);')


class GetModuleClassTest(unittest.TestCase):

    def test_returns_source_class(self):
        mod = _get_module_instance()
        self.assertTrue(hasattr(mod, 'Create'))
        self.assertTrue(six.callable(mod.Create))

    def _assert_converts(self, python_type, sql_type):
        fake_mprows = _get_fake_mprows(python_type)
        filesystem_root = '/tmp'
        path = 'temp.mpr'
        with patch.object(MPRowsFile, 'reader', new_callable=PropertyMock) as fake_reader:
            fake_reader.return_value = fake_mprows.reader
            mod = _get_module_instance()
            query, table = mod.Create('db', 'modulename', 'dbname', 'table1', filesystem_root, path)
            self.assertIn(sql_type, query)

    # Source.Create tests
    def test_returns_create_table_query_and_table(self):
        fake_mprows = _get_fake_mprows('int')
        filesystem_root = '/tmp'
        path = 'temp.mpr'
        with patch.object(MPRowsFile, 'reader', new_callable=PropertyMock) as fake_reader:
            fake_reader.return_value = fake_mprows.reader
            mod = _get_module_instance()
            ret = mod.Create('db', 'modulename', 'dbname', 'table1', filesystem_root, path)
            self.assertEqual(len(ret), 2)
            query, table = ret
            self.assertEqual('CREATE TABLE table1("column1" INTEGER);', query)
            self.assertTrue(hasattr(table, 'Open'))

    def test_converts_int_to_integer_sqlite_type(self):
        self._assert_converts('int', '("column1" INTEGER)')

    def test_converts_float_to_real_sqlite_type(self):
        self._assert_converts('float', '("column1" REAL)')

    def test_converts_str_to_text_sqlite_type(self):
        self._assert_converts('str', '("column1" TEXT)')

    def test_converts_date_to_date_sqlite_type(self):
        self._assert_converts('date', '("column1" DATE)')

    def test_converts_datetime_to_timestamp_sqlite_type(self):
        self._assert_converts('datetime', '("column1" TIMESTAMP WITHOUT TIME ZONE)')

    def test_raises_exception_if_type_conversion_failed(self):
        fake_mprows = _get_fake_mprows('unknown')
        mod = _get_module_instance()
        filesystem_root = '/tmp'
        path = 'temp.mpr'
        with patch.object(MPRowsFile, 'reader', new_callable=PropertyMock) as fake_reader:
            fake_reader.return_value = fake_mprows.reader
            try:
                mod.Create('db', 'modulename', 'dbname', 'table1', filesystem_root, path)
            except Exception as exc:
                self.assertIn('Do not know how to convert', str(exc))


def _get_fake_mprows(type_):
    """ Returns fake instance of the MPRowsFile. """
    mprows = Mock(spec=MPRowsFile)
    mprows.reader = AttrDict({
        'columns': [{'type': type_, 'name': 'column1', 'pos': 0}]})
    mprows.path = 'tmp'
    mprows._fs = Mock()
    mprows._fs.root_path = '/tmp'
    return mprows
