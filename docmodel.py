#!/usr/bin/env python
#
# Copyright 2020 Doug Blanding (dblanding@gmail.com)
#
# This file is part of kodacad.
# The latest  version of this file can be found at:
# //https://github.com/dblanding/kodacad
#
# kodacad is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# kodacad is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# if not, write to the Free Software Foundation, Inc.
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#

import logging
import os
import os.path

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Quantity import Quantity_Color, Quantity_ColorRGBA
from OCC.Core.STEPCAFControl import (STEPCAFControl_Reader,
                                     STEPCAFControl_Writer)
from OCC.Core.STEPControl import STEPControl_AsIs
from OCC.Core.TCollection import (TCollection_AsciiString,
                                  TCollection_ExtendedString)
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDF import (TDF_ChildIterator, TDF_CopyLabel, TDF_Label,
                          TDF_LabelSequence)
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.XCAFApp import XCAFApp_Application_GetApplication
from OCC.Core.XCAFDoc import (XCAFDoc_ColorGen, XCAFDoc_ColorSurf,
                              XCAFDoc_DocumentTool_ColorTool,
                              XCAFDoc_DocumentTool_LayerTool,
                              XCAFDoc_DocumentTool_MaterialTool,
                              XCAFDoc_DocumentTool_ShapeTool)
from OCC.Core.XmlXCAFDrivers import (XmlXCAFDrivers_DocumentRetrievalDriver,
                                     XmlXCAFDrivers_DocumentStorageDriver)
from OCC.Core.XSControl import XSControl_WorkSession
from PyQt5.QtWidgets import QFileDialog

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR) # set to DEBUG | INFO | ERROR


class TreeModel():
    """XCAF Tree Model of hierarchical CAD assembly data"""

    def __init__(self, title):
        # Create the application and document
        doc = TDocStd_Document(TCollection_ExtendedString(title))
        app = XCAFApp_Application_GetApplication()
        app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
        self.app = app
        self.doc = doc
        # Initialize tools
        self.shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
        self.shape_tool.SetAutoNaming(True)
        self.color_tool = XCAFDoc_DocumentTool_ColorTool(doc.Main())
        self.layer_tool = XCAFDoc_DocumentTool_LayerTool(doc.Main())
        self.l_materials = XCAFDoc_DocumentTool_MaterialTool(doc.Main())
        self.allChildLabels = []

    def getChildLabels(self, label):
        """Return list of child labels directly below label."""
        itlbl = TDF_ChildIterator(label, True)
        childlabels = []
        while itlbl.More():
            childlabels.append(itlbl.Value())
            itlbl.Next()
        return childlabels

    def getAllChildLabels(self, label, first=True):
        """Return list of all child labels (recursively) below label.

        This doesn't find anything at the second level down because
        the component labels of root do not have children, but rather
        they have references."""
        print("Entering 'getAllChildLabels'")
        if first:
            self.allChildLabels = []
        childLabels = self.getChildLabels(label)
        print(f"len(childLabels) = {len(childLabels)}")
        self.allChildLabels += childLabels
        print(f"len(allChildLabels) = {len(self.allChildLabels)}")
        for lbl in childLabels:
            self.getAllChildLabels(lbl, first=False)
        return self.allChildLabels

    def saveDoc(self, filename="foo.caf"):
        """Save doc to file (for educational purposes) (not working yet)

        https://www.opencascade.com/doc/occt-7.4.0/overview/html/occt_user_guides__ocaf.html#occt_ocaf_11
        """
        frmte = TCollection_ExtendedString("Xml-XCAF")
        #frmta = TCollection_AsciiString("MDTV-CAF")
        self.app.DefineFormat(TCollection_AsciiString("DocumentFormat"),
                              TCollection_AsciiString("MDTV-CAF"),
                              TCollection_AsciiString("caf"),
                              XmlXCAFDrivers_DocumentRetrievalDriver(),
                              XmlXCAFDrivers_DocumentStorageDriver(frmte))
        logger.debug("Saving doc to file")
        savefilename = TCollection_ExtendedString(filename)
        self.app.SaveAs(self.doc, savefilename)


