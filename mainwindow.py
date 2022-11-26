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

from collections import defaultdict
import logging
from PyQt5.QtCore import Qt, QPersistentModelIndex, QModelIndex
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QLabel,
    QLineEdit,
    QMainWindow,
    QTreeWidget,
    QMenu,
    QDockWidget,
    QDesktopWidget,
    QToolButton,
    QTreeWidgetItem,
    QAction,
    QFrame,
    QToolBar,
    QAbstractItemView,
    QInputDialog,
    QTreeWidgetItemIterator,
)
from OCC.Core.AIS import AIS_Shape, AIS_Line, AIS_Circle
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.CPnts import CPnts_AbscissaPoint_Length
from OCC.Core.gp import gp_Vec
from OCC.Core.Prs3d import Prs3d_LineAspect
from OCC.Core.Quantity import (
    Quantity_Color,
    Quantity_NOC_GRAY,
    Quantity_NOC_DARKGREEN,
    Quantity_NOC_MAGENTA1,
)
from OCC.Core.TopoDS import topods_Edge, topods_Vertex
import OCC.Display.OCCViewer
import OCC.Display.backend

used_backend = OCC.Display.backend.load_backend()
# from OCC.Display import qtDisplay
# import local version instead (allows changing rotate/pan/zoom controls)
from OCC import VERSION
# import myDisplay.qtDisplay as qtDisplay  # For pythonocc-7.4
from OCC.Display import qtDisplay  # For pythonocc-7.5
import rpnCalculator
from docmodel import DocModel
from version import APP_VERSION

print("OCC version: %s" % VERSION)

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  # set to DEBUG | INFO | ERROR

dm = DocModel()


