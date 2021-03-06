# -*- coding: utf-8 -*-
from decimal import Decimal

try:
    # py2, mock is external lib.
    from mock import patch
except ImportError:
    # py3, mock is included
    from unittest.mock import patch

import psycopg2

from six import binary_type

from fs.opener import fsopendir

from ambry_sources import get_source
from ambry_sources.med.postgresql import add_partition, _postgres_shares_group, POSTGRES_PARTITION_SCHEMA_NAME
from ambry_sources.mpf import MPRowsFile

from tests import PostgreSQLTestBase, TestBase


class Test(TestBase):

    @patch('ambry_sources.med.postgresql._postgres_shares_group')
    def test_creates_foreign_data_table_for_simple_fixed_mpr(self, fake_shares):
        fake_shares.return_value = True
        # build rows reader
        cache_fs = fsopendir(self.setup_temp_dir())
        sources = self.load_sources()
        spec = sources['simple_fixed']
        s = get_source(spec, cache_fs, callback=lambda x, y: (x, y))
        mprows = MPRowsFile(cache_fs, spec.name).load_rows(s)

        # first make sure file was not changed.
        expected_names = ['id', 'uuid', 'int', 'float']
        expected_types = ['int', binary_type.__name__, 'int', 'float']
        self.assertEqual(sorted([x['name'] for x in mprows.reader.columns]), sorted(expected_names))
        self.assertEqual(sorted([x['type'] for x in mprows.reader.columns]), sorted(expected_types))

        try:
            # create foreign data table
            PostgreSQLTestBase._create_postgres_test_db()
            conn = psycopg2.connect(**PostgreSQLTestBase.pg_test_db_data)

            try:
                with conn.cursor() as cursor:
                    # we have to close opened transaction.
                    cursor.execute('COMMIT;')
                    add_partition(cursor, mprows, 'table1')

                # try to query just added partition foreign data table.
                with conn.cursor() as cursor:
                    table = 'table1'

                    # count all rows
                    query = 'SELECT count(*) FROM {}.{};'.format(POSTGRES_PARTITION_SCHEMA_NAME, table)
                    cursor.execute(query)
                    result = cursor.fetchall()
                    self.assertEqual(result, [(10000,)])

                    # check first row
                    cursor.execute(
                        'SELECT id, uuid, int, float FROM {}.{} LIMIT 1;'
                        .format(POSTGRES_PARTITION_SCHEMA_NAME, table))
                    result = cursor.fetchall()
                    self.assertEqual(len(result), 1)
                    expected_first_row = (
                        1, 'eb385c36-9298-4427-8925-fe09294dbd', 30, Decimal('99.734691532'))
                    self.assertEqual(result[0], expected_first_row)

            finally:
                conn.close()
        finally:
            PostgreSQLTestBase._drop_postgres_test_db()

    def test_postgres_user_is_a_member_of_ambry_executor_group(self):
        self.assertTrue(_postgres_shares_group(), 'Add postgres user to ambry executor group.')
