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
from PyQt5.QtCore import Qt, QPersistentModelIndex, QModelIndex
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (QLabel, QMainWindow, QTreeWidget, QMenu,
                             QDockWidget, QDesktopWidget, QToolButton,
                             QLineEdit, QTreeWidgetItem, QAction, QFrame,
                             QToolBar, QAbstractItemView, QInputDialog,
                             QTreeWidgetItemIterator)
from OCC.Core.AIS import AIS_Shape, AIS_Line, AIS_Circle
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.CPnts import CPnts_AbscissaPoint_Length
from OCC.Core.gp import gp_Vec
from OCC.Core.Prs3d import Prs3d_LineAspect
from OCC.Core.Quantity import (Quantity_Color, Quantity_NOC_GRAY,
                               Quantity_NOC_DARKGREEN, Quantity_NOC_MAGENTA1)
from OCC.Core.TopoDS import topods_Edge, topods_Vertex
import OCC.Display.OCCViewer
import OCC.Display.backend
used_backend = OCC.Display.backend.load_backend()
# from OCC.Display import qtDisplay
# import local version instead (allows changing rotate/pan/zoom controls)
from OCC import VERSION
import myDisplay.qtDisplay as qtDisplay
import rpnCalculator
from docmodel import DocModel
from version import APP_VERSION
print("OCC version: %s" % VERSION)

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR) # set to DEBUG | INFO | ERROR

doc = DocModel()

class TreeView(QTreeWidget):
    """Assembly structure display

    The Part/Assy tree view GUI and is kept in sync with the XCAF data model
    by calling the function build_tree() whenever changes in the data model
    cause the tree view display to become out of date. By first clicking on a
    tree view item, then right clicking, a drop down list of options appears,
    allowing some modifications to be made to the model. Although the tree view
    display currently permits the user to make 'drag & drop' modifications,
    those changes are currently not propagated to the data model.
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
    """Main GUI window containing an assy tree view and a 3D display view"""

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
        self.transparency_dict = {}  # {uid: part display transparency}
        self.ancestor_dict = defaultdict(list)  # {uid: [list of ancestor shapes]}

        self.activeWp = None    # WorkPlane object
        self.activeWpUID = 0
        self.wp_dict = {}       # k = uid, v = wpObject
        self._wpNmbr = 1

        self.activeAsyUID = 0
        self.assy_list = []     # list of assy uid's
        self.showItemActive(0)
        self.activeAsy = self.setActiveAsy(self.activeAsyUID)

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

    def build_tree(self):
        """Build new tree view from doc.uid_dict.

        This method is called whenever doc.doc is modified in a way that would
        result in a change in the tree view. The tree view represents the
        hierarchical structure of the top assembly and its components."""
        self.clearTree()
        parent_item_dict = {}  # {uid: tree view item}
        for uid, dic in doc.uid_dict.items():
            # dic: {keys: 'entry', 'name', 'parent_uid', 'ref_entry'}
            entry = dic['entry']
            name = dic['name']
            parent_uid = dic['parent_uid']
            if parent_uid not in parent_item_dict:
                parent_item = self.assy_root
            else:
                parent_item = parent_item_dict[parent_uid]

            # create node in tree view
            item_name = [name, uid]
            item = QTreeWidgetItem(parent_item, item_name)
            item.setFlags(item.flags() | Qt.ItemIsTristate | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            self.treeView.expandItem(item)
            parent_item_dict[uid] = item

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
                if (uid in doc.part_dict) or (uid in self.wp_dict):
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
            if (uid in doc.part_dict) or (uid in self.wp_dict):
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
            if uid in doc.part_dict:
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
            entry = doc.uid_dict[uid]['entry']
            ref_ent = doc.uid_dict[uid]['ref_entry']
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
            if uid in doc.part_dict:
                self.transparency_dict[uid] = 0.6
                self.redraw()
            self.itemClicked = None

    def setOpaque(self):
        item = self.itemClicked
        if item:
            uid = item.text(1)
            if uid in doc.part_dict:
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
                doc.change_label_name(uid, newName)
                self.build_tree()

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
        self.activePart = doc.part_dict[uid]['shape']
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
        for uid in doc.part_dict:
            if uid in self.draw_list:
                if uid in self.transparency_dict:
                    transp = self.transparency_dict[uid]
                else:
                    transp = 0.0
                part_data = doc.part_dict[uid]
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
        for k in doc.part_dict:
            self.draw_list.append(k)
        for k in self.wp_dict:
            self.draw_list.append(k)
        self.syncCheckedToDrawList()
        self.redraw()

    def drawOnlyActivePart(self):
        uid = self.activePartUID
        if uid:
            self.eraseAll()
            self.draw_list.append(uid)
            self.canva._display.DisplayShape(doc.part_dict[uid]['shape'])
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
