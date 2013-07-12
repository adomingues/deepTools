from distutils.core import setup
from setuptools import setup

setup(
    name='deepTools',
    version='1.5',
    author='Fidel Ramirez',
    author_email='ramirez@ie-freiburg.mpg.de',
    packages=['deeptools', 'deeptools.test'],
    scripts=['bin/bamCompare', 'bin/bamCoverage', 'bin/bamCorrelate',
             'bin/heatmapper', 'bin/bamFingerprint', 'bin/estimateScaleFactor',
             'bin/PE_fragment_size', 'bin/computeMatrix', 'bin/profiler',
             'bin/computeMatrix', 'bin/computeGCBias', 'bin/correctGCBias'],
#    url='http://pypi.python.org/pypi/deepTools/',
    license='LICENSE.txt',
    description='Useful library to deal with mapped reads in sorted '
    'BAM format.',
    long_description=open('README.txt').read(),
    install_requires=[
        "numpy >= 1.6.2",
        "scipy >= 0.10.0",
        "matplotlib >= 1.2",
        "pysam >= 0.7.1",
        "bx-python >= 0.5.0",
    ],
)
