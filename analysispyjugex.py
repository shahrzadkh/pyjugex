# -*- coding: utf-8 -*-
from __future__ import division
from __future__ import print_function
import os, glob
import numpy as np
from numpy import *
import json
from numpy.linalg import inv
import scipy as sp
import scipy.stats.mstats
import xmltodict
import statsmodels.api as sm
from statsmodels.formula.api import ols
from scipy import stats
import sys
import requests, requests.exceptions
import pandas as pd
import shutil


def getSpecimenData(info):
    """
    For each specimen, extract the name and alignment matrix and put into a dict object
    """
    specimenD = dict()
    specimenD['name'] = info['name']
    x = info['alignment3d']
    specimenD['alignment3d'] = np.array([
    [x['tvr_00'], x['tvr_01'], x['tvr_02'], x['tvr_09']],
    [x['tvr_03'], x['tvr_04'], x['tvr_05'], x['tvr_10']],
    [x['tvr_06'], x['tvr_07'], x['tvr_08'], x['tvr_11']],
    [0, 0, 0, 1]])
    return specimenD

def transformSamples(samples, T):
    """
    Convert the MRI coordinates of samples to MNI152 space
    """
    np_T = np.array(T[0:3, 0:4])
    mri = np.vstack(s["sample"]["mri"] for s in samples)
    add = np.ones((len(mri), 1), dtype=np.int)
    mri = np.append(mri, add, axis=1)
    mri = np.transpose(mri)
    coords = np.matmul(np_T, mri)
    coords = coords.transpose()
    return coords

