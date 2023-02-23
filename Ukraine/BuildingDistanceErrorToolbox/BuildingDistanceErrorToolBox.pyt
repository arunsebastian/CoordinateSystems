# -*- coding: utf-8 -*-

import arcpy
import os,sys,json
import ntpath
import time
import importlib.util as imp_util
import pandas as pd

root_path = os.path.dirname(os.path.realpath(__file__))

IN_FID = 'IN_FID'
NEAR_FID = 'NEAR_FID'
NEAR_DISTANCE ='NEAR_DIST'
FROM_X = 'FROM_X'
FROM_Y = 'FROM_Y'
NEAR_X = 'NEAR_X'
NEAR_Y = 'NEAR_Y'

class Toolbox(object):
    def __init__(self):
        """Define the toolbox (the name of the toolbox is the name of the
        .pyt file)."""
        self.label = "Toolbox"
        self.alias = "toolbox"

        # List of tool classes associated with this toolbox
        self.tools = [BuildingDistanceErrorTool]


class BuildingDistanceErrorTool(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "BuildingDistanceErrorTool"
        self.description = ""
        self.canRunInBackground = False

        self.config = self.importConfig()
        
       

        # storing reference to the params
        self.params = arcpy.GetParameterInfo()
        arcpy.env.overwriteOutput = True

    def getParameterInfo(self):
        """Define parameter definitions"""
        params = []
        building_shp = arcpy.Parameter(           
            name="building_shp",
            displayName='Select GPS Files',
            datatype="DEShapefile",
            parameterType="Required",
            direction="Input"
        )
        building_shp.value = self.config.get('source')
        result_folder = arcpy.Parameter(           
            name="result_folder",
            displayName='Choose output folder',
            datatype="DEFolder",
            parameterType="Required",
            direction="Input"
        )
        result_folder.value = self.config.get('outFolder')
        distance_threshold = arcpy.Parameter(           
            name="distance_threshold",
            displayName='Distance Tolerance (in meters)',
            datatype="GPLong",
            parameterType="Required",
            direction="Input"
        )
        distance_threshold.value = self.config.get('distanceThreshold')
        params = [building_shp, result_folder,distance_threshold]
        return params

    def isLicensed(self):
        """Set whether tool is licensed to execute."""
        return True

    def updateParameters(self, parameters):
        """Modify the values and properties of parameters before internal
        validation is performed.  This method is called whenever a parameter
        has been changed."""
        return

    def updateMessages(self, parameters):
        """Modify the messages created by internal validation for each tool
        parameter.  This method is called after internal validation."""
        return

    def execute(self, parameters, messages):
        """The source code of the tool."""
         #set workspace
        try:
            self.setWorkspace()
            self.validateInputDataSet()
            self.createResultFeatureClass()
            self.processInputDataForPolygonProximity()
            
            #self.deleteTemporaryWorkspace()
        except Exception as error :
            self.log(error,'error')
            #self.deleteTemporaryWorkspace()
        return

    def postExecute(self, parameters):
        """This method takes place after outputs are processed and
        added to the display."""
        return
    

    #--------------------------------------------------------------------------------------------------------#

    def validateInputDataSet(self):
        inputFC = self.getInputFeatureClass()
        if inputFC:
            dsc = arcpy.da.Describe(inputFC).get('spatialReference')
            if  'meter' not in dsc.linearUnitName.lower():
                raise Exception("Invalid linear unit for coordinate system")
        else:
            raise Exception("Invalid file input")
    
    def createResultFeatureClass(self):
        errorPointFC = os.path.join(self.workspace,self.config.get('errorPointFC'))
        errorLineFC = os.path.join(self.workspace,self.config.get('errorLineFC'))
        if not arcpy.Exists(errorPointFC):
            arcpy.management.CreateFeatureclass(self.workspace, self.config.get('errorPointFC'), 'POINT',spatial_reference = self.getInputSpatialReference())
            arcpy.management.AddField(errorPointFC, "error_type", "TEXT")
            arcpy.management.AddField(errorPointFC, "distance", "TEXT")
        if not arcpy.Exists(errorLineFC):
            arcpy.management.CreateFeatureclass(self.workspace, self.config.get('errorLineFC'), 'POLYLINE',spatial_reference = self.getInputSpatialReference())
    
    def processInputDataForPolygonProximity(self):
        nearTable = self.generateValidNearTable(self.getInputFeatureClass())
        self.generateResultErrorFeatures(nearTable)
    
    def processInputDataForVertexProximity(self):
        return 1

    def generateValidNearTable(self,inputFC):
        distanceThreshold = next((param for param in self.params if param.name == 'distance_threshold'), None)
        outFC = os.path.join(self.workspace,'neartable')
        nearTable = arcpy.analysis.GenerateNearTable(inputFC,inputFC,outFC,int(distanceThreshold.valueAsText),location='LOCATION')

        #removing the records with near distance = 0 - helps in inspecting the near table for debugging
        with arcpy.da.UpdateCursor(nearTable,'*',where_clause=f'{NEAR_DISTANCE} = 0') as uCur:
            for dRow in uCur:
                uCur.deleteRow()
        return nearTable
    
    def generateResultErrorFeatures(self,table):
        fieldNames =['error_type','distance','Shape@']
        errorPointFC = os.path.join(self.workspace,self.config.get('errorPointFC'))
        errorLineFC = os.path.join(self.workspace,self.config.get('errorLineFC'))
        spatialRef = self.getInputSpatialReference()
        arcpy.management.XYToLine(table, errorLineFC, FROM_X, FROM_Y, NEAR_X, NEAR_Y, 'PLANAR', spatial_reference=spatialRef,attributes=True)
        with arcpy.da.InsertCursor(errorPointFC, fieldNames) as iCursor:
            with arcpy.da.SearchCursor(errorLineFC,[NEAR_DISTANCE,'Shape@'] ) as sCursor:
                for distance,line in sCursor:
                    midpoint = line.positionAlongLine(0.50,True).firstPoint
                    iCursor.insertRow(('ADJACENT_BUILDING_DISTANCE',str(distance),midpoint))
        
    def getInputSpatialReference(self):
        inputFC = self.getInputFeatureClass()
        dsc = arcpy.da.Describe(inputFC).get('spatialReference')
        return arcpy.SpatialReference(text=dsc.exportToString())

    def getInputFeatureClass(self):
        inputFileParam = next((param for param in self.params if param.name == 'building_shp'), None)
        inputFC = inputFileParam.valueAsText
        if inputFC and len(inputFC.strip()) > 0:
            return inputFC
        return None
    

    #--------------------------------------------------------------------------------------------------------#

    def log(self, msg, type='message'):
        if type == 'error':
            arcpy.AddError(msg)
        else:
            arcpy.AddMessage(msg)

    def setWorkspace(self):
        inputFileParam = next((param for param in self.params if param.name == 'building_shp'), None)
        resultFolderParam = next((param for param in self.params if param.name == 'result_folder'), None)
        if inputFileParam and inputFileParam.valueAsText:
            filePath = inputFileParam.valueAsText.strip()
            fileName = ntpath.basename(filePath)
            outGeodbName = f'{os.path.splitext(fileName)[0]}_DError_{int(time.time() * 1000)}.gdb'
            if resultFolderParam and resultFolderParam.valueAsText:
                self.workspace = str(arcpy.CreateFileGDB_management(resultFolderParam.valueAsText, outGeodbName))
                arcpy.env.workspace = self.workspace
            else:
                self.log("Invalid input params","error")
        else:
            self.log("Invalid input params","error")

    def importConfig(self):
        config_module_path = os.path.join(root_path,"config")
        sys.path.append(config_module_path)
        spec = imp_util.spec_from_file_location("config",os.path.join(config_module_path,'Config.py'))
        module = imp_util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = {var:vars(module)[var] for var in dir(module) if not var.startswith('_')}
        return config.get('CONFIG')

    def fet(self,in_table, input_fields=None, where_clause=None):
        """Function will convert an arcgis table into a pandas dataframe with an object ID index, and the selected
        input fields using an arcpy.da.SearchCursor."""
        OIDFieldName = arcpy.Describe(in_table).OIDFieldName
        if input_fields:
            final_fields = [OIDFieldName] + input_fields
        else:
            final_fields = [field.name for field in arcpy.ListFields(in_table)]
        data = [row for row in arcpy.da.SearchCursor(in_table, final_fields, where_clause=where_clause)]
        fc_dataframe = pd.DataFrame(data, columns=final_fields)
        fc_dataframe = fc_dataframe.set_index(OIDFieldName, drop=True)
        return fc_dataframe
