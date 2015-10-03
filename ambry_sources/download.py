# -*- coding: utf-8 -*-
"""

Copyright (c) 2015 Civic Knowledge. This file is licensed under the terms of the
Revised BSD License, included in this distribution as LICENSE.txt
"""

import functools
from os.path import join
import re
import ssl

from requests import HTTPError

from six.moves.urllib.parse import urlparse
from six.moves.urllib.request import urlopen

from fs.zipfs import ZipFS
from fs.s3fs import S3FS

from ambry_sources.exceptions import ConfigurationError, DownloadError, MissingCredentials
from ambry_sources.mpf import MPRowsFile
from ambry_sources.util import copy_file_or_flo, parse_url_to_dict

from .sources import GoogleSource, CsvSource, TsvSource, FixedSource, ExcelSource, PartitionSource,\
    SourceError, DelayedOpen, ShapefileSource


def get_source(spec, cache_fs,  account_accessor=None, clean=False):
    """
    Download a file from a URL and return it wrapped in a row-generating acessor object.

    :param spec: A SourceSpec that describes the source to fetch.
    :param cache_fs: A pyfilesystem filesystem to use for caching downloaded files.
    :param account_accessor: A callable to return the username and password to use for access FTP and S3 URLs.
    :param clean: Delete files in cache and re-download.

    :return: a SourceFile object.
    """

    try:
        cache_path, download_time = download(spec.url, cache_fs, account_accessor, clean=clean)
        spec.download_time = download_time
    except HTTPError as e:
        raise DownloadError("Failed to download {}; {}".format(spec.url, e))
    url_type = spec.get_urltype()

    if url_type == 'zip':
        fstor = extract_file_from_zip(cache_fs, cache_path, spec.url)

    elif url_type == 'gs':
        raise NotImplementedError()
        fstor = get_gs(spec.url, spec.segment, account_accessor)
    else:
        fstor = DelayedOpen(cache_fs, cache_path, 'rb')

    file_type = spec.get_filetype(fstor.path)

    spec.filetype = file_type

    TYPE_TO_SOURCE_MAP = {
        'gs': GoogleSource,
        'csv': CsvSource,
        'tsv': TsvSource,
        'fixed': FixedSource,
        'txt': FixedSource,
        'xls': ExcelSource,
        'xlsx': ExcelSource,
        'partition': PartitionSource,
        'shape': ShapefileSource}

    cls = TYPE_TO_SOURCE_MAP.get(file_type)
    if cls is None:
        raise SourceError(
            "Failed to determine file type for source '{}'; unknown type '{}' "
            .format(spec.name, file_type))

    return cls(spec, fstor, use_row_spec=False)


def import_source(spec, cache_fs,  file_path=None, account_accessor=None):
    """Download a source and load it into an MPR file. """

    s = get_source(spec, cache_fs,  account_accessor)

    if not file_path:
        file_path = spec.name

    f = MPRowsFile(cache_fs, file_path)
    w = f.writer

    w.set_spec(spec)

    for row in s:
        w.insert_row(row)

    w.close()

    return f


def extract_file_from_zip(cache_fs, cache_path, url):
    """
    For a zip archive, return the first file if no file_name is specified as a fragment in the url,
     or if a file_name is specified, use it as a regex to find a file in the archive

    :param cache_fs:
    :param cache_path:
    :param url:
    :return:
    """

    fs = ZipFS(cache_fs.open(cache_path, 'rb'))

    def walk_all(fs):
        return [join(e[0], x) for e in fs.walk() for x in e[1]]

    if '#' not in url:
        first = walk_all(fs)[0]
        fstor = DelayedOpen(fs, first, 'rU', container=(cache_fs, cache_path))

    else:
        _, fn_pattern = url.split('#')

        for file_name in walk_all(fs):

            if '_MACOSX' in file_name:
                continue

            if re.search(fn_pattern, file_name):
                fstor = DelayedOpen(fs, file_name, 'rb', container=(cache_fs, cache_path))
                break

        if not fstor:
            raise ConfigurationError('Failed to get file {} from archive {}'.format(file_name, fs))

    return fstor


