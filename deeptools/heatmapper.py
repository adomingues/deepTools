import sys
from os.path import splitext, basename
import gzip
from collections import OrderedDict
import numpy as np
import multiprocessing

# NGS packages
import pysam
from bx.intervals.io import GenomicIntervalReader


def compute_sub_matrix_wrapper(args):
    return heatmapper.compute_sub_matrix_worker(*args)


class heatmapper(object):
    """
    Class to handle the reading and
    plotting of matrices.
    """

    def __init__(self):
        self.parameters = None
        self.lengthDict = None
        self.matrix = None
        self.regions = None

    def computeMatrix(self, score_file_list, regions_file, parameters,
                      verbose=False):
        """
        Splits into
        multiple cores the computation of the scores
        per bin for each region (defined by a hash '#'
        in the regions (BED/GFF) file.
        """
        if parameters['body'] > 0 and \
                parameters['body'] % parameters['bin size'] > 0:
            sys.stderr.write("The --regionBodyLength has to be "
                             "a multiple of --binSize.\nCurrently the "
                             "values are {} {} for\nregionsBodyLength and "
                             "binSize respectively\n".format(
                    parameters['body'],
                    parameters['bin size']))
            exit(1)

        # the beforeRegionStartLength is extended such that
        # length is a multiple of binSize
        if parameters['downstream'] % parameters['bin size'] > 0:
            sys.stderr.write(
                "Length of region after the body has to be "
                "a multiple of --binSize.\nCurrent value "
                "is {}\n".format(parameters['downstream']))
            exit(1)

        if parameters['upstream'] % parameters['bin size'] > 0:
            sys.stderr.write(
                "Length of region before the body has to be a multiple of "
                "--binSize\nCurrent value is {}\n".format(
                    parameters['upstream']))
            exit(1)

        regions, group_labels, group_boundaries = \
            self.getRegionsAndGroups(regions_file, verbose=verbose)
        group_len = np.diff(group_boundaries)

        # check if a given group is too small. Groups that
        # are too small can't be plotted and an exception is thrown.
        if len(group_len) > 1:
            sum_len = sum(group_len)
            group_frac = [float(x)/sum_len for x in group_len]
            if min(group_frac) <= 0.002:
                sys.stderr.write(
                    "One of the groups defined in the bed file is "
                    "too small.\nGroups that are too small can't be plotted. "
                    "Please remove the group to continue.\n")
                exit(1)

        # args to pass to the multiprocessing workers
        mp_args = []
        # prepare groups of regions to send to workers.
        regions_per_worker = 400 / len(score_file_list)
        for index in range(0, len(regions), regions_per_worker):
            index_end = min(len(regions), index + regions_per_worker)
            mp_args.append((score_file_list, regions[index:index_end],
                            parameters))

        if len(mp_args) > 1 and parameters['proc number'] > 1:
            pool = multiprocessing.Pool(parameters['proc number'])
            res = pool.map_async(compute_sub_matrix_wrapper,
                                 mp_args).get(9999999)
        else:
            res = map(compute_sub_matrix_wrapper, mp_args)

        # each worker in the pools returns a tuple containing
        # the submatrix data and the regions that correspond to the
        # submatrix

        # merge all the submatrices into matrix
        matrix = np.concatenate([r[0] for r in res], axis=0)
        # mask invalid (nan) values
        matrix = np.ma.masked_invalid(matrix)

        assert matrix.shape[0] == len(regions), \
            "matrix length does not match regions length"

        regions_no_score = sum([r[2] for r in res])
        if len(regions) == 0:
            sys.stderr.write(
                "\nERROR: BED file does not contain any valid regions. "
                "Please check\n")
            exit(1)
        if regions_no_score == len(regions):
            sys.stderr.write(
                "\nERROR: None of the BED regions could be found in the bigWig"
                 "file.\nPlease check that the bigwig file is valid and "
                 "that the chromosome names between the BED file and "
                 "the bigWig file correspond to each other\n")
            exit(1)
        if regions_no_score > len(regions) * 0.75:
            file_type = 'bigwig' if score_file_list[0].endswith(".bw") \
                else "BAM"
            prcnt = 100 * float(regions_no_score) / len(regions)
            sys.stderr.write(
                "\n\nWarning: {:.2f}% of regions are *not* associated\n"
                "to any score in the given {} file. Check that the\n"
                "chromosome names from the BED file are consistent with\n"
                "the chromosome names in the given {} file and that both\n"
                "files refer to the same species\n\n".format(prcnt,
                                                             file_type,
                                                             file_type))


        self.parameters = parameters

        numcols = matrix.shape[1]
        num_ind_cols = self.getNumIndividualMatrixCols()
        sample_boundaries = range(0, numcols + num_ind_cols, num_ind_cols)
        sample_labels = [splitext(basename(x))[0] for x in score_file_list]

        self.matrix = _matrix(regions, matrix,
                              group_boundaries,
                              sample_boundaries,
                              group_labels,
                              sample_labels)

    @staticmethod
    def compute_sub_matrix_worker(score_file_list, regions,
                                  parameters):
        # read BAM or scores file
        if score_file_list[0].endswith(".bam"):
            bamfile_list = []
            for score_file in score_file_list:
                bamfile_list.append(pysam.Samfile(score_file, 'rb'))
        else:
            bigwig_list = []
            from bx.bbi.bigwig_file import BigWigFile
            for score_file in score_file_list:
                bigwig_list.append(BigWigFile(file=open(score_file, 'r' )))

        # determine the number of matrix columns based on the lengths
        # given by the user, times the number of score files
        matrixCols = len(score_file_list) * \
            ((parameters['downstream'] +
              parameters['upstream'] + parameters['body']) /
             parameters['bin size'])

        # create an empty matrix to store the values
        subMatrix = np.zeros((len(regions), matrixCols))
        subMatrix[:] = np.NAN

        j = 0
        subRegions = []
        regions_no_score = 0
        for feature in regions:
           # print some information
            if parameters['body'] > 0 and \
                    feature['end'] - feature['start'] < parameters['bin size']:
                if parameters['verbose']:
                    sys.stderr.write("A region that is shorter than "
                                     "then bin size was found: "
                                     "({}) {} {}:{}:{}. Skipping...\n".format(
                            (feature['end'] - feature['start']),
                            feature['name'], feature['chrom'],
                            feature['start'], feature['end']))
                continue

            if feature['strand'] == '-':
                a = parameters['upstream'] / parameters['bin size']
                b = parameters['downstream']  / parameters['bin size']
                start = feature['end']
                end = feature['start']
            else:
                b = parameters['upstream'] / parameters['bin size']
                a = parameters['downstream'] / parameters['bin size']
                start = feature['start']
                end = feature['end']

            # build zones:
            #  zone0: region before the region start,
            #  zone1: the body of the region (not always present)
            #  zone2: the region from the end of the region downstream
            #  the format for each zone is: start, end, number of bins
            if parameters['body'] > 0:
                zones = \
                    [(feature['start'] - b * parameters['bin size'],
                      feature['start'], b ),
                     (feature['start'],
                      feature['end'],
                      #feature['end'] - parameters['body'] /
                      #parameters['bin size'],
                      parameters['body'] / parameters['bin size']),
                     (feature['end'],
                      feature['end'] + a * parameters['bin size'], a)]
            elif parameters['ref point'] == 'TES':  # around TES
                zones = [(end - b * parameters['bin size'], end, b ),
                         (end, end + a * parameters['bin size'], a )]
            elif parameters['ref point'] == 'center':  # at the region center
                middlePoint = feature['start'] + (feature['end'] -
                                                  feature['start']) / 2
                zones = [(middlePoint - b * parameters['bin size'],
                          middlePoint, b),
                         (middlePoint,
                          middlePoint + a * parameters['bin size'], a)]
            else:  # around TSS
                zones = [(start - b * parameters['bin size'], start, b ),
                         (start, start + a * parameters['bin size'], a )]

            if feature['start'] - b * parameters['bin size'] < 0:
                if parameters['verbose']:
                    sys.stderr.write(
                        "Warning:region too close to chromosome start "
                        "for {} {}:{}:{}.\n".format(feature['name'],
                                                   feature['chrom'],
                                                   feature['start'],
                                                   feature['end']))
            coverage = []
            if score_file.endswith(".bam"):
                for bamfile in bamfile_list:
                    cov = heatmapper.coverageFromBam(
                        bamfile, feature['chrom'], zones,
                        parameters['bin size'],
                        parameters['bin avg type'])
                    if feature['strand'] == "-":
                        cov = cov[::-1]
                    coverage = np.hstack([coverage, cov])

            else:
                for bigwig in bigwig_list:
                    cov = heatmapper.coverageFromBigWig(
                        bigwig, feature['chrom'], zones,
                        parameters['bin size'],
                        parameters['bin avg type'],
                        parameters['missing data as zero'])
                    if feature['strand'] == "-":
                        cov = cov[::-1]
                    coverage = np.hstack([coverage, cov])

            """ 
            if coverage is None:
                regions_no_score += 1
                if parameters['verbose']:
                    sys.stderr.write(
                        "No data was found for region "
                        "{} {}:{}-{}. Skipping...\n".format(
                            feature['name'], feature['chrom'],
                            feature['start'], feature['end']))

                coverage = np.zeros(matrixCols)
                if not parameters['missing data as zero']:
                    coverage[:] = np.nan
                continue
            """
            try:
                temp = coverage.copy()
                temp[np.isnan(temp)] = 0
                totalScore = np.sum(temp)
            except:
                if parameters['verbose']:
                    sys.stderr.write(
                        "No scores defined for region "
                        "{} {}:{}-{}. Skipping...\n".format(feature['name'],
                                                            feature['chrom'],
                                                            feature['start'],
                                                            feature['end']))
                coverage = np.zeros(matrixCols)
                if not parameters['missing data as zero']:
                    coverage[:] = np.nan
                # to induce skipping if zero regions are omited this
                # variable is set to zero
                totalScore = 0

            if totalScore == 0:
                regions_no_score += 1
                if parameters['skip zeros']:
                    if parameters['verbose']:
                        sys.stderr.write(
                            "Skipping region with all scores equal to zero "
                            "for\n'{}' {}:{}-{}.\n\n".format(feature['name'],
                                                             feature['chrom'],
                                                             feature['start'],
                                                             feature['end']))
                    continue
                elif parameters['verbose']:
                    sys.stderr.write(
                        "Warning: All values are zero for "
                        "{} {}:{}-{}.\n".format(feature['name'],
                                                feature['chrom'],
                                                feature['start'],
                                                feature['end']))
                    sys.stderr.write(
                        "add --skipZeros to exclude such regions\n")

            if parameters['min threshold'] and \
                    coverage.min() <= parameters['min threshold']:
                continue
            if parameters['max threshold'] and \
                    coverage.max() >= parameters['max threshold']:
                continue
            if parameters['scale'] != 1:
                coverage = parameters['scale'] * coverage

            subMatrix[j, :] = coverage

            if parameters['nan after end'] and parameters['body'] == 0 \
                    and parameters['ref point'] == 'TSS':
                # convert the gene length to bin length
                region_length_in_bins = \
                    (feature['end'] - feature['start']) / \
                    parameters['bin size']
                b = parameters['upstream'] / parameters['bin size']
                # convert to nan any region after the end of the region
                subMatrix[j, b + region_length_in_bins:] = np.nan

            subRegions.append(feature)
            j += 1

        # remove empty rows
        subMatrix = subMatrix[0:j, :]
        if len(subRegions) != len(subMatrix[:, 0]):
            sys.stderr.write("regions lengths do not match\n")
        return (subMatrix, subRegions, regions_no_score)

    @staticmethod
    def coverageFromArray(valuesArray, zones, binSize, avgType):
        try:
            valuesArray[0]
        except IndexError, TypeError:
            sys.stderr.write("values array {}, zones {}\n".format(valuesArray,
                                                                  zones))

        cvgList = []
        start = zones[0][0]
        for zone_start, zone_end, num_bins in zones:
            # the linspace is to get equally spaced positions along the range
            # If the gene is short the sampling regions could overlap,
            # if it is long, the sampling regions would be spaced
            countsList = []

            # this case happens when the downstream or upstream
            # region is set to 0
            if zone_start == zone_end:
                continue

            (posArray, stepSize) = np.linspace(zone_start, zone_end, num_bins,
                                               endpoint=False,
                                               retstep=True)
            stepSize = np.ceil(stepSize)

            for pos in np.floor(posArray):
                indexStart = int(pos - start)
                #indexEnd   = int(indexStart + binSize)
                indexEnd   = int(indexStart + stepSize + 1)
                try:
                    countsList.append(
                        heatmapper.myAverage(valuesArray[indexStart:indexEnd],
                                             avgType))
                except Exception as detail:
                    sys.stderr.write("Exception found. "
                                     "Message: {}\n".format(detail))
            cvgList.append(np.array(countsList))
        return np.concatenate(cvgList)

    @staticmethod
    def changeChromNames(chrom):
        """
        Changes UCSC chromosome names to ensembl chromosome names
        and vice versa.
        TODO: mapping from chromosome names ... e.g. mt, unknown_ ...
        """
        if chrom.startswith('chr'):
            return chrom[3:]
        else:
            return 'chr%s' % chrom

    @staticmethod
    def coverageFromBam(bamfile, chrom, zones, binSize, avgType):
        """
        currently this method is deactivated because is too slow.
        It is preferred to create a coverage bigiwig file from the
        bam file and then run heatmapper.
        """
        if chrom not in bamfile.references:
            chrom = heatmapper.changeChromNames(chrom)
            if chrom not in bamfile.references:
                sys.stderr.write(
                    "Skipping region located at unknown chromosome: {} "
                    "Known chromosomes are: {}\n".format(chrom,
                                                         bamfile.references))
                return None
            else:
                sys.stderr.write("Warning: Your chromosome names do "
                                 "not match.\n Please check that the "
                                 "chromosome names in your BED "
                                 "file correspond to the names in your "
                                 "bigWig file.\n An empty line will be "
                                 "added to your heatmap."
                                 "scheme.\n")

        start = zones[0][0]
        end = zones[-1][1]
        try:
            valuesArray = np.zeros(end - start)
            for read in bamfile.fetch(chrom, min(0, start), end):
                indexStart = max(read.pos - start, 0)
                indexEnd = min(read.pos - start + read.qlen, end - start)
                valuesArray[indexStart:indexEnd] += 1
        except ValueError:
            sys.stderr.write(
                "Value out of range for region {}s {} {}"
                "\n".format(chrom, start, end))
            return np.array([0])  # return something inocuous

        return heatmapper.coverageFromArray(valuesArray, zones,
                                            binSize, avgType)

    @staticmethod
    def coverageFromBigWig(bigwig, chrom, zones, binSize, avgType,
                           nansAsZeros=False):

        """
        uses bigwig file reader from bx-python
        to query a region define by chrom and zones.
        The output is an array that contains the bigwig
        value per base pair. The summary over bins is
        done in a later step when coverageFromArray is called.
        This method is more reliable than quering the bins
        directly from the bigwig, which should be more efficient.

        By default, any region, even if no chromosome match is found
        on the bigwig file, produces a result. In other words
        no regions are skipped.

        This is useful if several matrices wants to be merged
        or if the sorted BED output of one computeMatrix operation
        needs to be used for other cases
        """

        # intialize values array. The length of the array
        # is the length of the region which is defined
        # by the start of the first zone zones[0][0]
        # to the end of the last zone zones[-1][1]
        valuesArray = np.zeros(zones[-1][1] - zones[0][0])
        if not nansAsZeros:
            valuesArray[:] = np.nan
        try:
            bw_array = bigwig.get_as_array(chrom,
                                           max(0, zones[0][0]),
                                           zones[-1][1])
        except Exception as detail:
                sys.stderr.write("Exception found. Message: "
                                 "{}\n".format(detail))

        if bw_array is None:
            # When bigwig.get_as_array queries a
            # chromosome that is not known
            # it returns None. Ideally, the bigwig should
            # be able to inform the known chromosome names
            # as is the case for bam files, but the
            # bx-python function does not allow access to
            # this info.
            altered_chrom = heatmapper.changeChromNames(chrom)
            bw_array = bigwig.get_as_array(altered_chrom,
                                           max(0, zones[0][0]),
                                           zones[-1][1])
            # test again if with the altered chromosome name
            # the bigwig returns something.
            if bw_array is None:
                sys.stderr.write("Warning: Your chromosome names do "
                                 "not match.\nPlease check that the "
                                 "chromosome names in your BED "
                                 "file\ncorrespond to the names in your "
                                 "bigWig file.\nAn empty line will be "
                                 "added you your heatmap.\nThe offending "
                                 "chromosome name is "
                                 "{}\n\n".format(chrom))

        if bw_array is not None:
            if zones[0][0] < 0:
                valuesArray = np.zeros(zones[-1][1] - zones[0][0])
                valuesArray[:] = np.nan
                valuesArray[abs(zones[0][0]):] = bw_array
            else:
                valuesArray = bw_array

        # replaces nans for zeros
        if nansAsZeros:
            valuesArray[np.isnan(valuesArray)] = 0
        return heatmapper.coverageFromArray(valuesArray, zones,
                                            binSize, avgType)

    @staticmethod
    def myAverage(valuesArray, avgType='mean'):
        """
        computes the mean, median, etc but only for those values
        that are not Nan
        """
        valuesArray = np.ma.masked_invalid(valuesArray)
        avg = np.__getattribute__(avgType)(valuesArray)
        if isinstance(avg, np.ma.core.MaskedConstant):
            return np.nan
        else:
            return avg

    def matrixFromDict(self, matrixDict, regionsDict, parameters):
        self.regionsDict = regionsDict
        self.matrixDict = matrixDict
        self.parameters = parameters
        self.lengthDict = OrderedDict()
        self.matrixAvgsDict = OrderedDict()

    def readMatrixFile(self, matrix_file, verbose=None,
                       default_group_name='label_1'):
        # reads a bed file containing the position
        # of genomic intervals
        # In case a hash sign '#' is found in the
        # file, this is considered as a delimiter
        # to split the heatmap into groups

        import json
        regions = []
        matrix_rows = []

        fh = gzip.open(matrix_file)
        for line in fh:
            line = line.strip()
            # read the header file containing the parameters
            # used
            if line.startswith("@"):
                # the parameters used are saved using
                # json
                self.parameters = json.loads(line[1:].strip())
                continue

            # split the line into bed interval and matrix values
            region = line.split('\t')
            chrom, start, end, name, mean, strand = region[0:6]
            matrix_rows.append(np.fromiter(region[6:], np.float))
            regions.append({'chrom': chrom, 'start': int(start),
                            'end': int(end), 'name': name, 'mean': float(mean),
                            'strand': strand})

        matrix = np.ma.masked_invalid(np.vstack(matrix_rows))
        self.matrix = _matrix(regions, matrix, self.parameters['group_boundaries'],
                         self.parameters['sample_boundaries'],
                         group_labels=self.parameters['group_labels'],
                         sample_labels=self.parameters['sample_labels'])


        return

    def saveMatrix(self, file_name):
        """
        saves the data required to reconstruct the matrix
        the format is:
        A header containing the parameters used to create the matrix
        encoded as:
        @key:value\tkey2:value2 etc...
        The rest of the file has the same first 5 columns of a
        BED file: chromosome name, start, end, name, score and strand,
        all separated by tabs. After the fifth column the matrix
        values are appended separated by tabs.
        Groups are separated by adding a line starting with a hash (#)
        and followed by the group name.

        The file is gzipped.
        """
        import json
        self.parameters['sample_labels'] = self.matrix.sample_labels
        self.parameters['group_labels'] = self.matrix.group_labels
        self.parameters['sample_boundaries'] = self.matrix.sample_boundaries
        self.parameters['group_boundaries'] = self.matrix.group_boundaries

        fh = gzip.open(file_name, 'wb')
        params_str = json.dumps(self.parameters, separators=(',', ':'))
        fh.write("@" + params_str + "\n")
        score_list = np.ma.masked_invalid(np.mean(self.matrix.matrix, axis=1))
        for idx, region in enumerate(self.matrix.regions):
            # join np_array values
            # keeping nans while converting them to strings
            score = self.matrix.matrix[idx, :]
            if np.ma.is_masked(score_list[idx]):
                score = 'nan'
            else:
                score = np.float(score_list[idx])
            matrix_values = "\t".join(
                np.char.mod('%f', self.matrix.matrix[idx, :]))
            fh.write(
                '{}\t{}\t{}\t{}\t{}\t{}\t{}\n'.format(
                    region['chrom'],
                    region['start'],
                    region['end'],
                    region['name'],
                    score,
                    region['strand'],
                    matrix_values))
        fh.close()

    def saveTabulatedValues(self, file_handle):
        bin = range(self.parameters['upstream'] * -1,
                    self.parameters['body'] + self.parameters['downstream'],
                    self.parameters['bin size'])

        avgDict = OrderedDict()
        stdDict = OrderedDict()

        for label, heatmapMatrix in self.matrixDict.iteritems():
            avgDict[label] = heatmapper.matrixAvg(heatmapMatrix, 'mean')
            stdDict[label] = heatmapper.matrixAvg(heatmapMatrix, 'std')

        file_handle.write(
            '#bin No.\t{}\n'.format(" mean\t std\t".join(avgDict.keys())))

        for j in range(0, len(avgDict[avgDict.keys()[0]])):
            file_handle.write('{}\t'.format(bin[j]))
            for label in self.matrixDict.keys():
                file_handle.write(
                    '{}\t{}\t'.format(avgDict[label][j], stdDict[label][j]))
            file_handle.write('\n')

        file_handle.close()

    def saveMatrixValues(self, file_name):
        # print a header telling the group names and their length
        fh = open(file_name, 'w')
        info = []
        groups_len = np.diff(self.matrix.group_boundaries)
        for i in range(len(self.matrix.group_labels)):
            info.append("{}:{}".format(self.matrix.group_labels[i],
                                       groups_len[i]))
        fh.write("#{}\n".format("\t".join(info)))
        # add to header the x axis values
        fh.write("#downstream:{}\tupstream:{}\tbody:{}\tbin size:{}\n".format(
                 self.parameters['downstream'],
                 self.parameters['upstream'],
                 self.parameters['body'],
                 self.parameters['bin size']))

        fh.close()
        # reopen again using append mode
        fh = open(file_name, 'a')
        np.savetxt(fh, self.matrix.matrix, fmt="%.4g")
        fh.close()

    def saveBED(self, file_handle):
        boundaries = np.array(self.matrix.group_boundaries)
        for idx, region in enumerate(self.matrix.regions):
            # the label id corresponds to the last boundary
            # that is smaller than the region index.
            # for example for a boundary array = [0, 10, 20]
            # and labels ['a', 'b', 'c'],
            # for index 5, the label is 'a', for
            # index 10, the label is 'b' etc
            label_idx = np.flatnonzero(boundaries <= idx)[-1]
            file_handle.write(
                '{}\t{}\t{}\t{}\t{}\t{}\t{}\n'.format(
                    region['chrom'],
                    region['start'],
                    region['end'],
                    region['name'],
                    0,
                    region['strand'],
                    self.matrix.group_labels[label_idx]))
        file_handle.close()

    @staticmethod
    def matrixAvg(matrix, avgType='mean'):
        matrix = np.ma.masked_invalid(matrix)
        return np.__getattribute__(avgType)(matrix, axis=0)

    @staticmethod
    def filterGenomicIntervalFile(file_handle):
        """
        Filter track lines out of a GenomicIntervalFile, normally from UCSC.
        Return an iterator over the lines of file_handle.
        """
        for line in file_handle:
            if line.startswith('browser') or line.startswith('track'):
                continue
            yield line

    @staticmethod
    def getRegionsAndGroups(regions_file, onlyMultiplesOf=1,
                            default_group_name='genes',
                            verbose=None):
        """
        Reads a bed file.
        In case is hash sign '#' is found in the
        file, this is considered as a delimiter
        to split the heatmap into groups

        Returns a list of regions, a list of labels
        and a list of places to split the regions
        """

        regions = []
        previnterval = None
        duplicates = 0
        totalintervals = 0
        includedintervals = 0
        group_labels = []
        group_boundaries = [0]
        for ginterval in GenomicIntervalReader(
            heatmapper.filterGenomicIntervalFile(regions_file),
            fix_strand=True):

            totalintervals += 1
            if ginterval.__str__().startswith('#'):
                if includedintervals > 1 and  \
                        includedintervals - group_boundaries[-1] > 1:
                    label = ginterval.__str__()[1:].strip()
                    newlabel = label
                    if label in group_labels:
                       # loop to find a unique label name
                        i = 0
                        while True:
                            i += 1
                            newlabel = label + "_r" + str(i)
                            if newlabel not in group_labels:
                                break

                    group_labels.append(label)
                    group_boundaries.append(includedintervals)
                continue
            # if the list of regions is to big, only
            # consider a fraction of the data
            if totalintervals % onlyMultiplesOf != 0:
                continue
            # check for regions that have the same position as the previous.
            # This assumes that the regions file given is sorted
            if previnterval and previnterval.chrom == ginterval.chrom and \
                    previnterval.start == ginterval.start and \
                    previnterval.end == ginterval.end and \
                    previnterval.strand == ginterval.strand:
                if verbose:
                    try:
                        genename = ginterval.fields[3]
                    except:
                        genename = ''
                    sys.stderr.write("*Warning* Duplicated region: "
                                     "{} {}:{}-{}.\n".format(
                            genename,
                            ginterval.chrom, ginterval.start,
                            ginterval.end))
                duplicates += 1

            previnterval = ginterval

            regions.append(heatmapper.ginterval2dict(ginterval))
            includedintervals += 1

        # in case we reach the end of the file
        # without encountering a hash,
        # a default name is given to regions
        if len(regions) > group_boundaries[-1]:
            group_labels.append(default_group_name)
            group_boundaries.append(includedintervals)

        if verbose and duplicates > 0:
            sys.stderr.write(
                "{} ({:.2f}) regions covering the exact same interval "
                "were found".format(duplicates,
                                    float(duplicates) * 100 / totalintervals))

        return regions, group_labels, group_boundaries

    @staticmethod
    def ginterval2dict(genomicInterval):
        """
        transforms a genomic interval from bx python
        into a dictionary
        """
        region = {'chrom': genomicInterval.chrom,
                  'start': genomicInterval.start,
                  'end': genomicInterval.end,
                  'strand': genomicInterval.strand}
        try:
            region['name'] = genomicInterval.fields[3]
        except IndexError:
            region['name'] = "No name"
        return region

    def getIndividualmatrices(self, matrix):
        """In case multiple matrices are saved one after the other
        this method splits them appart.
        Returns a list containing the matrices
        """
        num_cols = matrix.shape[1]
        num_ind_cols = self.getNumIndividualMatrixCols()
        matrices_list = []
        for i in range(0, num_cols, num_ind_cols):
            if i + num_ind_cols > num_cols:
                break
            matrices_list.append(matrix[:, i:i+num_ind_cols])
        return matrices_list

    def getNumIndividualMatrixCols(self):
        """
        returns the number of columns  that
        each matrix should have. This is done because
        the final matrix that is plotted can be composed
        of smaller matrices that are merged one after
        the other.
        """
        matrixCols = ((self.parameters['downstream'] +
                       self.parameters['upstream'] + 
                       self.parameters['body']) /
                      self.parameters['bin size'])

        return matrixCols


