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
        self.workspace = None
        self.scratchWorkspace = None
        
        # storing reference to the params
        self.params = arcpy.GetParameterInfo()
        arcpy.env.overwriteOutput = True

    def getParameterInfo(self):
        """Define parameter definitions"""
        params = []
        building_shp = arcpy.Parameter(           
            name="building_shp",
            displayName='Select SHP File',
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
            self.processInputDataForBoundaryProximity()
            self.processInputDataForVertexProximity()
            self.deleteTemporaryWorkspace()
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
        if not arcpy.Exists(errorPointFC):
            arcpy.management.CreateFeatureclass(self.workspace, self.config.get('errorPointFC'), 'POINT',spatial_reference = self.getInputSpatialReference())
            arcpy.management.AddField(errorPointFC, "error_type", "TEXT")
            arcpy.management.AddField(errorPointFC, "distance", "TEXT")
       
    
    def processInputDataForBoundaryProximity(self):
        self.log('Checking building proximity')
        error='ADJACENT_BUILDING_DISTANCE'
        tic = time.perf_counter()
        nearTable = self.generateValidNearTable(self.getInputFeatureClass())
        self.generateResultErrorFeatures(nearTable,error)
        self.log("Building proximity results: {:.4f} seconds".format(time.perf_counter() - tic))
    
    def processInputDataForVertexProximity(self):
        error='ADJACENT_VERTEX_DISTANCE'
        inputFC = self.getInputFeatureClass()
        outFC = os.path.join(self.scratchWorkspace,'vertices')
        with arcpy.da.SearchCursor(inputFC,['FID','Shape@'] ) as sCursor:
            for fid,shape in sCursor:
                tic = time.perf_counter()
                arcpy.management.FeatureVerticesToPoints(shape,outFC)
                nearTable = self.generateValidNearTable(outFC)
                self.generateResultErrorFeatures(nearTable,error)
                self.log("FID {}: Vertex proximity results: {:.4f} seconds".format(fid,time.perf_counter() - tic))
                

    def generateValidNearTable(self,inputFC):
        distanceThreshold = next((param for param in self.params if param.name == 'distance_threshold'), None)
        outFC = os.path.join('memory','neartable')
        nearTable = arcpy.analysis.GenerateNearTable(inputFC,inputFC,outFC,int(distanceThreshold.valueAsText),location='LOCATION')
        #removing the records with near distance = 0 - helps in inspecting the near table for debugging
        with arcpy.da.UpdateCursor(nearTable,'*',where_clause='{} = 0'.format(NEAR_DISTANCE)) as uCur:
            for dRow in uCur:
                uCur.deleteRow()
        return nearTable
    
    def generateResultErrorFeatures(self,nearTable,error):
        fieldNames =['error_type','distance','Shape@']
        errorPointFC = os.path.join(self.workspace,self.config.get('errorPointFC'))
        errorLineFC = os.path.join(self.workspace,self.config.get('errorLineFC'))
        errorLineTempFC = os.path.join(self.scratchWorkspace,self.config.get('errorLineFC'))
        spatialRef = self.getInputSpatialReference()
        arcpy.management.XYToLine(nearTable, errorLineTempFC, FROM_X, FROM_Y, NEAR_X, NEAR_Y, 'PLANAR', spatial_reference=spatialRef,attributes=True)
        arcpy.management.DeleteIdentical(errorLineTempFC, ['SHAPE'])
        if not arcpy.Exists(errorLineFC):
            arcpy.management.CreateFeatureclass(self.workspace, self.config.get('errorLineFC'), 'POLYLINE',template=errorLineTempFC,spatial_reference = self.getInputSpatialReference())
        arcpy.management.Append(errorLineTempFC, errorLineFC, "TEST")    
        with arcpy.da.InsertCursor(errorPointFC, fieldNames) as iCursor:
            with arcpy.da.SearchCursor(errorLineFC,[NEAR_DISTANCE,'SHAPE@XY'] ) as sCursor:
                for distance,xy in sCursor:
                    midpoint = arcpy.Point(xy[0],xy[1])
                    midPointGeom = arcpy.PointGeometry(midpoint,spatialRef)
                    iCursor.insertRow((error,str(distance),midPointGeom))
        arcpy.management.DeleteIdentical(errorPointFC, ['SHAPE'])
        
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
            uid = int(time.time() * 1000)
            filePath = inputFileParam.valueAsText.strip()
            fileName = ntpath.basename(filePath)
            outGdb= '{}_DError_{}.gdb'.format(os.path.splitext(fileName)[0],uid)
            scratchGdb = 'temp_{}.gdb'.format(uid)
            if resultFolderParam and resultFolderParam.valueAsText:
                self.workspace = str(arcpy.CreateFileGDB_management(resultFolderParam.valueAsText, outGdb))
                self.scratchWorkspace = str(arcpy.CreateFileGDB_management(resultFolderParam.valueAsText, scratchGdb))
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

    def deleteTemporaryWorkspace(self):
        if arcpy.Exists(self.scratchWorkspace):
            arcpy.env.workspace = self.scratchWorkspace
            fcs = self.listFeatureClassesInWorkspace()
            for fc in fcs:
                arcpy.Delete_management(fc)
            arcpy.management.ClearWorkspaceCache(self.scratchWorkspace)
            arcpy.env.scratchWorkspace = None
            arcpy.Delete_management(self.scratchWorkspace)

    def listFeatureClassesInWorkspace(self):
        feature_classes = []
        datasets = arcpy.ListDatasets(feature_type='feature')
        datasets = [''] + datasets if datasets is not None else []
        for ds in datasets:
            for fc in arcpy.ListFeatureClasses(feature_dataset=ds):
                feature_classes.append(fc)
        
        return feature_classes