def download(url, cache_fs, account_accessor=None, clean=False):
    """
    Download a URL and store it in the cache.

    :param url:
    :param cache_fs:
    :param account_accessor: callable of one argument (url) returning dict with credentials.
    :param clean: Remove files from cache and re-download
    :return:
    """
    import os.path
    import requests
    from fs.errors import NoSysPathError
    import filelock
    import time

    parsed = urlparse(str(url))

    # Create a name for the file in the cache, based on the URL
    cache_path = os.path.join(parsed.netloc, parsed.path.strip('/'))

    # If there is a query, hash it and add it to the path
    if parsed.query:
        import hashlib
        hash = hashlib.sha224(parsed.query).hexdigest()
        cache_path = os.path.join(cache_path, hash)

    download_time = False

    if clean and cache_fs.exists(cache_path):
        cache_fs.remove(cache_path)

    if not cache_fs.exists(cache_path):

        cache_fs.makedir(os.path.dirname(cache_path), recursive=True, allow_recreate=True)

        try:
            lock_file = cache_fs.getsyspath(cache_path + '.lock')
            FileLock = filelock.FileLock

        except NoSysPathError:
            # mem: caches, and others, don't have sys paths.
            class FileLock(object):

                def __init__(self, lf):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_val, exc_tb):
                    if exc_val:
                        raise exc_val

            lock_file = None

        # Use a file lock, in case two processes try to download the file at the same time.
        with FileLock(lock_file):

            try:

                if url.startswith('s3:'):
                    s3 = get_s3(url, account_accessor)
                    pd = parse_url_to_dict(url)

                    with cache_fs.open(cache_path, 'wb') as fout:
                        with s3.open(pd['path'], 'rb') as fin:
                            copy_file_or_flo(fin, fout)

                elif url.startswith('ftp:'):
                    import shutil
                    from contextlib import closing

                    with closing(urlopen(url)) as fin:
                        with cache_fs.open(cache_path, 'wb') as fout:
                            shutil.copyfileobj(fin, fout)
                else:

                    r = requests.get(url, stream=True)

                    r.raise_for_status()

                    # Requests will auto decode gzip responses, but not when streaming. This following
                    # monkey patch is recommended by a core developer at
                    # https://github.com/kennethreitz/requests/issues/2155
                    if r.headers.get('content-encoding') == 'gzip':
                        r.raw.read = functools.partial(r.raw.read, decode_content=True)

                    with cache_fs.open(cache_path, 'wb') as f:
                        copy_file_or_flo(r.raw, f)

                download_time = time.time()

            except KeyboardInterrupt:
                # This is really important -- its really bad to have partly downloaded
                # files being confused with fully downloaded ones.
                # FIXME. SHould also handle signals. deleteing partly downloaded files is important.
                # Maybe should have a sentinel file, or download to another name and move the
                # file after done.
                if cache_fs.exists(cache_path):
                    cache_fs.remove(cache_path)
                raise

    return cache_path, download_time


def get_s3(url, account_accessor):
    """ Gets file from s3 storage.

    Args:
        url (str): url of the file
        account_accessor (callable): callable returning dictionary with s3 credentials (access and secret
            at least)

    Example:
        get_s3('s3://example.com/file1.csv', lambda url: {'access': '<access>': 'secret': '<secret>'})

    Returns:
        S3FS instance (file-like):
    """

    # TODO: Hack the pyfilesystem fs.opener file to get credentials from a keychain
    # The monkey patch fixes a bug: https://github.com/boto/boto/issues/2836

    _old_match_hostname = ssl.match_hostname

    # FIXME. This issue is possibly better handled with https://pypi.python.org/pypi/backports.ssl_match_hostname
    def _new_match_hostname(cert, hostname):
        if hostname.endswith('.s3.amazonaws.com'):
            pos = hostname.find('.s3.amazonaws.com')
            hostname = hostname[:pos].replace('.', '') + hostname[pos:]
        return _old_match_hostname(cert, hostname)

    ssl.match_hostname = _new_match_hostname

    pd = parse_url_to_dict(url)

    if account_accessor is None or not callable(account_accessor):
        raise TypeError('account_accessor argument must be callable of one argument returning dict.')

    account = account_accessor(pd['netloc'])
    aws_access_key = account.get('access')
    aws_secret_key = account.get('secret')

    missing_credentials = []
    if not aws_access_key:
        missing_credentials.append('access')
    if not aws_secret_key:
        missing_credentials.append('secret')

    if missing_credentials:
        raise MissingCredentials(
            'dict returned by account_accessor callable for {} must contain not empty {} key(s)'
            .format(pd['netloc'], ', '.join(missing_credentials)),
            location=pd['netloc'],
            required_credentials=['access', 'secret'])

    s3 = S3FS(
        bucket=pd['netloc'],
        # prefix=pd['path'],
        aws_access_key=aws_access_key,
        aws_secret_key=aws_secret_key
    )

    # ssl.match_hostname = _old_match_hostname

    return s3


def get_gs(url, segment, account_acessor):

    import gspread
    from oauth2client.client import SignedJwtAssertionCredentials

    json_key = account_acessor('google_spreadsheets')

    scope = ['https://spreadsheets.google.com/feeds']

    credentials = SignedJwtAssertionCredentials(json_key['client_email'], json_key['private_key'], scope)

    spreadsheet_key = url.replace('gs://', '')

    gc = gspread.authorize(credentials)

    sh = gc.open_by_key(spreadsheet_key)

    return sh.worksheet(segment)
