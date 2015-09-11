#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from setuptools.command.test import test as TestCommand
import uuid

from pip.req import parse_requirements

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

import ambry_sources


if sys.argv[-1] == 'publish':
    os.system('python setup.py sdist upload')
    sys.exit()


with open(os.path.join(os.path.dirname(__file__), 'README.rst')) as f:
    readme = f.read()

packages = [
    'ambry_sources',
]

package_data = {
}

requires = parse_requirements('requirements.txt', session=uuid.uuid1())

classifiers = [
    'Development Status :: 4 - Beta',
    'Environment :: Web Environment',
    'Intended Audience :: Developers',
    'License :: OSI Approved :: MIT License',
    'Operating System :: OS Independent',
    'Programming Language :: Python',
    'Programming Language :: Python :: 2.6',
    'Programming Language :: Python :: 2.7',
    'Topic :: Software Development :: Debuggers',
    'Topic :: Software Development :: Libraries :: Python Modules',
]


class PyTest(TestCommand):
    user_options = [('pytest-args=', 'a', 'Arguments to pass to py.test')]

    def initialize_options(self):
        TestCommand.initialize_options(self)
        self.pytest_args = ''

    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        # import here, cause outside the eggs aren't loaded
        import pytest
        if 'capture' not in self.pytest_args:
            # capture arg is not given. Disable capture by default.
            self.pytest_args = self.pytest_args + ' --capture=no'

        errno = pytest.main(self.pytest_args)
        sys.exit(errno)


setup(
    name='ambry-sources',
    version=ambry_sources.__version__,
    description='Ambry Partition Message Pack File',
    long_description=readme,
    packages=packages,
    package_data=package_data,
    install_requires=[x for x in reversed([str(x.req) for x in requires])],
    tests_require=['pytest'],
    scripts=['scripts/ampr'],
    author=ambry_sources.__author__,
    author_email='eric@civicknowledge.com',
    url='https://github.com/streeter/python-skeleton',
    license='MIT',
    cmdclass={'test': PyTest},
    classifiers=classifiers,
)
