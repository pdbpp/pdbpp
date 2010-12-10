import os.path
from setuptools import setup, find_packages

readme = os.path.join(os.path.dirname(__file__), 'README')
long_description = open(readme).read()

setup(
    name='pdbpp',
    version='0.6',
    author='Antonio Cuni',
    author_email='anto.cuni@gmail.com',
    py_modules=['pdb'],
    url='http://bitbucket.org/antocuni/pdb',
    license='BSD',
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
                      "wmctrl",
                      "pygments"],
)
