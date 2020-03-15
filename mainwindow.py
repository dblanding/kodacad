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
import os, os.path
import sys
from PyQt5.QtCore import Qt, QPersistentModelIndex, QModelIndex
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (QLabel, QMainWindow, QTreeWidget, QMenu,
                             QDockWidget, QDesktopWidget, QToolButton,
                             QLineEdit, QTreeWidgetItem, QAction, QFrame,
                             QToolBar, QFileDialog, QAbstractItemView,
                             QInputDialog, QTreeWidgetItemIterator)
from OCC.Core.AIS import AIS_Shape, AIS_Line, AIS_Circle
from OCC.Core.BRep import BRep_Tool, BRep_Builder
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.CPnts import CPnts_AbscissaPoint_Length
from OCC.Core.gp import gp_Vec
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Interface import Interface_Static_SetCVal
from OCC.Core.Prs3d import Prs3d_LineAspect
from OCC.Core.Quantity import (Quantity_Color, Quantity_NOC_GRAY,
                               Quantity_NOC_DARKGREEN, Quantity_NOC_MAGENTA1)
from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCC.Core.TCollection import TCollection_ExtendedString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TDF import TDF_LabelSequence, TDF_Label, TDF_CopyLabel
from OCC.Core.TopoDS import (topods_Edge, topods_Vertex, TopoDS_Shape,
                             TopoDS_Compound)
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.XCAFApp import XCAFApp_Application_GetApplication
from OCC.Core.XCAFDoc import (XCAFDoc_DocumentTool_ShapeTool,
                              XCAFDoc_DocumentTool_ColorTool,
                              XCAFDoc_ColorGen)
