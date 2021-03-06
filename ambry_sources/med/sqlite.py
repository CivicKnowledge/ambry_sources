# -*- coding: utf-8 -*-
from datetime import datetime, date

from fs.opener import fsopendir

from six import binary_type, text_type

from ambry_sources.mpf import MPRowsFile

from ambry.util import get_logger
import logging

logger = get_logger(__name__, level=logging.INFO, propagate=False)

# Documents used to implement module and function:
# Module: http://apidoc.apsw.googlecode.com/hg/vtable.html
# Functions: http://www.drdobbs.com/database/query-anything-with-sqlite/202802959?pgno=3

# python type to sqlite type map.
TYPE_MAP = {
    'int': 'INTEGER',
    'float': 'REAL',
    binary_type.__name__: 'TEXT',
    text_type.__name__: 'TEXT',
    'date': 'DATE',
    'datetime': 'TIMESTAMP WITHOUT TIME ZONE'
}

MODULE_NAME = 'mod_partition'


class Table:
    """ Represents a table """
    def __init__(self, columns, mprows):
        """

        Args:
            columns (list of str): column names
            mprows (mpf.MPRowsFile):

        """
        self.columns = columns
        self.mprows = mprows

    def BestIndex(self, *args):
        return None

    def Open(self):
        return Cursor(self)

    def Disconnect(self):
        pass

    Destroy = Disconnect


class Cursor:
    """ Represents a cursor """
    def __init__(self, table):
        self.table = table
        self._reader = table.mprows.reader
        self._rows_iter = iter(self._reader.rows)
        self._current_row = next(self._rows_iter)
        self._row_id = 1

    def Filter(self, *args):
        pass

    def Eof(self):
        return self._current_row is None

    def Rowid(self):
        return self._row_id

    def Column(self, col):
        value = self._current_row[col]
        if isinstance(value, (date, datetime)):
            # Convert to ISO format.
            return value.isoformat()
        return value

    def Next(self):
        try:
            self._current_row = next(self._rows_iter)
            self._row_id += 1
            assert isinstance(self._current_row, (tuple, list)), self._current_row
        except StopIteration:
            self._current_row = None

    def Close(self):
        self._reader.close()
        self._reader = None


def install_mpr_module(connection):
    """ Install module which allow to execute queries over mpr files.

    Args:
        connection (apsw.Connection):

    """
    from apsw import MisuseError  # Moved into function to allow tests to run when it isn't installed

    try:
        connection.createmodule(MODULE_NAME, _get_module_instance())


    except MisuseError:
        # TODO: The best solution I've found to check for existance. Try again later,
        # because MisuseError might mean something else.
        pass


def _relation_exists(connection, relation):
    """ Returns True if relation (table or view) exists in the sqlite db. Otherwise returns False.

    Args:
        connection (apsw.Connection): connection to sqlite database who stores mpr data.
        partition (orm.Partition):

    Returns:
        boolean: True if relation exists, False otherwise.

    """
    query = 'SELECT 1 FROM sqlite_master WHERE (type=\'table\' OR type=\'view\') AND name=?;'
    cursor = connection.cursor()
    cursor.execute(query, [relation])
    result = cursor.fetchall()
    return result == [(1,)]

def add_partition(connection, mprows, table):
    """ Installs the module and reates virtual table for partition.

    Args:
        connection (apsw.Connection):
        mprows (mpf.MPRowsFile):
        table (str): name of the partition table ( the vid of the partition )

    """
    install_mpr_module(connection)

    if _relation_exists(connection, table):
        return


    # create a virtual table.
    cursor = connection.cursor()

    # .replace('.mpr','') ... drop extension because some partition may fail with
    # SQLError: SQLError: unrecognized token: See https://github.com/CivicKnowledge/ambry_sources/issues/22
    # for details. MPRows implementation is clever enough to restore partition before reading.

    if not mprows.exists:
        from ambry_sources.exceptions import VirtualTableError
        raise VirtualTableError("Non existent MPR file {}".format(mprows.url))

    query = 'CREATE VIRTUAL TABLE {table} using {module}({url});'\
            .format(table=table, module=MODULE_NAME, url=mprows.url.replace('.mpr',''))
    try:
        logger.debug("Creating VT with: {}".format(query))
        cursor.execute(query)
    except Exception as e:
        logger.warn("While adding a partition to sqlite warehouse, failed to exec '{}' ".format(query))
        raise


def _get_module_instance():
    """ Returns module instance for the partitions virtual tables.

    Note:
        There is only one module for all virtual tables.

    """

    class Source:
        def Create(self, db, modulename, dbname, tablename, # These argare are required by APSW
                   mpr_url, *args): # These are our args.

            mprows = MPRowsFile(mpr_url)

            columns_types = []
            column_names = []

            for column in sorted(mprows.reader.columns, key=lambda x: x['pos']):
                sqlite_type = TYPE_MAP.get(column['type'])
                if not sqlite_type:
                    raise Exception('Do not know how to convert {} to sql column.'.format(column['type']))
                columns_types.append('"{}" {}'.format(column['name'], sqlite_type))
                column_names.append(column['name'])

            columns_types_str = ',\n'.join(columns_types)
            schema = 'CREATE TABLE {}({});'.format(tablename, columns_types_str)

            return schema, Table(column_names, mprows)

        Connect = Create

    return Source()
