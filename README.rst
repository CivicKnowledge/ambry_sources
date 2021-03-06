Ambry Partition Message Pack File
=================================

This module supports the Ambry ETL framework by providing a file format for storing data and a collection
of import routines for other file formats

The Message Pack Rows (MPR) file format consists of a compressed collection of arrays, in message pack, followed by a
dictionary of metadata, also in Message Pack. The format efficiently stores tabular data and associates it with
metadata, with a few special features for use with data that can come from a variety of sources.

For instance, data a Fixed Width text file may not have a the column titles -- headers -- in the first row, so the
file can store a schema in metadata. Other files, such as those that originate in Excel, may not have their headers
on the first so the MPR file can specify a later row to be the start of data.

This module also includes classes for guessing the datatypes of each column, determining where the first row of data
begins, and computing statistics.

Command Line Interface
----------------------

The module installs a command line program ``ampr`` which can be used to inspect MPR files. Run ``ambry -h`` for help.


Source File Configuration
-------------------------

Parameters that can be set on a source file.

- url. The URL of the source file. If the URL has a fragment ( '#' ) the fragment represents a file inside of a zip archive
- segment. A number that indicates which worksheet to use in an Excel spreadsheet.
- header_lines. A comma seperated list of line numbers that should be used for the column headers
- urltype. If zip, indicates that the URL is for a zip file, for zip file that don't end in a 'zip' extension.
- filetype. A file extension to use for the file.
- encoding. A python encoding name. If missing, defaults to 'ascii', and is most often set to 'utf8'

Multicorn install
-----------------
.. code-block:: bash

    $ wget https://github.com/Kozea/Multicorn/archive/v1.2.3.zip
    $ unzip v1.2.3.zip
    $ cd Multicorn-1.2.3
    $ make && sudo make install

Virtualenv hint
---------------
Postgres FDW implementation does not work under virtual environment. You have to install ambry_sources to global environment and create \*.pth files for ambry_sources and multicorn in the site-packages of your virtual environment.
Create multicorn.pth file containing path to the multicorn package. Example (use your own path instead):
``/usr/local/lib/python2.7/dist-packages/multicorn-1.2.3_dev-py2.7-linux-i686.egg``
Add ambry_sources.pth file containing path to the ambry_sources package. Example (use your own path instead):
``/usr/local/lib/python2.7/dist-packages/ambry_sources``

Running tests
-------------
.. code-block:: bash

    $ git clone git@github.com:CivicKnowledge/ambry_sources.git
    $ cd ambry_sources
    $ pip install -r requirements.txt
    $ python setup.py test

Ignoring slow tests while developing (requires pytest installation).
.. code-block:: bash

    py.test tests/test_sources -k-slow

Installing Extras in Development
--------------------------------

The package defines two extras, geo, for geographic file formats, and fdw, for the Foreign Data Wrappers. To install
these extras in develop, run from the root of the distribution:

.. code-block:: bash

    pip install -e .[geo,fdw]

Making mpr files readable by postgres user.
-------------------------------------------

ambry_sources gives read permission to each member of the group of the user who executes ambry_sources. So,
to allow postgres read mpr files while executing queries you need to add postgres user to group of the user
who executes ambry_sources. Here is an example for debian (ubuntu).

.. code-block:: bash

    # add postgres user the executor group
    $ sudo usermod -a -G `id -g -n` postgres


Debugging postgres FDW
----------------------

1. Set postgres log level to debug by changing log_min_messages to DEBUG1:

.. code-block:: python

    log_min_messages = debug1

2. Set level of the ambry_sources.med.postgres to DEBUG level:

.. code-block:: python

    import logging
    import ambry_sources
    logger = logging.getLogger(ambry_sources.med.postgresql.__name__)
    logger.setLevel(logging.DEBUG)
    # Now use ambry_sources.med.postgres
    # ...

3. Restart postgres and run code. Check both - postgres and ambry_sources log files.
