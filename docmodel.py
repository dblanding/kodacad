#!/usr/bin/env python
#
# Copyright 2022 Doug Blanding (dblanding@gmail.com)
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

from dataclasses import dataclass
import logging
import os
import os.path

from OCC.Core.BinXCAFDrivers import binxcafdrivers_DefineFormat
from OCC.Core.XmlXCAFDrivers import xmlxcafdrivers_DefineFormat
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRep import BRep_Builder
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.PCDM import PCDM_SS_OK, PCDM_RS_OK
from OCC.Core.Quantity import Quantity_Color
from OCC.Core.STEPCAFControl import (
    STEPCAFControl_Reader,
    STEPCAFControl_Writer,
)
from OCC.Core.STEPControl import STEPControl_AsIs
from OCC.Core.TCollection import TCollection_ExtendedString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDF import (
    TDF_CopyLabel,
    TDF_Label,
    TDF_LabelSequence,
    TDF_ChildIterator,
)
from OCC.Core.TDocStd import TDocStd_Document, TDocStd_XLinkTool
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape
from OCC.Core.XCAFApp import XCAFApp_Application_GetApplication
from OCC.Core.XCAFDoc import (
    XCAFDoc_ColorGen,
    XCAFDoc_ColorSurf,
    XCAFDoc_DocumentTool_ColorTool,
    XCAFDoc_DocumentTool_ShapeTool,
)
from OCC.Core.XSControl import XSControl_WorkSession
from PyQt5.QtWidgets import QFileDialog

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # set to DEBUG | INFO | ERROR


@dataclass
class Prototype:
    """A prototype shape and its associated label

    Instantiate: foo_proto = prototype(shape, label)
    Retrieve: foo_proto.shape  or  foo_proto.label
    """

    shape: TopoDS_Shape
    label: TDF_Label


def create_doc():
    """Create (and return) XCAF doc and app

    entry       label <class 'OCC.Core.TDF.TDF_Label'>
    0:1         doc.Main()                          (Depth = 1)
    0:1:1       shape_tool is at this label entry   (Depth = 2)
    0:1:2       color_tool at this entry            (Depth = 2)
    0:1:1:1     root_label and all referred shapes  (Depth = 3)
    0:1:1:x:x   component labels (references)       (Depth = 4)
    """

    # Initialize the document
    # Choose format for TDocStd_Document
    doc_format = "BinXCAF"  # Use file ext .xbf to save in binary format
    # doc_format = "XmlXCAF"  # Use file ext .xml to save in xml format
    doc = TDocStd_Document(TCollection_ExtendedString(doc_format))
    app = XCAFApp_Application_GetApplication()
    app.NewDocument(TCollection_ExtendedString(doc_format), doc)
    binxcafdrivers_DefineFormat(app)
    # xmlxcafdrivers_DefineFormat(app)
    return doc, app