class Analysis:

    def __init__(self, gene_cache, verbose=False):
        """
        Initialize the Analysis class with various internal variables -
        gene_cache = Disk location where data from Allen Brain API has been downloaded and stored.
        probeids = list of probe ids associated with the give list of genes.
        genelist = given list of genes
        downloadgenelist = list of genes whose information is not in the cache yet, needs to be downloaded
        genesymbols =
        donorids = size donor ids of Allen Brain API
        vois = list fo two nii volumes for each region of interest
        main_r = Internal variable for storing mni coordinates and zscores corresponsing to each region of interest.
        mapthreshold = Internal variable to select or reject a sample
        result = dict for storing gene ids and associated p values.
        """
        self.probeids = []
        self.genelist = []
        self.downloadgenelist = []
        self.genesymbols = []
        self.genecache = {}
        self.donorids = ['15496', '14380', '15697', '9861', '12876', '10021'] #HARDCODING DONORIDS
        self.apidata = dict.fromkeys(['apiinfo', 'specimeninfo'])        
        self.specimenFactors = dict.fromkeys(['id', 'name', 'race', 'gender', 'age'])
        self.apidata['specimenInfo'] = []
        self.apidata['apiinfo'] = []
        self.vois = []
        self.main_r = []
        self.mapthreshold = 0.2
        self.n_rep = 1000
        self.result = None
        self.cache = gene_cache
        self.verboseflag = verbose
        self.anova_data = dict.fromkeys(['Age', 'Race', 'Specimen', 'Area', 'Zscores'])
        self.all_probe_data = dict.fromkeys(['uniqueId', 'combined_zscores'])
        self.probepath = os.path.join(self.cache, self.donorids[0]+'/probes.txt')
        if os.path.exists(self.cache) and not os.path.exists(self.probepath):
            shutil.rmtree(self.cache, ignore_errors = False)
        if not os.path.exists(self.cache):
            print(self.cache,' does not exist. It will take some time ')
        else:
            self.creategenecache()
            print(len(self.genecache),' genes exist in ', self.cache)

    def DifferentialAnalysis(self, genelist, roi1, roi2):
        if not genelist:
            print('Atleast one gene is needed for the analysis')
            exit()
        if not roi1 or not roi2:
            print('Atleast two regions are needed for the analysis')
        self.set_candidate_genes(genelist)
        self.set_ROI_MNI152(roi1, 0)
        self.set_ROI_MNI152(roi2, 1)
        self.cleanup()
        print('Starting the analysis. This may take some time.....')
        self.performAnova()
        return self.result

    def cleanup(self):
        for filename in glob.glob("output*"):
            os.remove(filename)

    def creategenecache(self):
        donorpath = os.path.join(self.cache, self.donorids[0])
        filename = os.path.join(donorpath, 'probes.txt')
        f = open(filename, "r")
        probes = json.load(f)
        for p in probes:
            self.genecache.update({p['gene-symbol'] : None})
        if self.verboseflag:
            print(self.genecache)

    def retrieveprobeids(self):
        """
        Retrieve probe ids for the given gene lists
        """
        connection = False
        if self.verboseflag:
            print('genelist ',self.genelist)
        for g in self.genelist:
            url = "http://api.brain-map.org/api/v2/data/query.xml?criteria=model::Probe,rma::criteria,[probe_type$eq'DNA'],products[abbreviation$eq'HumanMA'],gene[acronym$eq"+g+"],rma::options[only$eq'probes.id']"
            if self.verboseflag:
                print(url)
            try:
                response = requests.get(url)
            except requests.exceptions.RequestException as e:
                print('In retreiveprobeids')
                print(e)
                connection = True

            data = xmltodict.parse(response.text)

            self.probeids = self.probeids + [d['id'] for d in data['Response']['probes']['probe'] if g in self.downloadgenelist]
            self.genesymbols = self.genesymbols + [g for d in data['Response']['probes']['probe']]

        if self.verboseflag:
            print('probeids: ',self.probeids)
            print('genesymbols: ',self.genesymbols)


    def readCachedApiSpecimenData(self):
        """
        Read cached Allen Brain Api data from disk location
        """
        #self.downloadspecimens()
        for d in self.donorids:
            donorpath = os.path.join(self.cache, d)
            fileNameM = os.path.join(donorpath, 'specimenMat.txt')
            mat = np.loadtxt(fileNameM)
            fileNameN = os.path.join(donorpath, 'specimenName.txt')
            f = open(fileNameN, 'r')
            name = f.read()
            f.close()
            specimen = dict.fromkeys(['name', 'alignment3d'])
            specimen['name'] = name
            specimen['alignment3d'] = mat
            self.apidata['specimenInfo'].append(specimen)
            #LOAD SAMPLES
            fileName = os.path.join(donorpath, 'samples.txt')
            f = open(fileName, "r")
            samplesC = json.load(f)
            f.close()
            #LOAD PROBES
            fileName = os.path.join(donorpath, 'probes.txt')
            f = open(fileName, "r")
            probesC = json.load(f)
            f.close()
            #LOAD ZSCORES
            fileName = os.path.join(donorpath, 'zscores.txt')
            zscoresC = np.loadtxt(fileName)
            apiDataC = dict()
            apiDataC['samples'] = samplesC
            apiDataC['probes'] = probesC
            apiDataC['zscores'] = zscoresC
            self.apidata['apiinfo'].append(apiDataC)
            if self.verboseflag:
                print('inside readcachedata ',len(apiDataC['samples']), ' ', apiDataC['zscores'].shape, ' ', len(apiDataC['probes']))

    def set_ROI_MNI152(self, voi, index):
        """
        Set the region of interest from the downloaded nii files
        """
        if index < 0 or index > 1:
            print('only 0 and 1 are valid choices')
            exit()

        for i in range(0, len(self.apidata['specimenInfo'])):
            self.main_r.append(self.expressionSpmCorrelation(voi, self.apidata['apiinfo'][i], self.apidata['specimenInfo'][i], index))

    def queryapi(self, donorId):
        main = "http://api.brain-map.org/api/v2/data/query.json?criteria=service::human_microarray_expression[probes$in"
        end = ''.join(p+"," for p in self.probeids)
        url = main + end
        url = url[:-1]
        url += "][donors$eq"+donorId+"]"
        try:
            response = requests.get(url)
            text = requests.get(url).json()
        except requests.exceptions.RequestException as e:
            print('In queryapi ')
            print(e)
            exit()
        data = text['msg']
        if not os.path.exists(self.cache):
            os.makedirs(self.cache)
        donorPath = os.path.join(self.cache, donorId)
        if not os.path.exists(donorPath):
            os.makedirs(donorPath)
        nsamples = len(data['samples'])
        nprobes = len(data['probes'])

        zscores = np.array([[float(data['probes'][i]['z-score'][j]) for i in range(nprobes)] for j in range(nsamples)])

        fileName = os.path.join(donorPath, 'zscores.txt')
        with open(fileName, 'wb') as f:
            np.savetxt(f, zscores, fmt = '%.5f')

        fileName = os.path.join(donorPath, 'samples.txt')
        with open(fileName, 'w') as outfile:
            json.dump(data['samples'], outfile)

        fileName = os.path.join(donorPath, 'probes.txt')
        with open(fileName, 'w') as outfile:
            json.dump(data['probes'], outfile)

        apiData = dict()
        apiData['samples'] = data['samples']
        apiData['probes'] = data['probes']
        apiData['zscores'] = zscores
        if self.verboseflag:
            print('For ',donorId,' samples_length: ',len(apiData['samples']),' probes_length: ',len(apiData['probes']),' zscores_shape: ',apiData['zscores'].shape)
        return apiData



    def expressionSpmCorrelation(self, img, apidataind, specimen, index):
        """
        Create internal data structures with valid coordinates in MNI152 space corresponding to the regions of interest
        """       
        revisedApiData = dict.fromkeys(['zscores', 'coords', 'samples', 'probes', 'specimen', 'name'])
        revisedApiData['name'] = 'img'+str(index+1)
        dataImg = img.get_data()
        imgMni = img.affine
        invimgMni = inv(imgMni)
        Mni = specimen['alignment3d']
        T = np.dot(invimgMni, Mni)
        coords = transformSamples(apidataind['samples'], T)
        coords = (np.rint(coords)).astype(int)
        #How to use numpy.where
        coords = [np.array([-1, -1, -1]) if (coord > 0).sum() != 3 or dataImg[coord[0],coord[1],coord[2]] <= self.mapthreshold or dataImg[coord[0],coord[1],coord[2]] == 0 else coord for coord in coords]
        revisedApiData['coords'] = [c for c in coords if (c > 0).sum() == 3]
        revisedApiData['zscores'] = [z for (c, z) in zip(coords, apidataind['zscores']) if (c > 0).sum() == 3]
        revisedApiData['samples'] = apidataind['samples'][:]
        revisedApiData['probes'] = apidataind['probes'][:]
        revisedApiData['specimen'] = specimen['name']
        return revisedApiData



    def queryapipartial(self, donorId):
        """
        Query Allen Brain Api for the given set of genes
        """
        main = "http://api.brain-map.org/api/v2/data/query.json?criteria=service::human_microarray_expression[probes$in"
        end = ''.join(p+"," for p in self.probeids)
        url = main + end
        url = url[:-1]
        url += "][donors$eq"+donorId+"]"
        if self.verboseflag:
            print(url)
        try:
            response = requests.get(url)
            text = requests.get(url).json()
        except requests.exceptions.RequestException as e:
            print('In queryapipartial ')
            print(e)
            exit()
        data = text['msg']
        samples = []
        probes = []

        if not os.path.exists(self.cache):
            os.makedirs(self.cache)
        donorpath = os.path.join(self.cache, donorId)
        if not os.path.exists(donorpath):
            os.makedirs(donorpath)
        nsamples = len(data['samples'])
        nprobes = len(data['probes'])
        samples = data['samples']
        probes = data['probes']

        zscores = np.array([[float(data['probes'][i]['z-score'][j]) for i in range(nprobes)] for j in range(nsamples)])
        #LOAD PROBES
        fileName = os.path.join(donorpath, 'probes.txt')
        f = open(fileName, "r")
        probesC = json.load(f)
        f.close()
        #LOAD ZSCORES
        fileName = os.path.join(donorpath, 'zscores.txt')
        zscoresC = np.loadtxt(fileName)

        fileName = os.path.join(donorpath, 'samples.txt')
        with open(fileName, 'w') as outfile:
            json.dump(samples, outfile)
        probes = probesC + probes
        fileName = os.path.join(donorpath, 'probes.txt')
        with open(fileName, 'w') as outfile:
            json.dump(probes, outfile)
        zscores = np.append(zscoresC, zscores, axis=1)
        filename = os.path.join(donorpath, 'zscores.txt')
        np.savetxt(filename, zscores)

    def downloadspecimens(self):
        """
        Downlaod names and alignment matrix for each specimen/donor from Allen Brain Api and save them on disk as specimenName.txt
        and specimenMat.txt respectively, load.
        """
        specimens  = ['H0351.1015', 'H0351.1012', 'H0351.1016', 'H0351.2001', 'H0351.1009', 'H0351.2002']
        self.apidata['specimenInfo'] = []
        for i in range(0, len(specimens)):
            url = "http://api.brain-map.org/api/v2/data/Specimen/query.json?criteria=[name$eq"+"'"+specimens[i]+"']&include=alignment3d"
            if self.verboseflag:
                print(url)    
            try:
                text = requests.get(url).json()
            except requests.exceptions.RequestException as e:
                print('In downloadspecimens ')
                print(e)
                exit()
            data = text['msg'][0]
            res = getSpecimenData(data)
            self.apidata['specimenInfo'] = self.apidata['specimenInfo'] + [res]
        if self.verboseflag:
            print(self.apidata['specimenInfo'])
        for i in range(0, len(self.donorids)):
            factorPath = os.path.join(self.cache, self.donorids[i]+'/specimenName.txt')
            with open(factorPath, 'w') as outfile:
                outfile.write(self.apidata['specimenInfo'][i]['name'])
            factorPath = os.path.join(self.cache, self.donorids[i]+'/specimenMat.txt')
            np.savetxt(factorPath, self.apidata['specimenInfo'][i]['alignment3d'])

    def getapidata(self):
        """
        Loop through the donors and call queryapi() and populate apidata
        """
        self.apidata['apiinfo'] = []
        for i in range(0, len(self.donorids)):
            self.apidata['apiinfo'] = self.apidata['apiinfo'] + [self.queryapi(self.donorids[i])]

    def download_and_retrieve_gene_data(self):
        """
        Download data from Allen Brain Api for the given set of genes and specimen information
        """
        self.getapidata()
        self.downloadspecimens()

    def set_candidate_genes(self, genelist):
        """
        Set list of genes and prepare to read/download data for them.
        """
        self.genelist = genelist
        self.downloadgenelist = self.genelist[:]
        donorpath = os.path.join(self.cache, self.donorids[0])
        donorprobe = os.path.join(donorpath, 'probes.txt')
        if not os.path.exists(self.cache):
            if self.verboseflag:
                print('self.cache does not exist')
            self.retrieveprobeids()
            self.download_and_retrieve_gene_data()
        else:
            self.creategenecache()
            for k, v in self.genecache.items():
                if k in self.genelist:
                    self.downloadgenelist.remove(k)            
            if self.downloadgenelist:
                print('Microarray expression values of',len(self.downloadgenelist),'gene(s) need(s) to be downloaded')
            if self.verboseflag:
                print(self.downloadgenelist)
            self.retrieveprobeids()
            if self.downloadgenelist:
                for i in range(0, len(self.donorids)):
                    self.queryapipartial(self.donorids[i])
            self.readCachedApiSpecimenData()


    def getmeanzscores(self, gene_symbols, combined_zscores, area1len, area2len):
        """
        Compute Winsorzed mean of zscores over all genes.
        """
        unique_gene_symbols = np.unique(gene_symbols)
        indices = [np.where(np.in1d(gene_symbols, x))[0] for x in unique_gene_symbols]
        winsorzed_mean_zscores =  np.array([[np.mean(sp.stats.mstats.winsorize([combined_zscores[j][indices[i][k]] for k in range(0, len(indices[i]))], limits=0.1)) for i in range (len(unique_gene_symbols))] for j in range(len(combined_zscores))])
        self.all_probe_data['uniqueId'] = unique_gene_symbols
        self.all_probe_data['combined_zscores'] = winsorzed_mean_zscores

    def initialize(self):
        combined_zscores = [r['zscores'][i] for r in self.main_r for i in range(len(r['zscores']))]
        self.readSpecimenFactors(self.cache)
        if self.verboseflag:
            print("number of specimens ", len(self.specimenFactors), " name: ", len(self.specimenFactors['name']))
        self.getmeanzscores(self.genesymbols, combined_zscores, len([r['name'] for r in self.main_r if r['name'] == 'img1']), len([r['name'] for r in self.main_r if r['name'] == 'img2']))
        st = set(self.genesymbols)
        self.geneIds = [self.genesymbols[self.genesymbols.index(a)] if a in st else [self.genesymbols[0]] for ind, a in enumerate(self.all_probe_data['uniqueId'])]
        self.n_genes = len(self.all_probe_data['combined_zscores'][0])
        self.anova_data['Area'] = [r['name'] for r in self.main_r for i in range(len(r['zscores'])) if r['name'] == 'img1'] + [r['name'] for r in self.main_r for i in range(len(r['zscores'])) if r['name'] == 'img2']
        self.anova_data['Specimen'] = [r['specimen'] for r in self.main_r for i in range(len(r['zscores'])) if r['name'] == 'img1'] + [r['specimen'] for r in self.main_r for i in range(len(r['zscores'])) if r['name'] == 'img2']
        st = set(self.specimenFactors['name'])
        self.anova_data['Age'] = [self.specimenFactors['age'][self.specimenFactors['name'].index(a)] if a in st else [self.specimenFactors['age'][0]] for ind, a in enumerate(self.anova_data['Specimen'])]
        self.anova_data['Race'] = [self.specimenFactors['race'][self.specimenFactors['name'].index(a)] if a in st else [self.specimenFactors['race'][0]] for ind, a in enumerate(self.anova_data['Specimen'])]
        if self.verboseflag:
            print('race')
            print(self.anova_data['Race'])
            print('age')
            print(self.anova_data['Age'])
            print(len(self.genelist))

    def first_iteration(self):
        self.F_vec_ref_anovan = np.zeros(self.n_genes)
        for i in range(0, self.n_genes):
            self.anova_data['Zscores'] = self.all_probe_data['combined_zscores'][:,i]
            mod = ols('Zscores ~ Area + Specimen + Age + Race', data=self.anova_data).fit()
            aov_table = sm.stats.anova_lm(mod, typ=1)
            if self.verboseflag:
                print(aov_table)
            self.F_vec_ref_anovan[i] = aov_table['F'][0]

    def fwe_correction(self):
        invn_rep = 1/self.n_rep
        self.FWE_corrected_p = np.zeros(self.n_genes)
        self.F_mat_perm_anovan = np.zeros((self.n_rep, self.n_genes))
        self.F_mat_perm_anovan[0] = self.F_vec_ref_anovan
        self.F_vec_perm_anovan = np.zeros(self.n_genes)
        for rep in range(1, self.n_rep):
            for j in range(0, self.n_genes):
                shuffle = np.random.permutation(self.anova_data['Area'])
                self.anova_data['Area'] = shuffle
                self.anova_data['Zscores'] = self.all_probe_data['combined_zscores'][:,j]
                mod = ols('Zscores ~ Area + Specimen + Age + Race', data=self.anova_data).fit()
                aov_table = sm.stats.anova_lm(mod, typ=1)
                self.F_vec_perm_anovan[j] = aov_table['F'][0]
            self.F_mat_perm_anovan[rep] = self.F_vec_perm_anovan
        self.accumulate_result()

    def accumulate_result(self):
        invn_rep = 1/self.n_rep
        ref = self.F_mat_perm_anovan.max(1)
        self.FWE_corrected_p =  [len([1 for a in ref if a >= f])/self.n_rep if sys.version_info[0] >= 3 else len([1 for a in ref if a >= f])*invn_rep for f in self.F_vec_ref_anovan]
        self.result = dict(zip(self.geneIds, self.FWE_corrected_p))
        if self.verboseflag:
            print(self.result)

    def performAnova(self):
        """
        Perform one way anova on zscores as the dependent variable and specimen factors such as age, race, name and area
        as independent variables
        """
        self.initialize()
        self.first_iteration()
        self.fwe_correction()

    def buildSpecimenFactors(self, cache):
        """
        Download various factors such as age, name, race, gender of the six specimens from Allen Brain Api and create a dict.
        """
        url = "http://api.brain-map.org/api/v2/data/query.json?criteria=model::Donor,rma::criteria,products[id$eq2],rma::include,age,rma::options[only$eq%27donors.id,donors.name,donors.race_only,donors.sex%27]"
        try:
            text = requests.get(url).json()
        except requests.exceptions.RequestException as e:
            print('In buildspecimenfactors')
            print(e)
            exit()
        factorPath = os.path.join(cache, 'specimenFactors.txt')
        with open(factorPath, 'w') as outfile:
            json.dump(text, outfile)
        res = text['msg']

        self.specimenFactors['id'] = [r['id'] for r in res]
        self.specimenFactors['name'] = [r['name'] for r in res]
        self.specimenFactors['race'] = [r['race_only'] for r in res]
        self.specimenFactors['gender'] = [r['sex'] for r in res]
        self.specimenFactors['age'] = [r['age']['days']/365 for r in res]


    def readSpecimenFactors(self, cache):
        """
        Read various factors such as age, name, race, gender of the six specimens from disk.
        """
        fileName = os.path.join(cache, 'specimenFactors.txt')
        if not os.path.exists(fileName):
            self.buildSpecimenFactors(cache)
        f = open(fileName, "r")
        content = json.load(f)
        f.close()
        res = content['msg']
        self.specimenFactors = dict()
        self.specimenFactors['id'] = [r['id'] for r in res]
        self.specimenFactors['name'] = [r['name'] for r in res]
        self.specimenFactors['race'] = [r['race_only'] for r in res]
        self.specimenFactors['gender'] = [r['sex'] for r in res]
        self.specimenFactors['age'] = [r['age']['days']/365 for r in res]
