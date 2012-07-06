"""
Provide functions to collect data and upload data
"""

"""
JobInfoByID

Retrieve information about a job from couch and format it nicely.
"""

import sys
import datetime
import os
import time
import re
import urllib
import logging
from WMCore.Database.CMSCouch import CouchServer
from WMCore.DAOFactory import DAOFactory
from WMCore.Lexicon import splitCouchServiceURL, sanitizeURL
from WMComponent.AnalyticsDataCollector.DataCollectorEmulatorSwitch import emulatorHook

@emulatorHook
class LocalCouchDBData():

    def __init__(self, couchURL):
        # set the connection for local couchDB call
        self.couchURL = couchURL
        self.couchURLBase, self.dbName = splitCouchServiceURL(couchURL)
        self.jobCouchDB = CouchServer(self.couchURLBase).connectDatabase(self.dbName + "/jobs", False)
        self.fwjrsCouchDB = CouchServer(self.couchURLBase).connectDatabase(self.dbName + "/fwjrs", False)
        
    def getJobSummaryByWorkflowAndSite(self):
        """
        gets the job status information by workflow
    
        example
        {"rows":[
            {"key":['request_name1", "queued_first", "siteA"],"value":100},
            {"key":['request_name1", "queued_first", "siteB"],"value":100},
            {"key":['request_name1", "running", "siteA"],"value":100},
            {"key":['request_name1", "success", "siteB"],"value":100}\
         ]}
         and convert to 
         {'request_name1': {'queue_first': { 'siteA': 100}}
          'request_name1': {'queue_first': { 'siteB': 100}}
         }
        """
        options = {"group": True, "stale": "ok"}
        # site of data should be relatively small (~1M) for put in the memory 
        # If not, find a way to stream
        results = self.jobCouchDB.loadView("JobDump", "jobStatusByWorkflowAndSite",
                                        options)

        # reformat the doc to upload to reqmon db
        data = {}
        for x in results.get('rows', []):
            data.setdefault(x['key'][0], {})
            data[x['key'][0]].setdefault(x['key'][1], {}) 
            #data[x['key'][0]][x['key'][1]].setdefault(x['key'][2], {})
            data[x['key'][0]][x['key'][1]][x['key'][2]] = x['value'] 
        logging.info("Found %i requests" % len(data))
        return data

    def getEventSummaryByWorkflow(self):
        """
        gets the job status information by workflow
    
        example
        {"rows":[
            {"key":['request_name1", "/test/output_dataset1"],
             "value": {size: 20286644784714, events: 38938099, count: 6319, 
                       dataset: "/test/output_dataset1"}},
            {"key":['request_name1", "/test/output_dataset2"],
             "value": {size: 20286644784714, events: 38938099, count: 6319, 
                       dataset: "/test/output_dataset2"}},
            {"key":['request_name1", "/test/output_dataset3"],
             "value": {size: 20286644784714, events: 38938099, count: 6319, 
                       dataset: "/test/output_dataset3"}},
            {"key":['request_name1", "/test/output_dataset4"],
             "value": {size: 20286644784714, events: 38938099, count: 6319, 
                       dataset: "/test/output_dataset4"}},
         ]}
         and convert to 
         {'request_name1': {'size_event': [{size: 20286644784714, events: 38938099, count: 6319, 
                             dataset: "/test/output_dataset1"},
                             {size: 20286644784714, events: 38938099, count: 6319, 
                             dataset: "/test/output_dataset2"}]}
                       
          'request_name2': ...
        """
        options = {"group": True, "stale": "ok", "reduce":True}
        # site of data should be relatively small (~1M) for put in the memory 
        # If not, find a way to stream
        results = self.fwjrsCouchDB.loadView("FWJRDump", "outputByWorkflowName",
                                        options)

        # reformat the doc to upload to reqmon db
        data = {}
        for x in results.get('rows', []):
            data.setdefault(x['key'][0], [])
            data[x['key'][0]].append(x['value']) 
        logging.info("Found %i requests" % len(data))
        return data
    
@emulatorHook
class WMAgentDBData():

    def __init__(self, dbi, logger):
        
        # interface to WMBS/BossAir db
        bossAirDAOFactory = DAOFactory(package = "WMCore.BossAir",
                                       logger = logger, dbinterface = dbi)
        wmbsDAOFactory = DAOFactory(package = "WMCore.WMBS",
                                    logger = logger, dbinterface = dbi)
        wmAgentDAOFactory = DAOFactory(package = "WMCore.Agent.Database", 
                                     logger = logger, dbinterface = dbi)
        
        self.batchJobAction = bossAirDAOFactory(classname = "JobStatusByWorkflowAndSite")
        self.jobSlotAction = wmbsDAOFactory(classname = "Locations.GetJobSlotsByCMSName")
        self.componentStatusAction = wmAgentDAOFactory(classname = "CheckComponentStatus")

    def getHeartBeatWarning(self):
        
        results = self.componentStatusAction.execute(detail = True)
        agentInfo = {}
        agentInfo['down_components'] = []
        if len(results) == 0:
            agentInfo['status'] = 'ok'
        else:
            agentInfo['status'] = 'down'
            agentInfo['down_components'] = results    
        return agentInfo
    
    def getBatchJobInfo(self):
        return self.batchJobAction.execute()

    def getJobSlotInfo(self):
        return self.jobSlotAction.execute()
    