class DocModel:
    """Maintain the 3D CAD model in OCAF XDE format.

    Maintains self.part_dict and self.label_dict by parsing self.doc.
    These 2 dicts provide mainwindow with convenient access to CAD data.
    With the exception of the Top assembly, each item in the tree view
    represents a component label in the OCAF document and has a uid
    comprising the label entry with an appended '.' followed by an integer.
    The integer makes each instance unique (allowing to distinguish between
    different instances of shared data)."""

    def __init__(self):

        self.doc, self.app = create_doc()

        # To be used by redraw()
        self.part_dict = {}  # {uid: {keys: 'shape', 'name', 'color', 'loc'}}
        # To be used to construct treeView & access labels
        # {uid: {keys: 'entry', 'name', 'parent_uid', 'ref_entry', 'is_assy'}}
        self.label_dict = {}
        self._share_dict = {}  # {entry: highest_serial_nmbr_used}
        self.parent_uid_stack = []  # uid of parent lineage (topmost first)
        self.assy_entry_stack = []  # entries of containing assemblies, immediate last
        self.assy_loc_stack = []  # applicable <TopLoc_Location> locations

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
        """Generate new part_dict & label_dict from self.doc

        part_dict (dict of dicts) is used primarily for 3D display
        There is a one-to-one correspondence between each 'display-able'
        part (instance) and each item in part_dict

        part_dict = {uid:  {'shape': ,
                            'name': ,
                            'color': ,
                            'loc': }}

        label_dict (dict of dicts) is used primarily for tree view display
        There is a one-to-one correspondence between each item in the
        tree view and each item in label_dict

        label_dict = {uid: {'entry': ,
                            'name': ,
                            'parent_uid': ,
                            'ref_entry': ,
                            'is_assy': ,
                            'inv_loc': }}
        """

        # Initialize dictionaries & list
        self._share_dict = {'0:1:1': 0}  # {entry: ser_nbr}
        self.part_dict = {}
        self.label_dict = {}
        # Temporary use during unpacking
        self.parent_uid_stack = []  # uid of parent (topmost first)
        self.assy_entry_stack = ['0:1:1']  # [entries of containing assemblies]
        self.assy_loc_stack = []  # applicable <TopLoc_Location> locations

        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        # shape_tool.SetAutoNaming(True)  # not sure what this does, OK w/ or w/out

        # Find root label of self.doc
        labels = TDF_LabelSequence()
        nbr = labels.Length()  # number of labels at root
        logger.debug(f"Number of labels at doc root : {nbr}")
        shape_tool.GetShapes(labels)
        root_label = labels.Value(1)  # First label at root

        # Get root label information
        # The first label at root holds an assembly, it is the Top Assy.
        # Through this label, the entire assembly is accessible.
        # There is no need to explicitly examine other labels at root.
        # Also, the first label at root (Top Assy) is the only label
        # at root represented in the tree view (in label_dict)
        root_name = root_label.GetLabelName()
        root_entry = root_label.EntryDumpToString()
        root_uid = self.get_uid_from_entry(root_entry)
        loc = shape_tool.GetLocation(root_label)  # <TopLoc_Location>
        self.assy_loc_stack.append(loc)
        self.assy_entry_stack.append(root_entry)
        self.label_dict = {root_uid: {'entry': root_entry, 'name': root_name,
                                      'parent_uid': None, 'ref_entry': None,
                                      'is_assy': True, 'inv_loc': loc.Inverted()}}
        self.parent_uid_stack.append(root_uid)
        top_comps = TDF_LabelSequence()  # Components of Top Assy
        subchilds = False
        __ = shape_tool.GetComponents(root_label, top_comps, subchilds)
        if top_comps.Length():  # if root_label is_assy:
            logger.debug("")
            logger.debug("Parsing components of label entry %s)", root_entry)
            self.parse_components(top_comps, shape_tool, color_tool)
        else:
            print("Something went wrong while parsing document.")

    def parse_components(self, comps, shape_tool, color_tool):
        """Parse components from comps (LabelSequence).

        Components of an assembly are, by definition, references which
        refer to either a simple shape or a compound shape (an assembly).
        Components are essentially 'instances' of a referred shape or
        assembly and carry a location vector specifying the location of
        the referred shape or assembly.
        The root label and all referred labels have Depth = 3
        All component labels (references) have Depth = 4
        """

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
                self.label_dict[c_uid] = {'entry': c_entry,
                                          'name': c_name,
                                          'parent_uid': self.parent_uid_stack[-1],
                                          'ref_entry': ref_entry}
                if shape_tool.IsSimpleShape(ref_label):
                    self.label_dict[c_uid].update({'is_assy': False})
                    temp_assy_loc_stack = list(self.assy_loc_stack)
                    # Multiply locations in stack sequentially to a result
                    if len(temp_assy_loc_stack) > 1:
                        res_loc = temp_assy_loc_stack.pop(0)
                        for loc in temp_assy_loc_stack:
                            res_loc = res_loc.Multiplied(loc)
                        display_shape = BRepBuilderAPI_Transform(
                            c_shape, res_loc.Transformation()).Shape()
                    elif len(temp_assy_loc_stack) == 1:
                        res_loc = temp_assy_loc_stack.pop()
                        display_shape = BRepBuilderAPI_Transform(
                            c_shape, res_loc.Transformation()).Shape()
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
                    c_loc = shape_tool.GetLocation(c_label)
                    if c_loc:
                        loc = res_loc.Multiplied(c_loc)
                    color = Quantity_Color()
                    color_tool.GetColor(ref_shape, XCAFDoc_ColorSurf, color)
                    self.part_dict[c_uid] = {'shape': display_shape,
                                             'color': color,
                                             'name': c_name,
                                             'loc': loc}
                elif shape_tool.IsAssembly(ref_label):
                    self.label_dict[c_uid].update({'is_assy': True})
                    logger.debug("Referred item is an Assembly")
                    # Location vector is carried by component
                    a_loc = shape_tool.GetLocation(c_label)
                    # store inverted location transform in label_dict for this assembly
                    inv_loc = a_loc.Inverted()
                    self.label_dict[c_uid].update({'inv_loc': inv_loc})
                    self.assy_loc_stack.append(a_loc)
                    self.assy_entry_stack.append(ref_entry)
                    self.parent_uid_stack.append(c_uid)
                    r_comps = TDF_LabelSequence()  # Components of Assy
                    subchilds = False
                    isAssy = shape_tool.GetComponents(
                        ref_label, r_comps, subchilds)
                    logger.debug("Assy name: %s", ref_name)
                    logger.debug("Is Assembly? %s", isAssy)
                    logger.debug("Number of components: %s", r_comps.Length())
                    if r_comps.Length():
                        logger.debug("")
                        logger.debug(
                            "Parsing components of label entry %s)", ref_entry)
                        self.parse_components(r_comps, shape_tool, color_tool)
            else:
                print(f"Oops! All components are *not* references {c_uid}")
        self.assy_entry_stack.pop()
        self.assy_loc_stack.pop()
        self.parent_uid_stack.pop()

    def save_step_doc(self):
        """Export self.doc to STEP file."""

        prompt = 'Specify name for saved step file.'
        fname, __ = QFileDialog.getSaveFileName(None, prompt, './',
                                                "STEP files (*.stp *.STP *.step)")
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


    def open_doc(self):
        """Open (.xbf) file, assign it to self.doc

        This doesn't work in PythonOCC.
        Use workaround: save_step_doc / load_stp_at_top
        """

        prompt = 'Choose file to open.'
        fname, __ = QFileDialog.getOpenFileName(None, prompt, './',
                                                "native CAD format (*.xbf)")
        print(f"{fname = }")
        if not fname:
            print("Open file cancelled.")
            return

        # Create document to receive data from file
        doc, app = create_doc()

        # Read file and transfer to doc
        open_status = app.Open(TCollection_ExtendedString(fname), doc)
        if open_status == PCDM_RS_OK:
            print("File opened successfully.")
            # Save new doc to have a look at it
            self.doc = doc
            save_step_doc(doc)
            self.parse_doc()
        else:
            print("Unable to open file.")

    def save_doc(self, doc=None):
        """Save doc to file in XML Format (.xbf)"""

        # Enable using this method to save a doc other than self.doc
        if not doc:
            doc = self.doc

        prompt = 'Specify name of file for saved doc.'
        save_dialog = QFileDialog()
        fname, __ = save_dialog.getSaveFileName(None, prompt, './',
                                                "native CAD format (*.xbf)")
        if not fname:
            print("Save step cancelled.")
            return

        # append ".xml" if the user didn't
        if not fname.endswith('.xbf'):
            fname += '.xbf'

        # One of the few places app is needed
        save_status = self.app.SaveAs(doc, TCollection_ExtendedString(fname))
        if save_status == PCDM_SS_OK:
            print(f"File {fname} saved successfully.")
        else:
            print("File save failed.")

    def replace_shape(self, uid, modshape):
        """Replace referred shape with modshape of component with uid

        The modified part is a located instance of a referred shape stored
        at doc root. The user doesn't have access to this root shape. In order
        to modify this referred shape, the modified instance shape is moved
        back to the original location at doc root, then saved."""

        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        # shape is stored at label entry '0:1:1:n'
        n = int(self.label_dict[uid]['ref_entry'].split(':')[-1])
        color = self.part_dict[uid]['color']
        labels = TDF_LabelSequence()
        shape_tool.GetShapes(labels)
        label = labels.Value(n)  # nth label at root

        # If shape instance was moved from its root location to its instance
        # location, 'unmove' it to relocate it back to the root location.
        if self.part_dict[uid]['loc']:
            modshape.Move(self.part_dict[uid]['loc'].Inverted())
        # Replace oldshape in self.doc
        shape_tool.SetShape(label, modshape)
        color_tool.SetColor(modshape, color, XCAFDoc_ColorGen)
        shape_tool.UpdateAssemblies()
        self.parse_doc()  # generate new part_dict

    def add_component(self, shape, name, color):
        """Add new shape to top assembly of self.doc & return uid"""

        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        try:
            root_label = labels.Value(1)  # First label at root
        except RuntimeError as e:
            print(e)
            return
        component_label = shape_tool.AddComponent(root_label, shape, True)
        entry = component_label.EntryDumpToString()
        # Get referred label and apply color to it
        ref_label = TDF_Label()  # label of referred shape
        isRef = shape_tool.GetReferredShape(component_label, ref_label)
        if isRef:
            color_tool.SetColor(ref_label, color, XCAFDoc_ColorGen)
        set_label_name(component_label, name)
        logger.info('Part %s added to root label', name)
        shape_tool.UpdateAssemblies()
        self.doc = self.doc_linter(self.doc)  # part names get hosed without this
        self.parse_doc()
        uid = self.get_uid_from_entry(entry)
        return uid

    def add_component_to_asy(self, shape, name, color, tag=1):
        """Add new shape to label at root with tag & return uid"""

        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        try:
            asyLabel = labels.Value(tag)  # label at root with tag
        except RuntimeError as e:
            print(e)
            return
        new_label = shape_tool.AddComponent(asyLabel, shape, True)
        entry = new_label.EntryDumpToString()
        # Get referred label and apply color to it
        ref_label = TDF_Label()  # label of referred shape
        isRef = shape_tool.GetReferredShape(new_label, ref_label)
        if isRef:
            color_tool.SetColor(ref_label, color, XCAFDoc_ColorGen)
        set_label_name(new_label, name)
        logger.info('Part %s added to root label', name)
        shape_tool.UpdateAssemblies()
        self.doc = doc_linter(self.doc)  # This gets color to work
        self.parse_doc()
        uid = entry + '.0'  # this should work OK since it is new
        return uid

    def change_label_name(self, uid, name):
        """Change the name of component with uid."""

        entry, __ = uid.split('.')
        entry_parts = entry.split(':')
        if len(entry_parts) == 4:  # first label at root
            j = 1
            k = None
        elif len(entry_parts) == 5:  # part is a component of label at root
            j = int(entry_parts[3])  # number of label at root
            k = int(entry_parts[4])  # component number
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        labels = TDF_LabelSequence()  # labels at root of self.doc
        shape_tool.GetShapes(labels)
        label = labels.Value(j)
        comps = TDF_LabelSequence()  # Components of root_label
        subchilds = False
        __ = shape_tool.GetComponents(label, comps, subchilds)
        target_label = comps.Value(k)
        set_label_name(target_label, name)
        shape_tool.UpdateAssemblies()
        print(f"Name {name} set for part with uid = {uid}.")
        self.parse_doc()


