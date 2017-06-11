from __future__ import print_function

import sys
import os.path
from setuptools import setup, find_packages
from setuptools.command.install import install
from distutils.core import Command
import io
HACK = 'pdbmm_hijack_pdb.pth'

readme_path = os.path.join(os.path.dirname(__file__), 'README.rst')
changelog_path = os.path.join(os.path.dirname(__file__), 'CHANGELOG')

readme = io.open(readme_path, encoding='utf-8').read()
changelog = io.open(changelog_path, encoding='utf-8').read()

long_description = readme + '\n\n' + changelog


class install_with_pth(install):
    sub_commands = install.sub_commands + [
        ('install_pth_hack', lambda self:
            self.single_version_externally_managed)
    ]


class install_pth_hack(Command):
    user_options = [
        ('install-dir=', 'd', "directory to install to"),
    ]

    @property
    def target(self):
        return os.path.join(self.install_dir, HACK)

    def get_outputs(self):
        return [self.target]

    def initialize_options(self):
        self.install_dir = None

    def finalize_options(self):
        self.set_undefined_options(
            'install', ('install_lib', 'install_dir'))

    def run(self):
        with open(HACK) as infp:
            with open(self.target, 'w') as outfp:
                outfp.write(infp.read())


install_requires = [
    "fancycompleter>=0.2",
    "wmctrl",
    "pygments",
]

if sys.version_info < (2, 7):
    install_requires.append("ordereddict")


setup(
    name='pdbpp',
    use_scm_version=True,
    author='Antonio Cuni',
    author_email='anto.cuni@gmail.com',
    py_modules=['pdb', '_pdbpp_path_hack.pdb'],
    url='http://github.com/antocuni/pdb',
    license='BSD',
    platforms=['unix', 'linux', 'osx', 'cygwin', 'win32'],
    description='pdb++, a drop-in replacement for pdb',
    long_description=long_description,
    keywords='pdb debugger tab color completion',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.2',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
        'Programming Language :: Python',
        'Topic :: Utilities',
    ],
    install_requires=install_requires,
    extras_require={'funcsigs': ["funcsigs"]},
    setup_requires=['setuptools_scm'],
    cmdclass={
        'install': install_with_pth,
        'install_pth_hack': install_pth_hack,
    }
)