class TreeView(QTreeWidget):
    """Part & Assembly structure display

    The Part/Assy treeView display is kept in sync with the XCAF data model
    by calling the function build_tree() whenever changes in the data model
    cause the treeView display to become out of date. By first clicking on a
    treeView item, then right clicking, a drop down list of options appears,
    allowing some modifications to be made to the model. Although the treeView
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
        self.popMenu.exec_(self.mapToGlobal(point))

    def dropEvent(self, event):
        if event.source() == self:
            QAbstractItemView.dropEvent(self, event)

    def dropMimeData(self, parent, row, data, action):
        if action == Qt.MoveAction:
            return self.moveSelection(parent, row)
        return False

    def moveSelection(self, parent, position):
        # save the selected items
        selection = [QPersistentModelIndex(i) for i in self.selectedIndexes()]
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
                    self.insertTopLevelItem(
                        self.topLevelItemCount(), taken.pop(0))
            else:
                # insert the items at the specified position
                if parent_index.isValid():
                    parent.insertChild(
                        min(target, parent.childCount()), taken.pop(0))
                else:
                    self.insertTopLevelItem(
                        min(target, self.topLevelItemCount()), taken.pop(0)
                    )
        return True


class MainWindow(QMainWindow):
    """Main GUI window containing an assy tree view and a 3D display view

    The User controls whether parts displayed in the 3D display view are drawn
    or hidden through the use of check boxes on the tree view display. The list
    of the uid's of all the items currently hidden is held in self.hide_list.
    When tree view items are checked or unchecked, a list of unchecked items is
    compared to self.hide_list. That comparison results in two new lists:
    a list of items to be erased and a list of items to be drawn. The items to
    be erased are erased and the items to be drawn are drawn, and the hide_list
    is then updated.

    When a part is newly created or loaded (step), the doc model (dm) is changed and
    this results in the regeneration of the tree view. As the new tree view
    items are generated, they are shown checked except for the ones that are
    contained in the hide_list. """

    def __init__(self, *args):
        super().__init__()
        self.canvas = qtDisplay.qtViewer3d(self)
        # Renaming self.canvas._display (like below) doesn't work.
        # self.display = self.canvas._display
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.contextMenu)
        self.popMenu = QMenu(self)
        title = f"KodaCAD {APP_VERSION} "
        title += f"(Using: PythonOCC version {VERSION} with PyQt5 backend)"
        self.setWindowTitle(title)
        self.resize(960, 720)
        self.setCentralWidget(self.canvas)
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
        self.itemClicked = None  # TreeView item that has been mouse clicked

        # Internally, everything is always in mm
        # scale user input and output values
        # (user input values) * unitscale = value in mm
        # (output values) / unitscale = value in user's units
        self._unitDict = {"mm": 1.0, "in": 25.4, "ft": 304.8}
        self.units = "mm"
        self.unitscale = self._unitDict[self.units]
        self.unitsLabel = QLabel()
        self.unitsLabel.setText("Units: %s " % self.units)
        self.unitsLabel.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)

        self.endOpButton = QToolButton()
        self.endOpButton.setText("End Operation")
        self.endOpButton.clicked.connect(self.clearCallback)
        self.currOpLabel = QLabel()
        self.registeredCallback = None
        self.currOpLabel.setText("Current Operation: %s " %
                                 self.registeredCallback)

        self.lineEdit = QLineEdit()
        self.lineEdit.returnPressed.connect(self.appendToStack)

        status = self.statusBar()
        status.setSizeGripEnabled(False)
        status.addPermanentWidget(self.lineEdit)
        status.addPermanentWidget(self.currOpLabel)
        status.addPermanentWidget(self.endOpButton)
        status.addPermanentWidget(self.unitsLabel)
        status.showMessage("Ready", 5000)

        self.hide_list = []  # list of part uid's to be hidden (not displayed)
        self.floatStack = []  # storage stack for floating point values
        self.xyPtStack = []  # storage stack for 2d points (x, y)
        self.ptStack = []  # storage stack for gp_Pnts
        self.edgeStack = []  # storage stack for edge picks
        self.faceStack = []  # storage stack for face picks
        self.shapeStack = []  # storage stack for shape picks
        self.lineEditStack = []  # list of user inputs

        self.activePart = None  # <TopoDS_Shape> object
        self.activePartUID = 0
        self.transparency_dict = {}  # {uid: part display transparency}
        # {uid: [list of ancestor shapes]}
        self.ancestor_dict = defaultdict(list)
        self.ais_shape_dict = {}  # {uid: <AIS_Shape> object}

        self.activeWp = None  # WorkPlane object
        self.activeWpUID = 0
        self.wp_dict = {}  # k = uid, v = wpObject
        self._wpNmbr = 1

        self.activeAsyUID = 0
        self.assy_list = []  # list of assy uid's
        self.showItemActive(0)
        self.setActiveAsy(self.activeAsyUID)

        # Show 'Top' assy in initial tree view
        dm.parse_doc()
        self.build_tree()

    def createDockWidget(self):
        self.treeDockWidget = QDockWidget("Assy/Part Structure", self)
        self.treeDockWidget.setObjectName("treeDockWidget")
        self.treeDockWidget.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self.treeView = TreeView()  # Assy/Part structure (display)
        self.treeView.itemClicked.connect(self.treeViewItemClicked)
        self.treeDockWidget.setWidget(self.treeView)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.treeDockWidget)

    def centerOnScreen(self):
        """Centers the window on the screen."""
        resolution = QDesktopWidget().screenGeometry()
        self.move(
            (resolution.width() / 2) - (self.frameSize().width() / 2),
            (resolution.height() / 2) - (self.frameSize().height() / 2),
        )

    def contextMenu(self, point):
        self.menu = QMenu()
        self.popMenu.exec_(self.mapToGlobal(point))

    def add_menu(self, menu_name):
        _menu = self.menu_bar.addMenu("&" + menu_name)
        self._menus[menu_name] = _menu
        return _menu

    def add_function_to_menu(self, menu_name, text, _callable):
        assert callable(_callable), "the function supplied is not callable"
        try:
            _action = QAction(text, self)
            # if not, the "exit" action is now shown...
            # Qt is trying so hard to be native cocoa'ish that its a nuisance
            _action.setMenuRole(QAction.NoRole)
            _action.triggered.connect(_callable)
            self._menus[menu_name].addAction(_action)
        except KeyError:
            raise ValueError("the menu item %s does not exist" % (menu_name))

    def closeEvent(self, event):  # things that need to happen on exit
        try:
            self.calculator.close()
        except AttributeError:
            pass
        event.accept()

    #############################################
    #
    # treeView (QTreeWidget) building methods:
    #
    #############################################

    def build_tree(self):
        """Build new tree view from dm.label_dict.

        This method is called whenever dm.doc is modified in a way that would
        result in a change in the tree view. The tree view represents the
        hierarchical structure of the top assembly and its components."""
        self.clearTree()
        self.assy_list = []
        parent_item_dict = {}  # {uid: tree view item}
        for uid, dic in dm.label_dict.items():
            # dic: {keys: 'entry', 'name', 'parent_uid', 'ref_entry'}
            entry = dic["entry"]
            name = dic["name"]
            parent_uid = dic["parent_uid"]
            if parent_uid not in parent_item_dict:
                parent_item = self.assy_root
            else:
                parent_item = parent_item_dict[parent_uid]

            # create node in tree view
            item_name = [name, uid]
            item = QTreeWidgetItem(parent_item, item_name)
            item.setFlags(item.flags() | Qt.ItemIsTristate |
                          Qt.ItemIsUserCheckable)
            if uid in self.hide_list:
                item.setCheckState(0, Qt.Unchecked)
            else:
                item.setCheckState(0, Qt.Checked)
            self.treeView.expandItem(item)
            parent_item_dict[uid] = item
            # build assy_list
            if dic["is_assy"]:
                self.assy_list.append(uid)
        self.sync_treeview_to_active()
        # self.syncCheckedToDrawList()

    def clearTree(self):
        """Remove all tree view widget items and replace root item"""
        self.treeView.clear()
        self.assy_root, self.wp_root = self.create_root_items()
        self.repopulate_2D_tree_view()

    def create_root_items(self):
        """Create '2D' & '3D' root items in treeView."""
        root_item = ["/", "0"]  # [name, uid]
        tree_view_root = QTreeWidgetItem(self.treeView, root_item)
        self.treeView.expandItem(tree_view_root)
        wp_root = QTreeWidgetItem(tree_view_root, ["WP", "wp0"])
        self.treeView.expandItem(wp_root)
        ay_root = QTreeWidgetItem(tree_view_root, ["3D", "0:1:1.0"])
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

    #############################################
    #
    # treeView item action methods:
    #
    #############################################

    def treeViewItemClicked(self, item):
        """Called when treeView item is clicked"""

        self.itemClicked = item  # store item
        if not self.inSync():  # click may have been on checkmark.
            self.adjust_draw_hide()

    def inSync(self):
        """Return True if unchecked items are in sync with hide_list."""
        return set(self.uncheckedToList()) == set(self.hide_list)

    def uncheckedToList(self):
        """Return list of uid's of unchecked (part & wp) items in treeView."""
        dl = []
        for item in self.treeView.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            if item.checkState(0) == Qt.Unchecked:
                uid = item.text(1)
                if (uid in dm.part_dict) or (uid in self.wp_dict):
                    dl.append(uid)
        return dl

    def adjust_draw_hide(self):
        """Erase from 3D display any item that gets unchecked, draw when checked.

        An item is a treeView widget item. It may be a part, assy or workplane.
        For our purpose here, we only care if it is a part or wp because those
        are the only types that are displayed in the 3D view window. For parts,
        the display is adjusted incrementally. A newly checked part is drawn and
        a newly unchecked part is erased. However, because workplanes have a
        great many ais_shapes, ais lines, ais_circles and topoDS_shapes (edges &
        border) as well, it isn't practical to keep track of them all just so
        they can be removed incrementally. Also, when a new workplane is created,
        it is set active, so the old active workplane needs to be redrawn with a
        duller border color. Therefore, if there is a change in the hide_list
        involving a workplane, it is best to just clear the display and redraw
        all the workplanes that are not in the hide_list.
        """

        unchecked = self.uncheckedToList()
        unchecked_set = set(unchecked)
        hide_list = list(self.hide_list)
        hide_set = set(hide_list)
        newly_unchecked = unchecked_set - hide_set
        newly_checked = hide_set - unchecked_set
        for uid in newly_unchecked:
            # If a workplane is newly unchecked, redraw is needed
            if uid in self.wp_dict:
                self.hide_list.append(uid)
                self.redraw()
            # Otherwise, we can do an incremental change in the display
            elif uid in dm.part_dict:
                self.erase_shape(uid)  # Erase the shape
        for uid in newly_checked:
            if uid in dm.part_dict:
                self.draw_shape(uid)  # Draw the shape
            elif uid in self.wp_dict:
                self.draw_wp(uid)  # Draw the workplane
        self.hide_list = unchecked

    def syncUncheckedToHideList(self):
        """Use this method after building a new treeView to make sure items
        that were previously hidden are still unchecked in new treeView."""
        for item in self.treeView.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            uid = item.text(1)
            if (uid in dm.part_dict) or (uid in self.wp_dict):
                if uid in self.hide_list:
                    item.setCheckState(0, Qt.Unchecked)
                else:
                    item.setCheckState(0, Qt.Checked)

    def sortViewItems(self):
        """Return dicts of tree view items sorted by type: (prt, ay, wp)"""
        # Traverse all treeView widget items
        iterator = QTreeWidgetItemIterator(self.treeView)
        pdict = {}  # part-types    {uid: item}
        adict = {}  # asy-types     {uid: item}
        wdict = {}  # wp-types      {uid: item}
        while iterator.value():
            item = iterator.value()
            name = item.text(0)
            uid = item.text(1)
            if uid in dm.part_dict:
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
            if name in ["/", "WP", "3D"]:
                print(f"Root ({name}) tree view item")
            elif uid.startswith("wp"):
                print(f"Workplane: uid: {uid}; name: {name}")
            else:
                entry = dm.label_dict[uid]["entry"]
                ref_ent = dm.label_dict[uid]["ref_entry"]
                is_assy = dm.label_dict[uid]["is_assy"]
                if is_assy:
                    print(
                        f"Assembly: uid: {uid}; name: {name}; entry: {entry}; ref_entry: {ref_ent}"
                    )
                else:
                    print(
                        f"Part: uid: {uid}; name: {name}; entry: {entry}; ref_entry: {ref_ent}"
                    )

    def setClickedActive(self):
        """Set item clicked in treeView Active."""
        item = self.itemClicked
        if item:
            self.setItemActive(item)
            self.treeView.clearSelection()
            self.itemClicked = None

    def setItemActive(self, item):
        """Set (part, wp or assy) represented by treeView item to be active."""
        if item:
            name = item.text(0)
            uid = item.text(1)
            print(f"Part selected: {name}, UID: {uid}")
            pd, ad, wd = self.sortViewItems()
            if uid in pd:
                self.setActivePart(uid)
                sbText = f"{name} [uid={uid}] is now the active part"
            elif uid in wd:
                self.setActiveWp(uid)
                sbText = f"{name} [uid={uid}] is now the active workplane"
                self.redraw()  # update color of new active wp
            elif uid in ad:
                self.setActiveAsy(uid)
                sbText = f"{name} [uid={uid}] is now the active assembly"
            else:
                sbText = f"{name} [uid={uid}] Unable to set active."
            self.statusBar().showMessage(sbText, 5000)

    def showItemActive(self, uid):
        """Update tree view to show active status of (uid)."""
        pd, ad, wd = self.sortViewItems()
        if uid in pd:
            # Clear BG color of all part items
            for itm in pd.values():
                itm.setBackground(0, QBrush(QColor(255, 255, 255, 0)))
            # Set BG color of new active part
            pd[uid].setBackground(0, QBrush(QColor("gold")))
        elif uid in wd:
            # Clear BG color of all wp items
            for itm in wd.values():
                itm.setBackground(0, QBrush(QColor(255, 255, 255, 0)))
            # Set BG color of new active wp
            wd[uid].setBackground(0, QBrush(QColor("lightgreen")))
        elif uid in ad:
            # Clear BG color of all asy items
            for itm in ad.values():
                itm.setBackground(0, QBrush(QColor(255, 255, 255, 0)))
            # Set BG color of new active asy
            ad[uid].setBackground(0, QBrush(QColor("lightblue")))

    def sync_treeview_to_active(self):
        for uid in (self.activePartUID, self.activeAsyUID, self.activeWpUID):
            if uid:
                self.showItemActive(uid)

    def setTransparent(self):
        """Set treeView item clicked transparent"""
        item = self.itemClicked
        if item:
            uid = item.text(1)
            if uid in dm.part_dict:
                self.transparency_dict[uid] = 0.6
                self.erase_shape(uid)
                self.draw_shape(uid)
            self.itemClicked = None

    def setOpaque(self):
        """Set treeView item clicked opaque"""
        item = self.itemClicked
        if item:
            uid = item.text(1)
            if uid in dm.part_dict:
                self.transparency_dict.pop(uid)
                self.erase_shape(uid)
                self.draw_shape(uid)
            self.itemClicked = None

    def editName(self):
        """Edit name of treeView item clicked"""
        item = self.itemClicked
        if item:
            name = item.text(0)
            uid = item.text(1)
            prompt = "Enter new name for part %s" % name
            newName, OK = QInputDialog.getText(
                self, "Input Dialog", prompt, text=name)
            if OK:
                item.setText(0, newName)
                print(f"UID= {uid}, name = {newName}")
                self.treeView.clearSelection()
                self.itemClicked = None
                dm.change_label_name(uid, newName)
                self.build_tree()

    #############################################
    #
    # Administrative and data management methods:
    #
    #############################################

    def get_wp_uid(self, wp_objct):
        """ Assign (and return) a new uid to a new workplane.
            Add item to treeview (2D)
            Make wp active
            Add to self.wp_dict.
        """
        uid = "wp%i" % self._wpNmbr
        self._wpNmbr += 1
        self.wp_dict[uid] = wp_objct
        # Add treeView item
        itemName = [uid, uid]
        item = QTreeWidgetItem(self.wp_root, itemName)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        # Make new workplane active
        self.setActiveWp(uid)
        return uid

    def appendToStack(self):
        """Called when <ret> is pressed on line edit"""
        self.lineEditStack.append(self.lineEdit.text())
        self.lineEdit.clear()
        cb = self.registeredCallback
        if cb:
            cb([])  # call self.registeredCallback with arg=empty_list
        else:
            self.lineEditStack.pop()

    def setActivePart(self, uid):
        """Change active part status in a coordinated manner."""
        # modify status in self
        self.activePartUID = uid
        if uid:
            self.activePart = dm.part_dict[uid]["shape"]
            # show as active in treeView
            self.showItemActive(uid)
        else:
            self.activePart = None

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
        if uid:
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
        """Clear lineEditStack"""
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
        if currCallback:  # Make sure a callback isn't already registered
            self.clearCallback()
        self.canvas._display.register_select_callback(callback)
        self.registeredCallback = callback
        self.currOpLabel.setText("Current Operation: %s " %
                                 callback.__name__[:-1])

    def clearCallback(self):
        if self.registeredCallback:
            self.canvas._display.unregister_callback(self.registeredCallback)
            self.registeredCallback = None
            self.clearAllStacks()
            self.currOpLabel.setText("Current Operation: None ")
            self.statusBar().showMessage("")
            self.canvas._display.SetSelectionModeNeutral()

    #############################################
    #
    # 3D Display Draw/Hide methods:
    #
    #############################################

    def fitAll(self):
        """Fit all displayed parts and wp's to the screen"""
        self.canvas._display.FitAll()

    def redraw(self):
        """Erase & redraw all parts & workplanes except those in hide_list."""
        context = self.canvas._display.Context
        if not self.registeredCallback:
            self.canvas._display.SetSelectionModeNeutral()
            context.SetAutoActivateSelection(True)
        context.RemoveAll(True)
        # Redraw all parts except those hidden
        for uid in dm.part_dict:
            if uid not in self.hide_list:
                self.draw_shape(uid)
        # Redraw workplanes except those hidden
        self.redraw_workplanes()

    def redraw_workplanes(self):
        """Redraw all workplanes except those in self.hide_list"""

        for uid in self.wp_dict:
            if uid not in self.hide_list:
                self.draw_wp(uid)

    def draw_wp(self, uid):
        """Draw the workplane with uid."""
        context = self.canvas._display.Context
        if uid:
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
                self.canvas._display.DisplayShape(point)
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
                self.canvas._display.DisplayShape(edge, color="WHITE")
            self.canvas._display.Repaint()

    def draw_shape(self, uid):
        """Draw the part (shape) with uid."""
        context = self.canvas._display.Context
        if uid:
            if uid in self.transparency_dict:
                transp = self.transparency_dict[uid]
            else:
                transp = 0.0
            part_data = dm.part_dict[uid]
            shape = part_data["shape"]
            color = part_data["color"]
            try:
                aisShape = AIS_Shape(shape)
                self.ais_shape_dict[uid] = aisShape
                context.Display(aisShape, True)
                context.SetColor(aisShape, color, True)
                # Set shape transparency, a float from 0.0 to 1.0
                context.SetTransparency(aisShape, transp, True)
                drawer = aisShape.DynamicHilightAttributes()
                context.HilightWithColor(aisShape, drawer, True)
            except AttributeError as e:
                print(e)

    def erase_shape(self, uid):
        """Erase the part (shape) with uid."""
        if uid in self.ais_shape_dict:
            context = self.canvas._display.Context
            aisShape = self.ais_shape_dict[uid]
            # This did the job prior to PyOCC 7.6
            context.Remove(aisShape, True)
            # Added to get 'hide' working in PyOCC 7.6
            context.Erase(aisShape, True)

    #############################################
    #
    # 3D Measure functions...
    #
    #############################################

    def launchCalc(self):
        """Launch Calculator"""
        if not self.calculator:
            self.calculator = rpnCalculator.Calculator(self)
            self.calculator.show()

    def setUnits(self, units):
        """Set units of linear distance (Default is 'mm')"""
        if units in self._unitDict.keys():
            self.units = units
            self.unitscale = self._unitDict[self.units]
            self.unitsLabel.setText("Units: %s " % self.units)

    def distPtPt(self):
        """Measure distance between 2 selectable points on model or workplane"""
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
            self.canvas._display.SetSelectionModeVertex()
            statusText = "Select 2 points to measure distance."
            self.statusBar().showMessage(statusText)

    def distPtPtC(self, shapeList, *args):
        """Callback (collector) for distPtPt"""
        logger.debug("Edges selected: %s", shapeList)
        logger.debug("args: %s", args)  # args = x, y mouse coords
        for shape in shapeList:
            vrtx = topods_Vertex(shape)
            gpPt = BRep_Tool.Pnt(vrtx)  # convert vertex to gp_Pnt
            self.ptStack.append(gpPt)
        if len(self.ptStack) == 2:
            self.distPtPt()

    def edgeLen(self):
        """Measure length of a part edge or geometry profile line"""
        if self.edgeStack:
            edge = self.edgeStack.pop()
            edgelen = CPnts_AbscissaPoint_Length(BRepAdaptor_Curve(edge))
            edgelen = edgelen / self.unitscale
            self.calculator.putx(edgelen)
            self.edgeLen()
        else:
            self.registerCallback(self.edgeLenC)
            self.canvas._display.SetSelectionModeEdge()
            statusText = "Pick an edge to measure."
            self.statusBar().showMessage(statusText)

    def edgeLenC(self, shapeList, *args):
        """Callback (collector) for edgeLen"""
        logger.debug("Edges selected: %s", shapeList)
        logger.debug("args: %s", args)  # args = x, y mouse coords
        for shape in shapeList:
            edge = topods_Edge(shape)
            self.edgeStack.append(edge)
        if self.edgeStack:
            self.edgeLen()
