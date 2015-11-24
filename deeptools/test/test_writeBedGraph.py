from unittest import TestCase
from nose.tools import *
import os

import deeptools.writeBedGraph as wr
from deeptools.writeBedGraph import scaleCoverage

__author__ = 'fidel'


class TestWriteBedGraph(TestCase):
    def setUp(self):
        """
        The distribution of reads between the two bam files is as follows.

        They cover 200 bp::

              0                              100                           200
              |------------------------------------------------------------|
            A                                ==============>
                                                            <==============


            B                 <==============               ==============>
                                             ==============>
                                                            ==============>
        """

        self.root = "./test/test_data/"
        self.bamFile1  = self.root + "testA.bam"
        self.bamFile2  = self.root + "testB.bam"
        self.bamFile_PE  = self.root + "test_paired2.bam"
        self.chrom = '3R'

        self.step_size = 50
        self.bin_length = 50
        default_frag_length = None

        self.func_args =  {'scaleFactor': 1.0}

        self.c = wr.WriteBedGraph([self.bamFile1],
                                  binLength=self.bin_length,
                                  defaultFragmentLength=default_frag_length,
                                  stepSize=self.step_size)


    def test_writeBedGraph_worker(self):
        self.c.zerosToNans = False
        self.c.skipZeros = False

        tempFile = self.c.writeBedGraph_worker( '3R', 0, 200, scaleCoverage, self.func_args)
        res = open(tempFile, 'r').readlines()
        assert_equal(res,['3R\t0\t100\t0.00\n', '3R\t100\t200\t1.0\n'])
        os.remove(tempFile)

    def test_writeBedGraph_worker_zerotonan(self):
        # turn on zeroToNan
        self.c.zerosToNans = True
        tempFile2 = self.c.writeBedGraph_worker( '3R', 0, 200, scaleCoverage, self.func_args)
        res = open(tempFile2, 'r').readlines()
        assert_equal(res,['3R\t100\t200\t1.0\n'])
        os.remove(tempFile2)

    def test_writeBedGraph_worker_scaling(self):
        func_args = {'scaleFactor': 3.0}
        tempFile = self.c.writeBedGraph_worker( '3R', 0, 200, scaleCoverage, func_args)
        res = open(tempFile, 'r').readlines()
        assert_equal(res,['3R\t0\t100\t0.00\n', '3R\t100\t200\t3.0\n'])
        os.remove(tempFile)

    def test_writeBedGraph_worker_ignore_duplicates(self):
        self.c = wr.WriteBedGraph([self.bamFile2],
                                   binLength=self.bin_length,
                                   defaultFragmentLength=None,
                                   stepSize=self.step_size, ignoreDuplicates=True)
        self.c.zerosToNans = True

        tempFile = self.c.writeBedGraph_worker( '3R', 0, 200, scaleCoverage, self.func_args)
        res = open(tempFile, 'r').readlines()
        assert_equal(res, ['3R\t50\t200\t1.0\n'])
        os.remove(tempFile)

    def test_writeBedGraph_worker_smoothing(self):
        funcArgs = {'scaleFactor': 1.0}
        self.c.binLength = 20
        tempFile = self.c.writeBedGraph_worker( '3R', 100, 200, scaleCoverage, self.func_args, smooth_length=60)
        res = open(tempFile, 'r').readlines()
        assert_equal(res, ['3R\t50\t200\t1.0\n'])
        os.remove(tempFile)

    def test_writeBedGraph_worker_smoothing(self):
        """Test ratio (needs two bam files)"""
        from deeptools.writeBedGraph_dev import ratio
        funcArgs = {}
        self.c = wr.WriteBedGraph([self.bamFile1, self.bamFile2],
                                   binLength=self.bin_length,
                                   defaultFragmentLength=None,
                                   stepSize=self.step_size)
        tempFile = self.c.writeBedGraph_worker( '3R', 100, 200, ratio, funcArgs)
        assert_equal(open(tempFile, 'r').readlines(),
                     ['3R\t100\t150\t1.00\n', '3R\t150\t200\t0.5\n'])
        os.remove(tempFile)

    def test_writeBedGraph_cigar(self):
        """
        The bamFile1 contains a read at position 10
        with the following CIGAR: 10S20M10N10M10S
        that maps to a chromosome named chr_cigar.
        """

        # turn of read extension
        self.c.extendPairedEnds = False
        self.c.defaultFragmentLength = None
        self.c.binLength = 10
        self.c.stepSize = 10
        tempFile = self.c.writeBedGraph_worker( 'chr_cigar', 0, 100, scaleCoverage, self.func_args)
        res = open(tempFile, 'r').readlines()

        # the sigle read is split into bin 10-30, and then 40-50
        assert_equal(res,['chr_cigar\t0\t10\t0.00\n',
                          'chr_cigar\t10\t30\t1.00\n',
                          'chr_cigar\t30\t40\t0.00\n',
                          'chr_cigar\t40\t50\t1.00\n'])
        os.remove(tempFile)