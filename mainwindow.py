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

from collections import defaultdict
import logging
import os
import os.path
import sys
from PyQt5.QtCore import Qt, QPersistentModelIndex, QModelIndex
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (QLabel, QMainWindow, QTreeWidget, QMenu,
                             QDockWidget, QDesktopWidget, QToolButton,
                             QLineEdit, QTreeWidgetItem, QAction, QFrame,
                             QToolBar, QFileDialog, QAbstractItemView,
                             QInputDialog, QTreeWidgetItemIterator)
from OCC.Core.AIS import AIS_Shape, AIS_Line, AIS_Circle
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.CPnts import CPnts_AbscissaPoint_Length
from OCC.Core.gp import gp_Vec
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.Prs3d import Prs3d_LineAspect
from OCC.Core.Quantity import (Quantity_Color, Quantity_NOC_GRAY,
                               Quantity_NOC_DARKGREEN, Quantity_NOC_MAGENTA1)
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCC.Core.TCollection import TCollection_ExtendedString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TDF import TDF_LabelSequence, TDF_Label, TDF_CopyLabel
from OCC.Core.TopoDS import topods_Edge, topods_Vertex, TopoDS_Compound
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.XCAFApp import XCAFApp_Application_GetApplication
from OCC.Core.XCAFDoc import (XCAFDoc_DocumentTool_ShapeTool,
                              XCAFDoc_DocumentTool_ColorTool,
                              XCAFDoc_ColorGen, XCAFDoc_ColorSurf)
from OCC.Core.XSControl import XSControl_WorkSession
import OCC.Display.OCCViewer
import OCC.Display.backend
used_backend = OCC.Display.backend.load_backend()
# from OCC.Display import qtDisplay
# import local version instead (allows changing rotate/pan/zoom controls)
from OCC import VERSION
import myDisplay.qtDisplay as qtDisplay
import rpnCalculator
from treemodel import TreeModel
from version import APP_VERSION
print("OCC version: %s" % VERSION)


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR) # set to DEBUG | INFO | ERROR


class TreeView(QTreeWidget): # With 'drag & drop' ; context menu
    """ Display assembly structure.

    TO DO: This Part/Assy tree view GUI and the XCAF data model need to be
    maintained in sync with each other. That's not happening right now.
    While it is very slick (from the user's perspective) to be able to edit
    the assembly structure using 'drag & drop' of parts and assemblies within
    the QTreeWidget Part/Assy view, it's no simple task to keep the model in
    sync. There are some moves that need to be prohibited, such as moving an
    item into a child relationship with an item that is not an assembly.
    Currently, 'drag and drop' changes in the GUI are not propagated to the
    XCAF data model. As an alternative to 'drag & drop', consider adding an
    option to the RMB pop-up to change the parent of a QTreeWidgetItem.
    """

    def __init__(self, parent=None):
        QTreeWidget.__init__(self, parent)
        self.header().setHidden(True)
        self.setSelectionMode(self.ExtendedSelection)
        self.setDragDropMode(self.InternalMove)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.contextMenu)
        self.popMenu = QMenu(self)

    def contextMenu(self, point):
        self.menu = QMenu()
        action = self.popMenu.exec_(self.mapToGlobal(point))

    def dropEvent(self, event):
        if event.source() == self:
            QAbstractItemView.dropEvent(self, event)

    def dropMimeData(self, parent, row, data, action):
        if action == Qt.MoveAction:
            return self.moveSelection(parent, row)
        return False

    def moveSelection(self, parent, position):
    # save the selected items
        selection = [QPersistentModelIndex(i)
                     for i in self.selectedIndexes()]
        parent_index = self.indexFromItem(parent)
        if parent_index in selection:
            return False
        # save the drop location in case it gets moved
        target = self.model().index(position, 0, parent_index).row()
        if target < 0:
            target = position
        # remove the selected items
        taken = []
        for index in reversed(selection):
            item = self.itemFromIndex(QModelIndex(index))
            if item is None or item.parent() is None:
                taken.append(self.takeTopLevelItem(index.row()))
            else:
                taken.append(item.parent().takeChild(index.row()))
        # insert the selected items at their new positions
        while taken:
            if position == -1:
                # append the items if position not specified
                if parent_index.isValid():
                    parent.insertChild(parent.childCount(), taken.pop(0))
                else:
                    self.insertTopLevelItem(self.topLevelItemCount(),
                                            taken.pop(0))
            else:
                # insert the items at the specified position
                if parent_index.isValid():
                    parent.insertChild(min(target, parent.childCount()),
                                       taken.pop(0))
                else:
                    self.insertTopLevelItem(min(target, self.topLevelItemCount()),
                                            taken.pop(0))
        return True