def set_label_name(label, name):
    TDataStd_Name.Set(label, TCollection_ExtendedString(name))


def get_name_from_uid(doc, uid):
    """Get name of label with uid."""

    entry, __ = uid.split('.')
    entry_parts = entry.split(':')
    if len(entry_parts) == 4:  # first label at root
        j = 1
        k = None
    elif len(entry_parts) == 5:  # part is a component of label at root
        j = int(entry_parts[3])  # number of label at root
        k = int(entry_parts[4])  # component number
    shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
    labels = TDF_LabelSequence()  # labels at root of self.doc
    shape_tool.GetShapes(labels)
    label = labels.Value(j)
    comps = TDF_LabelSequence()  # Components of root_label
    subchilds = False
    __ = shape_tool.GetComponents(label, comps, subchilds)
    try:
        target_label = comps.Value(k)
        return target_label.GetLabelName()
    except RuntimeError as e:
        print(f"Index out of range {e}")
        return None


def set_name_from_uid(doc, uid, name):
    """Set name of label with uid."""

    entry, __ = uid.split('.')
    entry_parts = entry.split(':')
    if len(entry_parts) == 4:  # first label at root
        j = 1
        k = None
    elif len(entry_parts) == 5:  # part is a component of label at root
        j = int(entry_parts[3])  # number of label at root
        k = int(entry_parts[4])  # component number
    shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
    labels = TDF_LabelSequence()  # labels at root of self.doc
    shape_tool.GetShapes(labels)
    label = labels.Value(j)
    comps = TDF_LabelSequence()  # Components of root_label
    subchilds = False
    __ = shape_tool.GetComponents(label, comps, subchilds)
    try:
        target_label = comps.Value(k)
        set_label_name(target_label, name)
    except RuntimeError as e:
        print(f"Index out of range {e}")
        return None


