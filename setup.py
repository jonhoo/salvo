#!/usr/bin/env python3

# Always prefer setuptools over distutils
from setuptools import setup, find_packages
# To use a consistent encoding
from codecs import open
from os import path

# Import salvo __init__ for version
import salvo

here = path.abspath(path.dirname(__file__))

# Get the long description from the README file
with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='salvo',

    # Versions should comply with PEP440.  For a discussion on single-sourcing
    # the version across setup.py and the project code, see
    # https://packaging.python.org/en/latest/single_source_version.html
    version=salvo.__version__,

    description='Toolkit for provisioning '
                'large, '
                'single-shot, '
                'multi-worker '
                'computations.',
    long_description=long_description,

    url='https://github.com/jonhoo/salvo',

    author='Jon Gjengset',
    author_email='jon@thesquareplanet.com',

    license='MIT',

    # See https://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        'Development Status :: 1 - Planning',
        # 'Development Status :: 2 - Pre-Alpha',
        # 'Development Status :: 3 - Alpha',
        # 'Development Status :: 4 - Beta',
        # 'Development Status :: 5 - Production/Stable',
        # 'Development Status :: 6 - Mature',
        # 'Development Status :: 7 - Inactive',

        'Intended Audience :: Developers',
        'Topic :: System :: Clustering',
        'Topic :: System :: Distributed Computing',
        'Topic :: System :: Installation/Setup',
        'Topic :: System :: Software Distribution',

        'License :: OSI Approved :: MIT License',

        'Programming Language :: Python :: 3',
    ],

    keywords='deployment cloud-computing distributed-computing',

    packages=find_packages(exclude=['contrib', 'docs', 'tests']),

    install_requires=[],

    # $ pip install -e .[dev,test]
    extras_require={
        # 'dev': [],
        # 'test': [],
    },

    # http://peak.telecommunity.com/DevCenter/PythonEggs#accessing-package-resources
    # from pkg_resources import resource_string
    # foo_config = resource_string(__name__, 'foo.conf')
    package_data={
        # 'sample': ['package_data.dat'],
    },

    entry_points={
        'console_scripts': [
            'salvo = salvo.main:main',
        ],
    },
)