class MainWindow(QMainWindow):
    """Main GUI window containing a tree view of assy/parts and a 3D display.

    self.doc holds the 3D CAD model in OCAF TDocStd_Document format.
    It is read by parse_doc and parse_components methods, generating the
    items in the tree view and building uid_dict and part_dict to store the
    data with more convenient access.
    Each tree view item represents a label in the OCAF document and has a uid
    comprising the label entry appended with a '.' and an integer. The integer
    is needed to make it unique (allowing to distinguish between different
    instances of shared data).
    The tree view represents the hierarchical structure of the top assembly
    and its components. Each componenent refers to a label at the root level
    which is either a part or another assembly.
    """

    def __init__(self, *args):
        super().__init__()
        self.canva = qtDisplay.qtViewer3d(self)
        # Renaming self.canva._display (like below) doesn't work.
        # self.display = self.canva._display
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.contextMenu)
        self.popMenu = QMenu(self)
        title = f"KodaCAD {APP_VERSION} "
        title += f"(Using: PythonOCC version {VERSION} with PyQt5 backend)"
        self.setWindowTitle(title)
        self.resize(960, 720)
        self.setCentralWidget(self.canva)
        self.createDockWidget()
        self.wcToolBar = QToolBar("2D")  # Construction toolbar
        self.addToolBar(Qt.RightToolBarArea, self.wcToolBar)
        self.wcToolBar.setMovable(True)
        self.wgToolBar = QToolBar("2D")  # Geom Profile toolbar
        self.addToolBar(Qt.RightToolBarArea, self.wgToolBar)
        self.wgToolBar.setMovable(True)
        self.menu_bar = self.menuBar()
        self._menus = {}
        self._menu_methods = {}
        self.centerOnScreen()

        self.calculator = None

        self.assy_root, self.wp_root = self.create_root_items()
        self.itemClicked = None   # TreeView item that has been mouse clicked

        # Internally, everything is always in mm
        # scale user input and output values
        # (user input values) * unitscale = value in mm
        # (output values) / unitscale = value in user's units
        self._unitDict = {'mm': 1.0, 'in': 25.4, 'ft': 304.8}
        self.units = 'mm'
        self.unitscale = self._unitDict[self.units]
        self.unitsLabel = QLabel()
        self.unitsLabel.setText("Units: %s " % self.units)
        self.unitsLabel.setFrameStyle(QFrame.StyledPanel|QFrame.Sunken)

        self.endOpButton = QToolButton()
        self.endOpButton.setText('End Operation')
        self.endOpButton.clicked.connect(self.clearCallback)
        self.currOpLabel = QLabel()
        self.registeredCallback = None
        self.currOpLabel.setText("Current Operation: %s " % self.registeredCallback)

        self.lineEdit = QLineEdit()
        self.lineEdit.returnPressed.connect(self.appendToStack)

        status = self.statusBar()
        status.setSizeGripEnabled(False)
        status.addPermanentWidget(self.lineEdit)
        status.addPermanentWidget(self.currOpLabel)
        status.addPermanentWidget(self.endOpButton)
        status.addPermanentWidget(self.unitsLabel)
        status.showMessage("Ready", 5000)

        self.draw_list = []     # list of part uid's to be displayed
        self.floatStack = []    # storage stack for floating point values
        self.xyPtStack = []     # storage stack for 2d points (x, y)
        self.ptStack = []       # storage stack for gp_Pnts
        self.edgeStack = []     # storage stack for edge picks
        self.faceStack = []     # storage stack for face picks
        self.shapeStack = []    # storage stack for shape picks
        self.lineEditStack = [] # list of user inputs

        self.activePart = None  # <TopoDS_Shape> object
        self.activePartUID = 0
        self.part_dict = {}     # {uid: {dict keys: 'shape', 'name', 'color', 'loc'}}
        self.uid_dict = {}      # {uid: {dict keys: 'entry', 'name', 'ref_entry'}}
        self.transparency_dict = {}  # {uid: part display transparency}
        self.ancestor_dict = defaultdict(list)  # {uid: [list of ancestor shapes]}

        self.activeWp = None    # WorkPlane object
        self.activeWpUID = 0
        self.wp_dict = {}       # k = uid, v = wpObject
        self._wpNmbr = 1

        self.activeAsyUID = 0
        self.assy_list = []     # list of assy uid's
        self.showItemActive(0)
        self.doc, self.shape_tool, self.color_tool, self.rootLabel = self.createDoc()
        self.activeAsy = self.setActiveAsy(self.activeAsyUID)
        self.default_color = OCC.Display.OCCViewer.rgb_color(.2, .1, .1)

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
        self.setLabelName(rootLabel, "/")
        return (doc, shape_tool, color_tool, rootLabel)

    def createDockWidget(self):
        self.treeDockWidget = QDockWidget("Assy/Part Structure", self)
        self.treeDockWidget.setObjectName("treeDockWidget")
        self.treeDockWidget.setAllowedAreas(Qt.LeftDockWidgetArea| Qt.RightDockWidgetArea)
        self.treeView = TreeView()   # Assy/Part structure (display)
        self.treeView.itemClicked.connect(self.treeViewItemClicked)
        self.treeDockWidget.setWidget(self.treeView)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.treeDockWidget)

    ####  PyQt menuBar & general methods:

    def centerOnScreen(self):
        '''Centers the window on the screen.'''
        resolution = QDesktopWidget().screenGeometry()
        self.move((resolution.width() / 2) - (self.frameSize().width() / 2),
                  (resolution.height() / 2) - (self.frameSize().height() / 2))

    def add_menu(self, menu_name):
        _menu = self.menu_bar.addMenu("&"+menu_name)
        self._menus[menu_name] = _menu

    def add_function_to_menu(self, menu_name, text, _callable):
        assert callable(_callable), 'the function supplied is not callable'
        try:
            _action = QAction(text, self)
            # if not, the "exit" action is now shown...
            # Qt is trying so hard to be native cocoa'ish that its a nuisance
            _action.setMenuRole(QAction.NoRole)
            _action.triggered.connect(_callable)
            self._menus[menu_name].addAction(_action)
        except KeyError:
            raise ValueError('the menu item %s does not exist' % (menu_name))

    def closeEvent(self, event):    # things that need to happen on exit
        try:
            self.calculator.close()
        except AttributeError:
            pass
        event.accept()

    #############################################
    #
    # 'treeView' (QTreeWidget) related methods:
    #
    #############################################

    def addItemToTreeView(self, name, uid):
        itemName = [name, str(uid)]
        item = QTreeWidgetItem(self.assy_root, itemName)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)

    def clearTree(self):
        """Remove all tree view widget items and replace root item"""
        self.treeView.clear()
        self.assy_root, self.wp_root = self.create_root_items()
        self.repopulate_2D_tree_view()

    def create_root_items(self):
        """Create '2D' & '3D' root items in treeView."""
        root_item = ['/', '0']  # [name, uid]
        tree_view_root = QTreeWidgetItem(self.treeView, root_item)
        self.treeView.expandItem(tree_view_root)
        wp_root = QTreeWidgetItem(tree_view_root, ['2D', 'wp0'])
        self.treeView.expandItem(wp_root)
        ay_root = QTreeWidgetItem(tree_view_root, ['3D', '0:1:1.0'])
        self.treeView.expandItem(ay_root)
        return (ay_root, wp_root)

    def repopulate_2D_tree_view(self):
        """Add all workplanes to 2D section of tree view."""

        # add items to treeView
        for uid in self.wp_dict:
            itemName = [uid, uid]
            item = QTreeWidgetItem(self.wp_root, itemName)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)

    def contextMenu(self, point):
        self.menu = QMenu()
        action = self.popMenu.exec_(self.mapToGlobal(point))

    def treeViewItemClicked(self, item):  # called whenever treeView item is clicked
        self.itemClicked = item # store item
        if not self.inSync():   # click may have been on checkmark. Update draw_list (if needed)
            self.syncDrawListToChecked()
            self.redraw()

    def checkedToList(self):
        """Returns list of uid's of checked (part) items in treeView"""
        dl = []
        for item in self.treeView.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            if item.checkState(0) == Qt.Checked:
                uid = item.text(1)
                if (uid in self.part_dict) or (uid in self.wp_dict):
                    dl.append(uid)
        return dl

    def inSync(self):
        """Return True if checked items are in sync with draw_list."""
        return self.checkedToList() == self.draw_list

    def syncDrawListToChecked(self):
        self.draw_list = self.checkedToList()

    def syncCheckedToDrawList(self):
        for item in self.treeView.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            uid = item.text(1)
            if (uid in self.part_dict) or (uid in self.wp_dict):
                if uid in self.draw_list:
                    item.setCheckState(0, Qt.Checked)
                else:
                    item.setCheckState(0, Qt.Unchecked)

    def sortViewItems(self):
        """Return dicts of view items sorted by type: (prt, ay, wp)"""
        # Traverse all treeView items
        iterator = QTreeWidgetItemIterator(self.treeView)
        pdict = {}  # part-types    {uid: item}
        adict = {}  # asy-types     {uid: item}
        wdict = {}  # wp-types      {uid: item}
        while iterator.value():
            item = iterator.value()
            name = item.text(0)
            uid = item.text(1)
            if uid in self.part_dict:
                pdict[uid] = item
            elif uid in self.assy_list:
                adict[uid] = item
            elif uid in self.wp_dict:
                wdict[uid] = item
            iterator += 1
        return (pdict, adict, wdict)

    def showClickedInfo(self):
        """Show info for item clicked in treeView."""
        item = self.itemClicked
        if item:
            self.showItemInfo(item)

    def showItemInfo(self, item):
        """Show info for item clicked in treeView."""
        if item:
            name = item.text(0)
            uid = item.text(1)
            entry = self.uid_dict[uid]['entry']
            ref_ent = self.uid_dict[uid]['ref_entry']
            print(f"uid: {uid}; name: {name}; entry: {entry}; ref_entry: {ref_ent}")

    def setClickedActive(self):
        """Set item clicked in treeView Active."""
        item = self.itemClicked
        if item:
            self.setItemActive(item)
            self.treeView.clearSelection()
            self.itemClicked = None

    def setItemActive(self, item):
        """From tree view item, set (part, wp or assy) to be active."""
        if item:
            name = item.text(0)
            uid = item.text(1)
            print(f"Part selected: {name}, UID: {uid}")
            pd, ad, wd = self.sortViewItems()
            if uid in pd:
                self.setActivePart(uid)
                sbText = f"{name} [uid={uid}] is now the active part"
                #self.redraw()
            elif uid in wd:
                self.setActiveWp(uid)
                sbText = f"{name} [uid={uid}] is now the active workplane"
                self.redraw()  # update color of new active wp
            elif uid in ad:
                self.setActiveAsy(uid)
                sbText = f"{name} [uid={uid}] is now the active assembly"
            self.statusBar().showMessage(sbText, 5000)

    def showItemActive(self, uid):
        """Update tree view to show active status of (uid)."""
        pd, ad, wd = self.sortViewItems()
        if uid in pd:
            # Clear BG color of all part items
            for itm in pd.values():
                itm.setBackground(0, QBrush(QColor(255, 255, 255, 0)))
            # Set BG color of new active part
            pd[uid].setBackground(0, QBrush(QColor('gold')))
        elif uid in wd:
            # Clear BG color of all wp items
            for itm in wd.values():
                itm.setBackground(0, QBrush(QColor(255, 255, 255, 0)))
            # Set BG color of new active wp
            wd[uid].setBackground(0, QBrush(QColor('lightgreen')))
        elif uid in ad:
            # Clear BG color of all asy items
            for itm in ad.values():
                itm.setBackground(0, QBrush(QColor(255, 255, 255, 0)))
            # Set BG color of new active asy
            ad[uid].setBackground(0, QBrush(QColor('lightblue')))

    def setTransparent(self):
        item = self.itemClicked
        if item:
            uid = item.text(1)
            if uid in self.part_dict:
                self.transparency_dict[uid] = 0.6
                self.redraw()
            self.itemClicked = None

    def setOpaque(self):
        item = self.itemClicked
        if item:
            uid = item.text(1)
            if uid in self.part_dict:
                self.transparency_dict.pop(uid)
                self.redraw()
            self.itemClicked = None

    def editName(self): # Edit name of item clicked in treeView
        item = self.itemClicked
        sbText = '' # status bar text
        if item:
            name = item.text(0)
            uid = item.text(1)
            prompt = 'Enter new name for part %s' % name
            newName, OK = QInputDialog.getText(self, 'Input Dialog',
                                               prompt, text=name)
            if OK:
                item.setText(0, newName)
                print(f"UID= {uid}, name = {newName}")
        self.treeView.clearSelection()
        self.itemClicked = None
        self.set_label_name(uid, newName)

    def set_label_name(self, uid, name):
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
        self.parse_doc(tree=True)

    #############################################
    #
    # Administrative and data management methods:
    #
    #############################################

    def get_wp_uid(self, wp_objct):
        """
        Assign a uid to a new workplane & add to tree view (2D).
        """

        # Update appropriate dictionaries
        uid = "wp%i" % self._wpNmbr
        self._wpNmbr += 1
        self.wp_dict[uid] = wp_objct # wpObject
        # add item to treeView
        itemName = [uid, uid]
        item = QTreeWidgetItem(self.wp_root, itemName)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        # Make new workplane active
        self.setActiveWp(uid)
        # Add new uid to draw list and sync w/ treeView
        self.draw_list.append(uid)
        self.syncCheckedToDrawList()
        return uid

    def doc_linter(self):
        """Clean self.doc by cycling through a save/load STEP cycle.

        Refresh: self.shape_tool, self.color_tool, self.rootLabel."""

        # Create a file object to save to
        fname = "deleteme.txt"
        # Initialize STEP exporter
        WS = XSControl_WorkSession()
        step_writer = STEPCAFControl_Writer(WS, False)
        # Transfer shapes and write file
        step_writer.Transfer(self.doc, STEPControl_AsIs)
        status = step_writer.Write(fname)
        assert status == IFSelect_RetDone
        # Create new TreeModel and read STEP data
        tmodel = TreeModel("DOC")
        self.shape_tool = tmodel.shape_tool
        self.color_tool = tmodel.color_tool
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
            os.remove(fname)
        # Find root label of self.doc & save as self.rootLabel
        labels = TDF_LabelSequence()
        self.shape_tool.GetShapes(labels)
        try:
            self.rootLabel = labels.Value(1) # First label at root
        except RuntimeError as e:
            print(e)
            return

    def get_uid_from_entry(self, entry):
        """Generate uid from label entry

        In order to distinguish among multiple instances of shared data
        a uid is comprised of 'entry.serial_number', starting with 0.
        """
        if entry in self._share_dict:
            value = self._share_dict[entry]
        else:
            value = -1
        value += 1
        # update serial number in self._share_dict
        self._share_dict[entry] = value
        return entry + '.' + str(value)

    def parse_doc(self, tree=None):
        """Generate new part_dict, uid_dict (& optional) tree view items.

        self.doc is the data model containing both the 3D shapes and the
        assembly structure. This function reads self.doc and generates new
        updated versions of self.part_dict, self.uid_dict and (optionally)
        the tree view. If, for example, a part shape is being modified (or
        its name or color), there would be no need to update the tree view.
        """

        new_tree = True
        if tree is None:
            new_tree = False
        if new_tree:
            # Remove all existing widget items from tree view
            self.clearTree()
        # Initialize self._share_dict
        self._share_dict = {'0:1:1': 0}  # {entry: ser_nbr}
        self.assy_list = []  # assy uid's
        # To be used by redraw()
        self.part_dict = {}  # {uid: {'shape': , 'name': , 'color': }}
        # Temporary use during unpacking
        self.tree_view_item_dict = {'0:1:1': self.assy_root}  # {entry: item}
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
        # If first label at root holds an assembly, it is the Top Assy.
        # Through this label, the entire assembly is accessible.
        # There is no need to explicitly examine other labels at root.
        root_name = root_label.GetLabelName()
        root_entry = root_label.EntryDumpToString()
        root_uid = self.get_uid_from_entry(root_entry)
        parent_item = self.assy_root
        loc = shape_tool.GetLocation(root_label)  # <TopLoc_Location>
        self.assy_loc_stack.append(loc)
        self.assy_entry_stack.append(root_entry)
        self.uid_dict = {root_uid: {'entry': root_entry,
                                    'name': root_name,
                                    'ref_entry': None}}
        if new_tree:
            # create node in tree view
            item_name = [root_name, root_uid]
            item = QTreeWidgetItem(parent_item, item_name)
            item.setFlags(item.flags() | Qt.ItemIsTristate | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            self.treeView.expandItem(item)
            self.tree_view_item_dict[root_entry] = item
            self.assy_list.append(root_uid)

        top_comps = TDF_LabelSequence() # Components of Top Assy
        subchilds = False
        is_assy = shape_tool.GetComponents(root_label, top_comps, subchilds)
        if top_comps.Length():  # if is_assy:
            logger.debug("")
            logger.debug("Parsing components of label entry %s)", root_entry)
            self.parse_components(top_comps, shape_tool, color_tool, new_tree)
        else:
            print("Something is wrong.")

    def parse_components(self, comps, shape_tool, color_tool, new_tree):
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
            if new_tree:
                # create node in tree view
                item_name = [c_name, c_uid]
                parent = self.tree_view_item_dict[self.assy_entry_stack[-1]]
                item = QTreeWidgetItem(parent, item_name)
                item.setFlags(item.flags() | Qt.ItemIsTristate | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked)
                self.treeView.expandItem(item)
                self.tree_view_item_dict[c_entry] = item
            ref_label = TDF_Label()  # label of referred shape (or assembly)
            is_ref = shape_tool.GetReferredShape(c_label, ref_label)
            if is_ref:  # I think all components are references
                ref_name = ref_label.GetLabelName()
                ref_shape = shape_tool.GetShape(ref_label)
                ref_entry = ref_label.EntryDumpToString()
                self.uid_dict[c_uid] = {'entry': c_entry,
                                        'name': c_name,
                                        'ref_entry': ref_entry}
                if shape_tool.IsSimpleShape(ref_label):
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
                    c_loc = self.shape_tool.GetLocation(c_label)
                    if c_loc:
                        loc = res_loc.Multiplied(c_loc)
                    color = Quantity_Color()
                    color_tool.GetColor(ref_shape, XCAFDoc_ColorSurf, color)
                    self.part_dict[c_uid] = {'shape': c_shape,
                                             'color': color,
                                             'name': c_name,
                                             'loc': loc}
                elif self.shape_tool.IsAssembly(ref_label):
                    logger.debug("Referred item is an Assembly")
                    # Location vector is carried by component
                    aLoc = TopLoc_Location()
                    aLoc = self.shape_tool.GetLocation(c_label)
                    self.assy_loc_stack.append(aLoc)
                    self.assy_entry_stack.append(ref_entry)
                    if new_tree:
                        self.tree_view_item_dict[ref_entry] = item
                    self.assy_list.append(c_uid)
                    r_comps = TDF_LabelSequence() # Components of Assy
                    subchilds = False
                    isAssy = self.shape_tool.GetComponents(ref_label, r_comps, subchilds)
                    logger.debug("Assy name: %s", ref_name)
                    logger.debug("Is Assembly? %s", isAssy)
                    logger.debug("Number of components: %s", r_comps.Length())
                    if r_comps.Length():
                        logger.debug("")
                        logger.debug("Parsing components of label entry %s)",
                                     ref_entry)
                        self.parse_components(r_comps, shape_tool, color_tool, new_tree)
            else:
                print(f"I was wrong: All components are *not* references {c_uid}")
        self.assy_entry_stack.pop()
        self.assy_loc_stack.pop()

    def appendToStack(self):  # called when <ret> is pressed on line edit
        self.lineEditStack.append(self.lineEdit.text())
        self.lineEdit.clear()
        cb = self.registeredCallback
        if cb:
            cb([])  # call self.registeredCallback with arg=empty_list
        else:
            self.lineEditStack.pop()

    def setActivePart(self, uid):
        """Change active part status in coordinated manner."""
        # modify status in self
        self.activePartUID = uid
        self.activePart = self.part_dict[uid]['shape']
        # show as active in treeView
        self.showItemActive(uid)

    def setActiveWp(self, uid):
        """Change active workplane status in coordinated manner."""
        # modify status in self
        self.activeWpUID = uid
        self.activeWp = self.wp_dict[uid]
        # show as active in treeView
        self.showItemActive(uid)

    def setActiveAsy(self, uid):
        """Change active assembly status in coordinated manner."""
        # modify status in self
        self.activeAsyUID = uid
        # show as active in treeView
        self.showItemActive(uid)

    def valueFromCalc(self, value):
        """Receive value from calculator."""
        cb = self.registeredCallback
        if cb:
            self.lineEditStack.append(str(value))
            cb([])  # call self.registeredCallback with arg=empty_list
        else:
            print(value)

    def clearLEStack(self):
        self.lineEditStack = []

    def clearAllStacks(self):
        self.lineEditStack = []
        self.floatStack = []
        self.xyPtStack = []
        self.edgeStack = []
        self.faceStack = []
        self.ptStack = []

    def registerCallback(self, callback):
        currCallback = self.registeredCallback
        if currCallback:    # Make sure a callback isn't already registered
            self.clearCallback()
        self.canva._display.register_select_callback(callback)
        self.registeredCallback = callback
        self.currOpLabel.setText("Current Operation: %s " % callback.__name__[:-1])

    def clearCallback(self):
        if self.registeredCallback:
            self.canva._display.unregister_callback(self.registeredCallback)
            self.registeredCallback = None
            self.clearAllStacks()
            self.currOpLabel.setText("Current Operation: None ")
            self.statusBar().showMessage('')
            self.canva._display.SetSelectionModeNeutral()
            # self.redraw()

    #############################################
    #
    # 3D Display (Draw / Hide) methods:
    #
    #############################################

    def fitAll(self):
        self.canva._display.FitAll()

    def eraseAll(self):
        context = self.canva._display.Context
        context.RemoveAll(True)
        self.draw_list = []
        self.syncCheckedToDrawList()

    def redraw_wp(self):
        context = self.canva._display.Context
        for uid in self.wp_dict:
            if uid in self.draw_list:
                wp = self.wp_dict[uid]
                border = wp.border
                if uid == self.activeWpUID:
                    borderColor = Quantity_Color(Quantity_NOC_DARKGREEN)
                else:
                    borderColor = Quantity_Color(Quantity_NOC_GRAY)
                aisBorder = AIS_Shape(border)
                context.Display(aisBorder, True)
                context.SetColor(aisBorder, borderColor, True)
                transp = 0.8  # 0.0 <= transparency <= 1.0
                context.SetTransparency(aisBorder, transp, True)
                drawer = aisBorder.DynamicHilightAttributes()
                context.HilightWithColor(aisBorder, drawer, True)
                clClr = Quantity_Color(Quantity_NOC_MAGENTA1)
                for cline in wp.clines:
                    geomline = wp.geomLineBldr(cline)
                    aisline = AIS_Line(geomline)
                    aisline.SetOwner(geomline)
                    drawer = aisline.Attributes()
                    # asp parameters: (color, type, width)
                    asp = Prs3d_LineAspect(clClr, 2, 1.0)
                    drawer.SetLineAspect(asp)
                    aisline.SetAttributes(drawer)
                    context.Display(aisline, False)  # (see comment below)
                    # 'False' above enables 'context' mode display & selection
                pntlist = wp.intersectPts()  # type <gp_Pnt>
                for point in pntlist:
                    self.canva._display.DisplayShape(point)
                for ccirc in wp.ccircs:
                    aiscirc = AIS_Circle(wp.convert_circ_to_geomCirc(ccirc))
                    drawer = aisline.Attributes()
                    # asp parameters: (color, type, width)
                    asp = Prs3d_LineAspect(clClr, 2, 1.0)
                    drawer.SetLineAspect(asp)
                    aiscirc.SetAttributes(drawer)
                    context.Display(aiscirc, False)  # (see comment below)
                    # 'False' above enables 'context' mode display & selection
                for edge in wp.edgeList:
                    self.canva._display.DisplayShape(edge, color="WHITE")
                self.canva._display.Repaint()

    def redraw(self):
        context = self.canva._display.Context
        if not self.registeredCallback:
            self.canva._display.SetSelectionModeNeutral()
            context.SetAutoActivateSelection(True)
        context.RemoveAll(True)
        for uid in self.part_dict:
            if uid in self.draw_list:
                if uid in self.transparency_dict:
                    transp = self.transparency_dict[uid]
                else:
                    transp = 0.0
                part_data = self.part_dict[uid]
                shape = part_data['shape']
                name = part_data['name']
                color = part_data['color']
                try:
                    aisShape = AIS_Shape(shape)
                    context.Display(aisShape, True)
                    context.SetColor(aisShape, color, True)
                    # Set shape transparency, a float from 0.0 to 1.0
                    context.SetTransparency(aisShape, transp, True)
                    drawer = aisShape.DynamicHilightAttributes()
                    context.HilightWithColor(aisShape, drawer, True)
                except AttributeError as e:
                    print(e)
        self.redraw_wp()

    def drawAll(self):
        self.draw_list = []
        for k in self.part_dict:
            self.draw_list.append(k)
        for k in self.wp_dict:
            self.draw_list.append(k)
        self.syncCheckedToDrawList()
        self.redraw()

    def drawOnlyActivePart(self):
        self.eraseAll()
        uid = self.activePartUID
        self.draw_list.append(uid)
        self.canva._display.DisplayShape(self.part_dict[uid]['shape'])
        self.syncCheckedToDrawList()
        self.redraw()

    def drawOnlyPart(self, key):
        self.eraseAll()
        self.draw_list.append(key)
        self.syncCheckedToDrawList()
        self.redraw()

    def drawAddPart(self, key): # Add part to draw_list
        self.draw_list.append(key)
        self.syncCheckedToDrawList()
        self.redraw()

    def drawHidePart(self, key): # Remove part from draw_list
        if key in self.draw_list:
            self.draw_list.remove(key)
            self.syncCheckedToDrawList()
            self.redraw()

    #############################################
    #
    # Step Load / Save methods:
    #
    #############################################

    def copy_label(self, source_label, target_label):
        copy_label = TDF_CopyLabel()
        copy_label.Load(source_label, target_label)
        copy_label.Perform()
        return copy_label.IsDone()

    def loadStepAtRoot(self):
        """Get OCAF document from STEP file and assign it to win.doc.

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
        # Build new self.part_dict & tree view
        self.parse_doc(tree=True)
        self.drawAll()
        self.fitAll()

    def loadStep(self):
        """Get OCAF document from STEP file and 'paste' onto win.doc

        Todo: Get this working."""

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
        try:
            steprootLabel = labels.Value(1) # First label at root
            self.copy_label(steprootLabel, self.rootLabel)
            self.shape_tool.UpdateAssemblies()
        except RuntimeError as e:
            print(e)
            return
        # Repair self.doc by cycling through save/load
        self.doc_linter()
        # Build new self.part_dict & tree view
        self.parse_doc(tree=True)
        self.drawAll()
        self.fitAll()

    def loadStep(self):
        """Get OCAF document from STEP file and add (as component) to doc root.

        This is the way to open step files containing a single shape at root."""

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
                self.addComponent(shape, name, color)

    def loadStepTwo(self):
        """Get OCAF document from STEP file and 'paste' onto 0:1:1:1:1

        First create a box, resulting in box component at first label under root.
        Then run this to see if the step file being loaded replaces the box.
        Doesn't work. The name 'Box' of [0:1:1:1:1] is changed to the name of
        the step root assembly, but the referred shape is unchanged."""

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
        step_labels = TDF_LabelSequence()
        step_shape_tool.GetShapes(step_labels)
        steprootLabel = step_labels.Value(1)
        # Get target label of self.doc
        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        rootLabel = labels.Value(1)
        # Get first component label under rootLabel
        root_comps = TDF_LabelSequence() # Components of rootLabel
        subchilds = False
        is_assy = shape_tool.GetComponents(rootLabel, root_comps, subchilds)
        if is_assy:
            targetLabel = root_comps.Value(1)  # forst label under root
            self.copy_label(steprootLabel, targetLabel)
        self.shape_tool.UpdateAssemblies()
        # Repair self.doc by cycling through save/load
        self.doc_linter()
        # Build new self.part_dict & tree view
        self.parse_doc(tree=True)
        self.drawAll()
        self.fitAll()

    def saveStepActPrt(self):
        prompt = 'Choose filename for step file.'
        fnametuple = QFileDialog.getSaveFileName(None, prompt, './',
                                                 "STEP files (*.stp *.STP *.step)")
        fname, _ = fnametuple
        if not fname:
            print("Save step cancelled.")
            return

        # initialize the STEP exporter
        step_writer = STEPControl_Writer()
        Interface_Static_SetCVal("write.step.schema", "AP203")

        # transfer shapes and write file
        step_writer.Transfer(self.activePart, STEPControl_AsIs)
        status = step_writer.Write(fname)
        assert status == IFSelect_RetDone

    def replaceShape(self, modshape):
        """Replace active part shape with modified shape.

        The active part is a located instance of a referred shape stored
        at doc root. The user doesn't have access to this root shape. In order
        to modify the referred shape, the instance shape is modified, then
        moved back to the original location at doc root, then saved."""
        oldshape = self.activePart
        uid = self.activePartUID
        # Save oldshape to ancestorDict
        self.ancestor_dict[uid].append(oldshape)
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
        self.setActivePart(uid)  # Refresh shape in self.activePart
        self.redraw()

    def addComponent(self, shape, name, color):
        """Add new shape to top assembly of self.doc."""
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
        self.doc_linter()  # This gets color to work
        self.parse_doc(tree=True)
        # Get uid of new component, add to drawlist and set active
        entry = newLabel.EntryDumpToString()
        uid = entry + '.0'  # this is sort of lame
        self.drawAddPart(uid)
        self.setActivePart(uid)

    def add2RodAy(self, shape, name, color):
        """Add shape as a component of label whose entry is 0:1:1:2."""
        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        targetLabel = labels.Value(2) # second label at root
        newLabel = shape_tool.AddComponent(targetLabel, shape, True)
        # Get referrred label and apply color to it
        refLabel = TDF_Label()  # label of referred shape
        isRef = shape_tool.GetReferredShape(newLabel, refLabel)
        if isRef:
            color_tool.SetColor(refLabel, color, XCAFDoc_ColorGen)
        self.setLabelName(newLabel, name)
        self.setLabelName(refLabel, 'BOX')
        logger.info('Part %s added to root label', name)
        shape_tool.UpdateAssemblies()
        self.doc_linter()  # This gets color to work
        self.parse_doc(tree=True)
        self.syncDrawListToChecked()

    def getLabelName(self, label):
        return label.GetLabelName()

    def setLabelName(self, label, name):
        TDataStd_Name.Set(label, TCollection_ExtendedString(name))

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

    #############################################
    #
    # 3D Measure functons...
    #
    #############################################

    def launchCalc(self):
        if not self.calculator:
            self.calculator = rpnCalculator.Calculator(self)
            self.calculator.show()

    def setUnits(self, units):
        if units in self._unitDict.keys():
            self.units = units
            self.unitscale = self._unitDict[self.units]
            self.unitsLabel.setText("Units: %s " % self.units)

    def distPtPt(self):
        if len(self.ptStack) == 2:
            p2 = self.ptStack.pop()
            p1 = self.ptStack.pop()
            vec = gp_Vec(p1, p2)
            dist = vec.Magnitude()
            dist = dist / self.unitscale
            self.calculator.putx(dist)
            self.distPtPt()
        else:
            self.registerCallback(self.distPtPtC)
            # How to enable selecting intersection points on WP?
            self.canva._display.SetSelectionModeVertex()
            statusText = "Select 2 points to measure distance."
            self.statusBar().showMessage(statusText)

    def distPtPtC(self, shapeList, *args):  # callback (collector) for distPtPt
        logger.debug('Edges selected: %s', shapeList)
        logger.debug('args: %s', args)  # args = x, y mouse coords
        for shape in shapeList:
            vrtx = topods_Vertex(shape)
            gpPt = BRep_Tool.Pnt(vrtx) # convert vertex to gp_Pnt
            self.ptStack.append(gpPt)
        if len(self.ptStack) == 2:
            self.distPtPt()

    def edgeLen(self):
        if self.edgeStack:
            edge = self.edgeStack.pop()
            edgelen = CPnts_AbscissaPoint_Length(BRepAdaptor_Curve(edge))
            edgelen = edgelen / self.unitscale
            self.calculator.putx(edgelen)
            #self.redraw()
            self.edgeLen()
        else:
            self.registerCallback(self.edgeLenC)
            self.canva._display.SetSelectionModeEdge()
            statusText = "Pick an edge to measure."
            self.statusBar().showMessage(statusText)

    def edgeLenC(self, shapeList, *args):  # callback (collector) for edgeLen
        logger.debug('Edges selected: %s', shapeList)
        logger.debug('args: %s', args)  # args = x, y mouse coords
        for shape in shapeList:
            edge = topods_Edge(shape)
            self.edgeStack.append(edge)
        if self.edgeStack:
            self.edgeLen()