def doc_linter(doc):
    """Clean doc by cycling through a STEP save/load cycle."""

    # Create a file object to save to
    fname = "deleteme.txt"
    # Initialize STEP exporter
    WS = XSControl_WorkSession()
    step_writer = STEPCAFControl_Writer(WS, False)
    # Transfer shapes and write file
    step_writer.Transfer(doc, STEPControl_AsIs)
    status = step_writer.Write(fname)
    assert status == IFSelect_RetDone

    # Create temporary document to receive STEP data
    temp_doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    app = XCAFApp_Application_GetApplication()
    app.NewDocument(TCollection_ExtendedString(
        "MDTV-XCAF"), temp_doc)  # Was "MDTV-XCAF"
    binxcafdrivers_DefineFormat(app)

    step_reader = STEPCAFControl_Reader()
    step_reader.SetColorMode(True)
    step_reader.SetLayerMode(True)
    step_reader.SetNameMode(True)
    step_reader.SetMatMode(True)
    status = step_reader.ReadFile(fname)
    if status == IFSelect_RetDone:
        logger.info("Transfer doc to STEPCAFControl_Reader")
        step_reader.Transfer(temp_doc)
        os.remove(fname)
    return temp_doc


def copy_label_within_doc(source_label, target_label):
    """Intra-document copy (within a document)"""

    cp_label = TDF_CopyLabel()
    cp_label.Load(source_label, target_label)
    cp_label.Perform()
    return cp_label.IsDone()