class _matrix(object):
    """
    class to hold heatmapper matrices
    The base data is a large matrix
    with definition to know the boundaries for row and col divisions.
    Col divisions represent groups within a subset, e.g. Active and
    inactive from PolII bigwig data.

    Row division represent different samples, for example
    PolII in males vs. PolII in females.

    This is an internal class of the heatmapper class
    """


    def __init__(self, regions, matrix, group_boundaries, sample_boundaries,
                 group_labels=None, sample_labels=None):

        # simple checks
        assert matrix.shape[0] == group_boundaries[-1], \
            "row max do not match matrix shape"
        assert matrix.shape[1] == sample_boundaries[-1], \
            "col max do not match matrix shape"

        self.regions = regions
        self.matrix = matrix
        self.group_boundaries = group_boundaries
        self.sample_boundaries = sample_boundaries
        if group_labels is None:
            self.group_labels = ['group {}'.format(x)
                                 for x in range(len(group_boundaries)-1)]
        else:
            assert len(group_labels) == len(group_boundaries) - 1, \
                "number of group labels does not match number of groups"
            self.group_labels = group_labels

        if sample_labels is None:
            self.sample_labels = ['sample {}'.format(x)
                                 for x in range(len(sample_boundaries)-1)]
        else:
            assert len(sample_labels) == len(sample_boundaries) - 1, \
                "number of sample labels does not match number of samples"
            self.sample_labels = sample_labels

    def get_matrix(self, group, sample):
        """
        Returns a sub matrix from the large
        matrix. Group and sample are ids,
        thus, row = 0, col=0 get the first group
        of the first sample.

        Returns
        -------
        dictionary containing the matrix,
        the group label and the sample label
        """
        group_start = self.group_boundaries[group]
        group_end = self.group_boundaries[group+1]
        sample_start = self.sample_boundaries[sample]
        sample_end = self.sample_boundaries[sample+1]

        return {'matrix': self.matrix[group_start:group_end, :][:, sample_start:sample_end],
                'group': self.group_labels[group],
                'sample': self.sample_labels[sample]}


    def get_num_samples(self):
        return len(self.sample_labels)

    def get_num_groups(self):
        return len(self.group_labels)

    def set_group_labels(self, new_labels):
        """ sets new labels for groups
        """
        if len(new_labels) != len(self.group_labels):
            raise ValueError("length new labels != length original labels")
        self.group_labels = new_labels


    def set_sample_labels(self, new_labels):
        """ sets new labels for groups
        """
        if len(new_labels) != len(self.sample_labels):
            raise ValueError("length new labels != length original labels")
        self.sample_labels = new_labels

    def sort_groups(self, sort_using='mean', sort_method='no'):
        """
        Sorts and rearanges the submatrices according to the
        sorting method given.
        """
        if sort_method == 'no':
            return

        # compute the row average:
        if sort_using == 'region_length':
            matrix_avgs = np.array([x['end'] - x['start']
                                   for x in self.regions])
        else:
            matrix_avgs = np.__getattribute__(sort_using)(
                self.matrix, axis=1)

        # order per group
        _sorted_regions = []
        _sorted_matrix = []
        for idx in range(len(self.group_labels)):
            start = self.group_boundaries[idx]
            end = self.group_boundaries[idx+1]
            order = matrix_avgs[start:end].argsort()
            if sort_method == 'descend':
                order = order[::-1]
            _sorted_matrix.append(self.matrix[start:end, :][order, :])
            _reg = self.regions[start:end]
            for idx in order:
                _sorted_regions.append(_reg[idx])

        self.matrix = np.vstack(_sorted_matrix)
        self.regions = _sorted_regions

    def hmcluster(self, k, method='kmeans'):

        matrix = np.asarray(self.matrix)
        # replace nans for 0 otherwise kmeans produces a weird behaviour
        matrix[np.isnan(matrix)] = 0

        if method == 'kmeans':
            from scipy.cluster.vq import vq, kmeans

            centroids, _ = kmeans(matrix, k)
            # order the centroids in an attempt to
            # get the same cluster order
            order = np.argsort(centroids.mean(axis=1))
            cluster_labels,_ = vq(matrix, centroids[order, :])

        if method == 'hierarchical':
            # normally too slow for large data sets
            from scipy.cluster.hierarchy import fcluster, linkage
            Z = linkage(matrix, method='ward')
            cluster_labels = fcluster(Z, k, criterion='maxclust')

        # create groups using the clustering
        self.group_labels = []
        self.group_boundaries = [0]
        _clustered_regions = []
        _clustered_matrix = []
        for cluster in range(k):
            self.group_labels.append("cluster {}".format(cluster+1))
            cluster_ids = np.flatnonzero(cluster_labels == cluster)
            self.group_boundaries.append(self.group_boundaries[-1] +
                                         len(cluster_ids))
            _clustered_matrix.append(self.matrix[cluster_ids, :])
            for idx in cluster_ids:
                _clustered_regions.append(self.regions[idx])

        self.regions = _clustered_regions
        self.matrix = np.vstack(_clustered_matrix)
        return idx
