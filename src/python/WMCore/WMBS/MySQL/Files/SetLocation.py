#!/usr/bin/env python
"""
_SetLocation_

MySQL implementation of Files.SetLocation
"""

__revision__ = "$Id: SetLocation.py,v 1.12 2009/12/16 17:45:41 sfoulkes Exp $"
__version__ = "$Revision: 1.12 $"

from WMCore.Database.DBFormatter import DBFormatter

class SetLocation(DBFormatter):
    sql = """INSERT INTO wmbs_file_location (file, location) 
             SELECT :fileid, wmbs_location.id FROM wmbs_location 
             WHERE wmbs_location.site_name = :location"""
                
    def getBinds(self, file = None, location = None):
        if type(location) == type('string'):
            return self.dbi.buildbinds(self.dbi.makelist(file), 'fileid', 
                   self.dbi.buildbinds(self.dbi.makelist(location), 'location'))
        elif isinstance(location, (list, set)):
            binds = []
            for l in location:
                binds.extend(self.dbi.buildbinds(self.dbi.makelist(file), 'fileid', 
                   self.dbi.buildbinds(self.dbi.makelist(l), 'location')))
            return binds
        else:
            raise Exception, "Type of location argument is not allowed: %s" \
                                % type(location)
    
    def execute(self, file, location, conn = None, transaction = None):
        binds = self.getBinds(file, location)

        result = self.dbi.processData(self.sql, binds, conn = conn,
                                      transaction = transaction)
        return