class DocModel():
    """Maintain the 3D CAD model in OCAF TDocStd_Document format.

    Generates self.part_dict and self.uid_dict by parsing self.doc.
    These dictionaries provide mainwindow with convenient access to CAD data.
    Each item in the tree view represents a component label in the OCAF document and
    has a uid comprising the label entry appended with a '.' and an integer. The
    integer makes it unique (allowing to distinguish between different instances of
    shared data)."""

    def __init__(self):
        self.doc = self.createDoc()
        # To be used by redraw()
        self.part_dict = {}  # {uid: {keys: 'shape', 'name', 'color', 'loc'}}
        # To be used to construct treeView & access labels
        self.uid_dict = {}  # {uid: {keys: 'entry', 'name', 'parent_uid', 'ref_entry'}}
        self._share_dict = {}  # {entry: highest_serial_nmbr_used}
        self.parent_uid_stack = []  # uid of parent lineage (topmost first)
        self.assy_entry_stack = []  # entries of containing assemblies, immediate last
        self.assy_loc_stack = []  # applicable <TopLoc_Location> locations

    def createDoc(self):
        """Create XCAF doc with an empty assembly at entry 0:1:1:1.

        This is done only once in __init__."""

        # Create the application and document with empty rootLabel
        title = "Main document"
        doc = TDocStd_Document(TCollection_ExtendedString(title))
        app = XCAFApp_Application_GetApplication()
        app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(doc.Main())
        # type(doc.Main()) = <class 'OCC.Core.TDF.TDF_Label'>
        # doc.Main().EntryDumpToString() 0:1
        # shape_tool is at label entry = 0:1:1
        # Create empty rootLabel entry = 0:1:1:1
        rootLabel = shape_tool.NewShape()
        self.setLabelName(rootLabel, "Top")
        return doc

    def get_uid_from_entry(self, entry):
        """Generate uid from label entry. format: 'entry.serial_number' """
        if entry in self._share_dict:
            value = self._share_dict[entry]
        else:
            value = -1
        value += 1
        # update serial number in self._share_dict
        self._share_dict[entry] = value
        return entry + '.' + str(value)

    def parse_doc(self):
        """Generate new part_dict & uid_dict.

        part_dict (dict of dicts) is used primarily for 3D display
        part_dict = {uid: {'shape': ,
                            'name': ,
                            'color': }}
        uid_dict (dict of dicts) is used primarily for tree view display
        uid_dict = {uid:   {'entry': ,
                            'name': ,
                            'parent_uid': ,
                            'ref_entry': ,
                            'is_assy': }}
        """

        # Initialize dictionaries & list
        self._share_dict = {'0:1:1': 0}  # {entry: ser_nbr}
        self.part_dict = {}
        self.uid_dict = {}
        # Temporary use during unpacking
        self.parent_uid_stack = []  # uid of parent (topmost first)
        self.assy_entry_stack = ['0:1:1']  # [entries of containing assemblies]
        self.assy_loc_stack = []  # applicable <TopLoc_Location> locations

        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())

        # Find root label of self.doc
        labels = TDF_LabelSequence()
        shape_tool.GetShapes(labels)
        root_label = labels.Value(1) # First label at root
        nbr = labels.Length()  # number of labels at root
        logger.debug('Number of labels at doc root : %i', nbr)
        # Get root label information
        # The first label at root holds an assembly, it is the Top Assy.
        # Through this label, the entire assembly is accessible.
        # There is no need to explicitly examine other labels at root.
        root_name = root_label.GetLabelName()
        root_entry = root_label.EntryDumpToString()
        root_uid = self.get_uid_from_entry(root_entry)
        loc = shape_tool.GetLocation(root_label)  # <TopLoc_Location>
        self.assy_loc_stack.append(loc)
        self.assy_entry_stack.append(root_entry)
        self.uid_dict = {root_uid: {'entry': root_entry, 'name': root_name,
                                    'parent_uid': None, 'ref_entry': None,
                                    'is_assy': True}}
        self.parent_uid_stack.append(root_uid)
        top_comps = TDF_LabelSequence() # Components of Top Assy
        subchilds = False
        is_assy = shape_tool.GetComponents(root_label, top_comps, subchilds)
        if top_comps.Length():  # if is_assy:
            logger.debug("")
            logger.debug("Parsing components of label entry %s)", root_entry)
            self.parse_components(top_comps, shape_tool, color_tool)
        else:
            print("Something is wrong.")

    def parse_components(self, comps, shape_tool, color_tool):
        """Parse components from comps (LabelSequence).

        Components of an assembly are, by definition, references which refer
        to either a simple shape or a compound shape (an assembly).
        Components are essentially 'instances' of the referred shape or assembly
        and carry a location vector specifing the location of the referred
        shape or assembly."""

        for j in range(comps.Length()):
            logger.debug("Assy_entry_stack: %s", self.assy_entry_stack)
            logger.debug("loop %i of %i", j+1, comps.Length())
            c_label = comps.Value(j+1)  # component label <class 'TDF_Label'>
            c_name = c_label.GetLabelName()
            c_entry = c_label.EntryDumpToString()
            c_uid = self.get_uid_from_entry(c_entry)
            c_shape = shape_tool.GetShape(c_label)
            logger.debug("Component number %i", j+1)
            logger.debug("Component name: %s", c_name)
            logger.debug("Component entry: %s", c_entry)
            ref_label = TDF_Label()  # label of referred shape (or assembly)
            is_ref = shape_tool.GetReferredShape(c_label, ref_label)
            if is_ref:  # I think all components are references
                ref_name = ref_label.GetLabelName()
                ref_shape = shape_tool.GetShape(ref_label)
                ref_entry = ref_label.EntryDumpToString()
                self.uid_dict[c_uid] = {'entry': c_entry,
                                        'name': c_name,
                                        'parent_uid': self.parent_uid_stack[-1],
                                        'ref_entry': ref_entry}
                if shape_tool.IsSimpleShape(ref_label):
                    self.uid_dict[c_uid].update({'is_assy': False})
                    temp_assy_loc_stack = list(self.assy_loc_stack)
                    # Multiply locations in stack sequentially to a result
                    if len(temp_assy_loc_stack) > 1:
                        res_loc = temp_assy_loc_stack.pop(0)
                        for loc in temp_assy_loc_stack:
                            res_loc = res_loc.Multiplied(loc)
                        c_shape.Move(res_loc)
                    elif len(temp_assy_loc_stack) == 1:
                        res_loc = temp_assy_loc_stack.pop()
                        c_shape.Move(res_loc)
                    else:
                        res_loc = None
                    # It is possible for this component to both specify a
                    # location 'c_loc' and refer directly to a top level shape.
                    # If this component *does* specify a location 'c_loc',
                    # it will be applied to the referred shape without being
                    # included in temp_assy_loc_stack. But in order to keep
                    # track of the total location from the root shape to the
                    # instance, it needs to be accounted for (by mutiplying
                    # res_loc by it) before saving it to part_dict.
                    c_loc = None
                    c_loc = shape_tool.GetLocation(c_label)
                    if c_loc:
                        loc = res_loc.Multiplied(c_loc)
                    color = Quantity_Color()
                    color_tool.GetColor(ref_shape, XCAFDoc_ColorSurf, color)
                    self.part_dict[c_uid] = {'shape': c_shape,
                                             'color': color,
                                             'name': c_name,
                                             'loc': loc}
                elif shape_tool.IsAssembly(ref_label):
                    self.uid_dict[c_uid].update({'is_assy': True})
                    logger.debug("Referred item is an Assembly")
                    # Location vector is carried by component
                    aLoc = TopLoc_Location()
                    aLoc = shape_tool.GetLocation(c_label)
                    self.assy_loc_stack.append(aLoc)
                    self.assy_entry_stack.append(ref_entry)
                    self.parent_uid_stack.append(c_uid)
                    r_comps = TDF_LabelSequence() # Components of Assy
                    subchilds = False
                    isAssy = shape_tool.GetComponents(ref_label, r_comps, subchilds)
                    logger.debug("Assy name: %s", ref_name)
                    logger.debug("Is Assembly? %s", isAssy)
                    logger.debug("Number of components: %s", r_comps.Length())
                    if r_comps.Length():
                        logger.debug("")
                        logger.debug("Parsing components of label entry %s)", ref_entry)
                        self.parse_components(r_comps, shape_tool, color_tool)
            else:
                print(f"I was wrong: All components are *not* references {c_uid}")
        self.assy_entry_stack.pop()
        self.assy_loc_stack.pop()
        self.parent_uid_stack.pop()

    def doc_linter(self, doc=None):
        """Clean self.doc by cycling through a STEP save/load cycle."""

        if doc is None:
            doc = self.doc
        # Create a file object to save to
        fname = "deleteme.txt"
        # Initialize STEP exporter
        WS = XSControl_WorkSession()
        step_writer = STEPCAFControl_Writer(WS, False)
        # Transfer shapes and write file
        step_writer.Transfer(doc, STEPControl_AsIs)
        status = step_writer.Write(fname)
        assert status == IFSelect_RetDone
        # Create new TreeModel and read STEP data
        tmodel = TreeModel("DOC")
        shape_tool = tmodel.shape_tool
        color_tool = tmodel.color_tool
        step_reader = STEPCAFControl_Reader()
        step_reader.SetColorMode(True)
        step_reader.SetLayerMode(True)
        step_reader.SetNameMode(True)
        step_reader.SetMatMode(True)
        status = step_reader.ReadFile(fname)
        if status == IFSelect_RetDone:
            logger.info("Transfer doc to STEPCAFControl_Reader")
            step_reader.Transfer(tmodel.doc)
            os.remove(fname)
        return tmodel.doc

    def copy_label(self, source_label, target_label):
        cp_label = TDF_CopyLabel()
        cp_label.Load(source_label, target_label)
        cp_label.Perform()
        return cp_label.IsDone()

    def load_stp_at_top(self):
        """Get OCAF document from STEP file and assign it directly to self.doc.

        This works as a surrogate for loading a CAD project that has previously
        been saved as a STEP file."""

        prompt = 'Select STEP file to import'
        fnametuple = QFileDialog.getOpenFileName(None, prompt, './',
                                                 "STEP files (*.stp *.STP *.step)")
        fname, _ = fnametuple
        logger.debug("Load file name: %s", fname)
        if not fname:
            print("Load step cancelled")
            return
        tmodel = TreeModel("DOC")
        step_shape_tool = tmodel.shape_tool
        step_color_tool = tmodel.color_tool

        step_reader = STEPCAFControl_Reader()
        step_reader.SetColorMode(True)
        step_reader.SetLayerMode(True)
        step_reader.SetNameMode(True)
        step_reader.SetMatMode(True)

        status = step_reader.ReadFile(fname)
        if status == IFSelect_RetDone:
            logger.info("Transfer doc to STEPCAFControl_Reader")
            step_reader.Transfer(tmodel.doc)
        self.doc = tmodel.doc
        # Build new self.part_dict & self.uid_dict
        self.parse_doc()

    def load_stp_cmpnt(self):
        """Get OCAF document from STEP file and add (as component) to doc root.

        This is the way to load step files containing a single shape at root."""

        prompt = 'Select STEP file to import'
        fnametuple = QFileDialog.getOpenFileName(None, prompt, './',
                                                 "STEP files (*.stp *.STP *.step)")
        fname, _ = fnametuple
        logger.debug("Load file name: %s", fname)
        if not fname:
            print("Load step cancelled")
            return
        tmodel = TreeModel("DOC")
        step_shape_tool = tmodel.shape_tool
        step_color_tool = tmodel.color_tool

        step_reader = STEPCAFControl_Reader()
        step_reader.SetColorMode(True)
        step_reader.SetLayerMode(True)
        step_reader.SetNameMode(True)
        step_reader.SetMatMode(True)

        status = step_reader.ReadFile(fname)
        if status == IFSelect_RetDone:
            logger.info("Transfer doc to STEPCAFControl_Reader")
            step_reader.Transfer(tmodel.doc)
        # Get root label of step data
        labels = TDF_LabelSequence()
        step_shape_tool.GetShapes(labels)
        for j in range(labels.Length()):
            label = labels.Value(j+1)
            shape = step_shape_tool.GetShape(label)
            color = Quantity_Color()
            name = label.GetLabelName()
            step_color_tool.GetColor(shape, XCAFDoc_ColorSurf, color)
            isSimpleShape = step_shape_tool.IsSimpleShape(label)
            if isSimpleShape:
                _ = self.addComponent(shape, name, color)

    def load_stp_undr_top(self):
        """Paste step root label under 1st label at self.doc root

        Add a simple component to the first label at self.doc root.
        Set the component name to be the name of the step file.
        Then assign the label of the referred shape to 'targetLabel'.
        Finally, copy step root label onto 'targetLabel'.

        This works when copying file 'as1-oc-214.stp' to 0:1:1:2 (n=2) but does
        not get part color at higher values of n. Also doesn't work with file
        'as1_pe_203.stp' loaded at any value of n. ???
        """

        prompt = 'Select STEP file to import'
        fnametuple = QFileDialog.getOpenFileName(None, prompt, './',
                                                 "STEP files (*.stp *.STP *.step)")
        fname, _ = fnametuple  # fname = /path/to/some/filename.ext
        base = os.path.basename(fname)  # filename.ext
        filename, ext = os.path.splitext(base)
        logger.debug("Load file name: %s", fname)
        if not fname:
            print("Load step cancelled")
            return
        # Get the step data
        tmodel = TreeModel("STEP")
        step_shape_tool = tmodel.shape_tool
        step_color_tool = tmodel.color_tool

        step_reader = STEPCAFControl_Reader()
        step_reader.SetColorMode(True)
        step_reader.SetLayerMode(True)
        step_reader.SetNameMode(True)
        step_reader.SetMatMode(True)

        status = step_reader.ReadFile(fname)
        if status == IFSelect_RetDone:
            logger.info("Transfer doc to STEPCAFControl_Reader")
            step_reader.Transfer(tmodel.doc)
        # Delint tmodel.doc & make new tools
        step_doc = self.doc_linter(tmodel.doc)
        step_shape_tool = XCAFDoc_DocumentTool_ShapeTool(step_doc.Main())
        step_color_tool = XCAFDoc_DocumentTool_ColorTool(step_doc.Main())

        # Get root label of step data
        step_labels = TDF_LabelSequence()
        step_shape_tool.GetShapes(step_labels)
        steprootLabel = step_labels.Value(1)
        # Make a simple box and add it as a component
        myBody = BRepPrimAPI_MakeBox(4, 4, 4).Shape()
        _ = self.addComponent(myBody, filename, Quantity_ColorRGBA())
        step_shape_tool.UpdateAssemblies()
        # Get target label of self.doc
        labels = TDF_LabelSequence()  # labels at root
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        n = labels.Length()   # number of labels at root
        print(n)
        targetLabel = labels.Value(n)  # of ref shape of comp just added
        # Copy source label to target label
        self.copy_label(steprootLabel, targetLabel)
        shape_tool.UpdateAssemblies()
        # Repair self.doc by cycling through save/load
        self.doc = self.doc_linter()
        # Build new self.part_dict & tree view
        self.parse_doc()

    def saveStepDoc(self):
        """Export self.doc to STEP file."""

        prompt = 'Choose filename for step file.'
        fnametuple = QFileDialog.getSaveFileName(None, prompt, './',
                                                 "STEP files (*.stp *.STP *.step)")
        fname, _ = fnametuple
        if not fname:
            print("Save step cancelled.")
            return

        # initialize STEP exporter
        WS = XSControl_WorkSession()
        step_writer = STEPCAFControl_Writer(WS, False)

        # transfer shapes and write file
        step_writer.Transfer(self.doc, STEPControl_AsIs)
        status = step_writer.Write(fname)
        assert status == IFSelect_RetDone

    def replaceShape(self, uid, modshape):
        """Replace referred shape with modshape of component with uid

        The modified part is a located instance of a referred shape stored
        at doc root. The user doesn't have access to this root shape. In order
        to modify this referred shape, the modified instance shape is moved
        back to the original location at doc root, then saved."""
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        # shape is stored at label entry '0:1:1:n'
        n = int(self.uid_dict[uid]['ref_entry'].split(':')[-1])
        labels = TDF_LabelSequence()
        shape_tool.GetShapes(labels)
        label = labels.Value(n)  # nth label at root

        # If shape instance was moved from its root location to its instance
        # location, 'unmove' it to relocate it back to the root location.
        if self.part_dict[uid]['loc']:
            modshape.Move(self.part_dict[uid]['loc'].Inverted())
        # Replace oldshape in self.doc
        shape_tool.SetShape(label, modshape)
        shape_tool.UpdateAssemblies()
        self.parse_doc()  # generate new part_dict

    def addComponent(self, shape, name, color):
        """Add new shape to top assembly of self.doc & return uid"""
        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        try:
            rootLabel = labels.Value(1) # First label at root
        except RuntimeError as e:
            print(e)
            return
        newLabel = shape_tool.AddComponent(rootLabel, shape, True)
        # Get referrred label and apply color to it
        refLabel = TDF_Label()  # label of referred shape
        isRef = shape_tool.GetReferredShape(newLabel, refLabel)
        if isRef:
            color_tool.SetColor(refLabel, color, XCAFDoc_ColorGen)
        self.setLabelName(newLabel, name)
        logger.info('Part %s added to root label', name)
        shape_tool.UpdateAssemblies()
        self.doc = self.doc_linter()  # This gets color to work
        self.parse_doc()
        entry = newLabel.EntryDumpToString()
        uid = entry + '.0'  # this should work OK since it is new
        return uid

    def getLabelName(self, label):
        return label.GetLabelName()

    def setLabelName(self, label, name):
        TDataStd_Name.Set(label, TCollection_ExtendedString(name))

    def change_label_name(self, uid, name):
        """Change the name of component with uid."""
        entry, _ = uid.split('.')
        entry_parts = entry.split(':')
        if len(entry_parts) == 4:  # first label at root
            j = 1
            k = None
        elif len(entry_parts) == 5:  # part is a component of label at root
            j = int(entry_parts[3])  # number of label at root
            k = int(entry_parts[4])  # component number
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        labels = TDF_LabelSequence()  # labels at root of self.doc
        shape_tool.GetShapes(labels)
        label = labels.Value(j)
        comps = TDF_LabelSequence()  # Components of root_label
        subchilds = False
        is_assy = shape_tool.GetComponents(label, comps, subchilds)
        target_label = comps.Value(k)
        self.setLabelName(target_label, name)
        shape_tool.UpdateAssemblies()
        print(f"Name {name} set for part with uid = {uid}.")
        self.parse_doc()
