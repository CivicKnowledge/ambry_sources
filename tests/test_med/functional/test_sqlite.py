# -*- coding: utf-8 -*-

import apsw

from fs.opener import fsopendir

from six import binary_type

from ambry_sources import get_source
from ambry_sources.med.sqlite import add_partition
from ambry_sources.mpf import MPRowsFile

from tests import TestBase


class Test(TestBase):

    def test_creates_virtual_table_for_simple_fixed_mpr(self):
        # build rows reader
        cache_fs = fsopendir(self.setup_temp_dir())
        sources = self.load_sources()
        spec = sources['simple_fixed']
        s = get_source(spec, cache_fs, callback=lambda x, y: (x, y))
        mprows = MPRowsFile(cache_fs, spec.name).load_rows(s)

        # first make sure file not changed.
        expected_names = ['id', 'uuid', 'int', 'float']
        expected_types = ['int', binary_type.__name__, 'int', 'float']
        self.assertEqual([x['name'] for x in mprows.reader.columns], expected_names)
        self.assertEqual([x['type'] for x in mprows.reader.columns], expected_types)

        connection = apsw.Connection(':memory:')
        table = 'table1'
        add_partition(connection, mprows, table)

        # check all columns and some rows.
        cursor = connection.cursor()
        query = 'SELECT count(*) FROM {};'.format(table)
        result = cursor.execute(query).fetchall()
        self.assertEqual(result, [(10000,)])

        with mprows.reader as r:
            expected_first_row = next(iter(r)).row

        # query by columns.
        query = 'SELECT id, uuid, int, float FROM {} LIMIT 1;'.format(table)
        result = cursor.execute(query).fetchall()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], expected_first_row)
