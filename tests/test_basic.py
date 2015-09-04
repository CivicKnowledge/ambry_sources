# -*- coding: utf-8 -*-


import unittest
import ambry_sources
from fs.opener import fsopendir

class BasicTestSuite(unittest.TestCase):
    """Basic test cases."""

    def get_header_test_file(self, file_name):
        """ Creates source pipe from xls with given file name and returns it."""
        import os.path
        import tests
        import xlrd

        test_files_dir = os.path.join(os.path.dirname(tests.__file__), 'test_data', 'crazy_headers')

        class XlsSource(object):
            def __iter__(self):
                book = xlrd.open_workbook(os.path.join(test_files_dir, file_name))
                sheet = book.sheet_by_index(0)
                num_cols = sheet.ncols
                for row_idx in range(0, sheet.nrows):
                    row = []
                    for col_idx in range(0, num_cols):
                        value = sheet.cell(row_idx, col_idx).value
                        if value == '':
                            # FIXME: Is it valid requirement?
                            # intuiter requires None's in the empty cells.
                            value = None
                        row.append(value)
                    yield row

        return XlsSource()

    def load_sources(self):
        import tests
        import csv
        from os.path import join, dirname
        from ambry_sources.sources import ColumnSpec, SourceSpec

        test_data = fsopendir(join(dirname(tests.__file__), 'test_data'))

        sources = {}

        fixed_widths = (('id', 1, 6),
                        ('uuid', 7, 34),
                        ('int', 41, 3),
                        ('float', 44, 14),
                        )

        fw_columns = [ColumnSpec(**dict(zip('name start width'.split(), e))) for e in fixed_widths]

        with test_data.open('sources.csv') as f:
            r = csv.DictReader(f)

            for row in r:

                if row['name'] == 'simple_fixed':
                    row['columns'] = fw_columns

                ss = SourceSpec(**row)

                sources[ss.name] = ss

        return sources

    def test_download(self):
        """Just check that all of the sources can be downloaded without exceptions"""

        from ambry_sources import get_source

        cache_fs = fsopendir('temp://')

        sources = self.load_sources()

        for source_name, spec in sources.items():
            s = get_source(spec, cache_fs)
            print spec.url

            for i, row in enumerate(s):
                if i > 10:
                    break


    def test_row_intuit(self):
        from ambry_sources.intuit import RowIntuiter

        tf = self.get_header_test_file('two_comments_two_headers_300_data_rows.xls')

        ri = RowIntuiter(tf)

        for row in ri:
            pass

        print ri.headers
        print ri.comments






if __name__ == '__main__':
    unittest.main()
