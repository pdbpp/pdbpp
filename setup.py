from __future__ import print_function

import sys
import os.path
from setuptools import setup, find_packages

single_version = '--single-version-externally-managed'
if single_version in sys.argv:
    print('[pdb++] WARNING: ignoring unsupported option', single_version, file=sys.stderr)
    sys.argv.remove(single_version)

readme = os.path.join(os.path.dirname(__file__), 'README.rst')
changelog = os.path.join(os.path.dirname(__file__), 'CHANGELOG')
long_description = open(readme).read() + '\n\n' + open(changelog).read()

setup(
    name='pdbpp',
    version='0.8.1',
    author='Antonio Cuni',
    author_email='anto.cuni@gmail.com',
    py_modules=['pdb'],
    url='http://bitbucket.org/antocuni/pdb',
    license='BSD',
    platforms=['unix', 'linux', 'osx', 'cygwin', 'win32'],
    description='pdb++, a drop-in replacement for pdb',
    long_description=long_description,
    keywords='pdb debugger tab color completion',
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python",
        "Intended Audience :: Developers",
        "Operating System :: POSIX",
        "Topic :: Utilities",
        ],
    install_requires=["fancycompleter>=0.2",
                      "ordereddict",
                      "wmctrl",
                      "pygments"],
    extras_require={'funcsigs': ["funcsigs"]},
)