def copy_label(source_label, target_label):
    """Inter-document copy (between 2 documents)"""

    XLinkTool = TDocStd_XLinkTool()
    XLinkTool.Copy(target_label, source_label)


def save_step_doc(doc):
    """Export doc to STEP file."""

    prompt = 'Specify name for saved step file.'
    fname, __ = QFileDialog.getSaveFileName(None, prompt, './',
                                            "STEP files (*.stp *.STP *.step)")
    if not fname:
        print("Save step cancelled.")
        return

    # initialize STEP exporter
    WS = XSControl_WorkSession()
    step_writer = STEPCAFControl_Writer(WS, False)

    # transfer shapes and write file
    step_writer.Transfer(doc, STEPControl_AsIs)
    status = step_writer.Write(fname)
    assert status == IFSelect_RetDone


def _load_step():
    """Allow user to select step file to load, create doc and app,

    transfer step data to doc, return step_file_name, doc, app"""

    prompt = 'Select STEP file to import'
    f_path, __ = QFileDialog.getOpenFileName(
        None, prompt, './', "STEP files (*.stp *.STP *.step)")
    base = os.path.basename(f_path)  # f_name.ext
    step_file_name, ext = os.path.splitext(base)
    logger.debug("Load file name: %s", f_path)
    if not f_path:
        print("Load step cancelled")
        return

    # Create a new instance of DocModel for the step file
    doc, app = create_doc()

    # Create and prepare step reader
    step_reader = STEPCAFControl_Reader()
    step_reader.SetColorMode(True)
    step_reader.SetLayerMode(True)
    step_reader.SetNameMode(True)
    step_reader.SetMatMode(True)

    status = step_reader.ReadFile(f_path)
    if status == IFSelect_RetDone:
        logger.info("Transfer doc to STEPCAFControl_Reader")
        step_reader.Transfer(doc)
    return step_file_name, doc, app