def combineAnalyticsData(a, b, combineFunc = None):
    """
        combining 2 data which is the format of dict of dict of ...
        a = {'a' : {'b': {'c': 1}}, 'b' : {'b': {'c': 1}}}
        b = {'a' : {'b': {'d': 1}}}
        should return
        result = {'a' : {'b': {'c': 1, 'd': 1}}, 'b' : {'b': {'c': 1}}}
        
        result is not deep copy
        when combineFunc is specified, if one to the values are not dict type,
        it will try to combine two values. 
        i.e. combineFunc = lambda x, y: x + y
    """
    result = {}
    result.update(a)
    for key, value in b.items():
        if not result.has_key(key):
            result[key] = value
        else:
            if not combineFunc and (type(value) != dict or type(result[key]) != dict):
                # this will raise error if it can't combine two
                result[key] = combineFunc(value, result[key])
            else:
                result[key] = combineAnalyticsData(value, result[key])
    return result 

def convertToRequestCouchDoc(combinedRequests, fwjrInfo, agentInfo, uploadTime):
    requestDocs = []
    for request, status in combinedRequests.items():
        doc = {}
        doc.update(agentInfo)
        doc['type'] = "agent_request"
        doc['workflow'] = request
        # this will set doc['status'], and doc['sites']
        tempData = _convertToStatusSiteFormat(status)
        doc['status'] = tempData['status']
        doc['sites'] = tempData['sites']
        doc['timestamp'] = uploadTime
        doc['output_progress'] = fwjrInfo.get(request, [])
        requestDocs.append(doc)
    return requestDocs

def convertToAgentCouchDoc(agentInfo, acdcConfig, uploadTime):
    
    #sets the _id using agent url, need to be unique
    aInfo = {}
    aInfo.update(agentInfo)
    aInfo['_id'] = agentInfo["agent_url"]
    acdcURL = '%s/%s' % (acdcConfig.couchurl, acdcConfig.database)
    aInfo['acdc'] = _getCouchACDCHtmlBase(acdcURL)
    aInfo['timestamp'] = uploadTime
    aInfo['type'] = "agent_info"
    return aInfo
           
def _setMultiLevelStatus(statusData, status, value):
    """
    handle the sub status structure 
    (i.e. submitted_pending, submitted_running -> {submitted: {pending: , running:}})
    prerequisite: status structure is seperated by '_'
    Currently handle only upto 2 level stucture but can be extended
    """
    statusStruct = status.split('_')
    if len(statusStruct) == 1:
        statusData.setdefault(status, 0)
        statusData[status] += value
    else:
        # only assumes len is 2, can be extended
        statusData.setdefault(statusStruct[0], {})
        statusData[statusStruct[0]].setdefault(statusStruct[1], 0)
        statusData[statusStruct[0]][statusStruct[1]] += value
    return

def _convertToStatusSiteFormat(requestData):
    """
    convert data structure for couch db.
    "status": { "inWMBS": 100, "success": 1000, "inQueue": 100, "cooloff": 1000,
                "submitted": {"retry": 200, "running": 200, "pending": 200, "first": 200"},
                "failure": {"exception": 1000, "create": 1000, "submit": 1000},
                "queued": {"retry": 1000, "first": 1000}},
   "sites": {
       "T1_test-site-1": {"submitted": {"retry": 200, "running": 200, "pending": 200, "first": 200"},
                          "failure": {"exception": 100, "create": 10, "submit": 10},
                          "cooloff": 100, ...}
       },
    """
    data = {}
    data['status'] = {}
    data['sites'] = {}
    
    for status, siteJob in requestData.items():
        if type(siteJob) != dict:
            _setMultiLevelStatus(data['status'], status, siteJob)
        else:
            for site, job in siteJob.items():
                _setMultiLevelStatus(data['status'], status, int(job))
                if site != 'Agent':
                    if site is None:
                        site = 'unknown'
                    data['sites'].setdefault(site, {})
                    _setMultiLevelStatus(data['sites'][site], status, int(job))
    return data

def _getCouchACDCHtmlBase(acdcCouchURL):
    """
    TODO: currently it is hard code to the front page of ACDC
    When there is more information is available, it can be added
    through 
    """
    

    return '%s/_design/ACDC/collections.html' % sanitizeURL(acdcCouchURL)['url']