from OCC.Core.XSControl import XSControl_WorkSession
import OCC.Display.OCCViewer
import OCC.Display.backend
used_backend = OCC.Display.backend.load_backend()
# from OCC.Display import qtDisplay
# import local version instead (allows changing rotate/pan/zoom controls)
import myDisplay.qtDisplay as qtDisplay
from OCC import VERSION
import rpnCalculator
import stepXD
from version import APP_VERSION
print("OCC version: %s" % VERSION)


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # set to DEBUG | INFO | ERROR


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
    XCAF data model.
    IDEA: As an alternative to 'drag & drop', consider adding an option to
    the RMB pop-up to change the parent of a QTreeWidgetItem.
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
        if sys.platform == 'darwin':
            QtGui.qt_mac_set_native_menubar(False)
        self.menu_bar = self.menuBar()
        self._menus = {}
        self._menu_methods = {}
        self.centerOnScreen()

        self.calculator = None

        itemName = ['/', str(0)] # Root Item in TreeView
        self.treeViewRoot = QTreeWidgetItem(self.treeView, itemName)
        self.treeView.expandItem(self.treeViewRoot)
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

        self._currentUID = 0
        self.drawList = []      # list of part uid's to be displayed
        self.floatStack = []    # storage stack for floating point values
        self.xyPtStack = []     # storage stack for 2d points (x, y)
        self.ptStack = []       # storage stack for gp_Pnts
        self.edgeStack = []     # storage stack for edge picks
        self.faceStack = []     # storage stack for face picks
        self.shapeStack = []    # storage stack for shape picks
        self.lineEditStack = [] # list of user inputs

        self.activePart = None  # <TopoDS_Shape> object
        self.activePartUID = 0
        self._partDict = {}     # k = uid, v = <ToopoDS_Shape> object
        self._nameDict = {}     # k = uid, v = partName
        self._colorDict = {}    # k = uid, v = part display color
        self._transparencyDict = {}  # k = uid, v = part display transparency
        self._ancestorDict = defaultdict(list)  # k = uid, v = [list of ancestorUIDs]

        self.activeWp = None    # WorkPlane object
        self.activeWpUID = 0
        self._wpDict = {}       # k = uid, v = wpObject
        self._wpNmbr = 1

        self.activeAsyUID = 0
        self._assyDict = {self.activeAsyUID: TopLoc_Location()}  # k = uid, v = Loc
        self.showItemActive(0)
        self._labelDict = {}
        self.createDoc()   # <class 'OCC.Core.TDocStd.TDocStd_Document'>
        self.activeAsy = self.setActiveAsy(self.activeAsyUID)

    def createDoc(self):
        """Create XCAF doc with an empty assembly at entry 0:1:1:1.

        This is done only once in __init__."""

        # Create the application and document with empty rootLabel
        title = "Main document"
        doc = TDocStd_Document(TCollection_ExtendedString(title))
        app = XCAFApp_Application_GetApplication()
        app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(doc.Main())
        # type(doc.Main()) = <class 'OCC.Core.TDF.TDF_Label'>
        # doc.Main().EntryDumpToString() 0:1
        # shape_tool is at label entry = 0:1:1
        # Create empty rootLabel entry = 0:1:1:1
        rootLabel = shape_tool.NewShape()
        self.setLabelName(rootLabel, "/")
        self.doc = doc
        self.rootLabel = rootLabel
        self.shape_tool = shape_tool
        self._labelDict[0] = rootLabel

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
        except:
            pass
        event.accept()

    #############################################
    #
    # 'treeView' (QTreeWidget) related methods:
    #
    #############################################

    def contextMenu(self, point):
        self.menu = QMenu()
        action = self.popMenu.exec_(self.mapToGlobal(point))

    def treeViewItemClicked(self, item):  # called whenever treeView item is clicked
        self.itemClicked = item # store item
        if not self.inSync():   # click may have been on checkmark. Update drawList (if needed)
            self.syncDrawListToChecked()
            self.redraw()

    def checkedToList(self):
        """Returns list of uid's of checked (part) items in treeView"""
        dl = []
        for item in self.treeView.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            if item.checkState(0) == 2:
                strUID = item.text(1)
                uid = int(strUID)
                if (uid in self._partDict.keys()) or (uid in self._wpDict.keys()):
                    dl.append(uid)
        return dl

    def inSync(self):
        """Return True if checked items are in sync with drawList."""
        return self.checkedToList() == self.drawList

    def syncDrawListToChecked(self):
        self.drawList = self.checkedToList()

    def syncCheckedToDrawList(self):
        for item in self.treeView.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            strUID = item.text(1)
            uid = int(strUID)
            if (uid in self._partDict) or (uid in self._wpDict):
                if uid in self.drawList:
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
            strUID = item.text(1)
            uid = int(strUID)
            if uid in self._partDict:
                pdict[uid] = item
            elif uid in self._assyDict:
                adict[uid] = item
            elif uid in self._wpDict:
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
            strUID = item.text(1)
            uid = int(strUID)
            try:
                label = self._labelDict[uid]
            except KeyError as e:
                print(f"Not working for this item: {e}")
                return
            cname = label.GetLabelName()  # component name
            try:
                cEntry = label.EntryDumpToString()
                rlabel = TDF_Label()  # label of referred shape
                isRef = self.shape_tool.GetReferredShape(label, rlabel)
                if isRef:
                    rname = rlabel.GetLabelName()
                    rEntry = rlabel.EntryDumpToString()
                    print(f"UID: {uid}\t{cname}[{cEntry}] ==> {rname}[{rEntry}]")
                else:
                    print(f"UID: {uid}\t{cname}[{cEntry}]")
            except RuntimeError as e:
                print(e)

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
            strUID = item.text(1)
            uid = int(strUID)
            print(f"Part selected: {name}, UID: {uid}")
            pd, ad, wd = self.sortViewItems()
            if uid in pd:
                self.setActivePart(uid)
                sbText = "%s [uid=%i] is now the active part" % (name, uid)
                self.redraw()
            elif uid in wd:
                self.setActiveWp(uid)
                sbText = "%s [uid=%i] is now the active workplane" % (name, uid)
                self.redraw()
            elif uid in ad:
                self.setActiveAsy(uid)
                sbText = "%s [uid=%i] is now the active assembly" % (name, uid)
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
            strUID = item.text(1)
            uid = int(strUID)
            if uid in self._partDict:
                self._transparencyDict[uid] = 0.6
                self.redraw()
            self.itemClicked = None

    def setOpaque(self):
        item = self.itemClicked
        if item:
            strUID = item.text(1)
            uid = int(strUID)
            if uid in self._partDict:
                self._transparencyDict.pop(uid)
                self.redraw()
            self.itemClicked = None

    def editName(self): # Edit name of item clicked in treeView
        item = self.itemClicked
        sbText = '' # status bar text
        if item:
            name = item.text(0)
            strUID = item.text(1)
            uid = int(strUID)
            prompt = 'Enter new name for part %s' % name
            newName, OK = QInputDialog.getText(self, 'Input Dialog',
                                               prompt, text=name)
            if OK:
                item.setText(0, newName)
                sbText = "Part name changed to %s" % newName
                self._nameDict[uid] = newName
        self.treeView.clearSelection()
        self.itemClicked = None
        # Todo: update name in treeModel
        self.statusBar().showMessage(sbText, 5000)

    #############################################
    #
    # Administrative and data management methods:
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

    def getNewPartUID(self, objct, name="", ancestor=0,
                      typ='p', color=None):
        """
        Method for assigning a unique ID (serial number) to a new part
        (typ='p'), assembly (typ='a') or workplane (typ='w') generated
        within the application. Using the uid as a key, record the
        information in the various dictionaries. If the 'ancestor' parameter
        is non-zero, it holds the uid of an existing shape being modified.
        In this case, the uid of the modified shape is reused and the
        modified shape is archived.
        """
        if not ancestor:  # Get new UID
            uid = self._currentUID + 1
            self._currentUID = uid
        else:  # Re-use ancestor UID and archive older shape
            uid = ancestor
            self._ancestorDict[uid].append(objct)
            if uid == self.activePartUID:  # Don't overlook this
                self.activePart = objct
            if not name:
                name = self._nameDict[ancestor] # Keep ancestor name
        # Update appropriate dictionaries
        if typ == 'p':
            self._partDict[uid] = objct  # <TopoDS_Shape>
            if not name:
                name = 'Part'   # Default name
            if not ancestor:
                if color:   # Quantity_Color()
                    c = OCC.Display.OCCViewer.rgb_color(color.Red(),
                                                        color.Green(),
                                                        color.Blue())
                else:
                    c = OCC.Display.OCCViewer.rgb_color(.2, .1, .1)  # default color
                self._colorDict[uid] = c
                # add item to treeView
                self.addItemToTreeView(name, uid)
                # Make new part active
                self.setActivePart(uid)
        elif typ == 'a':
            if not name:
                name = 'Assembly'   # Default name
            self._assyDict[uid] = objct  # TopLoc_Location
            # add item to treeView
            self.addItemToTreeView(name, uid)
            # Make new assembly active
            self.setActiveAsy(uid)
        elif typ == 'w':
            name = "wp%i" % self._wpNmbr
            self._wpNmbr += 1
            self._wpDict[uid] = objct # wpObject
            # add item to treeView
            self.addItemToTreeView(name, uid)
            # Make new workplane active
            self.setActiveWp(uid)
        self._nameDict[uid] = name
        # Add new uid to draw list and sync w/ treeView
        self.drawList.append(uid)
        self.syncCheckedToDrawList()
        #self.addComponent()
        return uid

    def addItemToTreeView(self, name, uid):
        itemName = [name, str(uid)]
        item = QTreeWidgetItem(self.treeViewRoot, itemName)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)

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
        self.activePart = self._partDict[uid]
        # show as active in treeView
        self.showItemActive(uid)

    def setActiveWp(self, uid):
        """Change active workplane status in coordinated manner."""
        # modify status in self
        self.activeWpUID = uid
        self.activeWp = self._wpDict[uid]
        # show as active in treeView
        self.showItemActive(uid)

    def setActiveAsy(self, uid):
        """Change active assembly status in coordinated manner."""
        # modify status in self
        self.activeAsyUID = uid
        self.activeAsy = self._labelDict[uid]
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
            self.redraw()

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
        self.drawList = []
        self.syncCheckedToDrawList()

    def redraw(self):
        context = self.canva._display.Context
        if not self.registeredCallback:
            self.canva._display.SetSelectionModeNeutral()
            context.SetAutoActivateSelection(True)
        context.RemoveAll(True)
        for uid in self.drawList:
            if uid in self._partDict.keys():
                if uid in self._transparencyDict.keys():
                    transp = self._transparencyDict[uid]
                else:
                    transp = 0.0
                color = self._colorDict[uid]
                aisShape = AIS_Shape(self._partDict[uid])
                context.Display(aisShape, True)
                context.SetColor(aisShape, color, True)
                # Set shape transparency, a float from 0.0 to 1.0
                context.SetTransparency(aisShape, transp, True)
                drawer = aisShape.DynamicHilightAttributes()
                context.HilightWithColor(aisShape, drawer, True)
            elif uid in self._wpDict.keys():
                wp = self._wpDict[uid]
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

    def drawAll(self):
        self.drawList = []
        for k in self._partDict:
            self.drawList.append(k)
        for k in self._wpDict:
            self.drawList.append(k)
        self.syncCheckedToDrawList()
        self.redraw()

    def drawOnlyActivePart(self):
        self.eraseAll()
        uid = self.activePartUID
        self.drawList.append(uid)
        self.canva._display.DisplayShape(self._partDict[uid])
        self.syncCheckedToDrawList()
        self.redraw()

    def drawOnlyPart(self, key):
        self.eraseAll()
        self.drawList.append(key)
        self.syncCheckedToDrawList()
        self.redraw()

    def drawAddPart(self, key): # Add part to drawList
        self.drawList.append(key)
        self.syncCheckedToDrawList()
        self.redraw()

    def drawHidePart(self, key): # Remove part from drawList
        if key in self.drawList:
            self.drawList.remove(key)
            self.syncCheckedToDrawList()
            self.redraw()

    #############################################
    #
    # Step Load / Save methods:
    #
    #############################################

    def loadStep(self):
        """Bring in a step file as a 'disposable' treelib.Tree() structure.

        Each node of the tree contains the following tuple:
        (Name, UID, ParentUID, {Data})
        where the Data dictionary is:
        {'a': (isAssy?),
         'l': (TopLoc_Location),
         'c': (Quantity_Color),
         's': (TopoDS_Shape)}
        This format makes it convenient to:
        1. Build the assy, part, name and color dictionaries using uid keys,
        2. Display the model with all its component parts correctly located and
        3. Build the Part/Assy tree view GUI (QTreeWidget).

        Each QTreeWidgetItem is required to have a unique identifier. This means
        that multiple instances of the same CAD geometry will each have different
        uid's.
        """
        prompt = 'Select STEP file to import'
        fnametuple = QFileDialog.getOpenFileName(None, prompt, './',
                                                 "STEP files (*.stp *.STP *.step)")
        fname, _ = fnametuple
        logger.debug("Load file name: %s", fname)
        if not fname:
            print("Load step cancelled")
            return
        name = os.path.basename(fname).split('.')[0]
        nextUID = self._currentUID
        stepImporter = stepXD.StepImporter(fname, nextUID)

        stepdoc = stepImporter.doc

        step_shape_tool = XCAFDoc_DocumentTool_ShapeTool(stepdoc.Main())
        labels = TDF_LabelSequence()
        step_shape_tool.GetShapes(labels)
        logger.info('Number of labels at STEP_root : %i', labels.Length())
        try:
            steprootLabel = labels.Value(1) # First label at root
            # 'paste' this onto root label of self.doc
            copyLabel = TDF_CopyLabel(steprootLabel, self.rootLabel)
            copyLabel.Perform()
            shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
            shape_tool.UpdateAssemblies()
        except RuntimeError as e:
            print(e)
            return

        self._labelDict.update(stepImporter.labelDict)

        tree = stepImporter.tree
        tempTreeDict = {}   # uid:asyPrtTreeItem (used temporarily during unpack)
        treedump = tree.expand_tree(mode=tree.DEPTH)
        for uid in treedump:  # type(uid) == int
            node = tree.get_node(uid)
            name = node.tag
            itemName = [name, str(uid)]
            parentUid = node.bpointer
            if node.data['a']:  # Assembly
                if not parentUid: # This is the top level item
                    parentItem = self.treeViewRoot
                else:
                    parentItem = tempTreeDict[parentUid]
                item = QTreeWidgetItem(parentItem, itemName)
                item.setFlags(item.flags() | Qt.ItemIsTristate | Qt.ItemIsUserCheckable)
                self.treeView.expandItem(item)
                tempTreeDict[uid] = item
                Loc = node.data['l'] # Location object
                self._assyDict[uid] = Loc
            else:   # Part
                # add item to asyPrtTree treeView
                if not parentUid: # This is the top level item
                    parentItem = self.treeViewRoot
                else:
                    parentItem = tempTreeDict[parentUid]
                item = QTreeWidgetItem(parentItem, itemName)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked)
                tempTreeDict[uid] = item
                color = node.data['c']
                shape = node.data['s']
                # Update dictionaries
                self._partDict[uid] = shape
                self._nameDict[uid] = name
                if color:
                    c = OCC.Display.OCCViewer.rgb_color(color.Red(), color.Green(), color.Blue())
                else:
                    c = OCC.Display.OCCViewer.rgb_color(.2, .1, .1)   # default color
                self._colorDict[uid] = c
                self.activePartUID = uid           # Set as active part
                self.activePart = shape
                self.drawList.append(uid)   # Add to draw list

        keyList = tempTreeDict.keys()
        keyList = list(keyList)
        keyList.sort()
        maxUID = keyList[-1]
        self._currentUID = maxUID
        self.redraw()

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

    def addComponent(self):
        """Add active part to top assembly of self.doc."""
        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        try:
            rootLabel = labels.Value(1) # First label at root
        except RuntimeError as e:
            print(e)
            return
        newLabel = shape_tool.AddComponent(rootLabel, self.activePart, True)
        color = self._colorDict[self.activePartUID]
        # Get referrred label and apply color to it
        refLabel = TDF_Label()  # label of referred shape
        isRef = shape_tool.GetReferredShape(newLabel, refLabel)
        if isRef:
            color_tool.SetColor(refLabel, color, XCAFDoc_ColorGen)
        newName = self._nameDict[self.activePartUID]
        self.setLabelName(newLabel, newName)
        logger.info('Part %s added to root label', newName)
        shape_tool.UpdateAssemblies()
        self._labelDict[self.activePartUID] = newLabel

    def addComponents(self):
        """Add all parts in _partDict as components of top assy in self.doc"""

        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        logger.info('Number of labels at root : %i', labels.Length())
        try:
            rootlabel = labels.Value(1) # First label at root
        except RuntimeError:
            return
        # Set name of rootlabel to new value
        self.setLabelName(rootlabel, "Step")
        # Add component parts to assembly
        for uid, part in self._partDict.items():
            newLabel = shape_tool.AddComponent(rootlabel, part, True)
            color = self._colorDict[uid]
            # Get referrred label and apply color to it
            refLabel = TDF_Label()  # label of referred shape
            isRef = shape_tool.GetReferredShape(newLabel, refLabel)
            if isRef:
                color_tool.SetColor(refLabel, color, XCAFDoc_ColorGen)
            name = self._nameDict[uid]
            self.setLabelName(newLabel, name)
            logger.info('Component part %s added', name)

        # myAssembly->UpdateAssemblies();
        shape_tool.UpdateAssemblies()

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

    def dumpDoc(self):
        print(type(self.doc))
        logger.info("Analyzing doc")
        labels = TDF_LabelSequence()
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        color_tool = XCAFDoc_DocumentTool_ColorTool(self.doc.Main())
        shape_tool.GetShapes(labels)
        logger.info('Number of labels at root : %i', labels.Length())
        try:
            rootlabel = labels.Value(1) # First label at root
        except RuntimeError:
            return
        name = rootlabel.GetLabelName()
        logger.info('Name of root label: %s', name)
        isAssy = shape_tool.IsAssembly(rootlabel)
        logger.info("First label at root holds an assembly? %s", isAssy)
        if isAssy:
            # If first label at root holds an assembly, it is the Top Assembly.
            # Through this label, the entire assembly is accessible.
            # there is no need to examine other labels at root explicitly.
            entry = rootlabel.EntryDumpToString()
            logger.debug("Entry: %s", entry)
            logger.debug("Top assy name: %s", name)
            topComps = TDF_LabelSequence() # Components of Top Assy
            subchilds = False
            isAssy = shape_tool.GetComponents(rootlabel, topComps, subchilds)
            logger.debug("Is Assembly? %s", isAssy)
            logger.debug("Number of components: %s", topComps.Length())
            logger.debug("Is Reference? %s", shape_tool.IsReference(rootlabel))
            if topComps.Length():
                self.findComponents(rootlabel, topComps)

    def findComponents(self, label, comps):
        """Discover components from comps (LabelSequence) of an assembly (label).

        Components of an assembly are, by definition, references which refer to
        either a shape or another assembly. Components are essentially 'instances'
        of the referred shape or assembly, and carry a location vector specifing
        the location of the referred shape or assembly.
        """
        logger.debug("")
        logger.debug("Finding components of label entry %s)", label.EntryDumpToString())
        shape_tool = XCAFDoc_DocumentTool_ShapeTool(self.doc.Main())
        for j in range(comps.Length()):
            logger.debug("loop %i of %i", j+1, comps.Length())
            cLabel = comps.Value(j+1)  # component label <class 'OCC.Core.TDF.TDF_Label'>
            cShape = shape_tool.GetShape(cLabel)
            logger.debug("Component number %i", j+1)
            logger.debug("Component entry: %s", cLabel.EntryDumpToString())
            name = cLabel.GetLabelName()
            logger.debug("Component name: %s", name)
            refLabel = TDF_Label()  # label of referred shape (or assembly)
            isRef = shape_tool.GetReferredShape(cLabel, refLabel)
            if isRef:  # I think all components are references, but just in case...
                refShape = shape_tool.GetShape(refLabel)
                refEntry = refLabel.EntryDumpToString()
                leafName = name
                logger.debug("Entry referred to: %s", refEntry)
                refName = refLabel.GetLabelName()
                logger.debug("Name of referred item: %s", refName)
                if shape_tool.IsSimpleShape(refLabel):
                    logger.debug("Referred item is a Shape")
                elif shape_tool.IsAssembly(refLabel):
                    logger.debug("Referred item is an Assembly")
                    rComps = TDF_LabelSequence() # Components of Assy
                    subchilds = False
                    isAssy = shape_tool.GetComponents(refLabel, rComps, subchilds)
                    logger.debug("Assy name: %s", name)
                    logger.debug("Is Assembly? %s", isAssy)
                    logger.debug("Number of components: %s", rComps.Length())
                    if rComps.Length():
                        self.findComponents(refLabel, rComps)

    #############################################
    #
    # 3D Measure functons...
    #
    #############################################

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
            self.redraw()
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