def load_stp_at_top(dm):
    """Get OCAF document from STEP file and assign it directly to dm.doc.

    This works as a surrogate for loading a CAD project that has previously
    been saved as a STEP file."""

    try:
        f_name, doc, app = _load_step()
    except TypeError as e:
        print("Load step cancelled")
        return
    logger.info("Transfer temp_doc to STEPCAFControl_Reader")
    dm.doc = doc
    dm.app = app
    dm.parse_doc()


def load_stp_cmpnt(dm):
    """Get OCAF document from STEP file and add (as component) to doc root.

    This is the way to load step files containing a single shape at root."""
    f_name, doc, app = _load_step()
    shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool_ColorTool(doc.Main())

    # Get root label of step data
    labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    number_free_shapes_at_root = labels.Length()
    print(f"{number_free_shapes_at_root = }")
    for j in range(number_free_shapes_at_root):
        label = labels.Value(j+1)
        shape = shape_tool.GetShape(label)
        color = Quantity_Color()
        name = label.GetLabelName()
        color_tool.GetColor(shape, XCAFDoc_ColorSurf, color)
        if shape_tool.IsSimpleShape(label):
            __ = dm.add_component(shape, name, color)


def load_stp_undr_top(dm):
    """Add step file as a component under Top (root) label of dm.doc

    In order to get this to work, I have found that I need to use BRep_Builder
    to build the root label, adding the target label to it. I haven't figured
    out how to get it to add the target label to a pre-existing root label.

    Also, it works quite well with 'as1-oc-214.stp'
    Not so well with 'as1_pe_203.stp'
    """

    # Create shape_tool for project doc
    shape_tool = XCAFDoc_DocumentTool_ShapeTool(dm.doc.Main())

    # Create target shape & label, store in prototype dataclass
    target_shape = TopoDS_Compound()
    t_builder = BRep_Builder()
    t_builder.MakeCompound(target_shape)
    target_proto = Prototype(target_shape, shape_tool.AddShape(target_shape, True))

    # Create root compound shape & label, store in prototype dataclass
    root_shape = TopoDS_Compound()
    r_builder = BRep_Builder()
    r_builder.MakeCompound(root_shape)
    r_builder.Add(root_shape, target_proto.shape)
    root_proto = Prototype(root_shape, shape_tool.AddShape(root_shape, True))
    TDataStd_Name.Set(root_proto.label, TCollection_ExtendedString("Top"))

    # Get root label of step data (source label)
    step_file_name, step_doc, step_app = _load_step()
    step_labels = TDF_LabelSequence()
    step_shape_tool = XCAFDoc_DocumentTool_ShapeTool(step_doc.Main())
    step_shape_tool.GetShapes(step_labels)
    step_root_label = step_labels.Value(1)

    # Copy source label to target label
    copy_label(step_root_label, target_proto.label)

    # Set name of step file to component referring to new target label
    itr = TDF_ChildIterator(root_proto.label, False)
    while itr.More():
        component_label = itr.Value()
        TDataStd_Name.Set(
            component_label, TCollection_ExtendedString(step_file_name))
        itr.Next()
    shape_tool.UpdateAssemblies()

    # At this point, dm.doc isn't quite right, but somehow gets 'fixed' by
    # saving to step and reloading. This also fixes part colors.
    dm.doc = doc_linter(dm.doc)

    # Build new self.part_dict & tree view
    dm.parse_doc()
